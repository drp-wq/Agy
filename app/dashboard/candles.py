"""Real-time, deduplicated, rate-limit-safe OHLCV candle feed service for dashboard endpoints."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
import time
from typing import Any

import ccxt
from fastapi import HTTPException

from app.core.models import Candle, ExchangeId, MarketType
from app.exchanges.base import ExchangeOutageError
from app.service import TradingEngineService

ALLOWED_TIMEFRAMES: frozenset[str] = frozenset({"1m", "5m", "15m", "1h", "4h", "1d"})
MAX_CACHED_CANDLES = 500


@dataclass
class _CandleCacheEntry:
    candles_by_ts: dict[int, Candle] = field(default_factory=dict)
    fetched_at_mono: float = 0.0
    fetched_at_iso: str = ""
    source: str = "exchange_rest"


def normalize_since_ms(since: int | None) -> int | None:
    """Normalize epoch seconds or milliseconds into milliseconds."""
    if since is None or since <= 0:
        return None
    return since * 1000 if since < 10_000_000_000 else since


def sanitize_and_merge_candles(
    existing: dict[int, Candle],
    incoming: list[Candle],
) -> dict[int, Candle]:
    """Merge candles by timestamp, discarding invalid bars and bounding buffer size."""
    merged = dict(existing)
    for c in incoming:
        if (
            c.timestamp > 0
            and math.isfinite(c.open)
            and math.isfinite(c.high)
            and math.isfinite(c.low)
            and math.isfinite(c.close)
            and math.isfinite(c.volume)
        ):
            merged[c.timestamp] = c
    if len(merged) > MAX_CACHED_CANDLES:
        keep_keys = sorted(merged.keys())[-MAX_CACHED_CANDLES:]
        merged = {k: merged[k] for k in keep_keys}
    return merged


class CandleFeedService:
    """Coalesces and caches live candle queries per (exchange, market_type, symbol, timeframe)."""

    def __init__(self, cache_ttl_sec: float = 4.0) -> None:
        self.cache_ttl_sec = cache_ttl_sec
        self._cache: dict[str, _CandleCacheEntry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def get_candles(
        self,
        service: TradingEngineService,
        exchange: str,
        market_type: str,
        base: str,
        quote: str,
        timeframe: str,
        since: int | None = None,
        limit: int = 120,
        allow_stale: bool = False,
    ) -> dict[str, Any]:
        symbol = f"{base.upper()}/{quote.upper()}"
        tf = timeframe.strip()
        if tf not in ALLOWED_TIMEFRAMES:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported timeframe '{timeframe}'. Allowed: {sorted(ALLOWED_TIMEFRAMES)}",
            )
        try:
            ex_id = ExchangeId(exchange.lower())
            mtype = MarketType(market_type.lower())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        clamped_limit = max(1, min(int(limit), MAX_CACHED_CANDLES))
        since_ms = normalize_since_ms(since)
        cache_key = f"{ex_id.value}:{mtype.value}:{symbol}:{tf}"
        snap_key = f"{ex_id.value}:{mtype.value}:{symbol}"
        adapter = service.registry.get(ex_id)

        async with self._lock_for(cache_key):
            now_mono = time.monotonic()
            now_dt = datetime.now(timezone.utc)
            now_ts = int(now_dt.timestamp())
            now_iso = now_dt.isoformat()
            entry = self._cache.get(cache_key)

            # 1) Serve from fresh live cache within TTL to prevent exchange API spam
            if entry is not None and (now_mono - entry.fetched_at_mono) <= self.cache_ttl_sec:
                ordered = [entry.candles_by_ts[k] for k in sorted(entry.candles_by_ts.keys())]
                if since_ms is not None:
                    ordered = [c for c in ordered if c.timestamp >= since_ms]
                else:
                    ordered = ordered[-clamped_limit:]
                return self._build_payload(
                    ex_id=ex_id,
                    mtype=mtype,
                    symbol=symbol,
                    timeframe=tf,
                    candles=ordered,
                    source="live_cache",
                    incremental=since_ms is not None,
                    stale=False,
                    fetched_at=now_ts,
                    fetched_at_iso=entry.fetched_at_iso or now_iso,
                    health=adapter.health_status(),
                )

            # 2) Use freshly scanned snapshot cache if within TTL and not requesting incremental tail
            snap = service.scanner.latest_snapshots.get(snap_key)
            if snap and tf in snap.candles_by_tf and since_ms is None:
                snap_ts = snap.timestamp if snap.timestamp.tzinfo else snap.timestamp.replace(tzinfo=timezone.utc)
                snap_age = max(0.0, (now_dt - snap_ts).total_seconds())
                if snap_age <= self.cache_ttl_sec:
                    merged = sanitize_and_merge_candles(
                        entry.candles_by_ts if entry else {},
                        snap.candles_by_tf[tf],
                    )
                    self._cache[cache_key] = _CandleCacheEntry(
                        candles_by_ts=merged,
                        fetched_at_mono=now_mono,
                        fetched_at_iso=snap_ts.isoformat(),
                        source="scanner_cache",
                    )
                    ordered = [merged[k] for k in sorted(merged.keys())][-clamped_limit:]
                    return self._build_payload(
                        ex_id=ex_id,
                        mtype=mtype,
                        symbol=symbol,
                        timeframe=tf,
                        candles=ordered,
                        source="scanner_cache",
                        incremental=False,
                        stale=False,
                        fetched_at=now_ts,
                        fetched_at_iso=snap_ts.isoformat(),
                        health=adapter.health_status(),
                    )

            # 3) Fetch fresh candles from exchange adapter (incremental tail when since_ms is set)
            fetch_limit = min(max(clamped_limit, 5), 30) if (since_ms is not None and entry is not None) else clamped_limit
            try:
                fetched = await adapter.fetch_ohlcv(
                    symbol,
                    mtype,
                    timeframe=tf,
                    limit=fetch_limit,
                    since=since_ms,
                )
            except (
                ExchangeOutageError,
                ccxt.RateLimitExceeded,
                ccxt.RequestTimeout,
                ccxt.NetworkError,
                ccxt.ExchangeError,
            ) as exc:
                if allow_stale:
                    stale_bars = self._stale_fallback_bars(entry, snap, tf, since_ms, clamped_limit)
                    if stale_bars is not None:
                        return self._build_payload(
                            ex_id=ex_id,
                            mtype=mtype,
                            symbol=symbol,
                            timeframe=tf,
                            candles=stale_bars,
                            source="stale_cache",
                            incremental=since_ms is not None,
                            stale=True,
                            stale_reason=type(exc).__name__,
                            fetched_at=now_ts,
                            fetched_at_iso=entry.fetched_at_iso if entry else now_iso,
                            health=adapter.health_status(),
                        )
                raise self._map_exchange_error(ex_id, exc) from exc

            merged = sanitize_and_merge_candles(
                entry.candles_by_ts if entry else {},
                fetched,
            )
            self._cache[cache_key] = _CandleCacheEntry(
                candles_by_ts=merged,
                fetched_at_mono=time.monotonic(),
                fetched_at_iso=now_iso,
                source="exchange_rest",
            )
            if since_ms is not None:
                ordered = [c for c in (merged[k] for k in sorted(merged.keys())) if c.timestamp >= since_ms]
            else:
                ordered = [merged[k] for k in sorted(merged.keys())][-clamped_limit:]

            return self._build_payload(
                ex_id=ex_id,
                mtype=mtype,
                symbol=symbol,
                timeframe=tf,
                candles=ordered,
                source="exchange_rest",
                incremental=since_ms is not None,
                stale=False,
                fetched_at=now_ts,
                fetched_at_iso=now_iso,
                health=adapter.health_status(),
            )

    @staticmethod
    def _stale_fallback_bars(
        entry: _CandleCacheEntry | None,
        snap: Any,
        tf: str,
        since_ms: int | None,
        limit: int,
    ) -> list[Candle] | None:
        if entry is not None and entry.candles_by_ts:
            bars = [entry.candles_by_ts[k] for k in sorted(entry.candles_by_ts.keys())]
        elif snap is not None and tf in snap.candles_by_tf and snap.candles_by_tf[tf]:
            merged = sanitize_and_merge_candles({}, snap.candles_by_tf[tf])
            bars = [merged[k] for k in sorted(merged.keys())]
        else:
            return None
        if since_ms is not None:
            return [c for c in bars if c.timestamp >= since_ms]
        return bars[-limit:]

    @staticmethod
    def _map_exchange_error(ex_id: ExchangeId, exc: Exception) -> HTTPException:
        if isinstance(exc, ccxt.RateLimitExceeded):
            return HTTPException(
                status_code=429,
                detail={
                    "message": f"{ex_id.value.upper()} rate limit exceeded; retry with backoff",
                    "error_code": "rate_limited",
                    "exchange": ex_id.value,
                    "retry_after_sec": 15,
                },
                headers={"Retry-After": "15"},
            )
        if isinstance(exc, ccxt.RequestTimeout):
            return HTTPException(
                status_code=504,
                detail={
                    "message": f"{ex_id.value.upper()} OHLCV request timed out",
                    "error_code": "request_timeout",
                    "exchange": ex_id.value,
                },
            )
        if isinstance(exc, ExchangeOutageError):
            return HTTPException(
                status_code=503,
                detail={
                    "message": f"{ex_id.value.upper()} circuit breaker open (exchange disconnected)",
                    "error_code": "exchange_circuit_open",
                    "exchange": ex_id.value,
                },
            )
        return HTTPException(
            status_code=503,
            detail={
                "message": f"{ex_id.value.upper()} market feed unavailable ({type(exc).__name__})",
                "error_code": "exchange_unavailable",
                "exchange": ex_id.value,
            },
        )

    @staticmethod
    def _build_payload(
        *,
        ex_id: ExchangeId,
        mtype: MarketType,
        symbol: str,
        timeframe: str,
        candles: list[Candle],
        source: str,
        incremental: bool,
        stale: bool,
        fetched_at: int,
        fetched_at_iso: str,
        health: dict[str, Any],
        stale_reason: str | None = None,
    ) -> dict[str, Any]:
        return {
            "exchange": ex_id.value,
            "market_type": mtype.value,
            "symbol": symbol,
            "timeframe": timeframe,
            "source": source,
            "incremental": incremental,
            "stale": stale,
            "stale_reason": stale_reason,
            "fetched_at": fetched_at,
            "fetched_at_iso": fetched_at_iso,
            "last_candle_at": candles[-1].timestamp if candles else None,
            "exchange_healthy": bool(health.get("healthy", True)),
            "consecutive_errors": int(health.get("consecutive_errors", 0)),
            "last_ok_at": health.get("last_ok_at"),
            "candles": [c.model_dump() for c in candles],
        }
