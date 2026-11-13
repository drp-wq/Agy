"""Tests for unified 18-agent paper treasury, double-entry journal, FX, lifecycle, attribution, and invariants."""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import (
    EngineConfig,
    ExchangeCredentials,
    LoadedEnvironment,
    RiskPolicyConfig,
    UnifiedTreasuryConfig,
)
from app.core.models import ExchangeId
from app.core.security import (
    LIVE_EXECUTION_PERMANENTLY_DISABLED,
    LiveExecutionBlockedError,
    enforce_read_only_exchange,
)
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.server import create_dashboard_app
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.service import TradingEngineService
from app.treasury import (
    AgentSlotConfig,
    AgentSlotStatus,
    AgentStrategyAllocator,
    AttributionCategory,
    DuplicateIdempotencyError,
    InsufficientFundsError,
    InvalidTransferTransitionError,
    JournalEntry,
    JournalEntryLine,
    LedgerScope,
    TransferLifecycleState,
    TreasuryError,
    TreasuryInvariantError,
    UnifiedTreasuryLedger,
    to_decimal,
)


def _make_ledger(tmp_path: Path, initial_capital: float = 10000.0) -> tuple[PaperDatabase, UnifiedTreasuryLedger]:
    db = PaperDatabase(tmp_path / "treasury.sqlite3")
    cfg = UnifiedTreasuryConfig(
        enabled=True,
        initial_master_capital=initial_capital,
        default_usdt_usd_rate=0.9995,
    )
    ledger = UnifiedTreasuryLedger(db, cfg)
    return db, ledger


def test_18_agent_slots_configurable_and_not_all_active(tmp_path: Path):
    _, ledger = _make_ledger(tmp_path)
    slots = ledger.initialize_default_slots(18)
    assert len(slots) == 18
    # Do not assert 18 are already active; default initialized slots are IDLE
    assert all(s.status == AgentSlotStatus.IDLE for s in slots)

    # Activate 2 slots with custom budgets/limits
    s1 = ledger.configure_agent_slot(
        AgentSlotConfig(
            agent_id="agent_01",
            slot_index=1,
            name="Alpha Momentum",
            strategy="trend_following",
            exchange=ExchangeId.BINANCE,
            account_id="binance:sub_01",
            allowed_symbols=["BTC/USDT", "SOL/USDT"],
            capital_budget=Decimal("1500"),
            status=AgentSlotStatus.ACTIVE,
        )
    )
    assert s1.status == AgentSlotStatus.ACTIVE
    assert s1.capital_budget == Decimal("1500.00000000")
    assert sum(1 for s in ledger.list_agent_slots() if s.status == AgentSlotStatus.ACTIVE) == 1


