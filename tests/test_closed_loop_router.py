from decimal import Decimal
import pytest

from app.core.config import RiskPolicyConfig
from app.risk.guardian import GlobalRiskGuardian
from app.treasury.closed_loop_router import (
    ClosedLoopAllowlistError,
    ClosedLoopAssetRouter,
    ClosedLoopConcentrationError,
    ClosedLoopFeeLimitError,
    ClosedLoopRiskBlockedError,
    ClosedLoopTransferState,
    ClosedLoopVenue,
)
from app.treasury.models import DuplicateIdempotencyError, LiveTransferBlockedError


def test_closed_loop_multihop_conservation_and_idempotency():
    router = ClosedLoopAssetRouter()
    r1 = router.simulate_transfer(
        source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
        destination_venue=ClosedLoopVenue.CEX_BINANCE,
        amount_usd="1000.00",
        idempotency_key="hop-1",
        reason="allocate_binance",
    )
    assert r1.state == ClosedLoopTransferState.SETTLED
    assert r1.fee_usd == Decimal("0.00")

    # Idempotent replay returns exact same record
    r1_dup = router.simulate_transfer(
        source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
        destination_venue=ClosedLoopVenue.CEX_BINANCE,
        amount_usd="1000.00",
        idempotency_key="hop-1",
        reason="allocate_binance",
    )
    assert r1_dup.transfer_id == r1.transfer_id

    # Conflicting idempotency key raises
    with pytest.raises(DuplicateIdempotencyError):
        router.simulate_transfer(
            source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
            destination_venue=ClosedLoopVenue.CEX_BINANCE,
            amount_usd="500.00",
            idempotency_key="hop-1",
        )

    # Move to Solana DEX and sweep back to Hub
    r2 = router.simulate_transfer(
        source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
        destination_venue=ClosedLoopVenue.DEX_SOLANA,
        amount_usd="500.00",
        idempotency_key="hop-2",
        reason="allocate_solana",
    )
    assert r2.fee_usd == Decimal("0.25")
    inv = router.check_equity_invariant()
    assert inv["conservation_ok"] is True
    assert inv["drift_usd"] == "0.00"


def test_closed_loop_rejects_external_address_and_live_mode():
    router = ClosedLoopAssetRouter()
    with pytest.raises(ClosedLoopAllowlistError):
        router.simulate_transfer(
            source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
            destination_venue=ClosedLoopVenue.DEX_SOLANA,
            amount_usd="100.00",
            idempotency_key="bad-addr",
            destination_address="external://attacker-wallet",
        )

    with pytest.raises(ClosedLoopAllowlistError):
        router.simulate_transfer(
            source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
            destination_venue="cex_gateio",
            amount_usd="100.00",
            idempotency_key="bad-venue",
        )

    with pytest.raises(LiveTransferBlockedError):
        router.simulate_transfer(
            source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
            destination_venue=ClosedLoopVenue.CEX_OKX,
            amount_usd="100.00",
            idempotency_key="live-block",
            live_mode=True,
        )


def test_risk_guardian_veto_fee_limit_and_concentration_cap():
    guardian = GlobalRiskGuardian(RiskPolicyConfig())
    router = ClosedLoopAssetRouter(
        risk_guardian=guardian,
        max_transfer_fee_pct=Decimal("0.0002"),
        default_fee_bps=Decimal("10"),
        max_venue_concentration_pct=Decimal("0.35"),
    )

    # Fee limit check (10 bps > 2 bps)
    with pytest.raises(ClosedLoopFeeLimitError):
        router.simulate_transfer(
            source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
            destination_venue=ClosedLoopVenue.CEX_OKX,
            amount_usd="100.00",
            idempotency_key="fee-too-high",
        )

    # Concentration cap check (0 bps on Hub -> CEX_BINANCE, but > 35% of 27500)
    with pytest.raises(ClosedLoopConcentrationError):
        router.simulate_transfer(
            source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
            destination_venue=ClosedLoopVenue.CEX_BINANCE,
            amount_usd="7000.00",
            idempotency_key="conc-cap",
        )

    # Risk guardian veto
    guardian.trigger_emergency_stop("manual_test_halt")
    with pytest.raises(ClosedLoopRiskBlockedError):
        router.simulate_transfer(
            source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
            destination_venue=ClosedLoopVenue.CEX_BINANCE,
            amount_usd="100.00",
            idempotency_key="risk-halt",
        )


