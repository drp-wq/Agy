from decimal import Decimal

from app.core.config import RiskPolicyConfig
from app.intelligence.unified_brain import UnifiedTradingBrain, VenueBotMetrics
from app.risk.guardian import GlobalRiskGuardian
from app.treasury.closed_loop_router import ClosedLoopAssetRouter, ClosedLoopVenue


def test_unified_brain_allocates_and_sweeps_preserving_equity():
    guardian = GlobalRiskGuardian(RiskPolicyConfig())
    router = ClosedLoopAssetRouter(risk_guardian=guardian)
    brain = UnifiedTradingBrain(router=router, risk_guardian=guardian)

    metrics = [
        VenueBotMetrics(
            venue=ClosedLoopVenue.CEX_BINANCE,
            active=True,
            sharpe_estimate=Decimal("1.80"),
            net_pnl_usd=Decimal("320.00"),
            target_capital_usd=Decimal("4300.00"),
        ),
        VenueBotMetrics(
            venue=ClosedLoopVenue.CEX_OKX,
            active=True,
            sharpe_estimate=Decimal("0.90"),
            net_pnl_usd=Decimal("120.00"),
            target_capital_usd=Decimal("4500.00"),  # starts at 5500 -> sweeps 1000 back to Hub
        ),
        VenueBotMetrics(
            venue=ClosedLoopVenue.DEX_SOLANA,
            active=True,
            sharpe_estimate=Decimal("-0.40"),  # negative sharpe -> skipped for allocation
            net_pnl_usd=Decimal("-25.00"),
            target_capital_usd=Decimal("2000.00"),
        ),
    ]

    report = brain.step(metrics, cycle_id="c1")
    assert report.halted_by_risk_guardian is False
    assert report.live_execution_enabled is False
    assert len(report.actions_executed) == 2  # 1 sweep from OKX + 1 alloc to Binance
    assert report.invariant["conservation_ok"] is True
    assert any("dex_solana:score_or_drawdown_gate" in r for r in report.skipped_reasons)


def test_unified_brain_respects_risk_guardian_halt():
    guardian = GlobalRiskGuardian(RiskPolicyConfig())
    guardian.trigger_emergency_stop("drawdown_limit")
    router = ClosedLoopAssetRouter(risk_guardian=guardian)
    brain = UnifiedTradingBrain(router=router, risk_guardian=guardian)

    report = brain.step(
        [VenueBotMetrics(venue=ClosedLoopVenue.CEX_BINANCE, sharpe_estimate=Decimal("2.0"), target_capital_usd=Decimal("5000.00"))],
        cycle_id="c_halt",
    )
    assert report.halted_by_risk_guardian is True
    assert report.actions_executed == []
