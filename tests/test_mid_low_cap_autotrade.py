"""Comprehensive tests for mid/low-cap adaptive paper autotrading."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from app.core.config import EngineConfig, LoadedEnvironment, MidLowCapConfig
from app.core.models import (
    Candle,
    Direction,
    ExchangeId,
    MarketRegime,
    MarketSnapshot,
    MarketType,
    OrderStatus,
    PaperPosition,
    StrategySignal,
)
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED
from app.exchanges.base import BaseExchangeAdapter
from app.exchanges.registry import ExchangeRegistry
from app.execution.mid_low_cap_runner import MID_LOW_CAP_BOT_ID, MidLowCapAutoTradeRunner
from app.execution.paper_engine import PaperExecutionEngine
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.scanner.market_cap import MarketCapBand, MarketCapProvider, MarketCapRecord
from app.scanner.market_scanner import GlobalMarketScanner
from app.scanner.mid_low_cap_discovery import (
    DynamicSpotMarketDiscoverer,
    evaluate_safety_filters,
    is_leveraged_token,
    is_stablecoin_or_fiat,
)
from app.service import TradingEngineService
from app.strategies.mid_low_cap_selector import MidLowCapStrategySelector, compute_net_reward_risk
from app.strategies.orchestrator import AIStrategyOrchestrator


def _make_trending_candles(n: int = 100, start: float = 10.0, step: float = 0.12) -> list[Candle]:
    base_ts = int(datetime.now(timezone.utc).timestamp() * 1000) - n * 900_000
    candles: list[Candle] = []
    price = start
    for i in range(n):
        # Smooth upward trend with small pullbacks so indicators & walk-forward trigger cleanly
        delta = step if i % 5 != 0 else -step * 0.35
        o = price
        c = max(0.5, o + delta)
        h = max(o, c) + 0.08
        l = min(o, c) - 0.06
        candles.append(
            Candle(
                timestamp=base_ts + i * 900_000,
                open=round(o, 4),
                high=round(h, 4),
                low=round(l, 4),
                close=round(c, 4),
                volume=10_000.0 + (i % 7) * 500.0,
            )
        )
        price = c
    return candles


def _make_snapshot(
    symbol: str = "MIDCAP/USDT",
    exchange: ExchangeId = ExchangeId.BINANCE,
    market_type: MarketType = MarketType.SPOT,
    last_price: float = 20.0,
    spread_bps: float = 8.0,
    depth_usd: float = 120_000.0,
    volume_24h_usd: float = 15_000_000.0,
    atr: float = 0.4,
    regime: MarketRegime = MarketRegime.TRENDING_BULLISH,
    candles: list[Candle] | None = None,
    age_sec: float = 1.0,
) -> MarketSnapshot:
    c_list = candles if candles is not None else _make_trending_candles(90)
    half_spread = last_price * (spread_bps / 20_000.0)
    return MarketSnapshot(
        exchange=exchange,
        symbol=symbol,
        market_type=market_type,
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_sec),
        last_price=last_price,
        bid=last_price - half_spread,
        ask=last_price + half_spread,
        spread_bps=spread_bps,
        volume_24h_usd=volume_24h_usd,
        relative_volume=1.5,
        volatility=0.02,
        trend_strength=32.0,
        rsi=58.0,
        macd=0.2,
        macd_signal=0.1,
        macd_hist=0.1,
        atr=atr,
        ema_fast=last_price * 0.99,
        ema_slow=last_price * 0.97,
        ema_trend=last_price * 0.94,
        ema_alignment="bullish",
        support=last_price * 0.95,
        resistance=last_price * 1.05,
        order_book_depth_usd=depth_usd,
        liquidity_score=0.8,
        regime=regime,
        opportunity_score=75.0,
        candles_by_tf={"15m": c_list},
    )


def _fresh_cap(symbol: str, mc_usd: float, band: MarketCapBand) -> MarketCapRecord:
    return MarketCapRecord(
        base_symbol=symbol,
        market_cap_usd=mc_usd,
        circulating_supply=1_000_000.0,
        band=band,
        source="coingecko",
        source_id=symbol.lower(),
        fetched_at=datetime.now(timezone.utc),
        source_updated_at=datetime.now(timezone.utc),
        age_sec=1.0,
        is_fresh=True,
        ambiguous=False,
    )


class _StubClient:
    def __init__(self, markets: dict[str, Any], candles: list[Candle]) -> None:
        self.markets = markets
        self._candles = candles

    def load_markets(self, _reload: bool = False) -> dict[str, Any]:
        return self.markets

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        px = self._candles[-1].close
        return {"symbol": symbol, "last": px, "quoteVolume": 12_000_000.0}

    def fetch_order_book(self, symbol: str, limit: int = 20) -> dict[str, Any]:
        px = self._candles[-1].close
        bids = [[px * (1.0 - 0.0003 * (i + 1)), 1500.0] for i in range(5)]
        asks = [[px * (1.0 + 0.0003 * (i + 1)), 1500.0] for i in range(5)]
        return {"symbol": symbol, "bids": bids, "asks": asks}

    def fetch_ohlcv(self, symbol: str, timeframe: str = "15m", limit: int = 120) -> list[list[Any]]:
        return [[c.timestamp, c.open, c.high, c.low, c.close, c.volume] for c in self._candles[-limit:]]

    def close(self) -> None:
        pass


class _StubAdapter(BaseExchangeAdapter):
    exchange_id = ExchangeId.BINANCE

    def __init__(self, markets: dict[str, Any], candles: list[Candle]) -> None:
        self._stub_markets = markets
        self._stub_candles = candles
        super().__init__(credentials=None, client_factory=self._make_client)

    def _make_client(self, _opts: dict[str, Any]) -> _StubClient:
        return _StubClient(self._stub_markets, self._stub_candles)


def test_market_cap_classification_provenance_and_no_volume_inference() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    stale_iso = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()

    async def fake_fetcher(page: int, _per_page: int) -> list[dict[str, Any]]:
        if page > 1:
            return []
        return [
            {"id": "btc", "symbol": "btc", "name": "Bitcoin", "market_cap": 1_200_000_000_000, "circulating_supply": 19_000_000, "last_updated": now_iso},
            {"id": "mid1", "symbol": "mid", "name": "MidToken", "market_cap": 1_500_000_000, "circulating_supply": 100_000_000, "last_updated": now_iso},
            {"id": "low1", "symbol": "low", "name": "LowToken", "market_cap": 120_000_000, "circulating_supply": 50_000_000, "last_updated": now_iso},
            {"id": "mic1", "symbol": "mic", "name": "MicroToken", "market_cap": 10_000_000, "circulating_supply": 10_000_000, "last_updated": now_iso},
            {"id": "stale1", "symbol": "stl", "name": "StaleToken", "market_cap": 200_000_000, "circulating_supply": 10_000_000, "last_updated": stale_iso},
            {"id": "dup1", "symbol": "dup", "name": "DupA", "market_cap": 200_000_000, "circulating_supply": 10_000_000, "last_updated": now_iso},
            {"id": "dup2", "symbol": "dup", "name": "DupB", "market_cap": 80_000_000, "circulating_supply": 10_000_000, "last_updated": now_iso},
            {"id": "nosup", "symbol": "nosup", "name": "NoSupply", "market_cap": 200_000_000, "circulating_supply": 0, "last_updated": now_iso},
        ]

    cfg = MidLowCapConfig(market_cap_min_request_interval_sec=0.0, market_cap_max_pages=2)
    provider = MarketCapProvider(config=cfg, page_fetcher=fake_fetcher)
    assert asyncio.run(provider.refresh()) is True
    # Bounded & cached: second refresh should not make new requests
    reqs_before = provider.requests_made
    assert asyncio.run(provider.refresh()) is True
    assert provider.requests_made == reqs_before

    assert provider.classify_symbol("BTC").band == MarketCapBand.LARGE
    mid_rec = provider.classify_symbol("MID")
    assert mid_rec.band == MarketCapBand.MID
    assert mid_rec.source == "coingecko"
    assert mid_rec.source_id == "mid1"
    assert mid_rec.is_fresh is True

    assert provider.classify_symbol("LOW").band == MarketCapBand.LOW
    assert provider.classify_symbol("MIC").band == MarketCapBand.MICRO
    assert provider.classify_symbol("STL").band == MarketCapBand.UNKNOWN
    assert provider.classify_symbol("DUP").band == MarketCapBand.UNKNOWN
    assert provider.classify_symbol("DUP").ambiguous is True
    assert provider.classify_symbol("NOSUP").band == MarketCapBand.UNKNOWN
    # Unlisted token is UNKNOWN — never inferred from volume or price
    assert provider.classify_symbol("UNLISTED").band == MarketCapBand.UNKNOWN


def test_dynamic_discovery_excludes_inactive_leveraged_stablecoins_and_non_target_caps() -> None:
    now_iso = datetime.now(timezone.utc).isoformat()

    async def fake_fetcher(page: int, _per_page: int) -> list[dict[str, Any]]:
        if page > 1:
            return []
        return [
            {"id": "btc", "symbol": "btc", "name": "Bitcoin", "market_cap": 1_200_000_000_000, "circulating_supply": 19_000_000, "last_updated": now_iso},
            {"id": "mid1", "symbol": "mid", "name": "MidToken", "market_cap": 800_000_000, "circulating_supply": 100_000_000, "last_updated": now_iso},
            {"id": "low1", "symbol": "low", "name": "LowToken", "market_cap": 95_000_000, "circulating_supply": 20_000_000, "last_updated": now_iso},
            {"id": "usdc", "symbol": "usdc", "name": "USD Coin", "market_cap": 500_000_000, "circulating_supply": 500_000_000, "last_updated": now_iso},
            {"id": "btcup", "symbol": "btcup", "name": "BTCUP", "market_cap": 100_000_000, "circulating_supply": 1_000_000, "last_updated": now_iso},
        ]

    markets = {
        "BTC/USDT": {"symbol": "BTC/USDT", "base": "BTC", "quote": "USDT", "spot": True, "active": True},
        "MID/USDT": {"symbol": "MID/USDT", "base": "MID", "quote": "USDT", "spot": True, "active": True},
        "LOW/USDT": {"symbol": "LOW/USDT", "base": "LOW", "quote": "USDT", "spot": True, "active": True},
        "INACT/USDT": {"symbol": "INACT/USDT", "base": "MID", "quote": "USDT", "spot": True, "active": False},
        "MID/USDT:USDT": {"symbol": "MID/USDT:USDT", "base": "MID", "quote": "USDT", "spot": False, "swap": True, "active": True},
        "MID/BTC": {"symbol": "MID/BTC", "base": "MID", "quote": "BTC", "spot": True, "active": True},
        "USDC/USDT": {"symbol": "USDC/USDT", "base": "USDC", "quote": "USDT", "spot": True, "active": True},
        "BTCUP/USDT": {"symbol": "BTCUP/USDT", "base": "BTCUP", "quote": "USDT", "spot": True, "active": True},
        "ETH3L/USDT": {"symbol": "ETH3L/USDT", "base": "ETH3L", "quote": "USDT", "spot": True, "active": True},
        "NOPE/USDT": {"symbol": "NOPE/USDT", "base": "NOPE", "quote": "USDT", "spot": True, "active": True},
    }
    assert is_leveraged_token("BTCUP") is True
    assert is_leveraged_token("ETH3L") is True
    assert is_stablecoin_or_fiat("USDC") is True
    assert is_stablecoin_or_fiat("FDUSD") is True

    cfg = MidLowCapConfig(market_cap_min_request_interval_sec=0.0, scan_batch_size=1)
    provider = MarketCapProvider(config=cfg, page_fetcher=fake_fetcher)
    discoverer = DynamicSpotMarketDiscoverer(provider, config=cfg)
    adapter = _StubAdapter(markets, _make_trending_candles(80))

    discovered = asyncio.run(discoverer.discover_for_exchange(adapter))
    syms = [m.symbol for m in discovered]
    assert syms == ["MID/USDT", "LOW/USDT"]

    b1 = discoverer.next_scan_batch(ExchangeId.BINANCE, discovered)
    b2 = discoverer.next_scan_batch(ExchangeId.BINANCE, discovered)
    assert [m.symbol for m in b1] == ["MID/USDT"]
    assert [m.symbol for m in b2] == ["LOW/USDT"]


def test_safety_filters_reject_missing_stale_illiquid_wash_and_duplicate_base() -> None:
    cfg = MidLowCapConfig()
    mid_cap = _fresh_cap("MID", 500_000_000.0, MarketCapBand.MID)
    low_cap = _fresh_cap("LOW", 100_000_000.0, MarketCapBand.LOW)

    # Valid passes
    ok = evaluate_safety_filters(_make_snapshot("MID/USDT"), mid_cap, cfg)
    assert ok.passed is True

    # Unknown cap rejected
    unk_cap = MarketCapRecord(base_symbol="UNK", band=MarketCapBand.UNKNOWN, is_fresh=False)
    assert evaluate_safety_filters(_make_snapshot("UNK/USDT"), unk_cap, cfg).passed is False

    # Stale snapshot rejected
    assert evaluate_safety_filters(_make_snapshot("MID/USDT", age_sec=90.0), mid_cap, cfg).passed is False

    # Wide spread rejected
    assert evaluate_safety_filters(_make_snapshot("MID/USDT", spread_bps=85.0), mid_cap, cfg).passed is False

    # Low depth rejected
    assert evaluate_safety_filters(_make_snapshot("MID/USDT", depth_usd=5_000.0), mid_cap, cfg).passed is False

    # Low 24h volume rejected
    assert evaluate_safety_filters(_make_snapshot("MID/USDT", volume_24h_usd=100_000.0), mid_cap, cfg).passed is False

    # Insufficient candle history (recently listed) rejected
    short_candles = _make_trending_candles(20)
    assert evaluate_safety_filters(_make_snapshot("MID/USDT", candles=short_candles), mid_cap, cfg).passed is False

    # Suspicious turnover (volume / market_cap > 3.0) rejected
    assert evaluate_safety_filters(
        _make_snapshot("LOW/USDT", volume_24h_usd=450_000_000.0), low_cap, cfg
    ).passed is False

    # Same-base open position rejected (no averaging down / concentration)
    existing = [
        PaperPosition(
            bot_id=MID_LOW_CAP_BOT_ID,
            exchange=ExchangeId.OKX,
            symbol="MID/USDT",
            market_type=MarketType.SPOT,
            direction=Direction.LONG,
            qty=10.0,
            entry_price=20.0,
            mark_price=20.5,
        )
    ]
    res_dup = evaluate_safety_filters(_make_snapshot("MID/USDT"), mid_cap, cfg, existing_positions=existing)
    assert res_dup.passed is False
    assert any("no_averaging_down" in r for r in res_dup.reasons)


def test_adaptive_strategy_selector_enforces_rr_costs_and_oos() -> None:
    cfg = MidLowCapConfig(min_risk_reward_ratio=2.0, min_oos_trades=2, min_oos_net_expectancy_r=0.0)
    orch = AIStrategyOrchestrator()
    selector = MidLowCapStrategySelector(orch, config=cfg)

    net_rr = compute_net_reward_risk(
        entry_price=100.0, stop_loss=98.0, take_profit=105.0, fee_pct=0.001, slippage_pct=0.0005
    )
    assert net_rr > 1.5

    candles = _make_trending_candles(110, start=10.0, step=0.15)
    snap = _make_snapshot("MID/USDT", last_price=candles[-1].close, atr=0.35, candles=candles)
    cap_records = {"MID": _fresh_cap("MID", 600_000_000.0, MarketCapBand.MID)}

    signals, diags = selector.select_signals([snap], cap_records)
    assert len(signals) >= 1
    sig = signals[0]
    assert sig.strategy_name in cfg.allowed_strategies
    assert sig.direction == Direction.LONG
    assert sig.risk_reward_ratio >= 2.0
    assert sig.metadata["mid_low_cap"]["oos"]["trades"] >= 2
    assert sig.metadata["mid_low_cap"]["oos"]["net_expectancy_r"] > 0.0

    # HIGH_VOLATILITY regime should not be permitted for mid/low-cap spot
    bad_snap = _make_snapshot("MID/USDT", regime=MarketRegime.HIGH_VOLATILITY, candles=candles)
    sigs_bad, diags_bad = selector.select_signals([bad_snap], cap_records)
    assert sigs_bad == []
    assert any("regime_not_permitted" in d["reason"] for d in diags_bad)


def test_end_to_end_paper_execution_risk_limits_and_live_disabled(tmp_path: Path) -> None:
    assert LIVE_EXECUTION_PERMANENTLY_DISABLED is True
    with pytest.raises(ValueError):
        EngineConfig(live_orders_enabled=True)

    now_iso = datetime.now(timezone.utc).isoformat()
    trending = _make_trending_candles(110, start=10.0, step=0.15)

    async def fake_fetcher(page: int, _per_page: int) -> list[dict[str, Any]]:
        if page > 1:
            return []
        return [
            {"id": f"tok{i}", "symbol": f"tok{i}", "name": f"Token{i}", "market_cap": 150_000_000 + i * 50_000_000, "circulating_supply": 50_000_000, "last_updated": now_iso}
            for i in range(1, 6)
        ]

    markets = {
        f"TOK{i}/USDT": {"symbol": f"TOK{i}/USDT", "base": f"TOK{i}", "quote": "USDT", "spot": True, "active": True}
        for i in range(1, 6)
    }
    adapter = _StubAdapter(markets, trending)

    mlc_cfg = MidLowCapConfig(
        enabled=True,
        market_cap_min_request_interval_sec=0.0,
        scan_batch_size=5,
        min_oos_trades=2,
        max_simultaneous_positions=3,
        max_risk_per_trade_pct=0.0025,
        daily_loss_limit_pct=0.01,
        allocated_capital_usd=2500.0,
    )
    engine_cfg = EngineConfig(
        db_path=tmp_path / "paper_mlc.sqlite3",
        backup_dir=tmp_path / "backups",
        log_dir=tmp_path / "logs",
        mid_low_cap=mlc_cfg,
    )
    env = LoadedEnvironment(config=engine_cfg, credentials={})
    service = TradingEngineService(env=env, enable_telegram=False)
    service.registry._adapters = {ExchangeId.BINANCE: adapter}
    service.mid_low_cap_runner.cap_provider = MarketCapProvider(config=mlc_cfg, page_fetcher=fake_fetcher)
    service.mid_low_cap_runner.discoverer = DynamicSpotMarketDiscoverer(
        service.mid_low_cap_runner.cap_provider, config=mlc_cfg
    )

    try:
        summary = asyncio.run(service.mid_low_cap_runner.run_cycle())
        assert summary["enabled"] is True
        assert summary["discovered"] == 5
        assert summary["safe"] == 5
        assert summary["orders_processed"] == 3  # capped at max 3 simultaneous positions

        orders = service.db.list_orders(limit=20)
        mlc_orders = [o for o in orders if o.bot_id == MID_LOW_CAP_BOT_ID]
        assert len(mlc_orders) == 3
        assert all(o.is_paper is True for o in mlc_orders)
        assert all(o.status == OrderStatus.FILLED for o in mlc_orders)
        assert all(o.leverage == 1.0 for o in mlc_orders)

        positions = service.mid_low_cap_runner.sleeve_positions()
        assert len(positions) == 3
        assert all(p.is_paper is True for p in positions)

        fills = service.db.list_fills(limit=20)
        assert len([f for f in fills if f.bot_id == MID_LOW_CAP_BOT_ID]) >= 3

        # Verify 0.25% risk sizing
        sample_sig = StrategySignal(
            strategy_name="trend_following",
            exchange=ExchangeId.BINANCE,
            symbol="TOK1/USDT",
            market_type=MarketType.SPOT,
            direction=Direction.LONG,
            entry_low=19.9,
            entry_high=20.1,
            stop_loss=19.0,  # 5% stop distance
            take_profit_targets=[22.0],
            invalidation_condition="test",
            estimated_fee_pct=0.001,
            estimated_slippage_pct=0.0005,
            risk_reward_ratio=2.0,
            confidence_score=0.7,
            rationale="test ATR risk sizing",
        )
        notional = service.mid_low_cap_runner.compute_position_notional(
            sample_sig,
            _make_snapshot("TOK1/USDT", last_price=20.0, depth_usd=500_000.0),
            sleeve_equity=2500.0,
            available_cash=2500.0,
        )
        # 0.25% of 2500 = $6.25 risk; 5% stop -> $125 notional
        assert notional == pytest.approx(125.0, abs=0.5)

        # Verify 1% daily loss circuit breaker
        service.mid_low_cap_runner._daily_start_equity = 2500.0
        assert service.mid_low_cap_runner._check_daily_loss_limit(2470.0) is True
        assert service.mid_low_cap_runner._sleeve_circuit_tripped is True

        snap_state = service.state_snapshot()
        assert snap_state["mode"] == "PAPER_ONLY"
        assert snap_state["live_execution_disabled"] is True
        assert snap_state["mid_low_cap"]["open_positions_count"] == 3
    finally:
        asyncio.run(service.shutdown())