def test_auto_sweep_idle_capital_with_hysteresis():
    router = ClosedLoopAssetRouter()
    # CEX_OKX starts at 5500; target 5480 -> diff 20 <= hysteresis 50 -> no sweep
    assert router.auto_sweep_idle_capital(ClosedLoopVenue.CEX_OKX, target_usd="5480.00") is None

    # Target 4500 -> diff 1000 > 50 -> sweeps 1000 back to Hub
    swept = router.auto_sweep_idle_capital(ClosedLoopVenue.CEX_OKX, target_usd="4500.00")
    assert swept is not None
    assert swept.destination_venue == ClosedLoopVenue.HUB_BINANCE_MASTER
    assert swept.amount_usd == Decimal("1000.00")
    assert router.check_equity_invariant()["conservation_ok"] is True


def test_closed_loop_sqlite_persistence_survives_restart(tmp_path):
    db_file = tmp_path / "closed_loop.sqlite3"
    r1 = ClosedLoopAssetRouter(state_path=db_file)
    rec = r1.simulate_transfer(
        source_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
        destination_venue=ClosedLoopVenue.CEX_BINANCE,
        amount_usd="500.00",
        idempotency_key="persist-1",
    )

    # Reload from SQLite
    r2 = ClosedLoopAssetRouter(state_path=db_file)
    s2 = r2.summary()
    assert s2["balances_usd"]["hub_binance_master"] == "11500.00"
    assert s2["balances_usd"]["cex_binance"] == "4500.00"
    assert s2["transfer_count"] == 1
    assert s2["transfers"][0]["transfer_id"] == rec.transfer_id
    assert s2["invariant"]["conservation_ok"] is True


def test_router_reconciles_and_rejects_snapshot_drift(tmp_path):
    import json
    import sqlite3
    import pytest
    from app.treasury.models import TreasuryInvariantError
    path = tmp_path / 'router.sqlite3'
    router = ClosedLoopAssetRouter(state_path=path)
    router.simulate_transfer('hub_binance_master', 'cex_binance', '100', 'journal')
    assert router.reconcile()['balanced']
    with sqlite3.connect(path) as conn:
        data = json.loads(conn.execute('SELECT payload_json FROM closed_loop_state').fetchone()[0])
        data['balances']['cex_binance'] = str(Decimal(data['balances']['cex_binance']) + 1)
        data['balances']['hub_binance_master'] = str(Decimal(data['balances']['hub_binance_master']) - 1)
        conn.execute('UPDATE closed_loop_state SET payload_json = ?', (json.dumps(data),))
    with pytest.raises(TreasuryInvariantError, match='reconcile'):
        ClosedLoopAssetRouter(state_path=path)


def test_router_idempotency_survives_more_than_200_records_and_two_writers(tmp_path):
    path = tmp_path / 'router.sqlite3'
    first = ClosedLoopAssetRouter(state_path=path, default_fee_bps=Decimal('0'))
    second = ClosedLoopAssetRouter(state_path=path)
    original = first.simulate_transfer('hub_binance_master', 'cex_binance', '1', 'oldest')
    for i in range(201):
        second.simulate_transfer('cex_binance', 'hub_binance_master', '1', f'back-{i}')
    restarted = ClosedLoopAssetRouter(state_path=path)
    assert restarted.simulate_transfer('hub_binance_master', 'cex_binance', '1', 'oldest') == original
    assert restarted.summary()['transfer_count'] == 202
    assert restarted.reconcile()['balanced']
