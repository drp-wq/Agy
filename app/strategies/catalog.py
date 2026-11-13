"""Implementation of all 10 modular trading strategies."""

from __future__ import annotations

from app.core.models import Direction, MarketRegime, MarketSnapshot, MarketType, StrategySignal
from app.strategies.base import BaseStrategy, ConfidenceCalibrator


class TrendFollowingStrategy(BaseStrategy):
    name = "trend_following"
    supported_regimes = (MarketRegime.TRENDING_BULLISH, MarketRegime.TRENDING_BEARISH)

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        atr = snapshot.atr or snapshot.last_price * 0.01
        if snapshot.ema_alignment == "bullish" and snapshot.macd_hist > 0 and snapshot.trend_strength >= 22:
            sl = snapshot.last_price - 1.5 * atr
            tps = [snapshot.last_price + 2.5 * atr, snapshot.last_price + 4.0 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, tps,
                "Close below EMA21 or MACD histogram turns negative",
                f"Bullish EMA alignment with ADX={snapshot.trend_strength:.1f} and positive MACD hist",
                0.62, calibrator,
            )
        if snapshot.ema_alignment == "bearish" and snapshot.macd_hist < 0 and snapshot.trend_strength >= 22:
            sl = snapshot.last_price + 1.5 * atr
            tps = [snapshot.last_price - 2.5 * atr, snapshot.last_price - 4.0 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, tps,
                "Close above EMA21 or MACD histogram turns positive",
                f"Bearish EMA alignment with ADX={snapshot.trend_strength:.1f} and negative MACD hist",
                0.62, calibrator,
            )
        return None


class MomentumBreakoutStrategy(BaseStrategy):
    name = "momentum_breakout"
    supported_regimes = (MarketRegime.TRENDING_BULLISH, MarketRegime.TRENDING_BEARISH, MarketRegime.HIGH_VOLATILITY)

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        atr = snapshot.atr or snapshot.last_price * 0.01
        if snapshot.relative_volume < 1.3:
            return None
        if snapshot.last_price >= snapshot.resistance * 0.997 and snapshot.rsi > 55:
            sl = snapshot.last_price - 1.4 * atr
            tps = [snapshot.last_price + 2.8 * atr, snapshot.last_price + 4.2 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, tps,
                "Failed breakout back below resistance",
                f"Volume-backed breakout (RVOL={snapshot.relative_volume:.2f}) near resistance",
                0.60, calibrator,
            )
        if snapshot.last_price <= snapshot.support * 1.003 and snapshot.rsi < 45:
            sl = snapshot.last_price + 1.4 * atr
            tps = [snapshot.last_price - 2.8 * atr, snapshot.last_price - 4.2 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, tps,
                "Failed breakdown back above support",
                f"Volume-backed breakdown (RVOL={snapshot.relative_volume:.2f}) near support",
                0.60, calibrator,
            )
        return None


class BreakoutRetestStrategy(BaseStrategy):
    name = "breakout_retest"
    supported_regimes = (MarketRegime.TRENDING_BULLISH, MarketRegime.TRENDING_BEARISH, MarketRegime.RANGING)

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        atr = snapshot.atr or snapshot.last_price * 0.01
        candles = snapshot.candles_by_tf.get("15m") or []
        if len(candles) < 10:
            return None
        prev_high = max(c.high for c in candles[-10:-3])
        prev_low = min(c.low for c in candles[-10:-3])
        p = snapshot.last_price
        if abs(p - prev_high) <= 0.6 * atr and snapshot.ema_alignment == "bullish":
            sl = prev_high - 1.3 * atr
            tps = [p + 2.4 * atr, p + 3.6 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, tps,
                "Break back below retested resistance level",
                "Bullish retest of prior swing high",
                0.58, calibrator,
            )
        if abs(p - prev_low) <= 0.6 * atr and snapshot.ema_alignment == "bearish":
            sl = prev_low + 1.3 * atr
            tps = [p - 2.4 * atr, p - 3.6 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, tps,
                "Reclaim above retested support level",
                "Bearish retest of prior swing low",
                0.58, calibrator,
            )
        return None


