"""Base strategy interface, risk-reward helpers, and empirical confidence calibration."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict

from app.core.models import (
    Direction,
    MarketRegime,
    MarketSnapshot,
    StrategySignal,
)


class ConfidenceCalibrator:
    """Calibrates strategy confidence against observed out-of-sample/paper outcomes."""

    def __init__(self, prior_alpha: float = 2.0, prior_beta: float = 2.0) -> None:
        self.prior_alpha = prior_alpha
        self.prior_beta = prior_beta
        self._wins: dict[tuple[str, str], float] = defaultdict(float)
        self._losses: dict[tuple[str, str], float] = defaultdict(float)

    def record_outcome(self, strategy_name: str, regime: MarketRegime, is_win: bool) -> None:
        key = (strategy_name, regime.value)
        if is_win:
            self._wins[key] += 1.0
        else:
            self._losses[key] += 1.0

    def calibrated_confidence(
        self,
        strategy_name: str,
        regime: MarketRegime,
        raw_score: float,
    ) -> float:
        """Blend empirical Beta posterior mean with bounded raw signal strength."""
        key = (strategy_name, regime.value)
        wins = self._wins[key]
        losses = self._losses[key]
        empirical = (self.prior_alpha + wins) / (self.prior_alpha + self.prior_beta + wins + losses)
        bounded_raw = min(max(raw_score, 0.1), 0.9)
        total_obs = wins + losses
        obs_weight = min(0.75, total_obs / 20.0)
        blended = obs_weight * empirical + (1.0 - obs_weight) * (0.5 * empirical + 0.5 * bounded_raw)
        return round(min(max(blended, 0.05), 0.95), 4)

    def stats(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        keys = set(self._wins.keys()) | set(self._losses.keys())
        for strat, reg in sorted(keys):
            w = self._wins[(strat, reg)]
            l = self._losses[(strat, reg)]
            tot = w + l
            out[f"{strat}:{reg}"] = {
                "wins": w,
                "losses": l,
                "total": tot,
                "win_rate": round(w / tot, 4) if tot > 0 else 0.5,
            }
        return out


class BaseStrategy(ABC):
    """Abstract base class for all 10 trading strategies."""

    name: str
    supported_regimes: tuple[MarketRegime, ...]

    @abstractmethod
    def evaluate(
        self,
        snapshot: MarketSnapshot,
        calibrator: ConfidenceCalibrator,
    ) -> StrategySignal | None:
        """Return a structured StrategySignal or None if conditions are not met."""

    @staticmethod
    def build_signal(
        strategy_name: str,
        snapshot: MarketSnapshot,
        direction: Direction,
        stop_loss: float,
        take_profits: list[float],
        invalidation: str,
        rationale: str,
        raw_strength: float,
        calibrator: ConfidenceCalibrator,
        timeframe: str = "15m",
        fee_pct: float = 0.001,
    ) -> StrategySignal | None:
        price = snapshot.last_price
        if price <= 0 or stop_loss <= 0 or not take_profits:
            return None

        risk_dist = abs(price - stop_loss)
        reward_dist = abs(take_profits[0] - price)
        if risk_dist <= 0 or reward_dist <= 0:
            return None

        rr = round(reward_dist / risk_dist, 2)
        if rr < 1.05:
            return None

        slippage_pct = round(max(0.0002, (snapshot.spread_bps / 10000.0) * 0.6), 6)
        half_band = price * 0.0015
        conf = calibrator.calibrated_confidence(strategy_name, snapshot.regime, raw_strength)

        return StrategySignal(
            strategy_name=strategy_name,
            exchange=snapshot.exchange,
            symbol=snapshot.symbol,
            market_type=snapshot.market_type,
            timeframe=timeframe,
            direction=direction,
            entry_low=round(price - half_band, 6),
            entry_high=round(price + half_band, 6),
            stop_loss=round(stop_loss, 6),
            take_profit_targets=[round(tp, 6) for tp in take_profits],
            invalidation_condition=invalidation,
            estimated_fee_pct=fee_pct,
            estimated_slippage_pct=slippage_pct,
            risk_reward_ratio=rr,
            confidence_score=conf,
            rationale=rationale,
            regime=snapshot.regime,
        )
