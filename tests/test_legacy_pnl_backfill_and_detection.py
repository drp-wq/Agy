"""Tests for legacy PnL backfill, missing-fill/closure detection, and reversal integrity."""
from __future__ import annotations

from decimal import Decimal
import pytest

from app.core.config import EngineConfig, UnifiedTreasuryConfig
from app.core.models import (
    Direction,
    ExchangeId,
    MarketType,
    OrderStatus,
    PaperFill,
    PaperOrder,
    Side,
)
from app.persistence.database import PaperDatabase
from app.service import TradingEngineService
from app.treasury.legacy_pnl_backfill import (
    LegacyReplayMismatchError,
    backfill_legacy_paper_pnl,
    replay_paper_orders,
)
from app.treasury.reconciliation_audit import (
    audit_paper_pnl_coverage,
    audit_per_bot_paper_pnl,
    detect_missing_paper_order_evidence,
)


def _save_filled_order_with_fill(
    svc: TradingEngineService,
    order: PaperOrder,
    *,
    write_fill: bool = True,
) -> None:
    with svc.db.transaction() as conn:
        svc.db.save_order(order, conn=conn)
        if write_fill:
            svc.db.save_fill(
                PaperFill(
                    order_id=order.order_id,
                    bot_id=order.bot_id,
                    exchange=order.exchange,
                    symbol=order.symbol,
                    market_type=order.market_type,
                    side=order.side,
                    qty=order.filled_qty,
                    price=order.avg_fill_price,
                    fee_usd=order.fee_usd,
                    slippage_bps=order.slippage_bps,
                    is_paper=True,
                    timestamp=order.created_at,
                ),
                conn=conn,
            )


