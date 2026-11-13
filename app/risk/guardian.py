"""Shared Global Risk Guardian enforcing portfolio, exchange, symbol, and operational limits."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.core.config import RiskPolicyConfig
from app.core.models import (
    MarketSnapshot,
    MarketType,
    PaperPosition,
    RiskDecision,
    StrategySignal,
)

if TYPE_CHECKING:
    from app.persistence.database import PaperDatabase


def _correlation_bucket(symbol: str) -> str:
    base = symbol.split("/")[0].upper()
    if base in {"BTC", "ETH", "SOL", "AVAX", "LINK"}:
        return "major_crypto_beta"
    return f"idiosyncratic_{base}"


class GlobalRiskGuardian:
    """Central non-bypassable risk gate for all paper strategies and bots."""

    def __init__(self, policy: RiskPolicyConfig, db: PaperDatabase | None = None) -> None:
        self.policy = policy
        self._db = db
        self.emergency_stop_active: bool = False
        self.emergency_stop_reason: str | None = None
        self.circuit_breaker_tripped: bool = False
        self.circuit_breaker_reason: str | None = None
        self.peak_equity_usd: float = 0.0
        self.daily_start_equity_usd: float = 0.0
        self.daily_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._recent_signals: dict[str, float] = {}
        self.alerts: list[dict[str, Any]] = []
        self._load_persisted_state()

    def _load_persisted_state(self) -> None:
        if self._db is None:
            return
        state = self._db.load_risk_state("global_risk_guardian")
        if not isinstance(state, dict):
            return
        self.emergency_stop_active = bool(state.get("emergency_stop_active", False))
        self.emergency_stop_reason = state.get("emergency_stop_reason")
        self.circuit_breaker_tripped = bool(state.get("circuit_breaker_tripped", False))
        self.circuit_breaker_reason = state.get("circuit_breaker_reason")
        self.peak_equity_usd = float(state.get("peak_equity_usd", 0.0) or 0.0)
        self.daily_start_equity_usd = float(state.get("daily_start_equity_usd", 0.0) or 0.0)
        saved_date = state.get("daily_date")
        if isinstance(saved_date, str) and saved_date:
            self.daily_date = saved_date

    def _persist_state(self) -> None:
        if self._db is None:
            return
        self._db.save_risk_state(
            "global_risk_guardian",
            {
                "emergency_stop_active": self.emergency_stop_active,
                "emergency_stop_reason": self.emergency_stop_reason,
                "circuit_breaker_tripped": self.circuit_breaker_tripped,
                "circuit_breaker_reason": self.circuit_breaker_reason,
                "peak_equity_usd": self.peak_equity_usd,
                "daily_start_equity_usd": self.daily_start_equity_usd,
                "daily_date": self.daily_date,
            },
        )

    def _record_alert(self, level: str, message: str) -> None:
        self.alerts.insert(
            0,
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level,
                "message": message,
            },
        )
        del self.alerts[100:]

    def trigger_emergency_stop(self, reason: str) -> None:
        self.emergency_stop_active = True
        self.emergency_stop_reason = reason
        self._record_alert("CRITICAL", f"Emergency stop triggered: {reason}")
        self._persist_state()

    def reset_emergency_stop(self) -> None:
        self.emergency_stop_active = False
        self.emergency_stop_reason = None
        self.circuit_breaker_tripped = False
        self.circuit_breaker_reason = None
        self._record_alert("INFO", "Emergency stop and circuit breaker reset")
        self._persist_state()

    def update_equity_watermarks(self, current_equity_usd: float) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        changed = False
        if self.daily_start_equity_usd <= 0 or today != self.daily_date:
            self.daily_date = today
            self.daily_start_equity_usd = current_equity_usd
            changed = True
        if current_equity_usd > self.peak_equity_usd:
            self.peak_equity_usd = current_equity_usd
            changed = True
        if changed:
            self._persist_state()

    def can_accept_new_signal(
        self,
        signal: StrategySignal,
        positions: list[PaperPosition],
    ) -> tuple[bool, str | None]:
        """Lightweight pre-flight check to avoid rejection-storm DB bloat."""
        if self.emergency_stop_active:
            return False, "emergency_stop_active"
        if self.circuit_breaker_tripped:
            return False, "circuit_breaker_tripped"
        if len(positions) >= self.policy.max_concurrent_positions:
            return False, "max_concurrent_positions_reached"
        dedup_key = f"{signal.exchange.value}:{signal.market_type.value}:{signal.symbol}:{signal.direction.value}:{signal.strategy_name}"
        last_seen = self._recent_signals.get(dedup_key)
        if last_seen is not None and (time.monotonic() - last_seen) < self.policy.duplicate_signal_window_sec:
            return False, "duplicate_signal_suppressed"
        return True, None

    def evaluate(
        self,
        signal: StrategySignal,
        requested_notional_usd: float,
        leverage: float,
        total_equity_usd: float,
        positions: list[PaperPosition],
        snapshot: MarketSnapshot | None,
        exchange_healthy: bool = True,
    ) -> RiskDecision:
        alerts: list[str] = []
        self.update_equity_watermarks(total_equity_usd)

        if self.emergency_stop_active:
            return RiskDecision(
                approved=False,
                reason=f"emergency_stop_active:{self.emergency_stop_reason}",
                alerts=[f"Emergency stop active: {self.emergency_stop_reason}"],
            )

        if self.circuit_breaker_tripped:
            return RiskDecision(
                approved=False,
                reason=f"circuit_breaker_tripped:{self.circuit_breaker_reason}",
                alerts=[f"Circuit breaker tripped: {self.circuit_breaker_reason}"],
            )

        if not exchange_healthy:
            msg = f"API outage protection blocked {signal.exchange.value}"
            self._record_alert("WARNING", msg)
            return RiskDecision(approved=False, reason="api_outage_protection", alerts=[msg])

        if total_equity_usd <= 0 or requested_notional_usd <= 0:
            return RiskDecision(approved=False, reason="invalid_equity_or_notional")

        # Daily loss limit & max drawdown checks
        daily_loss_pct = (
            (self.daily_start_equity_usd - total_equity_usd) / self.daily_start_equity_usd
            if self.daily_start_equity_usd > 0
            else 0.0
        )
        if daily_loss_pct >= self.policy.daily_loss_limit_pct:
            self.circuit_breaker_tripped = True
            self.circuit_breaker_reason = f"daily_loss_limit ({daily_loss_pct:.2%})"
            self._record_alert("CRITICAL", self.circuit_breaker_reason)
            self._persist_state()
            return RiskDecision(approved=False, reason="daily_loss_limit_exceeded", alerts=[self.circuit_breaker_reason])

        drawdown_pct = (
            (self.peak_equity_usd - total_equity_usd) / self.peak_equity_usd
            if self.peak_equity_usd > 0
            else 0.0
        )
        if drawdown_pct >= self.policy.max_drawdown_pct:
            self.circuit_breaker_tripped = True
            self.circuit_breaker_reason = f"max_drawdown ({drawdown_pct:.2%})"
            self._record_alert("CRITICAL", self.circuit_breaker_reason)
            self._persist_state()
            return RiskDecision(approved=False, reason="max_drawdown_exceeded", alerts=[self.circuit_breaker_reason])

        # Stop-loss requirement
        if self.policy.require_stop_loss and signal.stop_loss <= 0:
            return RiskDecision(approved=False, reason="missing_stop_loss")

        # Stale market data check
        if snapshot is None:
            return RiskDecision(approved=False, reason="missing_market_snapshot")
        age_sec = (datetime.now(timezone.utc) - snapshot.timestamp).total_seconds()
        if age_sec > self.policy.max_market_data_age_sec:
            return RiskDecision(approved=False, reason=f"stale_market_data:{age_sec:.1f}s")

        # Duplicate signal suppression
        dedup_key = f"{signal.exchange.value}:{signal.market_type.value}:{signal.symbol}:{signal.direction.value}:{signal.strategy_name}"
        now_mono = time.monotonic()
        last_seen = self._recent_signals.get(dedup_key)
        if last_seen is not None and (now_mono - last_seen) < self.policy.duplicate_signal_window_sec:
            return RiskDecision(approved=False, reason="duplicate_signal_suppressed")

        # Leverage cap & liquidation buffer check
        effective_leverage = leverage if signal.market_type == MarketType.PERP else 1.0
        if effective_leverage > self.policy.max_leverage:
            return RiskDecision(
                approved=False,
                reason=f"leverage_cap_exceeded:{effective_leverage}>{self.policy.max_leverage}",
            )
        if effective_leverage > 1.0:
            approx_liq_dist_pct = 1.0 / effective_leverage
            if approx_liq_dist_pct < self.policy.min_liquidation_buffer_pct:
                return RiskDecision(
                    approved=False,
                    reason=f"liquidation_buffer_too_small:{approx_liq_dist_pct:.2f}<{self.policy.min_liquidation_buffer_pct:.2f}",
                )

        # Concurrent positions limit
        if len(positions) >= self.policy.max_concurrent_positions:
            return RiskDecision(approved=False, reason="max_concurrent_positions_reached")

        # Exposure limits
        portfolio_exp = sum(abs(p.qty * p.mark_price) for p in positions)
        exchange_exp = sum(abs(p.qty * p.mark_price) for p in positions if p.exchange == signal.exchange)
        symbol_exp = sum(abs(p.qty * p.mark_price) for p in positions if p.symbol == signal.symbol)
        corr_bucket = _correlation_bucket(signal.symbol)
        corr_exp = sum(
            abs(p.qty * p.mark_price) for p in positions if _correlation_bucket(p.symbol) == corr_bucket
        )

        max_port = total_equity_usd * self.policy.max_portfolio_exposure_pct
        max_ex = total_equity_usd * self.policy.max_per_exchange_exposure_pct
        max_sym = total_equity_usd * self.policy.max_per_symbol_exposure_pct
        max_corr = total_equity_usd * self.policy.max_correlated_exposure_pct

        headroom = min(
            max_port - portfolio_exp,
            max_ex - exchange_exp,
            max_sym - symbol_exp,
            max_corr - corr_exp,
        )
        if headroom <= 10.0:
            reason = "exposure_limit_reached"
            self._record_alert("WARNING", f"{reason} for {signal.exchange.value} {signal.symbol}")
            return RiskDecision(approved=False, reason=reason)

        approved_notional = min(requested_notional_usd, headroom)
        if approved_notional < requested_notional_usd:
            alerts.append(f"Notional clipped from {requested_notional_usd:.2f} to {approved_notional:.2f} by risk caps")

        price = snapshot.last_price
        if price <= 0:
            return RiskDecision(approved=False, reason="invalid_mark_price")

        self._recent_signals[dedup_key] = now_mono
        qty = round(approved_notional / price, 6)
        return RiskDecision(approved=True, reason="approved", adjusted_qty=qty, alerts=alerts)

    def status_summary(self, total_equity_usd: float, positions: list[PaperPosition]) -> dict[str, Any]:
        port_exp = sum(abs(p.qty * p.mark_price) for p in positions)
        return {
            "emergency_stop_active": self.emergency_stop_active,
            "emergency_stop_reason": self.emergency_stop_reason,
            "circuit_breaker_tripped": self.circuit_breaker_tripped,
            "circuit_breaker_reason": self.circuit_breaker_reason,
            "total_equity_usd": round(total_equity_usd, 2),
            "peak_equity_usd": round(self.peak_equity_usd, 2),
            "portfolio_exposure_usd": round(port_exp, 2),
            "portfolio_exposure_pct": round(port_exp / total_equity_usd, 4) if total_equity_usd > 0 else 0.0,
            "open_positions": len(positions),
            "recent_alerts": self.alerts[:20],
        }