class MeanReversionStrategy(BaseStrategy):
    name = "mean_reversion"
    supported_regimes = (MarketRegime.RANGING, MarketRegime.UNCERTAIN)

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        atr = snapshot.atr or snapshot.last_price * 0.01
        if snapshot.rsi <= 32.0 and snapshot.last_price < snapshot.ema_slow:
            sl = snapshot.last_price - 1.3 * atr
            tp1 = max(snapshot.ema_slow, snapshot.last_price + 2.0 * atr)
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, [tp1],
                "RSI continues dropping below 22 with expanding downward momentum",
                f"Oversold mean reversion (RSI={snapshot.rsi:.1f}) below EMA21",
                0.56, calibrator,
            )
        if snapshot.rsi >= 68.0 and snapshot.last_price > snapshot.ema_slow:
            sl = snapshot.last_price + 1.3 * atr
            tp1 = min(snapshot.ema_slow, snapshot.last_price - 2.0 * atr)
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, [tp1],
                "RSI breaks above 78 with expanding upward momentum",
                f"Overbought mean reversion (RSI={snapshot.rsi:.1f}) above EMA21",
                0.56, calibrator,
            )
        return None


class RangeTradingStrategy(BaseStrategy):
    name = "range_trading"
    supported_regimes = (MarketRegime.RANGING,)

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        span = snapshot.resistance - snapshot.support
        if span <= 0:
            return None
        pos_in_range = (snapshot.last_price - snapshot.support) / span
        if pos_in_range <= 0.22:
            sl = snapshot.support - 0.25 * span
            tp1 = snapshot.support + 0.70 * span
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, [tp1],
                "Clean breakdown below range support",
                f"Range support bounce (pos={pos_in_range:.2f})",
                0.55, calibrator,
            )
        if pos_in_range >= 0.78:
            sl = snapshot.resistance + 0.25 * span
            tp1 = snapshot.resistance - 0.70 * span
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, [tp1],
                "Clean breakout above range resistance",
                f"Range resistance rejection (pos={pos_in_range:.2f})",
                0.55, calibrator,
            )
        return None


class GridTradingStrategy(BaseStrategy):
    name = "grid_trading"
    supported_regimes = (MarketRegime.RANGING, MarketRegime.UNCERTAIN)

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        atr = snapshot.atr or snapshot.last_price * 0.008
        if snapshot.trend_strength > 26:
            return None
        mid = (snapshot.support + snapshot.resistance) / 2.0 if snapshot.resistance > snapshot.support else snapshot.ema_slow
        if snapshot.last_price < mid - 0.5 * atr:
            sl = snapshot.last_price - 1.8 * atr
            tps = [snapshot.last_price + 2.2 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, tps,
                "Strong directional trend breakout invalidating grid",
                "Grid lower-band accumulation in low-ADX market",
                0.53, calibrator,
            )
        if snapshot.last_price > mid + 0.5 * atr:
            sl = snapshot.last_price + 1.8 * atr
            tps = [snapshot.last_price - 2.2 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, tps,
                "Strong directional trend breakout invalidating grid",
                "Grid upper-band distribution in low-ADX market",
                0.53, calibrator,
            )
        return None


class VolatilityExpansionStrategy(BaseStrategy):
    name = "volatility_expansion"
    supported_regimes = (MarketRegime.HIGH_VOLATILITY, MarketRegime.TRENDING_BULLISH, MarketRegime.TRENDING_BEARISH)

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        atr = snapshot.atr or snapshot.last_price * 0.015
        if snapshot.volatility < 0.015 or snapshot.relative_volume < 1.2:
            return None
        if snapshot.macd_hist > 0 and snapshot.last_price > snapshot.ema_fast:
            sl = snapshot.last_price - 1.5 * atr
            tps = [snapshot.last_price + 3.0 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, tps,
                "Volatility contraction back below EMA9",
                f"Upside volatility expansion (vol={snapshot.volatility:.4f}, RVOL={snapshot.relative_volume:.2f})",
                0.59, calibrator,
            )
        if snapshot.macd_hist < 0 and snapshot.last_price < snapshot.ema_fast:
            sl = snapshot.last_price + 1.5 * atr
            tps = [snapshot.last_price - 3.0 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, tps,
                "Volatility contraction back above EMA9",
                f"Downside volatility expansion (vol={snapshot.volatility:.4f}, RVOL={snapshot.relative_volume:.2f})",
                0.59, calibrator,
            )
        return None


