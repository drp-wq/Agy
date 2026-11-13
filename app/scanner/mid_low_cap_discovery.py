"""Dynamic USDT spot market discovery and hard safety filters for mid/low-cap assets."""

from __future__ import annotations

import logging
import re
import statistics
import time
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import MidLowCapConfig
from app.core.models import Candle, ExchangeId, MarketSnapshot, MarketType, PaperPosition
from app.exchanges.base import BaseExchangeAdapter
from app.scanner.market_cap import MarketCapBand, MarketCapProvider, MarketCapRecord

logger = logging.getLogger("multi_exchange.mid_low_cap_discovery")

STABLECOIN_AND_FIAT_BASES: frozenset[str] = frozenset(
    {
        "USDT",
        "USDC",
        "FDUSD",
        "TUSD",
        "BUSD",
        "DAI",
        "USDP",
        "USDE",
        "PYUSD",
        "UST",
        "USTC",
        "USDD",
        "FRAX",
        "LUSD",
        "GUSD",
        "SUSD",
        "EUR",
        "EURC",
        "AEUR",
        "GBP",
        "AUD",
        "TRY",
        "BRL",
        "USD1",
        "RLUSD",
        "USDS",
        "APXUSD",
        "USD0",
    }
)

_LEVERAGED_TOKEN_RE = re.compile(r"^[A-Z0-9]+(?:UP|DOWN|BULL|BEAR|[2345][LS])$")


class DiscoveredSpotMarket(BaseModel):
    exchange: ExchangeId
    symbol: str
    base: str
    quote: str
    market_type: MarketType = MarketType.SPOT
    active: bool = True
    cap_record: MarketCapRecord


class SafetyFilterResult(BaseModel):
    passed: bool
    reasons: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


def is_leveraged_token(base: str, market_meta: dict[str, Any] | None = None) -> bool:
    upper = base.strip().upper()
    if _LEVERAGED_TOKEN_RE.match(upper):
        return True
    if market_meta:
        info = market_meta.get("info") or {}
        if isinstance(info, dict):
            if info.get("isSpotTradingAllowed") is False:
                return True
            token_type = str(info.get("contractType") or info.get("type") or "").lower()
            if "leveraged" in token_type or "etf" in token_type:
                return True
    return False


def is_stablecoin_or_fiat(base: str) -> bool:
    return base.strip().upper() in STABLECOIN_AND_FIAT_BASES


