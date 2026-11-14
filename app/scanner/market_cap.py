"""Circulating market-cap classification with documented provenance, caching, and freshness checks."""

from __future__ import annotations

import asyncio
import logging
import time
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

import httpx
from pydantic import BaseModel, Field

from app.core.config import MidLowCapConfig

logger = logging.getLogger("multi_exchange.market_cap")

COINGECKO_MARKETS_URL = "https://api.coingecko.com/api/v3/coins/markets"


class MarketCapBand(str, Enum):
    MICRO = "micro"
    LOW = "low"
    MID = "mid"
    LARGE = "large"
    MEGA = "mega"
    UNKNOWN = "unknown"

    @property
    def is_target_mid_low(self) -> bool:
        return self in (MarketCapBand.LOW, MarketCapBand.MID)


class MarketCapRecord(BaseModel):
    base_symbol: str
    market_cap_usd: float | None = None
    circulating_supply: float | None = None
    band: MarketCapBand = MarketCapBand.UNKNOWN
    source: str = "unavailable"
    source_id: str | None = None
    source_name: str | None = None
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_updated_at: datetime | None = None
    age_sec: float | None = None
    is_fresh: bool = False
    ambiguous: bool = False
    reason: str | None = None


def _parse_iso_dt(raw: str | None) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    try:
        cleaned = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def classify_market_cap_band(
    market_cap_usd: float | None,
    config: MidLowCapConfig,
    *,
    is_fresh: bool = True,
    ambiguous: bool = False,
) -> MarketCapBand:
    """Classify a coin purely from verified circulating market cap; never infer from volume/price."""
    if ambiguous or not is_fresh or market_cap_usd is None or market_cap_usd <= 0:
        return MarketCapBand.UNKNOWN
    if hasattr(config, "mega_cap_min_usd"):
        import math
        if not math.isfinite(market_cap_usd) or market_cap_usd < config.micro_cap_min_usd:
            return MarketCapBand.UNKNOWN
        if market_cap_usd >= config.mega_cap_min_usd:
            return MarketCapBand.MEGA
    if market_cap_usd < config.low_cap_min_usd:
        return MarketCapBand.MICRO
    if market_cap_usd < config.low_cap_max_usd:
        return MarketCapBand.LOW
    if market_cap_usd <= config.mid_cap_max_usd:
        return MarketCapBand.MID
    return MarketCapBand.LARGE


PageFetcher = Callable[[int, int], Awaitable[list[dict[str, Any]]]]


