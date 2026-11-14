"""Safe AI collaboration contract with clear role boundaries and bounded advisory outputs."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.core.security import (
    LIVE_EXECUTION_PERMANENTLY_DISABLED,
    SecretRedactionFilter,
    assert_paper_only,
)
from app.intelligence.binance_intel import (
    BinanceAccountObservation,
    BinanceMarketReport,
    BinanceMarketSignalSummary,
    ReconciliationCoverageAssessment,
)


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentRole(str, Enum):
    MARKET_OBSERVER = "market_observer"
    ACCOUNT_AUDITOR = "account_auditor"
    RISK_GATEKEEPER = "risk_gatekeeper"
    STRATEGY_SYNTHESIZER = "strategy_synthesizer"


class RoleAuthorityBoundary(BaseModel):
    """Explicit capability boundary for an AI collaboration participant."""

    role: AgentRole
    can_read_market_data: bool = True
    can_read_account_snapshot: bool = True
    can_execute_live: Literal[False] = False
    can_modify_credentials: Literal[False] = False
    can_write_exchange: Literal[False] = False
    max_output_chars: int = Field(default=500, ge=50, le=2000)


class RoleHandoff(BaseModel):
    """Bounded structured contribution from one collaboration role."""

    role: AgentRole
    authority: RoleAuthorityBoundary
    status: Literal["OK", "DEGRADED", "BLOCKED"]
    summary: str = Field(..., max_length=500)
    evidence_keys: list[str] = Field(default_factory=list, max_length=12)


class AdvisoryActionType(str, Enum):
    OBSERVE = "OBSERVE"
    PAPER_SIGNAL_REVIEW = "PAPER_SIGNAL_REVIEW"
    RECONCILE_EXPAND = "RECONCILE_EXPAND"
    HOLD = "HOLD"
    INVESTIGATE_BLOCKER = "INVESTIGATE_BLOCKER"
    CAPITAL_ALLOCATION_PROPOSAL = "CAPITAL_ALLOCATION_PROPOSAL"
    SWEEP_TO_HUB_PROPOSAL = "SWEEP_TO_HUB_PROPOSAL"


class BoundedAdvisoryAction(BaseModel):
    """Non-executing bounded recommendation produced by the AI collaboration."""

    action_type: AdvisoryActionType
    target: str = Field(..., max_length=64)
    rationale: str = Field(..., max_length=240)
    priority: Literal["LOW", "MEDIUM", "HIGH"]
    execution_mode: Literal["PAPER_ADVISORY_ONLY"] = "PAPER_ADVISORY_ONLY"


class AICollaborationBrief(BaseModel):
    """Validated, bounded, read-only collaboration output across all four roles."""

    schema_version: Literal["1.0"] = "1.0"
    generated_at_utc: str
    execution_mode: Literal["PAPER_ADVISORY_ONLY"] = "PAPER_ADVISORY_ONLY"
    live_execution_allowed: Literal[False] = False
    live_readiness: Literal["NOT_APPROVED"] = "NOT_APPROVED"
    roles: list[RoleHandoff] = Field(..., min_length=4, max_length=4)
    market_summary: str = Field(..., max_length=500)
    top_opportunities: list[BinanceMarketSignalSummary] = Field(default_factory=list, max_length=5)
    reconciliation_coverage: ReconciliationCoverageAssessment
    completeness_claim_allowed: bool
    recommended_actions: list[BoundedAdvisoryAction] = Field(default_factory=list, max_length=5)
    warnings: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _enforce_contract_invariants(self) -> AICollaborationBrief:
        if not LIVE_EXECUTION_PERMANENTLY_DISABLED:
            raise ValueError("Structural live execution lock must remain enabled.")
        if self.live_execution_allowed is not False:
            raise ValueError("live_execution_allowed must always be False.")
        if (
            not self.reconciliation_coverage.complete_account_reconciliation
            and self.completeness_claim_allowed
        ):
            raise ValueError(
                "completeness_claim_allowed cannot be True when complete_account_reconciliation is False."
            )
        present_roles = {handoff.role for handoff in self.roles}
        expected_roles = set(AgentRole)
        if present_roles != expected_roles:
            missing = sorted(r.value for r in (expected_roles - present_roles))
            raise ValueError(f"All 4 collaboration roles are required; missing: {missing}")
        return self


class AICollaborationCoordinator:
    """Coordinates read-only Binance intelligence with Gemini/AI advisory synthesis."""

    def __init__(self, redactor: SecretRedactionFilter | None = None) -> None:
        self._redactor = redactor or SecretRedactionFilter()

    def _clean(self, text: str, max_len: int) -> str:
        return self._redactor.redact(text).strip()[:max_len]

    def build_brief(
        self,
        market_report: BinanceMarketReport,
        account_obs: BinanceAccountObservation,
        readiness_report: dict[str, Any] | None = None,
    ) -> AICollaborationBrief:
        """Assemble and validate a bounded multi-role advisory brief."""
        assert_paper_only("paper")

        readiness_ok = bool((readiness_report or {}).get("operational_checks_passed", False))
        pending_items = list((readiness_report or {}).get("pending") or [])
        cov = account_obs.reconciliation

        # 1. Market Observer
        if market_report.markets_scanned > 0:
            top = market_report.top_opportunities[0]
            market_summary = self._clean(
                f"Scanned {market_report.markets_scanned} Binance markets "
                f"({market_report.spot_count} spot, {market_report.perp_count} perp). "
                f"Top score: {top.symbol} ({top.market_type.value}) score={top.opportunity_score:.3f}, "
                f"regime={top.regime.value}, spread={top.spread_bps:.2f}bps.",
                500,
            )
            market_status: Literal["OK", "DEGRADED", "BLOCKED"] = "OK"
        else:
            market_summary = "No Binance market snapshots available for analysis."
            market_status = "DEGRADED"

        observer_handoff = RoleHandoff(
            role=AgentRole.MARKET_OBSERVER,
            authority=RoleAuthorityBoundary(
                role=AgentRole.MARKET_OBSERVER,
                can_read_market_data=True,
                can_read_account_snapshot=False,
            ),
            status=market_status,
            summary=market_summary,
            evidence_keys=["markets_scanned", "top_opportunities", "cross_exchange_dislocations"],
        )

        # 2. Account Auditor
        auditor_status: Literal["OK", "DEGRADED", "BLOCKED"] = (
            "OK" if account_obs.auth_status == "ok" else "DEGRADED"
        )
        auditor_summary = self._clean(
            f"Read-only auth={account_obs.auth_status}, assets={account_obs.non_zero_asset_count}, "
            f"open_futures_positions={account_obs.open_futures_positions}. "
            f"Reconciliation={cov.coverage_type} (complete={cov.complete_account_reconciliation}).",
            500,
        )
        auditor_handoff = RoleHandoff(
            role=AgentRole.ACCOUNT_AUDITOR,
            authority=RoleAuthorityBoundary(
                role=AgentRole.ACCOUNT_AUDITOR,
                can_read_market_data=False,
                can_read_account_snapshot=True,
            ),
            status=auditor_status,
            summary=auditor_summary,
            evidence_keys=["auth_status", "reconciliation_coverage", "uncovered_areas"],
        )

        # 3. Risk Gatekeeper
        gate_status: Literal["OK", "DEGRADED", "BLOCKED"] = "OK" if readiness_ok else "DEGRADED"
        gate_summary = self._clean(
            f"Paper-only lock active (LIVE_EXECUTION_PERMANENTLY_DISABLED={LIVE_EXECUTION_PERMANENTLY_DISABLED}). "
            f"Operational checks passed={readiness_ok}; live_readiness=NOT_APPROVED; "
            f"pending={len(pending_items)} item(s).",
            500,
        )
        gate_handoff = RoleHandoff(
            role=AgentRole.RISK_GATEKEEPER,
            authority=RoleAuthorityBoundary(
                role=AgentRole.RISK_GATEKEEPER,
                can_read_market_data=True,
                can_read_account_snapshot=True,
            ),
            status=gate_status,
            summary=gate_summary,
            evidence_keys=["live_execution_disabled", "operational_checks_passed", "pending"],
        )

        # 4. Strategy Synthesizer (bounded advisory actions)
        actions: list[BoundedAdvisoryAction] = []
        if market_report.top_opportunities:
            best = market_report.top_opportunities[0]
            actions.append(
                BoundedAdvisoryAction(
                    action_type=AdvisoryActionType.PAPER_SIGNAL_REVIEW,
                    target=f"binance:{best.market_type.value}:{best.symbol}",
                    rationale=self._clean(
                        f"Review paper signal alignment for {best.symbol} "
                        f"(score={best.opportunity_score:.3f}, regime={best.regime.value}).",
                        240,
                    ),
                    priority="MEDIUM",
                )
            )
        if not cov.complete_account_reconciliation:
            actions.append(
                BoundedAdvisoryAction(
                    action_type=AdvisoryActionType.RECONCILE_EXPAND,
                    target="binance:reconciliation",
                    rationale=self._clean(
                        f"Coverage is {cov.coverage_type}; uncovered={', '.join(cov.uncovered_areas[:3])}.",
                        240,
                    ),
                    priority="HIGH",
                )
            )
        if not readiness_ok:
            actions.append(
                BoundedAdvisoryAction(
                    action_type=AdvisoryActionType.INVESTIGATE_BLOCKER,
                    target="platform:readiness",
                    rationale="Operational readiness checks not confirmed passed.",
                    priority="HIGH",
                )
            )
        if not actions:
            actions.append(
                BoundedAdvisoryAction(
                    action_type=AdvisoryActionType.HOLD,
                    target="binance:paper",
                    rationale="Continue passive paper observation under existing risk limits.",
                    priority="LOW",
                )
            )

        synth_summary = self._clean(
            f"Synthesized {len(actions)} bounded paper-only advisory action(s); "
            f"completeness_claim_allowed={cov.completeness_claim_allowed}.",
            500,
        )
        synth_handoff = RoleHandoff(
            role=AgentRole.STRATEGY_SYNTHESIZER,
            authority=RoleAuthorityBoundary(
                role=AgentRole.STRATEGY_SYNTHESIZER,
                can_read_market_data=True,
                can_read_account_snapshot=True,
            ),
            status="OK",
            summary=synth_summary,
            evidence_keys=["recommended_actions", "completeness_claim_allowed"],
        )

        warnings: list[str] = [self._clean(w, 200) for w in market_report.warnings]
        if not cov.complete_account_reconciliation:
            warnings.append(
                self._clean(
                    f"Partial reconciliation only ({cov.coverage_type}); do not claim full account coverage.",
                    200,
                )
            )
        if pending_items:
            warnings.append(
                self._clean(f"Live readiness pending: {', '.join(str(x) for x in pending_items[:4])}", 200)
            )

        return AICollaborationBrief(
            generated_at_utc=_utc_iso(),
            roles=[observer_handoff, auditor_handoff, gate_handoff, synth_handoff],
            market_summary=market_summary,
            top_opportunities=market_report.top_opportunities[:5],
            reconciliation_coverage=cov,
            completeness_claim_allowed=cov.completeness_claim_allowed,
            recommended_actions=actions[:5],
            warnings=warnings[:10],
        )
