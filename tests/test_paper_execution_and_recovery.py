"""Tests for simultaneous paper execution, partial fills, idempotency, crash recovery, and backup."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.core.config import RiskPolicyConfig
from app.core.models import (
    Direction,
    ExchangeId,
    MarketRegime,
    MarketSnapshot,
    MarketType,
    OrderStatus,
    PaperOrder,
    Side,
    StrategySignal,
)
from app.execution.paper_engine import PaperExecutionEngine
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian


def _snap(price=50000.0, depth=20000.0) -> MarketSnapshot:
    return MarketSnapshot(
        exchange=ExchangeId.OKX,
        symbol="BTC/USDT",
        market_type=MarketType.SPOT,
        timestamp=datetime.now(timezone.utc),
        last_price=price,
        bid=price - 5.0,
        ask=price + 5.0,
        spread_bps=2.0,
        volume_24h_usd=50_000_000.0,
        order_book_depth_usd=depth,
        liquidity_score=0.9,
        regime=MarketRegime.TRENDING_BULLISH,
    )


def _sig() -> StrategySignal:
    return StrategySignal(
        strategy_name="trend_following",
        exchange=ExchangeId.OKX,
        symbol="BTC/USDT",
        market_type=MarketType.SPOT,
        direction=Direction.LONG,
        entry_low=49950.0,
        entry_high=50050.0,
        stop_loss=48500.0,
        take_profit_targets=[52500.0],
        invalidation_condition="Close below SL",
        estimated_fee_pct=0.001,
        estimated_slippage_pct=0.0002,
        risk_reward_ratio=1.7,
        confidence_score=0.62,
        rationale="test",
    )


def test_partial_fills_idempotency_and_reconciliation(tmp_path: Path):
    db = PaperDatabase(tmp_path / "paper.sqlite3")
    risk = GlobalRiskGuardian(RiskPolicyConfig())
    engine = PaperExecutionEngine(db, risk)
    engine.ensure_bot_account("bot_okx_1", ExchangeId.OKX, 10000.0)

    # Notional 1500 > 2% of 20000 depth (400) -> triggers 2 partial fill slices
    snap = _snap(price=50000.0, depth=20000.0)
    ord1 = engine.submit_signal_order(
        bot_id="bot_okx_1",
        signal=_sig(),
        requested_notional_usd=1500.0,
        leverage=1.0,
        snapshot=snap,
        client_order_id="cid_unique_1",
    )
    assert ord1.status == OrderStatus.FILLED
    assert ord1.is_paper is True
    assert ord1.fee_usd > 0

    fills = db.list_fills()
    assert len(fills) == 2
    assert all(f.is_paper for f in fills)

    # Idempotent duplicate client_order_id returns same order without double-filling
    ord_dup = engine.submit_signal_order(
        bot_id="bot_okx_1",
        signal=_sig(),
        requested_notional_usd=1500.0,
        leverage=1.0,
        snapshot=snap,
        client_order_id="cid_unique_1",
    )
    assert ord_dup.order_id == ord1.order_id
    assert len(db.list_fills()) == 2

    rec = engine.reconcile_positions()
    assert rec["reconciled"] is True
    assert rec["open_positions"] == 1

    # Trigger take-profit exit
    closed = engine.mark_to_market_and_check_exits({"okx:spot:BTC/USDT": _snap(price=53000.0)})
    assert len(closed) == 1
    assert len(db.list_positions("bot_okx_1")) == 0
    acct = db.get_account("bot_okx_1")
    assert acct["realized_pnl_usd"] > 0

    # Backup & restore check
    bkp = db.backup_to(tmp_path / "backups" / "snap.sqlite3")
    assert bkp.exists() and bkp.stat().st_size > 0
    db.close()


def test_crash_recovery_resolves_incomplete_orders(tmp_path: Path):
    db = PaperDatabase(tmp_path / "crash.sqlite3")
    risk = GlobalRiskGuardian(RiskPolicyConfig())
    engine = PaperExecutionEngine(db, risk)

    stale_order = PaperOrder(
        client_order_id="crashed_1",
        bot_id="bot_1",
        signal_id="sig_1",
        exchange=ExchangeId.BYBIT,
        symbol="ETH/USDT",
        market_type=MarketType.SPOT,
        side=Side.BUY,
        requested_qty=1.0,
        status=OrderStatus.SUBMITTED,
        is_paper=True,
    )
    db.save_order(stale_order)
    report = engine.recover_on_startup()
    assert report["recovered_orders"] == 1
    recovered = db.get_order_by_client_id("crashed_1")
    assert recovered is not None
    assert recovered.status == OrderStatus.CANCELED
    db.close()


def test_margin_reservation_and_limit_partial_fill_recovery(tmp_path: Path):
    from app.core.models import OrderType

    db = PaperDatabase(tmp_path / "margin_partial.sqlite3")
    risk = GlobalRiskGuardian(RiskPolicyConfig(), db=db)
    engine = PaperExecutionEngine(db, risk)
    engine.ensure_bot_account("bot_margin", ExchangeId.OKX, 10000.0)
    engine.ensure_bot_account("bot_small", ExchangeId.OKX, 500.0)

    # L2 book with asks at 50000 (0.01) and 50100 (0.05); limit at 50050 fills only 0.01 of 0.03 requested
    snap = _snap(price=50000.0, depth=50000.0)
    snap.asks = [(50000.0, 0.01), (50100.0, 0.05)]
    ord_lim = engine.submit_signal_order(
        bot_id="bot_margin",
        signal=_sig(),
        requested_notional_usd=1500.0,
        leverage=1.0,
        snapshot=snap,
        client_order_id="cid_limit_partial",
        order_type=OrderType.LIMIT,
        limit_price=50050.0,
    )
    assert ord_lim.status == OrderStatus.PARTIALLY_FILLED
    assert ord_lim.filled_qty == 0.01
    assert ord_lim.filled_qty < ord_lim.requested_qty

    acct = db.get_account("bot_margin")
    assert acct is not None
    assert acct["reserved_margin_usd"] > 490.0

    # Order requiring more free margin than bot_small's available cash (500) is rejected
    sig2 = _sig()
    sig2.symbol = "ETH/USDT"
    snap2 = _snap(price=2000.0, depth=50000.0)
    snap2.symbol = "ETH/USDT"
    ord_rej = engine.submit_signal_order(
        bot_id="bot_small",
        signal=sig2,
        requested_notional_usd=1000.0,
        leverage=1.0,
        snapshot=snap2,
        client_order_id="cid_over_margin",
    )
    assert ord_rej.status == OrderStatus.REJECTED
    assert ord_rej.reject_reason == "insufficient_free_margin"

    # Crash recovery cancels unfilled remainder of PARTIALLY_FILLED order instead of marking FILLED
    rec = engine.recover_on_startup()
    assert rec["recovered_orders"] == 1
    ord_after = db.get_order_by_client_id("cid_limit_partial")
    assert ord_after is not None
    assert ord_after.status == OrderStatus.CANCELED
    assert ord_after.reject_reason == "unfilled_remainder_canceled_on_recovery"

    # Closing position releases reserved margin
    closed = engine.mark_to_market_and_check_exits({"okx:spot:BTC/USDT": _snap(price=53000.0)})
    assert len(closed) == 1
    acct_after = db.get_account("bot_margin")
    assert acct_after is not None
    assert acct_after["reserved_margin_usd"] == 0.0
    db.close()


def test_concurrent_multibot_execution_and_transaction_rollback(tmp_path: Path):
    from concurrent.futures import ThreadPoolExecutor
    import pytest

    db = PaperDatabase(tmp_path / "concurrent.sqlite3")
    risk = GlobalRiskGuardian(RiskPolicyConfig(), db=db)
    engine = PaperExecutionEngine(db, risk)

    bots = [
        ("bot_okx", ExchangeId.OKX, "BTC/USDT"),
        ("bot_bybit", ExchangeId.BYBIT, "ETH/USDT"),
        ("bot_binance", ExchangeId.BINANCE, "SOL/USDT"),
    ]
    for bot_id, ex, _ in bots:
        engine.ensure_bot_account(bot_id, ex, 10000.0)

    def _submit(i: int):
        bot_id, ex, sym = bots[i % len(bots)]
        snap = _snap(price=1000.0, depth=500000.0)
        snap.exchange = ex
        snap.symbol = sym
        snap.bid = 999.5
        snap.ask = 1000.5
        sig = _sig()
        sig.strategy_name = f"strat_{i}"
        sig.exchange = ex
        sig.symbol = sym
        sig.entry_low = 999.0
        sig.entry_high = 1001.0
        sig.stop_loss = 950.0
        sig.take_profit_targets = [1100.0]
        return engine.submit_signal_order(
            bot_id=bot_id,
            signal=sig,
            requested_notional_usd=500.0,
            leverage=1.0,
            snapshot=snap,
            client_order_id=f"conc_{i}",
        )

    with ThreadPoolExecutor(max_workers=6) as pool:
        orders = list(pool.map(_submit, range(12)))

    assert len(orders) == 12
    assert all(o.status == OrderStatus.FILLED for o in orders), [(o.client_order_id, o.status, o.reject_reason) for o in orders]
    assert len(db.list_fills()) == 12
    for bot_id, _, _ in bots:
        acct = db.get_account(bot_id)
        assert acct is not None
        assert acct["cash_usd"] < 10000.0
        assert acct["reserved_margin_usd"] > 0.0

    # Failure-injection: exception inside db.transaction() rolls back atomically
    before_cash = db.get_account("bot_okx")["cash_usd"]
    with pytest.raises(RuntimeError, match="injected_failure"):
        with db.transaction() as conn:
            conn.execute(
                "UPDATE paper_accounts SET cash_usd = cash_usd - 5000.0 WHERE bot_id = ?",
                ("bot_okx",),
            )
            raise RuntimeError("injected_failure")
    assert db.get_account("bot_okx")["cash_usd"] == before_cash

    # Persistent bot config state across DB reopen
    db.save_bot_enabled("bot_okx", False)
    db.close()
    db2 = PaperDatabase(tmp_path / "concurrent.sqlite3")
    assert db2.load_bot_enabled("bot_okx") is False
    db2.close()


def test_accounting_invariant_across_exchanges_open_and_close(tmp_path: Path):
    db = PaperDatabase(tmp_path / "invariant.sqlite3")
    risk = GlobalRiskGuardian(RiskPolicyConfig(), db=db)
    engine = PaperExecutionEngine(db, risk)

    initial_cash = 5000.0
    for idx, (ex, sym) in enumerate(
        [(ExchangeId.OKX, "BTC/USDT"), (ExchangeId.BYBIT, "ETH/USDT"), (ExchangeId.BINANCE, "SOL/USDT")]
    ):
        bot_id = f"inv_{ex.value}"
        engine.ensure_bot_account(bot_id, ex, initial_cash)
        snap = _snap(price=1000.0 + idx * 100.0, depth=100000.0)
        snap.exchange = ex
        snap.symbol = sym
        snap.bid = snap.last_price - 0.5
        snap.ask = snap.last_price + 0.5
        sig = _sig()
        sig.strategy_name = f"inv_strat_{ex.value}"
        sig.exchange = ex
        sig.symbol = sym
        sig.entry_low = snap.last_price - 5.0
        sig.entry_high = snap.last_price + 5.0
        sig.stop_loss = snap.last_price * 0.9
        sig.take_profit_targets = [snap.last_price * 1.1]

        order = engine.submit_signal_order(
            bot_id=bot_id,
            signal=sig,
            requested_notional_usd=1000.0,
            leverage=1.0,
            snapshot=snap,
            client_order_id=f"inv_cid_{ex.value}",
        )
        assert order.status == OrderStatus.FILLED

        # Close via take-profit
        exit_snap = _snap(price=snap.last_price * 1.12, depth=100000.0)
        exit_snap.exchange = ex
        exit_snap.symbol = sym
        exit_snap.bid = exit_snap.last_price - 0.5
        exit_snap.ask = exit_snap.last_price + 0.5
        closed = engine.mark_to_market_and_check_exits({f"{ex.value}:spot:{sym}": exit_snap})
        assert len(closed) == 1

        acct = db.get_account(bot_id)
        assert acct is not None
        assert acct["reserved_margin_usd"] == 0.0
        assert abs(acct["cash_usd"] - (initial_cash + acct["realized_pnl_usd"])) < 1e-4
    db.close()
