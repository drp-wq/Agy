"""Unified AI Brain coordinator for AGY + Codex paper capital allocation and closed-loop sweeps."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.risk.guardian import GlobalRiskGuardian
from app.treasury.closed_loop_router import (
    ClosedLoopAssetRouter,
    ClosedLoopTransferRecord,
    ClosedLoopVenue,
)
from app.treasury.models import QUANT_2, to_decimal, utc_now


class VenueBotMetrics(BaseModel):
    """Normalized performance and health snapshot for a CEX or DEX bot venue."""

    venue: ClosedLoopVenue
    active: bool = True
    net_pnl_usd: Decimal = Decimal("0.00")
    sharpe_estimate: Decimal = Decimal("0.00")
    drawdown_pct: Decimal = Decimal("0.00")
    target_capital_usd: Decimal = Decimal("4000.00")


class UnifiedBrainCycleReport(BaseModel):
    """Deterministic audit report of one UnifiedTradingBrain allocation/sweep cycle."""

    timestamp_utc: str = Field(default_factory=lambda: utc_now().isoformat())
    mode: str = "PAPER_SIMULATION_ONLY"
    live_execution_enabled: bool = False
    halted_by_risk_guardian: bool = False
    actions_executed: list[ClosedLoopTransferRecord] = Field(default_factory=list)
    skipped_reasons: list[str] = Field(default_factory=list)
    invariant: dict[str, Any] = Field(default_factory=dict)


class UnifiedTradingBrain:
    """Coordinates paper capital allocations and idle-capital sweeps across 3 CEXs and Solana DEX."""

    def __init__(
        self,
        router: ClosedLoopAssetRouter,
        risk_guardian: GlobalRiskGuardian | None = None,
        min_step_usd: Decimal = Decimal("100.00"),
        max_step_usd: Decimal = Decimal("500.00"),
    ) -> None:
        self.router = router
        self.risk_guardian = risk_guardian or router.risk_guardian
        self.min_step_usd = to_decimal(min_step_usd, QUANT_2)
        self.max_step_usd = to_decimal(max_step_usd, QUANT_2)
        self.last_report: UnifiedBrainCycleReport | None = None

    def step(
        self,
        metrics: list[VenueBotMetrics],
        cycle_id: str,
    ) -> UnifiedBrainCycleReport:
        """Evaluate venue bot metrics and execute paper closed-loop allocations or sweeps."""
        if self.risk_guardian is not None and (
            self.risk_guardian.emergency_stop_active or self.risk_guardian.circuit_breaker_tripped
        ):
            report = UnifiedBrainCycleReport(
                halted_by_risk_guardian=True,
                skipped_reasons=["global_risk_guardian_halt_active"],
                invariant=self.router.check_equity_invariant(),
            )
            self.last_report = report
            return report

        executed: list[ClosedLoopTransferRecord] = []
        skipped: list[str] = []
        summary = self.router.summary()
        balances = {ClosedLoopVenue(k): to_decimal(v, QUANT_2) for k, v in summary["balances_usd"].items()}

        # 1. Sweep excess or inactive venue capital back to Hub first
        for item in metrics:
            if item.venue == ClosedLoopVenue.HUB_BINANCE_MASTER:
                continue
            target = Decimal("0.00") if not item.active else item.target_capital_usd
            try:
                swept = self.router.auto_sweep_idle_capital(
                    venue=item.venue,
                    target_usd=target,
                    idempotency_prefix=f"brain_sweep_{cycle_id}",
                )
                if swept is not None:
                    executed.append(swept)
            except Exception as exc:
                skipped.append(f"{item.venue.value}:sweep_skipped:{exc}")

        # 2. Allocate from Hub to top-scoring active venues below target
        scored = sorted(
            [m for m in metrics if m.active and m.venue != ClosedLoopVenue.HUB_BINANCE_MASTER],
            key=lambda m: (m.sharpe_estimate, m.net_pnl_usd),
            reverse=True,
        )
        for item in scored:
            if item.drawdown_pct >= Decimal("0.05") or item.sharpe_estimate < Decimal("0.00"):
                skipped.append(f"{item.venue.value}:score_or_drawdown_gate")
                continue
            current_bal = balances.get(item.venue, Decimal("0.00"))
            deficit = to_decimal(item.target_capital_usd - current_bal, QUANT_2)
            if deficit < self.min_step_usd:
                continue
            step_amt = min(deficit, self.max_step_usd)
            try:
                rec = self.router.simulate_transfer(
                    source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
                    destination_venue=item.venue,
                    amount_usd=step_amt,
                    idempotency_key=f"brain_alloc_{cycle_id}_{item.venue.value}",
                    reason=f"unified_brain_rebalance:sharpe={item.sharpe_estimate}",
                    requested_by="unified_ai_brain",
                )
                executed.append(rec)
            except Exception as exc:
                skipped.append(f"{item.venue.value}:alloc_skipped:{exc}")

        report = UnifiedBrainCycleReport(
            actions_executed=executed,
            skipped_reasons=skipped,
            invariant=self.router.check_equity_invariant(),
        )
        self.last_report = report
        return report