class MarketCapProvider:
    """Bounded, rate-limited, cached circulating market-cap provider (no API secrets required)."""

    def __init__(
        self,
        config: MidLowCapConfig | None = None,
        page_fetcher: PageFetcher | None = None,
        cache_path: Path | None = None,
    ) -> None:
        self.config = config or MidLowCapConfig()
        self._page_fetcher = page_fetcher or self._fetch_coingecko_page
        self._cache: dict[str, list[dict[str, Any]]] = {}
        self._cache_fetched_at: datetime | None = None
        self._cache_monotonic: float = 0.0
        self._last_request_monotonic: float = 0.0
        self._lock = asyncio.Lock()
        self.last_error: str | None = None
        self.requests_made: int = 0
        self._refresh_failures = 0
        self._retry_after_monotonic = 0.0
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache_restored = False
        self._restore_cache()

    def _restore_cache(self):
        if self.cache_path is None or not self.cache_path.exists():
            return
        try:
            if self.cache_path.stat().st_size > 10_000_000:
                raise ValueError('oversize_cap_cache')
            saved = json.loads(self.cache_path.read_text())
            fetched = _parse_iso_dt(saved.get('fetched_at'))
            age = (datetime.now(timezone.utc)-fetched).total_seconds() if fetched else None
            cache = saved.get('cache', {})
            if not isinstance(cache, dict) or any(not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows) for rows in cache.values()):
                raise ValueError('invalid_cap_cache')
            # Preserve original provenance and age; never re-date cached public data.
            if age is not None and 0 <= age <= self.config.market_cap_max_age_sec:
                self._cache, self._cache_fetched_at = cache, fetched
                self._cache_monotonic = time.monotonic()-age
                self.cache_restored = True
            retry_at = _parse_iso_dt(saved.get('retry_at'))
            if retry_at:
                remaining = max(0, min(3600, (retry_at-datetime.now(timezone.utc)).total_seconds()))
                self._retry_after_monotonic = time.monotonic()+remaining
            self._refresh_failures = min(10, max(0, int(saved.get('failures',0))))
            self.last_error = saved.get('last_error') if isinstance(saved.get('last_error'),str) else None
        except (ValueError, TypeError, OSError):
            self.last_error = 'invalid_persisted_market_cap_cache'

    def _persist_cache(self):
        if self.cache_path is None:
            return
        from datetime import timedelta
        remaining = max(0, self._retry_after_monotonic-time.monotonic())
        saved = {'cache':self._cache, 'fetched_at':self._cache_fetched_at.isoformat() if self._cache_fetched_at else None,
                 'retry_at':(datetime.now(timezone.utc)+timedelta(seconds=remaining)).isoformat(),
                 'failures':self._refresh_failures, 'last_error':self.last_error}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_suffix('.tmp')
        with tmp.open('w') as stream:
            os.chmod(tmp, 0o600)
            json.dump(saved, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, self.cache_path)

    async def _fetch_coingecko_page(self, page: int, per_page: int) -> list[dict[str, Any]]:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": str(per_page),
            "page": str(page),
            "sparkline": "false",
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(COINGECKO_MARKETS_URL, params=params)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                raise ValueError("Unexpected market cap API payload format")
            return data

    def _is_cache_valid(self) -> bool:
        if not self._cache or self._cache_fetched_at is None:
            return False
        return (time.monotonic() - self._cache_monotonic) < self.config.market_cap_cache_ttl_sec

    async def refresh(self, force: bool = False) -> bool:
        """Fetch bounded pages of circulating market-cap metadata with rate limiting."""
        async with self._lock:
            if not force and self._is_cache_valid():
                return True
            if time.monotonic() < self._retry_after_monotonic:
                return False

            by_symbol: dict[str, list[dict[str, Any]]] = {}
            try:
                for page in range(1, self.config.market_cap_max_pages + 1):
                    elapsed = time.monotonic() - self._last_request_monotonic
                    wait_sec = self.config.market_cap_min_request_interval_sec - elapsed
                    if wait_sec > 0 and self._last_request_monotonic > 0:
                        await asyncio.sleep(wait_sec)
                    self._last_request_monotonic = time.monotonic()
                    self.requests_made += 1
                    rows = await self._page_fetcher(page, 250)
                    if not rows:
                        break
                    for row in rows:
                        sym = str(row.get("symbol") or "").strip().upper()
                        if not sym:
                            continue
                        by_symbol.setdefault(sym, []).append(row)
                    if len(rows) < 250:
                        break

                if by_symbol:
                    self._cache = by_symbol
                    self._cache_fetched_at = datetime.now(timezone.utc)
                    self._cache_monotonic = time.monotonic()
                    self.last_error = None
                    self._refresh_failures = 0
                    self._retry_after_monotonic = 0.0
                    return True
                self.last_error = "empty_market_cap_response"
                return False
            except Exception as exc:
                self._refresh_failures += 1
                delay = min(60 * 2 ** min(self._refresh_failures - 1, 4), 900)
                if isinstance(exc, httpx.HTTPStatusError):
                    try:
                        delay = max(delay, min(float(exc.response.headers.get('Retry-After', 0)), 3600))
                    except ValueError:
                        pass
                self._retry_after_monotonic = time.monotonic() + delay
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Market-cap refresh failed (%s); preserving safety UNKNOWN fallback", type(exc).__name__)
                return False
            finally:
                self._persist_cache()

    def classify_symbol(self, base_symbol: str, now: datetime | None = None) -> MarketCapRecord:
        """Classify a base symbol synchronously from cached market-cap records."""
        sym = base_symbol.strip().upper()
        ref_now = now or datetime.now(timezone.utc)
        rows = self._cache.get(sym)
        if not rows or self._cache_fetched_at is None:
            return MarketCapRecord(
                base_symbol=sym,
                band=MarketCapBand.UNKNOWN,
                source="unavailable",
                fetched_at=ref_now,
                is_fresh=False,
                reason=self.last_error or "market_cap_unavailable",
            )

        # Sort descending by market_cap so canonical highest-cap asset is first
        valid_rows = [
            r for r in rows
            if isinstance(r.get("market_cap"), (int, float))
            and float(r["market_cap"]) > 0
            and isinstance(r.get("circulating_supply"), (int, float))
            and float(r["circulating_supply"]) > 0
        ]
        if not valid_rows:
            return MarketCapRecord(
                base_symbol=sym,
                band=MarketCapBand.UNKNOWN,
                source=self.config.market_cap_provider,
                fetched_at=self._cache_fetched_at,
                is_fresh=False,
                reason="missing_circulating_market_cap_or_supply",
            )

        valid_rows.sort(key=lambda r: float(r["market_cap"]), reverse=True)
        top = valid_rows[0]
        top_mc = float(top["market_cap"])
        top_supply = float(top["circulating_supply"])

        # Ambiguity guard: if a second distinct coin with same ticker has >10% of top's market cap
        ambiguous = False
        if len(valid_rows) > 1:
            second_mc = float(valid_rows[1]["market_cap"])
            if second_mc >= top_mc * 0.10:
                ambiguous = True

        source_updated = _parse_iso_dt(top.get("last_updated"))
        ref_ts = source_updated or self._cache_fetched_at
        age_sec = max(0.0, (ref_now - ref_ts).total_seconds())
        is_fresh = age_sec <= self.config.market_cap_max_age_sec

        band = classify_market_cap_band(
            top_mc,
            self.config,
            is_fresh=is_fresh,
            ambiguous=ambiguous,
        )
        reason = None
        if ambiguous:
            reason = "ambiguous_ticker_collision"
        elif not is_fresh:
            reason = f"stale_market_cap:{age_sec:.0f}s"

        return MarketCapRecord(
            base_symbol=sym,
            market_cap_usd=top_mc,
            circulating_supply=top_supply,
            band=band,
            source=self.config.market_cap_provider,
            source_id=str(top.get("id") or ""),
            source_name=str(top.get("name") or ""),
            fetched_at=self._cache_fetched_at,
            source_updated_at=source_updated,
            age_sec=round(age_sec, 1),
            is_fresh=is_fresh,
            ambiguous=ambiguous,
            reason=reason,
        )

    async def get_classification(self, base_symbol: str) -> MarketCapRecord:
        await self.refresh(force=False)
        return self.classify_symbol(base_symbol)

    def status_summary(self) -> dict[str, Any]:
        extra_bands = {}
        if hasattr(self.config, 'mega_cap_min_usd'):
            extra_bands = {'micro': [self.config.micro_cap_min_usd, self.config.low_cap_min_usd],
                           'large': [self.config.mid_cap_max_usd, self.config.mega_cap_min_usd],
                           'mega': [self.config.mega_cap_min_usd, None]}
        return {
            "provider": self.config.market_cap_provider,
            "cached_symbols": len(self._cache),
            "cache_valid": self._is_cache_valid(),
            "cache_fetched_at": self._cache_fetched_at.isoformat() if self._cache_fetched_at else None,
            "requests_made": self.requests_made,
            "retry_after_sec": round(max(0, self._retry_after_monotonic-time.monotonic()), 1),
            "cache_restored": self.cache_restored,
            "last_error": self.last_error,
            "bands_usd": {
                **extra_bands,
                "low": [self.config.low_cap_min_usd, self.config.low_cap_max_usd],
                "mid": [self.config.mid_cap_min_usd, self.config.mid_cap_max_usd],
            },
        }