class DynamicSpotMarketDiscoverer:
    """Discovers active USDT spot markets dynamically from read-only exchange metadata."""

    def __init__(
        self,
        cap_provider: MarketCapProvider,
        config: MidLowCapConfig | None = None,
    ) -> None:
        self.cap_provider = cap_provider
        self.config = config or MidLowCapConfig()
        self._cache: dict[str, tuple[float, list[DiscoveredSpotMarket]]] = {}
        self._batch_cursor: dict[str, int] = {}

    async def discover_for_exchange(
        self,
        adapter: BaseExchangeAdapter,
        force: bool = False,
    ) -> list[DiscoveredSpotMarket]:
        ex_key = adapter.exchange_id.value
        now_mono = time.monotonic()
        cached = self._cache.get(ex_key)
        if not force and cached is not None:
            cached_at, items = cached
            if (now_mono - cached_at) < self.config.discovery_cache_ttl_sec:
                return items

        await self.cap_provider.refresh(force=False)
        markets = await adapter.load_markets(reload=False)

        discovered: list[DiscoveredSpotMarket] = []
        seen_symbols: set[str] = set()

        for sym, meta in (markets or {}).items():
            if not isinstance(meta, dict):
                continue
            if meta.get("active") is not True:
                continue
            is_spot = meta.get("spot") is True or meta.get("type") == "spot"
            if not is_spot:
                continue
            if meta.get("swap") or meta.get("future") or meta.get("option"):
                continue

            quote = str(meta.get("quote") or "").strip().upper()
            base = str(meta.get("base") or "").strip().upper()
            clean_sym = str(meta.get("symbol") or sym).split(":")[0]
            if quote != "USDT" or not base or "/" not in clean_sym:
                continue
            if clean_sym in seen_symbols:
                continue
            if is_stablecoin_or_fiat(base):
                continue
            if is_leveraged_token(base, meta):
                continue

            cap_rec = self.cap_provider.classify_symbol(base)
            if not cap_rec.band.is_target_mid_low or not cap_rec.is_fresh:
                continue

            seen_symbols.add(clean_sym)
            discovered.append(
                DiscoveredSpotMarket(
                    exchange=adapter.exchange_id,
                    symbol=clean_sym,
                    base=base,
                    quote=quote,
                    market_type=MarketType.SPOT,
                    active=True,
                    cap_record=cap_rec,
                )
            )

        # Deterministic ordering: highest market cap first within target bands, capped
        discovered.sort(
            key=lambda m: (m.cap_record.market_cap_usd or 0.0, m.symbol),
            reverse=True,
        )
        capped = discovered[: self.config.max_discovered_symbols_per_exchange]
        self._cache[ex_key] = (now_mono, capped)
        return capped

    async def discover_global(
        self,
        adapters: list[BaseExchangeAdapter],
        force: bool = False,
    ) -> list[DiscoveredSpotMarket]:
        """Discover and rank mid/low-cap USDT spot markets globally across all healthy exchanges."""
        all_markets: list[DiscoveredSpotMarket] = []
        for adapter in adapters:
            try:
                disc = await self.discover_for_exchange(adapter, force=force)
                all_markets.extend(disc)
            except Exception as exc:
                logger.warning(
                    "Global mid/low-cap discovery failed on %s: %s",
                    adapter.exchange_id.value,
                    type(exc).__name__,
                )

        # Global ordering: interleave LOW and MID cap markets across exchanges
        low_caps = [m for m in all_markets if m.cap_record.band == MarketCapBand.LOW]
        mid_caps = [m for m in all_markets if m.cap_record.band == MarketCapBand.MID]
        low_caps.sort(key=lambda m: (m.cap_record.market_cap_usd or 0.0, m.exchange.value, m.symbol), reverse=True)
        mid_caps.sort(key=lambda m: (m.cap_record.market_cap_usd or 0.0, m.exchange.value, m.symbol), reverse=True)

        global_list: list[DiscoveredSpotMarket] = []
        li, mi = 0, 0
        while li < len(low_caps) or mi < len(mid_caps):
            if li < len(low_caps):
                global_list.append(low_caps[li])
                li += 1
            if mi < len(mid_caps):
                global_list.append(mid_caps[mi])
                mi += 1
        return global_list

    def next_global_scan_batch(
        self,
        discovered_global: list[DiscoveredSpotMarket],
        batch_size: int | None = None,
    ) -> list[DiscoveredSpotMarket]:
        """Return a bounded rotating batch from the global cross-exchange pool."""
        if not discovered_global:
            return []
        size = min(batch_size or self.config.scan_batch_size, len(discovered_global))
        cursor_key = "__global__"
        start = self._batch_cursor.get(cursor_key, 0) % len(discovered_global)
        end = start + size
        if end <= len(discovered_global):
            batch = discovered_global[start:end]
        else:
            batch = discovered_global[start:] + discovered_global[: end - len(discovered_global)]
        self._batch_cursor[cursor_key] = end % len(discovered_global)
        return batch

    def next_scan_batch(
        self,
        exchange: ExchangeId,
        discovered: list[DiscoveredSpotMarket],
    ) -> list[DiscoveredSpotMarket]:
        """Return a bounded rotating batch of symbols to avoid exchange rate-limit spikes."""
        if not discovered:
            return []
        batch_size = min(self.config.scan_batch_size, len(discovered))
        ex_key = exchange.value
        start = self._batch_cursor.get(ex_key, 0) % len(discovered)
        end = start + batch_size
        if end <= len(discovered):
            batch = discovered[start:end]
        else:
            batch = discovered[start:] + discovered[: end - len(discovered)]
        self._batch_cursor[ex_key] = end % len(discovered)
        return batch


def _has_suspicious_candle_spike(candles: list[Candle], max_spike_ratio: float) -> bool:
    if len(candles) < 10:
        return False
    vols = [c.volume for c in candles if c.volume > 0]
    if len(vols) < 5:
        return True
    med = statistics.median(vols)
    if med <= 0:
        return True
    for c in candles[-12:]:
        if c.volume > med * max_spike_ratio:
            price_range_pct = ((c.high - c.low) / c.open) if c.open > 0 else 0.0
            # Huge volume spike with negligible price movement is a classic wash-trade signature
            if price_range_pct < 0.002:
                return True
    return False


