"""Adaptive mid/low-cap strategy selector with regime, OOS evidence, and net-expectancy gating."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.config import MidLowCapConfig
from app.core.models import (
    Candle,
    Direction,
    ExchangeId,
    MarketRegime,
    MarketSnapshot,
    MarketType,
    StrategySignal,
)
from app.execution.paper_engine import EXCHANGE_FEE_SCHEDULE
from app.scanner.indicators import compute_indicators
from app.scanner.market_cap import MarketCapRecord
from app.strategies.base import BaseStrategy
from app.strategies.orchestrator import AIStrategyOrchestrator

REGIME_STRATEGY_MAP: dict[MarketRegime, tuple[str, ...]] = {
    MarketRegime.TRENDING_BULLISH: ("trend_following", "breakout_retest"),
    MarketRegime.TRENDING_BEARISH: ("trend_following", "breakout_retest"),
    MarketRegime.RANGING: ("mean_reversion", "breakout_retest"),
}


def compute_net_reward_risk(
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    fee_pct: float,
    slippage_pct: float,
) -> float:
    """Compute reward-to-risk ratio net of round-trip fees and slippage."""
    if entry_price <= 0:
        return 0.0
    risk_pct = abs(entry_price - stop_loss) / entry_price
    reward_pct = abs(take_profit - entry_price) / entry_price
    if risk_pct <= 0 or reward_pct <= 0:
        return 0.0
    round_trip_cost = 2.0 * (fee_pct + slippage_pct)
    net_reward = reward_pct - round_trip_cost
    net_risk = risk_pct + round_trip_cost
    if net_reward <= 0 or net_risk <= 0:
        return 0.0
    return round(net_reward / net_risk, 4)


class MidLowCapStrategySelector:
    """Selects adaptive signals from AIStrategyOrchestrator only when regime & OOS evidence permit."""

    def __init__(
        self,
        orchestrator: AIStrategyOrchestrator,
        config: MidLowCapConfig | None = None,
    ) -> None:
        self.orchestrator = orchestrator
        self.config = config or MidLowCapConfig()
        self._oos_cache: dict[tuple[str, str, str, int], dict[str, Any]] = {}

    def _allowed_for_regime(self, regime: MarketRegime) -> list[BaseStrategy]:
        regime_names = REGIME_STRATEGY_MAP.get(regime, ())
        allowed = set(self.config.allowed_strategies)
        return [
            s
            for s in self.orchestrator.strategies
            if s.name in allowed and s.name in regime_names and regime in s.supported_regimes
        ]

    def walk_forward_net_oos(
        self,
        candles: list[Candle],
        strategy: BaseStrategy,
        symbol: str,
        exchange: ExchangeId,
        fee_pct: float,
        slippage_pct: float,
        train_window: int = 40,
        horizon_bars: int = 12,
    ) -> dict[str, Any]:
        """Walk-forward OOS evaluation incorporating round-trip fees & slippage in R-multiples."""
        if not candles or len(candles) < train_window + horizon_bars + 2:
            return {
                "strategy": strategy.name,
                "trades": 0,
                "wins": 0,
                "win_rate": 0.0,
                "gross_expectancy_r": 0.0,
                "net_expectancy_r": 0.0,
            }

        cache_key = (exchange.value, symbol, strategy.name, candles[-1].timestamp)
        if cache_key in self._oos_cache:
            return self._oos_cache[cache_key]

        trades = 0
        wins = 0
        gross_r_list: list[float] = []
        net_r_list: list[float] = []

        for idx in range(train_window, len(candles) - horizon_bars):
            past = candles[: idx + 1]
            ind = compute_indicators(past)
            snap = MarketSnapshot(
                exchange=exchange,
                symbol=symbol,
                market_type=MarketType.SPOT,
                timestamp=datetime.now(timezone.utc),
                last_price=past[-1].close,
                bid=past[-1].close * 0.9995,
                ask=past[-1].close * 1.0005,
                spread_bps=10.0,
                volume_24h_usd=5_000_000.0,
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
            sig = strategy.evaluate(snap, self.orchestrator.calibrator)
            if sig is None:
                continue
            if not self.config.allow_short and sig.direction != Direction.LONG:
                continue

            entry = past[-1].close
            sl = sig.stop_loss
            risk = abs(entry - sl)
            if entry <= 0 or risk <= 0:
                continue

            # Enforce >= min_risk_reward_ratio target during OOS evaluation as well
            tp = self._select_target_for_min_rr(entry, sl, sig.take_profit_targets, sig.direction)
            if tp is None:
                continue

            future = candles[idx + 1 : idx + 1 + horizon_bars]
            gross_r = 0.0
            for bar in future:
                if sig.direction == Direction.LONG:
                    if bar.low <= sl:
                        gross_r = -1.0
                        break
                    if bar.high >= tp:
                        gross_r = (tp - entry) / risk
                        break
                else:
                    if bar.high >= sl:
                        gross_r = -1.0
                        break
                    if bar.low <= tp:
                        gross_r = (entry - tp) / risk
                        break
            else:
                exit_px = future[-1].close
                gross_r = (
                    (exit_px - entry) / risk
                    if sig.direction == Direction.LONG
                    else (entry - exit_px) / risk
                )

            cost_r = (2.0 * (fee_pct + slippage_pct) * entry) / risk
            net_r = gross_r - cost_r

            trades += 1
            if net_r > 0:
                wins += 1
            gross_r_list.append(gross_r)
            net_r_list.append(net_r)

        result = {
            "strategy": strategy.name,
            "trades": trades,
            "wins": wins,
            "win_rate": round(wins / trades, 4) if trades else 0.0,
            "gross_expectancy_r": round(sum(gross_r_list) / trades, 4) if trades else 0.0,
            "net_expectancy_r": round(sum(net_r_list) / trades, 4) if trades else 0.0,
        }
        self._oos_cache[cache_key] = result
        return result

    def _select_target_for_min_rr(
        self,
        entry: float,
        stop_loss: float,
        targets: list[float],
        direction: Direction,
    ) -> float | None:
        risk_dist = abs(entry - stop_loss)
        if risk_dist <= 0 or not targets:
            return None
        for tp in targets:
            valid_dir = (tp > entry) if direction == Direction.LONG else (tp < entry)
            if not valid_dir:
                continue
            if abs(tp - entry) / risk_dist >= self.config.min_risk_reward_ratio:
                return tp
        return None

    def select_signals(
        self,
        snapshots: list[MarketSnapshot],
        cap_records: dict[str, MarketCapRecord],
    ) -> tuple[list[StrategySignal], list[dict[str, Any]]]:
        """Return approved mid/low-cap signals and diagnostic rejection reasons."""
        selected: list[StrategySignal] = []
        diagnostics: list[dict[str, Any]] = []

        for snap in snapshots:
            if snap.market_type != MarketType.SPOT:
                continue
            eligible = self._allowed_for_regime(snap.regime)
            if not eligible:
                diagnostics.append(
                    {"symbol": snap.symbol, "exchange": snap.exchange.value, "reason": f"regime_not_permitted:{snap.regime.value}"}
                )
                continue

            fee_sched = EXCHANGE_FEE_SCHEDULE.get(
                (snap.exchange, MarketType.SPOT), {"taker": 0.0010}
            )
            fee_pct = fee_sched["taker"]
            slippage_pct = max(0.0002, (snap.spread_bps / 10000.0) * 0.6)
            candles = snap.candles_by_tf.get("15m") or snap.candles_by_tf.get("1h") or []

            for strat in eligible:
                sig = strat.evaluate(snap, self.orchestrator.calibrator)
                if sig is None:
                    continue
                if not self.config.allow_short and sig.direction != Direction.LONG:
                    diagnostics.append(
                        {"symbol": snap.symbol, "strategy": strat.name, "reason": "spot_short_disabled"}
                    )
                    continue

                # Ensure ATR stop and >= min_risk_reward_ratio (2.0)
                if snap.atr <= 0 or sig.stop_loss <= 0:
                    diagnostics.append(
                        {"symbol": snap.symbol, "strategy": strat.name, "reason": "missing_atr_stop"}
                    )
                    continue

                chosen_tp = self._select_target_for_min_rr(
                    snap.last_price, sig.stop_loss, sig.take_profit_targets, sig.direction
                )
                if chosen_tp is None:
                    diagnostics.append(
                        {"symbol": snap.symbol, "strategy": strat.name, "reason": "below_min_rr_2.0"}
                    )
                    continue

                risk_dist = abs(snap.last_price - sig.stop_loss)
                gross_rr = round(abs(chosen_tp - snap.last_price) / risk_dist, 2)
                net_rr = compute_net_reward_risk(
                    snap.last_price, sig.stop_loss, chosen_tp, fee_pct, slippage_pct
                )
                if net_rr <= 0:
                    diagnostics.append(
                        {"symbol": snap.symbol, "strategy": strat.name, "reason": "negative_net_rr_after_costs"}
                    )
                    continue

                oos = self.walk_forward_net_oos(
                    candles=candles,
                    strategy=strat,
                    symbol=snap.symbol,
                    exchange=snap.exchange,
                    fee_pct=fee_pct,
                    slippage_pct=slippage_pct,
                )
                if oos["trades"] < self.config.min_oos_trades:
                    diagnostics.append(
                        {
                            "symbol": snap.symbol,
                            "strategy": strat.name,
                            "reason": f"insufficient_oos_trades:{oos['trades']}",
                        }
                    )
                    continue
                if oos["net_expectancy_r"] <= self.config.min_oos_net_expectancy_r:
                    diagnostics.append(
                        {
                            "symbol": snap.symbol,
                            "strategy": strat.name,
                            "reason": f"non_positive_oos_net_expectancy:{oos['net_expectancy_r']}",
                        }
                    )
                    continue

                # Reorder take_profit_targets so chosen_tp is primary TP1
                reordered_tps = [chosen_tp] + [t for t in sig.take_profit_targets if t != chosen_tp]
                base = snap.symbol.split("/")[0].upper()
                cap_rec = cap_records.get(base)
                sig.take_profit_targets = reordered_tps
                sig.risk_reward_ratio = gross_rr
                sig.estimated_fee_pct = fee_pct
                sig.estimated_slippage_pct = round(slippage_pct, 6)
                sig.metadata = {
                    **sig.metadata,
                    "mid_low_cap": {
                        "cap_band": cap_rec.band.value if cap_rec else "unknown",
                        "market_cap_usd": cap_rec.market_cap_usd if cap_rec else None,
                        "cap_source": cap_rec.source if cap_rec else None,
                        "net_rr": net_rr,
                        "oos": oos,
                    },
                }
                selected.append(sig)

        selected.sort(
            key=lambda s: (
                s.metadata.get("mid_low_cap", {}).get("oos", {}).get("net_expectancy_r", 0.0),
                s.confidence_score * s.risk_reward_ratio,
            ),
            reverse=True,
        )
        return selected, diagnostics
