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