def test_accounting_conservation_double_entry_and_append_only(tmp_path: Path):
    db, ledger = _make_ledger(tmp_path, initial_capital=5000.0)
    ledger.initialize_default_slots(18)

    # Imbalanced journal entry must be rejected
    with pytest.raises(TreasuryInvariantError):
        ledger.post_journal_entry(
            JournalEntry(
                idempotency_key="bad_imbalance",
                event_type="TEST",
                reference_id="ref_1",
                fx_rate_to_usd=Decimal("0.9995"),
                fx_timestamp=ledger.get_latest_fx_rate().as_of,
                lines=[
                    JournalEntryLine(account_code="a", asset="USDT", debit=Decimal("100"), usd_value=Decimal("99.95")),
                    JournalEntryLine(account_code="b", asset="USDT", credit=Decimal("90"), usd_value=Decimal("89.955")),
                ],
            )
        )

    # Single master pool conservation: cannot double spend across agents
    ledger.reserve_capital("agent_01", "3000", idempotency_key="res_1")
    with pytest.raises(InsufficientFundsError):
        ledger.reserve_capital("agent_02", "2500", idempotency_key="res_2")

    pool = ledger.get_master_pool()
    assert pool.total_capital == Decimal("5000.00000000")
    assert pool.free_capital == Decimal("2000.00000000")
    assert pool.reserved_capital == Decimal("3000.00000000")

    rec = ledger.reconcile()
    assert rec["balanced"] is True
    assert rec["pool_conservation_ok"] is True

    # Append-only SQLite trigger blocks updates/deletes on journal
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db._conn.execute("UPDATE treasury_journal_entries SET event_type = 'HACKED'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        db._conn.execute("DELETE FROM treasury_journal_lines")


def test_idempotency_and_duplicate_transfer_rejection(tmp_path: Path):
    _, ledger = _make_ledger(tmp_path, initial_capital=10000.0)
    ledger.initialize_default_slots(18)

    t1 = ledger.reserve_capital("agent_01", "1000", idempotency_key="idem_res_1")
    t1_repeat = ledger.reserve_capital("agent_01", "1000", idempotency_key="idem_res_1")
    assert t1.transfer_id == t1_repeat.transfer_id
    assert ledger.get_master_pool().free_capital == Decimal("9000.00000000")

    # Reusing idempotency key with different amount must fail
    with pytest.raises(DuplicateIdempotencyError):
        ledger.reserve_capital("agent_01", "2000", idempotency_key="idem_res_1")


def test_transfer_lifecycle_and_ownership_approval_guards(tmp_path: Path):
    _, ledger = _make_ledger(tmp_path, initial_capital=10000.0)
    ledger.initialize_default_slots(18)

    trf = ledger.reserve_capital("agent_01", "1200", idempotency_key="life_1")
    assert trf.state == TransferLifecycleState.RESERVED

    # Cannot skip directly to USER_APPROVED
    with pytest.raises(InvalidTransferTransitionError):
        ledger.approve_transfer(trf.transfer_id, approved_by="operator", ownership_verified=True)

    trf = ledger.propose_transfer(trf.transfer_id)
    assert trf.state == TransferLifecycleState.TRANSFER_PROPOSED
    assert "MANUAL PAPER INSTRUCTION" in (trf.manual_instruction or "")

    # Ownership verification is required
    with pytest.raises(TreasuryError, match="ownership verification"):
        ledger.approve_transfer(trf.transfer_id, approved_by="operator", ownership_verified=False)

    trf = ledger.approve_transfer(trf.transfer_id, approved_by="operator_alice", ownership_verified=True)
    assert trf.state == TransferLifecycleState.USER_APPROVED

    trf = ledger.mark_external_transfer_pending(trf.transfer_id)
    assert trf.state == TransferLifecycleState.EXTERNAL_TRANSFER_PENDING

    # Empty readonly evidence rejected
    with pytest.raises(TreasuryError, match="evidence"):
        ledger.confirm_from_readonly_evidence(trf.transfer_id, readonly_evidence_ref="")

    trf = ledger.confirm_from_readonly_evidence(
        trf.transfer_id, readonly_evidence_ref="ro_tx_9988", fee_amount="1.5"
    )
    assert trf.state == TransferLifecycleState.CONFIRMED_FROM_EXCHANGE_READONLY_EVIDENCE

    trf = ledger.release_to_agent_available(trf.transfer_id)
    assert trf.state == TransferLifecycleState.AGENT_AVAILABLE
    assert ledger.get_agent_slot("agent_01").available_capital == Decimal("1200.00000000")

    trf = ledger.allocate_to_paper_trading(trf.transfer_id)
    assert trf.state == TransferLifecycleState.PAPER_TRADING
    assert ledger.get_agent_slot("agent_01").trading_capital == Decimal("1200.00000000")

    # Cancellation of a reserved transfer returns capital to master_pool:free
    trf2 = ledger.reserve_capital("agent_02", "800", idempotency_key="life_cancel")
    assert ledger.get_master_pool().free_capital == Decimal("8000.00000000")
    cancelled = ledger.cancel_or_reject_transfer(trf2.transfer_id, reject=False, reason="test cancel")
    assert cancelled.state == TransferLifecycleState.CANCELLED
    assert ledger.get_master_pool().free_capital == Decimal("8800.00000000")
    assert ledger.reconcile()["balanced"] is True


def test_usd_usdt_fx_distinction_and_pnl_attribution(tmp_path: Path):
    _, ledger = _make_ledger(tmp_path, initial_capital=10000.0)
    ledger.initialize_default_slots(18)

    fx = ledger.record_fx_rate("USDT", "USD", "0.9980", source="test_fx_feed")
    usd_val, used_rate, _ = ledger.value_in_usd("1000", "USDT", fx)
    assert used_rate == Decimal("0.99800000")
    assert usd_val == Decimal("998.00000000")

    usd_exact, usd_rate, _ = ledger.value_in_usd("1000", "USD")
    assert usd_rate == Decimal("1.00000000")
    assert usd_exact == Decimal("1000.00000000")

    # External deposit is NOT trading profit
    ledger.record_external_deposit("2000", idempotency_key="dep_1", asset="USDT")
    assert ledger.get_master_pool().total_capital == Decimal("12000.00000000")

    # Record trading PnL, fees, funding, slippage
    ledger.record_attribution(
        idempotency_key="pnl_1",
        agent_id="agent_01",
        exchange="binance",
        account_id="binance:paper_sub_01",
        strategy="trend_following",
        symbol="BTC/USDT",
        asset="USDT",
        category=AttributionCategory.TRADE_REALIZED_PNL,
        amount="500",
        fx_snapshot=fx,
    )
    ledger.record_attribution(
        idempotency_key="fee_1",
        agent_id="agent_01",
        exchange="binance",
        account_id="binance:paper_sub_01",
        strategy="trend_following",
        symbol="BTC/USDT",
        asset="USDT",
        category=AttributionCategory.TRADING_FEE,
        amount="-10",
        fx_snapshot=fx,
    )
    ledger.record_attribution(
        idempotency_key="fund_1",
        agent_id="agent_01",
        exchange="binance",
        account_id="binance:paper_sub_01",
        strategy="trend_following",
        symbol="BTC/USDT",
        asset="USDT",
        category=AttributionCategory.FUNDING,
        amount="-5",
        fx_snapshot=fx,
    )
    ledger.record_attribution(
        idempotency_key="slip_1",
        agent_id="agent_01",
        exchange="binance",
        account_id="binance:paper_sub_01",
        strategy="trend_following",
        symbol="BTC/USDT",
        asset="USDT",
        category=AttributionCategory.SLIPPAGE,
        amount="-5",
        fx_snapshot=fx,
    )

    by_agent = ledger.get_attribution_summary("agent_id")
    # Net trading profit = (500 - 10 - 5 - 5) * 0.998 = 480 * 0.998 = 479.04
    assert by_agent["agent_01"]["net_trading_profit_usd"] == "479.04"
    # External deposit is tracked separately and has 0.00 net trading profit
    assert by_agent["treasury_master"]["external_deposits_usd"] == "1996.00"
    assert by_agent["treasury_master"]["net_trading_profit_usd"] == "0.00"

    by_symbol = ledger.get_attribution_summary("symbol")
    assert by_symbol["BTC/USDT"]["realized_pnl_usd"] == "499.00"


def test_agent_allocator_risk_caps_cooldowns_and_global_guardian_supremacy(tmp_path: Path):
    _, ledger = _make_ledger(tmp_path, initial_capital=10000.0)
    ledger.initialize_default_slots(18)
    ledger.configure_agent_slot(
        AgentSlotConfig(
            agent_id="agent_01",
            slot_index=1,
            name="Agent 01",
            strategy="trend_following",
            exchange=ExchangeId.BINANCE,
            account_id="binance:paper_sub_01",
            capital_budget=Decimal("1000"),
            status=AgentSlotStatus.ACTIVE,
        )
    )
    risk = GlobalRiskGuardian(RiskPolicyConfig())
    allocator = AgentStrategyAllocator(ledger, risk)

    # First rebalance applies and respects concentration cap (25% of 10000 = 2500)
    props = allocator.propose_rebalance(apply=True)
    p1 = next(p for p in props if p.agent_id == "agent_01")
    assert p1.approved is True
    assert p1.proposed_budget <= Decimal("2500.00")

    # Immediate second rebalance is blocked by cooldown if budget changed
    props2 = allocator.propose_rebalance(apply=True)
    p1_2 = next(p for p in props2 if p.agent_id == "agent_01")
    if p1.proposed_budget != p1.current_budget:
        assert p1_2.approved is False
        assert "cooldown" in (p1_2.blocked_reason or "")

    # GlobalRiskGuardian emergency stop cannot be overridden by any agent
    risk.trigger_emergency_stop("manual_halt")
    props3 = allocator.propose_rebalance(apply=True)
    assert all(not p.approved for p in props3)
    assert all("global_risk_guardian_emergency_stop" in (p.blocked_reason or "") for p in props3)


def test_crash_recovery_preserves_treasury_and_existing_paper_ledger(tmp_path: Path):
    db_path = tmp_path / "recover.sqlite3"
    db1 = PaperDatabase(db_path)
    db1.ensure_account("okx_spot_alpha", "okx", 3000.0)

    cfg = UnifiedTreasuryConfig(enabled=True, initial_master_capital=10000.0)
    ledger1 = UnifiedTreasuryLedger(db1, cfg)
    ledger1.initialize_default_slots(18)
    ledger1.simulate_paper_transfer_e2e("agent_01", "1500", "crash_trf_1", approved_by="ops")
    db1.close()

    # Re-open after simulated crash
    db2 = PaperDatabase(db_path)
    acct = db2.get_account("okx_spot_alpha")
    assert acct is not None
    assert acct["cash_usd"] == 3000.0

    ledger2 = UnifiedTreasuryLedger(db2, cfg)
    assert len(ledger2.list_agent_slots()) == 18
    assert ledger2.get_agent_slot("agent_01").trading_capital == Decimal("1500.00000000")
    assert ledger2.get_master_pool().free_capital == Decimal("8500.00000000")
    assert ledger2.reconcile()["balanced"] is True
    db2.close()


def test_dashboard_auth_feature_flag_and_no_live_invariants(tmp_path: Path):
    assert LIVE_EXECUTION_PERMANENTLY_DISABLED is True
    with pytest.raises(ValueError):
        UnifiedTreasuryConfig(paper_only=False)

    # Read-only exchange enforcement blocks withdraw and transfer
    class DummyEx:
        def withdraw(self): ...
        def transfer(self): ...
        def create_order(self): ...

    ex = enforce_read_only_exchange(DummyEx())
    with pytest.raises(LiveExecutionBlockedError):
        ex.withdraw()
    with pytest.raises(LiveExecutionBlockedError):
        ex.transfer()

    # Default feature flag is OFF -> /api/treasury/overview returns 503
    cfg_off = EngineConfig(base_dir=tmp_path, db_path=tmp_path / "off.sqlite3", log_dir=tmp_path / "logs")
    assert cfg_off.unified_treasury.enabled is False
    svc_off = TradingEngineService(
        env=LoadedEnvironment(config=cfg_off, credentials={"okx": ExchangeCredentials("okx")}),
        enable_telegram=False,
    )
    app_off = create_dashboard_app(svc_off, auth_config=DashboardAuthConfig("admin", "pass1234"))
    client_off = TestClient(app_off)
    assert client_off.get("/api/treasury/overview", auth=("admin", "pass1234")).status_code == 503

    # Feature flag ON -> unauthenticated blocked (401), authenticated succeeds
    cfg_on = EngineConfig(
        base_dir=tmp_path,
        db_path=tmp_path / "on.sqlite3",
        log_dir=tmp_path / "logs",
        unified_treasury=UnifiedTreasuryConfig(enabled=True),
    )
    svc_on = TradingEngineService(
        env=LoadedEnvironment(config=cfg_on, credentials={"okx": ExchangeCredentials("okx")}),
        enable_telegram=False,
    )
    app_on = create_dashboard_app(svc_on, auth_config=DashboardAuthConfig("admin", "pass1234"))
    client_on = TestClient(app_on)

    assert client_on.get("/api/treasury/overview").status_code == 401
    assert client_on.post("/api/treasury/paper/init-slots").status_code == 401

    init_res = client_on.post("/api/treasury/paper/init-slots", auth=("admin", "pass1234"))
    assert init_res.status_code == 200
    assert init_res.json()["configured_slots"] == 18

    sim_res = client_on.post(
        "/api/treasury/paper/simulate-transfer",
        json={"agent_id": "agent_01", "amount": "500", "idempotency_key": "api_sim_1"},
        auth=("admin", "pass1234"),
    )
    assert sim_res.status_code == 200
    assert sim_res.json()["transfer"]["state"] == "paper_trading"
    assert sim_res.json()["transfer"]["is_paper"] is True

    ov = client_on.get("/api/treasury/overview", auth=("admin", "pass1234"))
    assert ov.status_code == 200
    assert ov.json()["reconciliation"]["balanced"] is True
    assert ov.json()["reconciliation"]["paper_separated_from_real"] is True
