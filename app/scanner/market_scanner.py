"""Continuous multi-exchange market scanner across OKX and Bybit."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.models import Candle, ExchangeId, MarketSnapshot, MarketType
from app.exchanges.base import BaseExchangeAdapter
from app.exchanges.registry import ExchangeRegistry
from app.scanner.indicators import compute_indicators

logger = logging.getLogger("multi_exchange.scanner")

ALL_TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "1h", "4h", "1d")
DEFAULT_SCAN_SYMBOLS: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
    "LINK/USDT",
)


class GlobalMarketScanner:
    """Scans Spot & Perpetual markets on OKX and Bybit concurrently."""

    def __init__(
        self,
        registry: ExchangeRegistry,
        symbols: tuple[str, ...] = DEFAULT_SCAN_SYMBOLS,
        timeframes: tuple[str, ...] = ALL_TIMEFRAMES,
        max_concurrency: int = 4,
    ) -> None:
        self.registry = registry
        self.symbols = symbols
        self.timeframes = timeframes
        self._sem = asyncio.Semaphore(max_concurrency)
        self.latest_snapshots: dict[str, MarketSnapshot] = {}
        self.ranked_opportunities: list[MarketSnapshot] = []
        self.last_scan_at: datetime | None = None

    @staticmethod
    def _key(exchange: ExchangeId, symbol: str, market_type: MarketType) -> str:
        return f"{exchange.value}:{market_type.value}:{symbol}"

    async def scan_pair(
        self,
        adapter: BaseExchangeAdapter,
        symbol: str,
        market_type: MarketType,
        fetch_all_tfs: bool = True,
    ) -> MarketSnapshot | None:
        async with self._sem:
            try:
                ticker = await adapter.fetch_ticker(symbol, market_type)
                ob = await adapter.fetch_order_book(symbol, market_type, limit=20)
                tfs_to_fetch = self.timeframes if fetch_all_tfs else ("15m", "1h")
                candles_by_tf: dict[str, list[Candle]] = {}
                for tf in tfs_to_fetch:
                    candles_by_tf[tf] = await adapter.fetch_ohlcv(
                        symbol, market_type, timeframe=tf, limit=120
                    )

                primary_candles = candles_by_tf.get("15m") or next(iter(candles_by_tf.values()), [])
                depth_usd = ob.bid_depth_usd + ob.ask_depth_usd
                ind = compute_indicators(
                    primary_candles,
                    spread_bps=ob.spread_bps,
                    order_book_depth_usd=depth_usd,
                )

                funding_rate: float | None = None
                open_interest_usd: float | None = None
                liq_signal: str | None = None
                if market_type == MarketType.PERP:
                    funding_rate = await adapter.fetch_funding_rate(symbol)
                    open_interest_usd = await adapter.fetch_open_interest(symbol)
                    if funding_rate is not None and abs(funding_rate) > 0.0005:
                        liq_signal = "crowded_longs" if funding_rate > 0 else "crowded_shorts"

                last_price = float(ticker.get("last") or ob.best_bid or 0.0)
                vol_24h = float(ticker.get("quoteVolume") or 0.0)

                opp_score = self._score_opportunity(
                    volume_24h=vol_24h,
                    rel_vol=ind["relative_volume"],
                    trend_strength=ind["trend_strength"],
                    liquidity_score=ind["liquidity_score"],
                    volatility=ind["volatility"],
                )

                snap = MarketSnapshot(
                    exchange=adapter.exchange_id,
                    symbol=symbol,
                    market_type=market_type,
                    timestamp=datetime.now(timezone.utc),
                    ticker_source_timestamp_ms=ticker.get("timestamp"),
                    book_source_timestamp_ms=ob.source_timestamp_ms,
                    last_price=last_price,
                    bid=ob.best_bid,
                    ask=ob.best_ask,
                    spread_bps=ob.spread_bps,
                    volume_24h_usd=round(vol_24h, 2),
                    relative_volume=ind["relative_volume"],
                    volatility=ind["volatility"],
                    trend_strength=ind["trend_strength"],
                    rsi=ind["rsi"],
                    macd=ind["macd"],
                    macd_signal=ind["macd_signal"],
                    macd_hist=ind["macd_hist"],
                    atr=ind["atr"],
                    ema_fast=ind["ema_fast"],
                    ema_slow=ind["ema_slow"],
                    ema_trend=ind["ema_trend"],
                    ema_alignment=ind["ema_alignment"],
                    support=ind["support"],
                    resistance=ind["resistance"],
                    funding_rate=funding_rate,
                    open_interest_usd=open_interest_usd,
                    liquidation_signal=liq_signal,
                    order_book_depth_usd=round(depth_usd, 2),
                    liquidity_score=ind["liquidity_score"],
                    regime=ind["regime"],
                    opportunity_score=opp_score,
                    bids=ob.bids,
                    asks=ob.asks,
                    candles_by_tf=candles_by_tf,
                )
                self.latest_snapshots[self._key(adapter.exchange_id, symbol, market_type)] = snap
                return snap
            except Exception as exc:
                logger.warning(
                    "Scan failed for %s %s %s: %s",
                    adapter.exchange_id.value,
                    market_type.value,
                    symbol,
                    type(exc).__name__,
                )
                return None

    @staticmethod
    def _score_opportunity(
        volume_24h: float,
        rel_vol: float,
        trend_strength: float,
        liquidity_score: float,
        volatility: float,
    ) -> float:
        vol_score = min(1.0, volume_24h / 100_000_000.0) if volume_24h > 0 else 0.2
        rvol_score = min(1.0, rel_vol / 2.5)
        trend_score = min(1.0, trend_strength / 50.0)
        volat_score = min(1.0, volatility / 0.03)
        raw = (
            0.30 * liquidity_score
            + 0.25 * trend_score
            + 0.20 * rvol_score
            + 0.15 * vol_score
            + 0.10 * volat_score
        )
        return round(min(max(raw * 100.0, 0.0), 100.0), 2)

    async def scan_all(self, fetch_all_tfs: bool = False) -> list[MarketSnapshot]:
        tasks = []
        for adapter in self.registry.all().values():
            for symbol in self.symbols:
                for mtype in (MarketType.SPOT, MarketType.PERP):
                    tasks.append(self.scan_pair(adapter, symbol, mtype, fetch_all_tfs=fetch_all_tfs))

        results = await asyncio.gather(*tasks, return_exceptions=False)
        valid = [r for r in results if r is not None]
        valid.sort(key=lambda s: s.opportunity_score, reverse=True)
        self.ranked_opportunities = valid
        self.last_scan_at = datetime.now(timezone.utc)
        return valid

    def summary_rows(self) -> list[dict[str, Any]]:
        return [
            s.model_dump(exclude={"candles_by_tf"})
            for s in self.ranked_opportunities
        ]
