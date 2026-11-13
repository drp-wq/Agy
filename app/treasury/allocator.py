"""Risk-adjusted agent strategy budget allocator subordinate to GlobalRiskGuardian."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.risk.guardian import GlobalRiskGuardian
from app.treasury.ledger import UnifiedTreasuryLedger
from app.treasury.models import (
    AgentSlotStatus,
    QUANT_2,
    RebalanceProposal,
    to_decimal,
    utc_now,
)


class AgentStrategyAllocator:
    """Proposes and applies paper budget rebalances within loss caps, concentration limits, and cooldowns."""

    def __init__(self, ledger: UnifiedTreasuryLedger, risk_guardian: GlobalRiskGuardian) -> None:
        self.ledger = ledger
        self.risk_guardian = risk_guardian
        self._last_rebalance_by_agent: dict[str, datetime] = {}

    def _compute_risk_adjusted_score(self, agent_id: str, budget: Decimal) -> Decimal:
        summary = self.ledger.get_attribution_summary("agent_id").get(agent_id)
        if not summary:
            return Decimal("0")
        net_pnl = to_decimal(summary["net_trading_profit_usd"])
        denom = max(budget, Decimal("100"))
        # Simple bounded risk-adjusted return ratio
        return to_decimal(net_pnl / denom)

    def propose_rebalance(self, apply: bool = False) -> list[RebalanceProposal]:
        pool = self.ledger.get_master_pool()
        slots = self.ledger.list_agent_slots()
        now = utc_now()

        max_single_budget = to_decimal(
            pool.total_capital * to_decimal(self.ledger.config.max_single_agent_concentration_pct),
            QUANT_2,
        )
        max_aggregate_budget = to_decimal(
            pool.total_capital * to_decimal(self.ledger.config.max_aggregate_allocated_pct),
            QUANT_2,
        )

        # Global Risk Guardian is supreme: no agent can override emergency stop or circuit breaker.
        global_halt_reason: str | None = None
        if self.risk_guardian.emergency_stop_active:
            global_halt_reason = f"global_risk_guardian_emergency_stop:{self.risk_guardian.emergency_stop_reason}"
        elif self.risk_guardian.circuit_breaker_tripped:
            global_halt_reason = f"global_risk_guardian_circuit_breaker:{self.risk_guardian.circuit_breaker_reason}"

        proposals: list[RebalanceProposal] = []
        running_aggregate = Decimal("0")

        for slot in slots:
            if slot.status in {AgentSlotStatus.DISABLED, AgentSlotStatus.PAUSED}:
                continue

            score = self._compute_risk_adjusted_score(slot.agent_id, slot.capital_budget)
            summary = self.ledger.get_attribution_summary("agent_id").get(slot.agent_id, {})
            net_pnl = to_decimal(summary.get("net_trading_profit_usd", "0"))

            loss_cap_pct = min(
                slot.daily_loss_limit_pct,
                to_decimal(self.ledger.config.agent_daily_loss_cap_pct),
            )
            loss_cap_usd = to_decimal(max(slot.capital_budget, Decimal("100")) * loss_cap_pct, QUANT_2)

            blocked_reason: str | None = None
            approved = True
            reason = "risk_adjusted_rebalance"

            if global_halt_reason is not None:
                proposed = min(slot.capital_budget, Decimal("0"))
                approved = False
                blocked_reason = global_halt_reason
                reason = "blocked_by_global_risk_guardian"
            elif net_pnl <= -loss_cap_usd:
                proposed = Decimal("0")
                approved = True
                reason = f"loss_cap_breached:net_pnl={net_pnl}<=-{loss_cap_usd}"
            else:
                # Scale budget modestly by risk-adjusted score, capped by concentration & aggregate limits
                multiplier = Decimal("1.0") + max(Decimal("-0.5"), min(Decimal("0.5"), score))
                base_target = max(slot.capital_budget, to_decimal(pool.total_capital / Decimal("18"), QUANT_2))
                raw_target = to_decimal(base_target * multiplier, QUANT_2)
                capped_single = min(raw_target, max_single_budget)
                remaining_aggregate = max(Decimal("0"), max_aggregate_budget - running_aggregate)
                proposed = min(capped_single, remaining_aggregate)

                last_ts = self._last_rebalance_by_agent.get(slot.agent_id)
                if last_ts is not None:
                    elapsed = (now - last_ts).total_seconds()
                    if elapsed < self.ledger.config.rebalance_cooldown_sec:
                        approved = False
                        blocked_reason = (
                            f"rebalance_cooldown_active:{int(self.ledger.config.rebalance_cooldown_sec - elapsed)}s"
                        )
                        proposed = slot.capital_budget

            if approved:
                running_aggregate += proposed
            else:
                running_aggregate += slot.capital_budget

            prop = RebalanceProposal(
                agent_id=slot.agent_id,
                current_budget=slot.capital_budget,
                proposed_budget=proposed,
                risk_adjusted_score=score,
                reason=reason,
                approved=approved,
                blocked_reason=blocked_reason,
                created_at=now,
            )
            proposals.append(prop)

            with self.ledger.db.transaction() as conn:
                conn.execute(
                    """
                    INSERT INTO treasury_rebalance_proposals (
                        proposal_id, agent_id, current_budget, proposed_budget,
                        risk_adjusted_score, reason, approved, blocked_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        prop.proposal_id,
                        prop.agent_id,
                        str(prop.current_budget),
                        str(prop.proposed_budget),
                        str(prop.risk_adjusted_score),
                        prop.reason,
                        1 if prop.approved else 0,
                        prop.blocked_reason,
                        prop.created_at.isoformat(),
                    ),
                )
                if apply and prop.approved and prop.proposed_budget != prop.current_budget:
                    conn.execute(
                        """
                        UPDATE treasury_agent_slots
                        SET capital_budget = ?, updated_at = ?
                        WHERE agent_id = ?
                        """,
                        (str(prop.proposed_budget), now.isoformat(), prop.agent_id),
                    )
                    self._last_rebalance_by_agent[prop.agent_id] = now

        return proposals
