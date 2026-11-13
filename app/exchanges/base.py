"""Base exchange adapter with rate-limiting, retry backoff, outage isolation, and read-only enforcement."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, TypeVar

import ccxt

from app.core.config import ExchangeCredentials
from app.core.models import (
    Candle,
    ExchangeId,
    InstrumentSpec,
    MarketType,
    OrderBookSnapshot,
)
from app.core.security import enforce_read_only_exchange

T = TypeVar("T")
logger = logging.getLogger("multi_exchange.exchanges")


class ExchangeOutageError(RuntimeError):
    """Raised when an exchange adapter is in circuit-breaker cooldown."""


class BaseExchangeAdapter:
    """Thread-safe, rate-limited, read-only exchange adapter."""

    exchange_id: ExchangeId

    def __init__(
        self,
        credentials: ExchangeCredentials | None = None,
        timeout_ms: int = 15000,
        max_retries: int = 2,
        outage_threshold: int = 5,
        cooldown_sec: float = 30.0,
        client_factory: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.credentials = credentials
        self.timeout_ms = timeout_ms
        self.max_retries = max_retries
        self.outage_threshold = outage_threshold
        self.cooldown_sec = cooldown_sec
        self._client_factory = client_factory
        self._lock = threading.RLock()
        self._consecutive_errors = 0
        self._circuit_open_until = 0.0
        self._last_error: str | None = None
        self._last_ok_at: datetime | None = None
        self._markets_loaded = False
        self._public_client = self._build_client(authenticated=False)
        self._auth_client = self._build_client(authenticated=True)
        self._client = self._auth_client

    def _build_ccxt_options(self, authenticated: bool = False) -> dict[str, Any]:
        opts: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": self.timeout_ms,
        }
        if authenticated and self.credentials and self.credentials.is_configured:
            opts.update(self.credentials.as_ccxt_auth())
        return opts

    def _create_raw_client(self, opts: dict[str, Any]) -> Any:
        raise NotImplementedError

    def _build_client(self, authenticated: bool = False) -> Any:
        opts = self._build_ccxt_options(authenticated=authenticated)
        client = self._client_factory(opts) if self._client_factory else self._create_raw_client(opts)
        return enforce_read_only_exchange(client)

    def normalize_symbol(self, symbol: str, market_type: MarketType) -> str:
        """Normalize symbol to CCXT unified spot or linear perpetual format."""
        base_sym = symbol.split(":")[0]
        if market_type == MarketType.PERP:
            if ":USDT" not in symbol and base_sym.endswith("/USDT"):
                return f"{base_sym}:USDT"
            return symbol
        return base_sym

    def _check_circuit(self) -> None:
        now = time.monotonic()
        if now < self._circuit_open_until:
            remaining = round(self._circuit_open_until - now, 1)
            raise ExchangeOutageError(
                f"{self.exchange_id.value} circuit open for {remaining}s ({self._last_error})"
            )

    def _call_with_retry(self, fn: Callable[[], T], op_name: str) -> T:
        self._check_circuit()
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                with self._lock:
                    result = fn()
                self._consecutive_errors = 0
                self._circuit_open_until = 0.0
                self._last_error = None
                self._last_ok_at = datetime.now(timezone.utc)
                return result
            except (ccxt.NetworkError, ccxt.RequestTimeout, ccxt.RateLimitExceeded) as exc:
                last_exc = exc
                self._consecutive_errors += 1
                self._last_error = f"{type(exc).__name__}: {op_name}"
                if isinstance(exc, ccxt.RateLimitExceeded):
                    # Honor throttling with longer exponential backoff and a
                    # shared adapter cooldown if the final retry is exhausted.
                    if attempt < self.max_retries:
                        time.sleep(min(3.0 * (2**attempt), 30.0))
                    else:
                        self._circuit_open_until = max(
                            self._circuit_open_until, time.monotonic() + 60.0
                        )
                elif attempt < self.max_retries:
                    time.sleep(0.35 * (2**attempt))
            except Exception as exc:
                last_exc = exc
                self._consecutive_errors += 1
                self._last_error = f"{type(exc).__name__}: {op_name}"
                break

        if self._consecutive_errors >= self.outage_threshold:
            self._circuit_open_until = time.monotonic() + self.cooldown_sec
            logger.warning(
                "Exchange %s entered outage cooldown for %.0fs after %d errors",
                self.exchange_id.value,
                self.cooldown_sec,
                self._consecutive_errors,
            )
        assert last_exc is not None
        raise last_exc

    async def _run(self, fn: Callable[[], T], op_name: str) -> T:
        return await asyncio.to_thread(self._call_with_retry, fn, op_name)

    async def load_markets(self, reload: bool = False) -> dict[str, Any]:
        if self._markets_loaded and not reload:
            return getattr(self._public_client, "markets", {}) or {}
        markets = await self._run(lambda: self._public_client.load_markets(reload), "load_markets")
        self._markets_loaded = True
        return markets

    async def get_instrument_spec(self, symbol: str, market_type: MarketType) -> InstrumentSpec:
        markets = await self.load_markets()
        norm = self.normalize_symbol(symbol, market_type)
        m = markets.get(norm) or markets.get(symbol) or {}
        precision = m.get("precision", {}) or {}
        limits = m.get("limits", {}) or {}
        amount_limits = limits.get("amount", {}) or {}
        cost_limits = limits.get("cost", {}) or {}
        lev_limits = limits.get("leverage", {}) or {}
        base, quote = (norm.split(":")[0].split("/") + ["USDT"])[:2]

        return InstrumentSpec(
            exchange=self.exchange_id,
            symbol=norm,
            market_type=market_type,
            base=m.get("base", base),
            quote=m.get("quote", quote),
            price_precision=float(precision.get("price") or 0.01),
            amount_precision=float(precision.get("amount") or 0.0001),
            min_amount=float(amount_limits.get("min") or 0.0001),
            min_cost=float(cost_limits.get("min") or 5.0),
            contract_size=float(m.get("contractSize") or 1.0),
            max_leverage=float(lev_limits.get("max") or (10.0 if market_type == MarketType.PERP else 1.0)),
            maker_fee=float(m.get("maker") or 0.0008),
            taker_fee=float(m.get("taker") or 0.0010),
        )

    async def fetch_ticker(self, symbol: str, market_type: MarketType) -> dict[str, Any]:
        norm = self.normalize_symbol(symbol, market_type)
        return await self._run(lambda: self._public_client.fetch_ticker(norm), f"fetch_ticker:{norm}")

    async def fetch_order_book(
        self, symbol: str, market_type: MarketType, limit: int = 20
    ) -> OrderBookSnapshot:
        norm = self.normalize_symbol(symbol, market_type)
        ob = await self._run(
            lambda: self._public_client.fetch_order_book(norm, limit=limit),
            f"fetch_order_book:{norm}",
        )
        bids = [(float(row[0]), float(row[1])) for row in (ob.get("bids") or [])[:limit] if len(row) >= 2]
        asks = [(float(row[0]), float(row[1])) for row in (ob.get("asks") or [])[:limit] if len(row) >= 2]
        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0
        mid = (best_bid + best_ask) / 2.0 if (best_bid and best_ask) else 0.0
        spread_bps = ((best_ask - best_bid) / mid * 10000.0) if mid > 0 else 0.0
        bid_depth_usd = sum(p * q for p, q in bids)
        ask_depth_usd = sum(p * q for p, q in asks)
        return OrderBookSnapshot(
            exchange=self.exchange_id,
            symbol=norm,
            market_type=market_type,
            timestamp=datetime.now(timezone.utc),
            best_bid=best_bid,
            best_ask=best_ask,
            spread_bps=round(spread_bps, 3),
            bid_depth_usd=round(bid_depth_usd, 2),
            ask_depth_usd=round(ask_depth_usd, 2),
            bids=bids,
            asks=asks,
        )

    async def fetch_ohlcv(
        self,
        symbol: str,
        market_type: MarketType,
        timeframe: str = "15m",
        limit: int = 150,
        since: int | None = None,
    ) -> list[Candle]:
        norm = self.normalize_symbol(symbol, market_type)

        def _call() -> Any:
            if since is None:
                return self._public_client.fetch_ohlcv(norm, timeframe=timeframe, limit=limit)
            try:
                return self._public_client.fetch_ohlcv(
                    norm, timeframe=timeframe, since=since, limit=limit
                )
            except TypeError:
                return self._public_client.fetch_ohlcv(norm, timeframe=timeframe, limit=limit)

        raw = await self._run(_call, f"fetch_ohlcv:{norm}:{timeframe}")
        by_ts: dict[int, Candle] = {}
        for row in raw or []:
            if not row or len(row) < 6:
                continue
            try:
                ts = int(row[0])
                o, h, l, c, v = float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])
            except (TypeError, ValueError):
                continue
            if ts <= 0 or not all(math.isfinite(x) for x in (o, h, l, c, v)):
                continue
            if since is not None and ts < since:
                continue
            by_ts[ts] = Candle(timestamp=ts, open=o, high=h, low=l, close=c, volume=v)
        return [by_ts[k] for k in sorted(by_ts.keys())]

    async def fetch_funding_rate(self, symbol: str) -> float | None:
        norm = self.normalize_symbol(symbol, MarketType.PERP)
        try:
            res = await self._run(
                lambda: self._public_client.fetch_funding_rate(norm),
                f"fetch_funding_rate:{norm}",
            )
            rate = res.get("fundingRate")
            return float(rate) if rate is not None else None
        except Exception:
            return None

    async def fetch_open_interest(self, symbol: str) -> float | None:
        norm = self.normalize_symbol(symbol, MarketType.PERP)
        try:
            res = await self._run(
                lambda: self._public_client.fetch_open_interest(norm),
                f"fetch_open_interest:{norm}",
            )
            oi_val = res.get("openInterestValue") or res.get("openInterestAmount")
            return float(oi_val) if oi_val is not None else None
        except Exception:
            return None

    async def fetch_read_only_balance(self) -> dict[str, Any]:
        if not self.credentials or not self.credentials.is_configured:
            return {"configured": False, "balances": {}}
        raw = await self._run(lambda: self._auth_client.fetch_balance(), "fetch_balance")
        totals = raw.get("total") or {}
        non_zero = {k: float(v) for k, v in totals.items() if v and float(v) > 0}
        return {"configured": True, "balances": non_zero}

    async def fetch_read_only_positions(self) -> list[dict[str, Any]]:
        if not self.credentials or not self.credentials.is_configured:
            return []
        try:
            raw = await self._run(lambda: self._auth_client.fetch_positions(), "fetch_positions")
            positions = []
            for pos in raw or []:
                contracts = float(pos.get("contracts") or 0.0)
                if contracts != 0.0:
                    positions.append(
                        {
                            "symbol": pos.get("symbol"),
                            "side": pos.get("side"),
                            "contracts": contracts,
                            "entry_price": float(pos.get("entryPrice") or 0.0),
                            "unrealized_pnl": float(pos.get("unrealizedPnl") or 0.0),
                        }
                    )
            return positions
        except Exception as exc:
            logger.debug("fetch_positions unavailable on %s: %s", self.exchange_id.value, type(exc).__name__)
            return []

    def health_status(self) -> dict[str, Any]:
        now = time.monotonic()
        circuit_open = now < self._circuit_open_until
        return {
            "exchange": self.exchange_id.value,
            "healthy": not circuit_open and self._consecutive_errors < self.outage_threshold,
            "circuit_open": circuit_open,
            "consecutive_errors": self._consecutive_errors,
            "last_error": self._last_error,
            "last_ok_at": self._last_ok_at.isoformat() if self._last_ok_at else None,
            "credentials_configured": bool(self.credentials and self.credentials.is_configured),
            "read_only_enforced": True,
        }

    def close(self) -> None:
        for client in (self._public_client, self._auth_client):
            try:
                if hasattr(client, "close"):
                    client.close()
            except Exception:
                pass