def test_detect_missing_fill_and_sltp_closure_evidence(tmp_path):
    cfg = EngineConfig(
        db_path=tmp_path / "detect.sqlite3",
        log_dir=tmp_path / "logs",
        unified_treasury=UnifiedTreasuryConfig(enabled=True),
    )
    svc = TradingEngineService(config=cfg, enable_telegram=False)
    try:
        svc.db.ensure_account("bot_a", "binance", 1000.0)
        open_ord = PaperOrder(
            order_id="ord_open_1",
            client_order_id="cid_open_1",
            bot_id="bot_a",
            signal_id="sig_1",
            exchange=ExchangeId.BINANCE,
            symbol="BTC/USDT",
            market_type=MarketType.SPOT,
            side=Side.BUY,
            requested_qty=0.1,
            filled_qty=0.1,
            avg_fill_price=100.0,
            fee_usd=0.01,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        _save_filled_order_with_fill(svc, open_ord, write_fill=True)
        svc.paper_engine._apply_fill_to_position_and_account(open_ord, 100.0)

        # Simulate legacy pre-v3 sl_tp_exit bug: order saved & account mutated without fill or closure row
        exit_ord = PaperOrder(
            order_id="ord_sltp_1",
            client_order_id="exit_1",
            bot_id="bot_a",
            signal_id="sl_tp_exit",
            exchange=ExchangeId.BINANCE,
            symbol="BTC/USDT",
            market_type=MarketType.SPOT,
            side=Side.SELL,
            requested_qty=0.1,
            filled_qty=0.1,
            avg_fill_price=90.0,
            fee_usd=0.009,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        with svc.db.transaction() as conn:
            svc.db.save_order(exit_ord, conn=conn)
            svc.db.update_account_cash(
                "bot_a",
                cash_delta=-1.009,
                realized_pnl_delta=-1.009,
                reserved_margin_delta=-10.0,
                conn=conn,
            )
            conn.execute(
                "DELETE FROM paper_positions WHERE bot_id = 'bot_a'"
            )

        svc.sync_paper_fill_fees_to_treasury()
        detected = detect_missing_paper_order_evidence(svc.db)
        assert detected["filled_orders"] == 2
        assert detected["missing_fills_count"] == 1
        assert detected["missing_fill_order_ids"] == ["ord_sltp_1"]
        assert detected["missing_sltp_closures_count"] == 1
        assert detected["missing_sltp_closure_order_ids"] == ["ord_sltp_1"]
        assert detected["complete"] is False

        audit = svc.paper_pnl_audit()
        assert audit["orders_missing_fills"] == 1
        assert audit["sltp_orders_missing_closures"] == 1
        assert audit["orders_evidence_complete"] is False
        assert audit["recorded_evidence_coverage_ok"] is False
        assert Decimal(audit["unexplained_usd"]) == Decimal("-1.009")
    finally:
        svc.db.close()


def test_deterministic_idempotent_backfill_and_ledger_invariants(tmp_path):
    db_path = tmp_path / "legacy_backfill.sqlite3"
    cfg = EngineConfig(
        db_path=db_path,
        log_dir=tmp_path / "logs",
        unified_treasury=UnifiedTreasuryConfig(enabled=True, initial_master_capital=10000.0),
    )
    svc = TradingEngineService(config=cfg, enable_telegram=False)
    try:
        svc.treasury.initialize_default_slots(18)
        svc.db.ensure_account("bybit_perp_funding", "bybit", 2000.0)
        svc.db.ensure_account("okx_spot_alpha", "okx", 3000.0)

        # Bot 1: Open SHORT 2.0 @ 100, add SHORT 1.0 @ 106 -> avg entry 102.0, then legacy sl_tp_exit BUY 3.0 @ 105 -> gross -9.0
        o1 = PaperOrder(
            order_id="o_byb_1",
            client_order_id="c_byb_1",
            bot_id="bybit_perp_funding",
            signal_id="sig_1",
            exchange=ExchangeId.BYBIT,
            symbol="SOL/USDT",
            market_type=MarketType.PERP,
            side=Side.SELL,
            requested_qty=2.0,
            filled_qty=2.0,
            avg_fill_price=100.0,
            leverage=2.0,
            fee_usd=0.11,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        o2 = PaperOrder(
            order_id="o_byb_2",
            client_order_id="c_byb_2",
            bot_id="bybit_perp_funding",
            signal_id="sig_2",
            exchange=ExchangeId.BYBIT,
            symbol="SOL/USDT",
            market_type=MarketType.PERP,
            side=Side.SELL,
            requested_qty=1.0,
            filled_qty=1.0,
            avg_fill_price=106.0,
            leverage=2.0,
            fee_usd=0.0583,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        for o in (o1, o2):
            _save_filled_order_with_fill(svc, o, write_fill=True)
            svc.paper_engine._apply_fill_to_position_and_account(o, o.avg_fill_price)

        # Legacy SL/TP exit without paper_fills or paper_realized_closures
        o3_exit = PaperOrder(
            order_id="o_byb_exit",
            client_order_id="exit_byb_1",
            bot_id="bybit_perp_funding",
            signal_id="sl_tp_exit",
            exchange=ExchangeId.BYBIT,
            symbol="SOL/USDT",
            market_type=MarketType.PERP,
            side=Side.BUY,
            requested_qty=3.0,
            filled_qty=3.0,
            avg_fill_price=105.0,
            leverage=2.0,
            fee_usd=0.17325,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        with svc.db.transaction() as conn:
            svc.db.save_order(o3_exit, conn=conn)
            svc.db.update_account_cash(
                "bybit_perp_funding",
                cash_delta=-9.0 - 0.17325,
                realized_pnl_delta=-9.0 - 0.17325,
                reserved_margin_delta=-153.0,
                conn=conn,
            )
            conn.execute("DELETE FROM paper_positions WHERE bot_id = 'bybit_perp_funding'")

        # Bot 2: Open LONG 0.5 @ 2000 (keeps open position, verifies open position replay)
        o4 = PaperOrder(
            order_id="o_okx_1",
            client_order_id="c_okx_1",
            bot_id="okx_spot_alpha",
            signal_id="sig_3",
            exchange=ExchangeId.OKX,
            symbol="ETH/USDT",
            market_type=MarketType.SPOT,
            side=Side.BUY,
            requested_qty=0.5,
            filled_qty=0.5,
            avg_fill_price=2000.0,
            leverage=1.0,
            fee_usd=1.0,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        _save_filled_order_with_fill(svc, o4, write_fill=True)
        svc.paper_engine._apply_fill_to_position_and_account(o4, 2000.0)

        svc.sync_paper_fill_fees_to_treasury()
        journal_count_before = svc.db._conn.execute(
            "SELECT COUNT(*) FROM treasury_journal_entries"
        ).fetchone()[0]
        pool_before = svc.db._conn.execute(
            "SELECT total_capital, free_capital, reserved_capital FROM treasury_master_pool"
        ).fetchone()
        accts_before = svc.db.list_accounts()
        svc.db.close()

        # Run 1
        res1 = backfill_legacy_paper_pnl(db_path, actor="test_backfill")
        assert res1["missing_fills_found"] == 1
        assert res1["missing_closures_found"] == 1
        assert res1["inserted_fills"] == 1
        assert res1["inserted_closures"] == 1
        assert Decimal(res1["missing_fees_usd"]) == Decimal("0.17325")
        assert Decimal(res1["missing_gross_usd"]) == Decimal("-9.0")
        assert res1["treasury_attribution_sync"] == {
            "inserted_fee_attributions": 1,
            "inserted_closure_attributions": 1,
        }
        assert res1["invariants_unchanged"] is True
        assert res1["integrity"]["ok"] is True
        assert res1["after_audit"]["recorded_evidence_coverage_ok"] is True
        assert res1["after_audit"]["orders_evidence_complete"] is True
        assert abs(Decimal(res1["after_audit"]["unexplained_usd"])) < Decimal("1e-9")

        # Run 2 (idempotency check)
        res2 = backfill_legacy_paper_pnl(db_path, actor="test_backfill")
        assert res2["missing_fills_found"] == 0
        assert res2["missing_closures_found"] == 0
        assert res2["inserted_fills"] == 0
        assert res2["inserted_closures"] == 0
        assert res2["treasury_attribution_sync"] == {
            "inserted_fee_attributions": 0,
            "inserted_closure_attributions": 0,
        }
        assert res2["invariants_unchanged"] is True

        db = PaperDatabase(db_path)
        try:
            assert db.list_accounts() == accts_before
            journal_count_after = db._conn.execute(
                "SELECT COUNT(*) FROM treasury_journal_entries"
            ).fetchone()[0]
            assert journal_count_after == journal_count_before
            pool_after = db._conn.execute(
                "SELECT total_capital, free_capital, reserved_capital FROM treasury_master_pool"
            ).fetchone()
            assert tuple(pool_after) == tuple(pool_before)
            audit_rows = db._conn.execute(
                "SELECT * FROM audit_logs WHERE event_type = 'HISTORICAL_SLTP_EVIDENCE_BACKFILLED'"
            ).fetchall()
            assert len(audit_rows) == 1
        finally:
            db.close()
    finally:
        pass


def test_backfill_aborts_on_replay_mismatch(tmp_path):
    db_path = tmp_path / "mismatch.sqlite3"
    db = PaperDatabase(db_path)
    db.ensure_account("bot_bad", "binance", 1000.0)
    db.save_order(
        PaperOrder(
            order_id="ord_m1",
            client_order_id="cid_m1",
            bot_id="bot_bad",
            signal_id="sl_tp_exit",
            exchange=ExchangeId.BINANCE,
            symbol="BTC/USDT",
            market_type=MarketType.SPOT,
            side=Side.BUY,
            requested_qty=1.0,
            filled_qty=1.0,
            avg_fill_price=100.0,
            fee_usd=0.1,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
    )
    # Intentionally leave cash_usd untouched (mismatch against replay)
    db.close()

    with pytest.raises(LegacyReplayMismatchError):
        backfill_legacy_paper_pnl(db_path)

    db2 = PaperDatabase(db_path)
    try:
        assert db2._conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0] == 0
    finally:
        db2.close()


def test_reversal_order_and_replay_detection_compatibility(tmp_path):
    cfg = EngineConfig(
        db_path=tmp_path / "rev_detect.sqlite3",
        log_dir=tmp_path / "logs",
        unified_treasury=UnifiedTreasuryConfig(enabled=True),
    )
    svc = TradingEngineService(config=cfg, enable_telegram=False)
    try:
        svc.db.ensure_account("binance_perp_paper", "binance", 3000.0)
        open_ord = PaperOrder(
            order_id="rev_o1",
            client_order_id="rev_c1",
            bot_id="binance_perp_paper",
            signal_id="s1",
            exchange=ExchangeId.BINANCE,
            symbol="ETH/USDT",
            market_type=MarketType.PERP,
            side=Side.BUY,
            requested_qty=1.0,
            filled_qty=1.0,
            avg_fill_price=2000.0,
            leverage=2.0,
            fee_usd=1.0,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        rev_ord = PaperOrder(
            order_id="rev_o2",
            client_order_id="rev_c2",
            bot_id="binance_perp_paper",
            signal_id="s2",
            exchange=ExchangeId.BINANCE,
            symbol="ETH/USDT",
            market_type=MarketType.PERP,
            side=Side.SELL,
            requested_qty=1.5,
            filled_qty=1.5,
            avg_fill_price=2100.0,
            leverage=2.0,
            fee_usd=1.575,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        for o in (open_ord, rev_ord):
            _save_filled_order_with_fill(svc, o, write_fill=True)
            svc.paper_engine._apply_fill_to_position_and_account(o, o.avg_fill_price)

        svc.sync_paper_fill_fees_to_treasury()
        audit = svc.paper_pnl_audit()
        assert audit["recorded_evidence_coverage_ok"] is True
        assert audit["orders_missing_fills"] == 0
        assert audit["sltp_orders_missing_closures"] == 0
        assert abs(Decimal(audit["unexplained_usd"])) < Decimal("1e-9")

        replay = replay_paper_orders(svc.db._conn, flip_reversal_residual=True)
        assert replay.replayed_orders == 2
        assert replay.missing_fills == []
        assert replay.missing_closures == []
        assert abs(replay.per_bot_replayed["binance_perp_paper"]["reserved_margin_usd"] - 525.0) < 1e-6
    finally:
        svc.db.close()


def test_six_accounts_multi_exchange_per_bot_backfill_and_float_ulp_zero(tmp_path):
    """Verify all 6 paper accounts across Binance/Bybit/OKX reconcile to exact '0' unexplained PnL."""
    db_path = tmp_path / "six_bots.sqlite3"
    cfg = EngineConfig(
        db_path=db_path,
        log_dir=tmp_path / "logs",
        unified_treasury=UnifiedTreasuryConfig(enabled=True, initial_master_capital=10000.0),
    )
    svc = TradingEngineService(config=cfg, enable_telegram=False)
    bots = [
        ("binance_perp_paper", ExchangeId.BINANCE, MarketType.PERP, 1500.0, 2.0),
        ("binance_spot_paper", ExchangeId.BINANCE, MarketType.SPOT, 2500.0, 1.0),
        ("bybit_perp_funding", ExchangeId.BYBIT, MarketType.PERP, 2000.0, 2.0),
        ("bybit_spot_swing", ExchangeId.BYBIT, MarketType.SPOT, 2500.0, 1.0),
        ("okx_perp_momentum", ExchangeId.OKX, MarketType.PERP, 2500.0, 2.0),
        ("okx_spot_alpha", ExchangeId.OKX, MarketType.SPOT, 3000.0, 1.0),
    ]
    try:
        for idx, (bot_id, ex, mkt, eq, lev) in enumerate(bots, start=1):
            svc.db.ensure_account(bot_id, ex.value, eq)
            open_ord = PaperOrder(
                order_id=f"open_{bot_id}",
                client_order_id=f"cid_open_{bot_id}",
                bot_id=bot_id,
                signal_id=f"sig_{idx}",
                exchange=ex,
                symbol="SOL/USDT",
                market_type=mkt,
                side=Side.BUY,
                requested_qty=1.0,
                filled_qty=1.0,
                avg_fill_price=100.0,
                leverage=lev,
                fee_usd=0.1,
                status=OrderStatus.FILLED,
                is_paper=True,
            )
            _save_filled_order_with_fill(svc, open_ord, write_fill=True)
            svc.paper_engine._apply_fill_to_position_and_account(open_ord, 100.0)

            # Simulate legacy sl_tp_exit missing both paper_fills and paper_realized_closures
            exit_px = 95.0 - idx
            gross = round((exit_px - 100.0) * 1.0, 6)
            fee = 0.05
            exit_ord = PaperOrder(
                order_id=f"exit_{bot_id}",
                client_order_id=f"cid_exit_{bot_id}",
                bot_id=bot_id,
                signal_id="sl_tp_exit",
                exchange=ex,
                symbol="SOL/USDT",
                market_type=mkt,
                side=Side.SELL,
                requested_qty=1.0,
                filled_qty=1.0,
                avg_fill_price=exit_px,
                leverage=lev,
                fee_usd=fee,
                status=OrderStatus.FILLED,
                is_paper=True,
            )
            with svc.db.transaction() as conn:
                svc.db.save_order(exit_ord, conn=conn)
                # Inject tiny IEEE-754 ULP noise (~1e-16) to verify _real_to_decimal handling
                ulp_noise = 3e-16 if idx % 2 else -4e-16
                svc.db.update_account_cash(
                    bot_id,
                    cash_delta=gross - fee + ulp_noise,
                    realized_pnl_delta=gross - fee + ulp_noise,
                    reserved_margin_delta=-(100.0 / lev),
                    conn=conn,
                )
                conn.execute("DELETE FROM paper_positions WHERE bot_id = ?", (bot_id,))

        svc.sync_paper_fill_fees_to_treasury()
        before_bots = audit_per_bot_paper_pnl(svc.db)
        assert len(before_bots) == 6
        assert all(not b["reconciled"] for b in before_bots)
        svc.db.close()

        res = backfill_legacy_paper_pnl(db_path, actor="test_six_bots")
        assert res["inserted_fills"] == 6
        assert res["inserted_closures"] == 6
        assert res["after_audit"]["unexplained_usd"] == "0"
        assert res["after_audit"]["recorded_evidence_coverage_ok"] is True
        assert res["after_audit"]["orders_evidence_complete"] is True
        assert len(res["per_bot_audit"]) == 6
        assert all(b["reconciled"] and b["unexplained_usd"] == "0" for b in res["per_bot_audit"])
    finally:
        pass


def test_cli_backfill_backup_and_prod_guard(tmp_path, monkeypatch):
    import json
    import subprocess
    import sys

    db_path = tmp_path / "cli_test.sqlite3"
    backup_path = tmp_path / "cli_backup.sqlite3"
    cfg = EngineConfig(
        db_path=db_path,
        log_dir=tmp_path / "logs",
        unified_treasury=UnifiedTreasuryConfig(enabled=True),
    )
    svc = TradingEngineService(config=cfg, enable_telegram=False)
    svc.db.ensure_account("binance_spot_paper", "binance", 2500.0)
    o1 = PaperOrder(
        order_id="cli_o1",
        client_order_id="cli_c1",
        bot_id="binance_spot_paper",
        signal_id="sig_cli",
        exchange=ExchangeId.BINANCE,
        symbol="BTC/USDT",
        market_type=MarketType.SPOT,
        side=Side.BUY,
        requested_qty=0.1,
        filled_qty=0.1,
        avg_fill_price=1000.0,
        fee_usd=0.1,
        status=OrderStatus.FILLED,
        is_paper=True,
    )
    _save_filled_order_with_fill(svc, o1, write_fill=True)
    svc.paper_engine._apply_fill_to_position_and_account(o1, 1000.0)
    o2 = PaperOrder(
        order_id="cli_o2",
        client_order_id="cli_c2",
        bot_id="binance_spot_paper",
        signal_id="sl_tp_exit",
        exchange=ExchangeId.BINANCE,
        symbol="BTC/USDT",
        market_type=MarketType.SPOT,
        side=Side.SELL,
        requested_qty=0.1,
        filled_qty=0.1,
        avg_fill_price=950.0,
        fee_usd=0.095,
        status=OrderStatus.FILLED,
        is_paper=True,
    )
    with svc.db.transaction() as conn:
        svc.db.save_order(o2, conn=conn)
        svc.db.update_account_cash(
            "binance_spot_paper",
            cash_delta=-5.095,
            realized_pnl_delta=-5.095,
            reserved_margin_delta=-100.0,
            conn=conn,
        )
        conn.execute("DELETE FROM paper_positions WHERE bot_id = 'binance_spot_paper'")
    svc.sync_paper_fill_fees_to_treasury()
    svc.db.close()

    dry = subprocess.run(
        [sys.executable, "scripts/backfill_legacy_paper_pnl.py", "--db-path", str(db_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    dry_json = json.loads(dry.stdout)
    assert dry_json["dry_run"] is True
    assert dry_json["missing_fills_found"] == 1
    assert dry_json["missing_closures_found"] == 1

    apply_run = subprocess.run(
        [
            sys.executable,
            "scripts/backfill_legacy_paper_pnl.py",
            "--db-path",
            str(db_path),
            "--backup-to",
            str(backup_path),
            "--apply",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    apply_json = json.loads(apply_run.stdout)
    assert apply_json["dry_run"] is False
    assert apply_json["inserted_fills"] == 1
    assert apply_json["inserted_closures"] == 1
    assert apply_json["after_audit"]["unexplained_usd"] == "0"
    assert apply_json["pre_run_backup"]["integrity_check"] == "ok"
    assert backup_path.exists()

    # Refusal guard when attempting --apply on live prod DB path without override flag
    prod_guard = subprocess.run(
        [
            sys.executable,
            "scripts/backfill_legacy_paper_pnl.py",
            "--db-path",
            "/root/multi-exchange-paper/data/paper_engine.sqlite3",
            "--apply",
        ],
        capture_output=True,
        text=True,
    )
    assert prod_guard.returncode != 0
    assert "Refusing to mutate production DB" in prod_guard.stderr


def test_backfill_cli_dry_run_apply_and_prod_guard(tmp_path, monkeypatch, capsys):
    import json
    import sys
    from scripts import backfill_legacy_paper_pnl as cli

    db_path = tmp_path / "cli_test.sqlite3"
    cfg = EngineConfig(
        db_path=db_path,
        log_dir=tmp_path / "logs",
        unified_treasury=UnifiedTreasuryConfig(enabled=True, initial_master_capital=10000.0),
    )
    svc = TradingEngineService(config=cfg, enable_telegram=False)
    try:
        svc.treasury.initialize_default_slots(18)
        svc.db.ensure_account("binance_spot_paper", "binance", 2500.0)
        o_open = PaperOrder(
            order_id="cli_o1",
            client_order_id="cli_c1",
            bot_id="binance_spot_paper",
            signal_id="sig_open",
            exchange=ExchangeId.BINANCE,
            symbol="SOL/USDT",
            market_type=MarketType.SPOT,
            side=Side.BUY,
            requested_qty=2.0,
            filled_qty=2.0,
            avg_fill_price=100.0,
            fee_usd=0.2,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        _save_filled_order_with_fill(svc, o_open, write_fill=True)
        svc.paper_engine._apply_fill_to_position_and_account(o_open, 100.0)

        o_exit = PaperOrder(
            order_id="cli_o2",
            client_order_id="cli_exit_1",
            bot_id="binance_spot_paper",
            signal_id="sl_tp_exit",
            exchange=ExchangeId.BINANCE,
            symbol="SOL/USDT",
            market_type=MarketType.SPOT,
            side=Side.SELL,
            requested_qty=2.0,
            filled_qty=2.0,
            avg_fill_price=95.0,
            fee_usd=0.19,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        with svc.db.transaction() as conn:
            svc.db.save_order(o_exit, conn=conn)
            svc.db.update_account_cash(
                "binance_spot_paper",
                cash_delta=-10.0 - 0.19,
                realized_pnl_delta=-10.0 - 0.19,
                reserved_margin_delta=-200.0,
                conn=conn,
            )
            conn.execute("DELETE FROM paper_positions WHERE bot_id = 'binance_spot_paper'")
        svc.sync_paper_fill_fees_to_treasury()
    finally:
        svc.db.close()

    # Production DB guard must refuse --apply without --allow-production-db
    monkeypatch.setattr(
        sys,
        "argv",
        ["backfill_legacy_paper_pnl.py", "--db-path", "/root/multi-exchange-paper/data/paper_engine.sqlite3", "--apply"],
    )
    with pytest.raises(SystemExit, match="Refusing to mutate production DB"):
        cli.main()

    # Dry-run must not mutate DB
    monkeypatch.setattr(sys, "argv", ["backfill_legacy_paper_pnl.py", "--db-path", str(db_path)])
    assert cli.main() == 0
    dry_out = json.loads(capsys.readouterr().out)
    assert dry_out["dry_run"] is True
    assert dry_out["missing_fills_found"] == 1
    assert dry_out["missing_closures_found"] == 1

    # Apply must repair cleanly
    monkeypatch.setattr(sys, "argv", ["backfill_legacy_paper_pnl.py", "--db-path", str(db_path), "--apply"])
    assert cli.main() == 0
    apply_out = json.loads(capsys.readouterr().out)
    assert apply_out["dry_run"] is False
    assert apply_out["inserted_fills"] == 1
    assert apply_out["inserted_closures"] == 1
    assert apply_out["after_audit"]["recorded_evidence_coverage_ok"] is True
    assert abs(Decimal(apply_out["after_audit"]["unexplained_usd"])) < Decimal("1e-9")
