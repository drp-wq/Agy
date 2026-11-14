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


def test_brain_restart_cycle_replay_and_conflict(tmp_path):
    import pytest
    from app.treasury.models import DuplicateIdempotencyError
    path = tmp_path / 'router.sqlite3'
    metrics = [VenueBotMetrics(venue=ClosedLoopVenue.CEX_BINANCE,
                              target_capital_usd=Decimal('4500'))]
    brain = UnifiedTradingBrain(ClosedLoopAssetRouter(state_path=path))
    report = brain.step(metrics, 'restart')
    restarted = UnifiedTradingBrain(ClosedLoopAssetRouter(state_path=path))
    assert restarted.last_report == report
    assert restarted.step(metrics, 'restart') == report
    assert restarted.router.summary()['transfer_count'] == 1
    assert restarted.router.reconcile()['balanced']
    with pytest.raises(DuplicateIdempotencyError):
        restarted.step([], 'restart')


def test_brain_missing_metrics_persist_without_transfers(tmp_path):
    path = tmp_path / 'router.sqlite3'
    brain = UnifiedTradingBrain(ClosedLoopAssetRouter(state_path=path))
    report = brain.step([], 'missing', unavailable_reason='stale_service')
    assert report.actions_executed == []
    assert UnifiedTradingBrain(ClosedLoopAssetRouter(state_path=path)).last_report == report


def test_brain_report_write_failure_rolls_back_transfer(tmp_path):
    import sqlite3
    import pytest
    path = tmp_path / 'router.sqlite3'
    brain = UnifiedTradingBrain(ClosedLoopAssetRouter(state_path=path))
    before = brain.router.summary()['balances_usd']
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TRIGGER fail_cycle BEFORE INSERT ON unified_brain_cycles "
                     "BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        brain.step([VenueBotMetrics(venue=ClosedLoopVenue.CEX_BINANCE,
                                   target_capital_usd=Decimal('4500'))], 'failure')
    assert brain.router.summary()['balances_usd'] == before
    assert brain.last_report is None
    assert ClosedLoopAssetRouter(state_path=path).summary()['transfer_count'] == 0


def test_service_metrics_stale_fail_closed(tmp_path):
    from types import SimpleNamespace
    import pytest
    from app.intelligence.service_metrics import collect_service_metrics
    with pytest.raises(ValueError, match='stale'):
        collect_service_metrics(SimpleNamespace(last_cycle_at=None),
                                ClosedLoopAssetRouter(state_path=tmp_path / 'router.sqlite3'))


def test_guardian_halt_survives_restart_with_persisted_report(tmp_path):
    from app.persistence.database import PaperDatabase
    path = tmp_path / 'risk.sqlite3'
    db = PaperDatabase(path)
    guardian = GlobalRiskGuardian(RiskPolicyConfig(), db=db)
    guardian.trigger_emergency_stop('restart_halt')
    db.close()
    db = PaperDatabase(path)
    guardian = GlobalRiskGuardian(RiskPolicyConfig(), db=db)
    router = ClosedLoopAssetRouter(state_path=tmp_path / 'router.sqlite3', risk_guardian=guardian)
    brain = UnifiedTradingBrain(router)
    report = brain.step([VenueBotMetrics(venue=ClosedLoopVenue.CEX_BINANCE,
                                        target_capital_usd=Decimal('4500'))], 'halt_restart')
    assert report.halted_by_risk_guardian
    assert report.actions_executed == []
    assert UnifiedTradingBrain(ClosedLoopAssetRouter(state_path=router.state_path)).last_report == report
    db.close()


def test_service_metrics_use_observed_accounts_and_ledger_targets(tmp_path):
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from app.intelligence.service_metrics import collect_service_metrics
    account = {'exchange': 'binance', 'cash_usd': '1000', 'realized_pnl_usd': '10'}
    service = SimpleNamespace(
        last_cycle_at=datetime.now(timezone.utc),
        db=SimpleNamespace(list_accounts=lambda: [account], list_positions=lambda: []),
        treasury=SimpleNamespace(reconcile=lambda: {'balanced': True},
                                 list_agent_slots=lambda: [SimpleNamespace(
                                     exchange=SimpleNamespace(value='binance'), capital_budget=Decimal('4300'))]),
    )
    router = ClosedLoopAssetRouter(state_path=tmp_path / 'router.sqlite3')
    for i, equity in enumerate(('1000', '1010', '1005', '1020')):
        account['cash_usd'] = equity
        service.last_cycle_at = datetime.now(timezone.utc) - timedelta(seconds=30 * (3 - i))
        with router.transaction():
            metrics = collect_service_metrics(service, router)
        if i < 3:
            assert metrics == []
    assert len(metrics) == 1
    assert metrics[0].venue == ClosedLoopVenue.CEX_BINANCE
    assert metrics[0].target_capital_usd == Decimal('4300')
    assert metrics[0].net_pnl_usd == Decimal('10')
    assert metrics[0].sharpe_estimate > 0
