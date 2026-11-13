"""Tests for GlobalRiskGuardian: limits, stale data, dedup, outage protection, emergency stop."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.core.config import RiskPolicyConfig
from app.core.models import (
    Direction,
    ExchangeId,
    MarketRegime,
    MarketSnapshot,
    MarketType,
    PaperPosition,
    StrategySignal,
)
from app.risk.guardian import GlobalRiskGuardian


def _snap(exchange=ExchangeId.OKX, symbol="BTC/USDT", age_sec=1.0) -> MarketSnapshot:
    return MarketSnapshot(
        exchange=exchange,
        symbol=symbol,
        market_type=MarketType.PERP,
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=age_sec),
        last_price=50000.0,
        bid=49995.0,
        ask=50005.0,
        spread_bps=2.0,
        volume_24h_usd=100_000_000.0,
        order_book_depth_usd=200_000.0,
        liquidity_score=0.95,
        regime=MarketRegime.TRENDING_BULLISH,
    )


def _sig(exchange=ExchangeId.OKX, symbol="BTC/USDT", sl=49000.0, strat="trend_following") -> StrategySignal:
    return StrategySignal(
        strategy_name=strat,
        exchange=exchange,
        symbol=symbol,
        market_type=MarketType.PERP,
        direction=Direction.LONG,
        entry_low=49950.0,
        entry_high=50050.0,
        stop_loss=sl,
        take_profit_targets=[52000.0],
        invalidation_condition="Close below SL",
        estimated_fee_pct=0.0005,
        estimated_slippage_pct=0.0002,
        risk_reward_ratio=2.0,
        confidence_score=0.6,
        rationale="test",
    )


def test_risk_controls_and_emergency_stop():
    guardian = GlobalRiskGuardian(RiskPolicyConfig(max_leverage=3.0, max_concurrent_positions=2))
    snap = _snap()
    sig = _sig()

    # Approved baseline
    d1 = guardian.evaluate(sig, 1000.0, 2.0, 10000.0, [], snap, exchange_healthy=True)
    assert d1.approved is True
    assert d1.adjusted_qty > 0

    # Duplicate signal suppressed
    d_dup = guardian.evaluate(sig, 1000.0, 2.0, 10000.0, [], snap, exchange_healthy=True)
    assert d_dup.approved is False
    assert d_dup.reason == "duplicate_signal_suppressed"

    # Stale market data rejected
    d_stale = guardian.evaluate(_sig(strat="s2"), 1000.0, 2.0, 10000.0, [], _snap(age_sec=120.0))
    assert d_stale.approved is False
    assert d_stale.reason.startswith("stale_market_data")

    # Leverage cap rejected
    d_lev = guardian.evaluate(_sig(strat="s3"), 1000.0, 5.0, 10000.0, [], snap)
    assert d_lev.approved is False
    assert d_lev.reason.startswith("leverage_cap_exceeded")

    # API outage protection
    d_out = guardian.evaluate(_sig(strat="s4"), 1000.0, 2.0, 10000.0, [], snap, exchange_healthy=False)
    assert d_out.approved is False
    assert d_out.reason == "api_outage_protection"

    # Max concurrent positions
    pos = [
        PaperPosition(bot_id="b1", exchange=ExchangeId.OKX, symbol="ETH/USDT", market_type=MarketType.SPOT, direction=Direction.LONG, qty=0.5, entry_price=3000.0, mark_price=3000.0),
        PaperPosition(bot_id="b2", exchange=ExchangeId.BYBIT, symbol="SOL/USDT", market_type=MarketType.SPOT, direction=Direction.LONG, qty=5.0, entry_price=150.0, mark_price=150.0),
    ]
    d_maxpos = guardian.evaluate(_sig(strat="s5"), 500.0, 1.0, 10000.0, pos, snap)
    assert d_maxpos.approved is False
    assert d_maxpos.reason == "max_concurrent_positions_reached"

    # Emergency stop blocks & resets cleanly
    guardian.trigger_emergency_stop("unit test")
    d_estop = guardian.evaluate(_sig(strat="s6"), 500.0, 1.0, 10000.0, [], snap)
    assert d_estop.approved is False
    assert d_estop.reason.startswith("emergency_stop_active")
    guardian.reset_emergency_stop()
    d_after = guardian.evaluate(_sig(strat="s7"), 500.0, 1.0, 10000.0, [], snap)
    assert d_after.approved is True


def test_daily_loss_circuit_breaker():
    guardian = GlobalRiskGuardian(RiskPolicyConfig(daily_loss_limit_pct=0.03))
    guardian.update_equity_watermarks(10000.0)
    d = guardian.evaluate(_sig(), 500.0, 1.0, 9600.0, [], _snap())
    assert d.approved is False
    assert d.reason == "daily_loss_limit_exceeded"
    assert guardian.circuit_breaker_tripped is True
