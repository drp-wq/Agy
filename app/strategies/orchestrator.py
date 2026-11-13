"""AI Strategy Orchestrator: regime-aware selection, walk-forward & out-of-sample evaluation."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.models import (
    Candle,
    Direction,
    ExchangeId,
    MarketRegime,
    MarketSnapshot,
    MarketType,
    StrategySignal,
)
from app.scanner.indicators import compute_indicators
from app.strategies.base import BaseStrategy, ConfidenceCalibrator
from app.strategies.catalog import build_all_strategies


class AIStrategyOrchestrator:
    """Selects and ranks strategy signals based on measurable market regimes and OOS calibration."""

    def __init__(self, strategies: list[BaseStrategy] | None = None) -> None:
        self.strategies = strategies or build_all_strategies()
        self.calibrator = ConfidenceCalibrator()
        self.latest_signals: list[StrategySignal] = []

    def select_strategies_for_regime(self, regime: MarketRegime) -> list[BaseStrategy]:
        if regime == MarketRegime.LOW_LIQUIDITY:
            return []
        return [s for s in self.strategies if regime in s.supported_regimes]

    def generate_signals(self, snapshots: list[MarketSnapshot]) -> list[StrategySignal]:
        signals: list[StrategySignal] = []
        for snap in snapshots:
            eligible = self.select_strategies_for_regime(snap.regime)
            for strat in eligible:
                sig = strat.evaluate(snap, self.calibrator)
                if sig is not None:
                    signals.append(sig)
        signals.sort(key=lambda s: (s.confidence_score * s.risk_reward_ratio), reverse=True)
        self.latest_signals = signals
        return signals

    def walk_forward_evaluate(
        self,
        candles: list[Candle],
        strategy: BaseStrategy,
        symbol: str = "BTC/USDT",
        exchange: ExchangeId = ExchangeId.OKX,
        market_type: MarketType = MarketType.SPOT,
        train_window: int = 60,
        horizon_bars: int = 12,
    ) -> dict[str, Any]:
        """Strict walk-forward OOS evaluation with zero look-ahead bias."""
        if len(candles) < train_window + horizon_bars + 2:
            return {"strategy": strategy.name, "trades": 0, "win_rate": 0.0, "expectancy_r": 0.0}

        trades = 0
        wins = 0
        r_multiples: list[float] = []

        for idx in range(train_window, len(candles) - horizon_bars):
            # Strict past-only slice: candles[:idx+1]
            past = candles[: idx + 1]
            ind = compute_indicators(past)
            snap = MarketSnapshot(
                exchange=exchange,
                symbol=symbol,
                market_type=market_type,
                timestamp=datetime.now(timezone.utc),
                last_price=past[-1].close,
                bid=past[-1].close * 0.9998,
                ask=past[-1].close * 1.0002,
                spread_bps=4.0,
                volume_24h_usd=10_000_000.0,
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
                liquidity_score=ind["liquidity_score"],
                regime=ind["regime"],
                candles_by_tf={"15m": past[-60:], "1h": past[-60:]},
            )
            sig = strategy.evaluate(snap, self.calibrator)
            if sig is None:
                continue

            entry = past[-1].close
            sl = sig.stop_loss
            tp = sig.take_profit_targets[0]
            risk = abs(entry - sl)
            if risk <= 0:
                continue

            future = candles[idx + 1 : idx + 1 + horizon_bars]
            outcome_r = 0.0
            for bar in future:
                if sig.direction == Direction.LONG:
                    if bar.low <= sl:
                        outcome_r = -1.0
                        break
                    if bar.high >= tp:
                        outcome_r = (tp - entry) / risk
                        break
                else:
                    if bar.high >= sl:
                        outcome_r = -1.0
                        break
                    if bar.low <= tp:
                        outcome_r = (entry - tp) / risk
                        break
            else:
                exit_px = future[-1].close
                outcome_r = (
                    (exit_px - entry) / risk
                    if sig.direction == Direction.LONG
                    else (entry - exit_px) / risk
                )

            trades += 1
            is_win = outcome_r > 0
            if is_win:
                wins += 1
            r_multiples.append(outcome_r)
            self.calibrator.record_outcome(strategy.name, snap.regime, is_win)

        return {
            "strategy": strategy.name,
            "trades": trades,
            "wins": wins,
            "win_rate": round(wins / trades, 4) if trades else 0.0,
            "expectancy_r": round(sum(r_multiples) / trades, 4) if trades else 0.0,
        }