def evaluate_safety_filters(
    snapshot: MarketSnapshot | None,
    cap_record: MarketCapRecord | None,
    config: MidLowCapConfig,
    existing_positions: list[PaperPosition] | None = None,
    now: datetime | None = None,
) -> SafetyFilterResult:
    """Enforce hard safety gates; reject rather than trade on any missing or suspicious data."""
    reasons: list[str] = []
    ref_now = now or datetime.now(timezone.utc)

    if cap_record is None:
        return SafetyFilterResult(passed=False, reasons=["missing_market_cap_record"])
    if cap_record.ambiguous:
        reasons.append("ambiguous_market_cap_ticker")
    if not cap_record.is_fresh:
        reasons.append(f"stale_or_unverified_market_cap:{cap_record.reason or 'stale'}")
    if not cap_record.band.is_target_mid_low:
        reasons.append(f"non_target_cap_band:{cap_record.band.value}")
    if cap_record.market_cap_usd is None or cap_record.market_cap_usd <= 0:
        reasons.append("missing_circulating_market_cap")

    if snapshot is None:
        return SafetyFilterResult(passed=False, reasons=reasons + ["missing_market_snapshot"])

    if snapshot.market_type != MarketType.SPOT:
        reasons.append("non_spot_market_rejected")

    if snapshot.last_price <= 0 or snapshot.bid <= 0 or snapshot.ask <= 0 or snapshot.ask < snapshot.bid:
        reasons.append("invalid_quote_prices")

    age_sec = (ref_now - snapshot.timestamp).total_seconds()
    if age_sec > config.max_market_data_age_sec or age_sec < -5.0:
        reasons.append(f"stale_market_data:{age_sec:.1f}s")

    if snapshot.spread_bps <= 0 or snapshot.spread_bps > config.max_spread_bps:
        reasons.append(f"spread_too_wide:{snapshot.spread_bps:.2f}bps")

    if snapshot.order_book_depth_usd < config.min_order_book_depth_usd:
        reasons.append(f"insufficient_depth:{snapshot.order_book_depth_usd:.0f}")

    if snapshot.volume_24h_usd < config.min_24h_volume_usd:
        reasons.append(f"insufficient_24h_volume:{snapshot.volume_24h_usd:.0f}")

    candles = snapshot.candles_by_tf.get("15m") or snapshot.candles_by_tf.get("1h") or []
    if len(candles) < config.min_history_candles:
        reasons.append(f"insufficient_history:{len(candles)}_candles")

    if snapshot.atr <= 0:
        reasons.append("missing_or_zero_atr")

    turnover_ratio: float | None = None
    if cap_record.market_cap_usd and cap_record.market_cap_usd > 0:
        turnover_ratio = snapshot.volume_24h_usd / cap_record.market_cap_usd
        if turnover_ratio > config.max_turnover_ratio:
            reasons.append(f"suspicious_volume_turnover:{turnover_ratio:.2f}x")

    if _has_suspicious_candle_spike(candles, config.max_single_candle_volume_spike):
        reasons.append("suspicious_candle_volume_anomaly")

    # Concentration & no averaging down / martingale check
    base = snapshot.symbol.split("/")[0].upper()
    open_same_base = [
        p for p in (existing_positions or [])
        if p.symbol == snapshot.symbol or p.symbol.split("/")[0].upper() == base
    ]
    if len(open_same_base) >= config.max_positions_per_base:
        reasons.append(f"concentration_or_no_averaging_down:{base}")

    return SafetyFilterResult(
        passed=len(reasons) == 0,
        reasons=reasons,
        metrics={
            "cap_band": cap_record.band.value,
            "market_cap_usd": cap_record.market_cap_usd,
            "spread_bps": snapshot.spread_bps,
            "depth_usd": snapshot.order_book_depth_usd,
            "volume_24h_usd": snapshot.volume_24h_usd,
            "turnover_ratio": round(turnover_ratio, 4) if turnover_ratio is not None else None,
            "history_candles": len(candles),
            "age_sec": round(age_sec, 2),
        },
    )