class MTFConfluenceStrategy(BaseStrategy):
    name = "mtf_confluence"
    supported_regimes = (MarketRegime.TRENDING_BULLISH, MarketRegime.TRENDING_BEARISH)

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        c_15m = snapshot.candles_by_tf.get("15m") or []
        c_1h = snapshot.candles_by_tf.get("1h") or []
        if len(c_15m) < 20 or len(c_1h) < 20:
            return None
        up_15m = c_15m[-1].close > c_15m[-10].close
        up_1h = c_1h[-1].close > c_1h[-10].close
        atr = snapshot.atr or snapshot.last_price * 0.01
        if up_15m and up_1h and snapshot.ema_alignment == "bullish":
            sl = snapshot.last_price - 1.5 * atr
            tps = [snapshot.last_price + 2.6 * atr, snapshot.last_price + 4.0 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, tps,
                "1h trend reversal below swing low",
                "15m and 1h bullish multi-timeframe confluence",
                0.65, calibrator, timeframe="1h",
            )
        if (not up_15m) and (not up_1h) and snapshot.ema_alignment == "bearish":
            sl = snapshot.last_price + 1.5 * atr
            tps = [snapshot.last_price - 2.6 * atr, snapshot.last_price - 4.0 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, tps,
                "1h trend reversal above swing high",
                "15m and 1h bearish multi-timeframe confluence",
                0.65, calibrator, timeframe="1h",
            )
        return None


class MarketStructureStrategy(BaseStrategy):
    name = "market_structure"
    supported_regimes = (MarketRegime.TRENDING_BULLISH, MarketRegime.TRENDING_BEARISH, MarketRegime.RANGING)

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        candles = snapshot.candles_by_tf.get("15m") or []
        if len(candles) < 20:
            return None
        first_half = candles[-20:-10]
        second_half = candles[-10:]
        hh = max(c.high for c in second_half) > max(c.high for c in first_half)
        hl = min(c.low for c in second_half) > min(c.low for c in first_half)
        lh = max(c.high for c in second_half) < max(c.high for c in first_half)
        ll = min(c.low for c in second_half) < min(c.low for c in first_half)
        atr = snapshot.atr or snapshot.last_price * 0.01

        if hh and hl:
            sl = min(c.low for c in second_half) - 0.5 * atr
            tps = [snapshot.last_price + 2.5 * (snapshot.last_price - sl)]
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, tps,
                "Break of higher-low structure",
                "Bullish market structure (HH + HL)",
                0.61, calibrator,
            )
        if lh and ll:
            sl = max(c.high for c in second_half) + 0.5 * atr
            tps = [snapshot.last_price - 2.5 * (sl - snapshot.last_price)]
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, tps,
                "Break of lower-high structure",
                "Bearish market structure (LH + LL)",
                0.61, calibrator,
            )
        return None


class FundingAwarePerpStrategy(BaseStrategy):
    name = "funding_aware_perp"
    supported_regimes = (
        MarketRegime.RANGING,
        MarketRegime.HIGH_VOLATILITY,
        MarketRegime.TRENDING_BULLISH,
        MarketRegime.TRENDING_BEARISH,
    )

    def evaluate(self, snapshot: MarketSnapshot, calibrator: ConfidenceCalibrator) -> StrategySignal | None:
        if snapshot.market_type != MarketType.PERP or snapshot.funding_rate is None:
            return None
        atr = snapshot.atr or snapshot.last_price * 0.01
        fr = snapshot.funding_rate
        if fr >= 0.0005 and snapshot.rsi >= 60:
            sl = snapshot.last_price + 1.5 * atr
            tps = [snapshot.last_price - 2.5 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.SHORT, sl, tps,
                "Funding normalizes below +0.02% with upward breakout",
                f"Crowded long funding fade (funding={fr:.5f}, RSI={snapshot.rsi:.1f})",
                0.57, calibrator, fee_pct=0.0006,
            )
        if fr <= -0.0005 and snapshot.rsi <= 40:
            sl = snapshot.last_price - 1.5 * atr
            tps = [snapshot.last_price + 2.5 * atr]
            return self.build_signal(
                self.name, snapshot, Direction.LONG, sl, tps,
                "Funding normalizes above -0.02% with downward breakdown",
                f"Crowded short funding squeeze (funding={fr:.5f}, RSI={snapshot.rsi:.1f})",
                0.57, calibrator, fee_pct=0.0006,
            )
        return None


def build_all_strategies() -> list[BaseStrategy]:
    return [
        TrendFollowingStrategy(),
        MomentumBreakoutStrategy(),
        BreakoutRetestStrategy(),
        MeanReversionStrategy(),
        RangeTradingStrategy(),
        GridTradingStrategy(),
        VolatilityExpansionStrategy(),
        MTFConfluenceStrategy(),
        MarketStructureStrategy(),
        FundingAwarePerpStrategy(),
    ]
