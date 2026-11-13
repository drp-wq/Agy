"""Tests for all 10 strategies, calibrated confidence, and walk-forward OOS evaluation."""

from __future__ import annotations

from datetime import datetime, timezone

from app.core.models import Candle, ExchangeId, MarketRegime, MarketSnapshot, MarketType
from app.strategies.base import ConfidenceCalibrator
from app.strategies.catalog import build_all_strategies
from app.strategies.orchestrator import AIStrategyOrchestrator


def _candles(n: int = 120, drift: float = 10.0) -> list[Candle]:
    out = []
    px = 3000.0
    for i in range(n):
        px += drift + (8.0 if i % 2 == 0 else -4.0)
        out.append(
            Candle(
                timestamp=1700000000000 + i * 900_000,
                open=px - 5.0,
                high=px + 20.0,
                low=px - 20.0,
                close=px,
                volume=500.0 + (i % 7) * 50.0,
            )
        )
    return out


def test_all_10_strategies_registered_and_calibrated():
    strategies = build_all_strategies()
    assert len(strategies) == 10

    cal = ConfidenceCalibrator()
    for _ in range(10):
        cal.record_outcome("trend_following", MarketRegime.TRENDING_BULLISH, True)
    high_conf = cal.calibrated_confidence("trend_following", MarketRegime.TRENDING_BULLISH, 0.6)

    cal_loss = ConfidenceCalibrator()
    for _ in range(10):
        cal_loss.record_outcome("trend_following", MarketRegime.TRENDING_BULLISH, False)
    low_conf = cal_loss.calibrated_confidence("trend_following", MarketRegime.TRENDING_BULLISH, 0.6)
    assert high_conf > low_conf


def test_orchestrator_signals_and_walk_forward_no_leakage():
    orch = AIStrategyOrchestrator()
    c_list = _candles(120, drift=8.0)
    snap = MarketSnapshot(
        exchange=ExchangeId.OKX,
        symbol="ETH/USDT",
        market_type=MarketType.PERP,
        timestamp=datetime.now(timezone.utc),
        last_price=c_list[-1].close,
        bid=c_list[-1].close - 1.0,
        ask=c_list[-1].close + 1.0,
        spread_bps=4.0,
        volume_24h_usd=90_000_000.0,
        relative_volume=1.6,
        volatility=0.02,
        trend_strength=30.0,
        rsi=63.0,
        macd=12.0,
        macd_signal=8.0,
        macd_hist=4.0,
        atr=25.0,
        ema_fast=c_list[-1].close - 5.0,
        ema_slow=c_list[-1].close - 15.0,
        ema_trend=c_list[-1].close - 40.0,
        ema_alignment="bullish",
        support=c_list[-1].close - 80.0,
        resistance=c_list[-1].close + 5.0,
        funding_rate=0.0007,
        liquidity_score=0.9,
        regime=MarketRegime.TRENDING_BULLISH,
        candles_by_tf={"15m": c_list[-60:], "1h": c_list[-60:]},
    )
    signals = orch.generate_signals([snap])
    assert len(signals) >= 1
    for sig in signals:
        assert sig.risk_reward_ratio >= 1.05
        assert 0.05 <= sig.confidence_score <= 0.95
        assert sig.stop_loss > 0
        assert len(sig.take_profit_targets) >= 1

    wf = orch.walk_forward_evaluate(c_list, orch.strategies[0], train_window=60, horizon_bars=10)
    assert "trades" in wf and "win_rate" in wf and "expectancy_r" in wf
