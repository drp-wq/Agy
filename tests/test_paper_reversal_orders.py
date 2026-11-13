"""Regression tests for full close, partial close, and reversal-order handling across Binance, Bybit, and OKX."""
from __future__ import annotations

from decimal import Decimal
import pytest

from app.core.config import EngineConfig, RiskPolicyConfig, UnifiedTreasuryConfig
from app.core.models import (
    Direction,
    ExchangeId,
    MarketType,
    OrderStatus,
    PaperFill,
    PaperOrder,
    Side,
)
from app.execution.paper_engine import PaperExecutionEngine
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.service import TradingEngineService


def _make_engine(tmp_path):
    db = PaperDatabase(tmp_path / "reversal.sqlite3")
    risk = GlobalRiskGuardian(RiskPolicyConfig())
    engine = PaperExecutionEngine(db, risk)
    return db, engine


@pytest.mark.parametrize(
    ("exchange", "market_type", "leverage", "bot_id"),
    [
        (ExchangeId.BINANCE, MarketType.SPOT, 1.0, "binance_spot_test"),
        (ExchangeId.BINANCE, MarketType.PERP, 2.0, "binance_perp_test"),
        (ExchangeId.BYBIT, MarketType.SPOT, 1.0, "bybit_spot_test"),
        (ExchangeId.BYBIT, MarketType.PERP, 2.0, "bybit_perp_test"),
        (ExchangeId.OKX, MarketType.SPOT, 1.0, "okx_spot_test"),
        (ExchangeId.OKX, MarketType.PERP, 3.0, "okx_perp_test"),
    ],
)
def test_reversal_preserves_residual_qty_margin_and_pnl(
    tmp_path, exchange: ExchangeId, market_type: MarketType, leverage: float, bot_id: str
):
    db, engine = _make_engine(tmp_path)
    try:
        engine.ensure_bot_account(bot_id, exchange, 5000.0)

        # 1. Open LONG 1.0 @ 100.0 (fee 0.1)
        open_order = PaperOrder(
            order_id=f"ord_open_{bot_id}",
            client_order_id=f"cid_open_{bot_id}",
            bot_id=bot_id,
            signal_id="sig_open",
            exchange=exchange,
            symbol="SOL/USDT",
            market_type=market_type,
            side=Side.BUY,
            requested_qty=1.0,
            filled_qty=1.0,
            avg_fill_price=100.0,
            leverage=leverage,
            fee_usd=0.1,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        with db.transaction() as conn:
            db.save_order(open_order, conn=conn)
            engine._apply_fill_to_position_and_account(open_order, 100.0, conn=conn)

        acct = db.get_account(bot_id)
        assert abs(acct["reserved_margin_usd"] - round(100.0 / leverage, 6)) < 1e-6

        # 2. Reversal SELL 2.5 @ 110.0 (fee 0.275) -> closes 1.0 LONG (+10.0 gross), opens 1.5 SHORT @ 110.0
        rev_order = PaperOrder(
            order_id=f"ord_rev_{bot_id}",
            client_order_id=f"cid_rev_{bot_id}",
            bot_id=bot_id,
            signal_id="sig_rev",
            exchange=exchange,
            symbol="SOL/USDT",
            market_type=market_type,
            side=Side.SELL,
            requested_qty=2.5,
            filled_qty=2.5,
            avg_fill_price=110.0,
            leverage=leverage,
            fee_usd=0.275,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        with db.transaction() as conn:
            db.save_order(rev_order, conn=conn)
            engine._apply_fill_to_position_and_account(rev_order, 110.0, conn=conn)

        positions = db.list_positions(bot_id)
        assert len(positions) == 1
        pos = positions[0]
        assert pos.direction == Direction.SHORT
        assert abs(pos.qty - 1.5) < 1e-6
        assert abs(pos.entry_price - 110.0) < 1e-6

        acct = db.get_account(bot_id)
        expected_realized = round(10.0 - 0.1 - 0.275, 6)
        expected_margin = round((1.5 * 110.0) / leverage, 6)
        assert abs(acct["realized_pnl_usd"] - expected_realized) < 1e-6
        assert abs(acct["cash_usd"] - (5000.0 + expected_realized)) < 1e-6
        assert abs(acct["reserved_margin_usd"] - expected_margin) < 1e-6

        closure = db._conn.execute(
            "SELECT * FROM paper_realized_closures WHERE order_id = ?", (rev_order.order_id,)
        ).fetchone()
        assert closure is not None
        assert Decimal(closure["close_qty"]) == Decimal("1.0")
        assert Decimal(closure["gross_realized_usd"]) == Decimal("10.0")
    finally:
        db.close()


def test_partial_and_full_close_and_treasury_audit_reconcile(tmp_path):
    cfg = EngineConfig(
        db_path=tmp_path / "pf.sqlite3",
        log_dir=tmp_path / "logs",
        unified_treasury=UnifiedTreasuryConfig(enabled=True),
    )
    svc = TradingEngineService(config=cfg, enable_telegram=False)
    try:
        svc.db.ensure_account("okx_perp_test", "okx", 2000.0)

        # Open SHORT 2.0 @ 50.0 (fee 0.05)
        o1 = PaperOrder(
            order_id="o1",
            client_order_id="c1",
            bot_id="okx_perp_test",
            signal_id="s1",
            exchange=ExchangeId.OKX,
            symbol="AVAX/USDT",
            market_type=MarketType.PERP,
            side=Side.SELL,
            requested_qty=2.0,
            filled_qty=2.0,
            avg_fill_price=50.0,
            leverage=2.0,
            fee_usd=0.05,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        # Partial close BUY 0.5 @ 48.0 (fee 0.012) -> +1.0 gross
        o2 = PaperOrder(
            order_id="o2",
            client_order_id="c2",
            bot_id="okx_perp_test",
            signal_id="s2",
            exchange=ExchangeId.OKX,
            symbol="AVAX/USDT",
            market_type=MarketType.PERP,
            side=Side.BUY,
            requested_qty=0.5,
            filled_qty=0.5,
            avg_fill_price=48.0,
            leverage=2.0,
            fee_usd=0.012,
            status=OrderStatus.FILLED,
            is_paper=True,
        )
        # Full close remaining 1.5 @ 52.0 (fee 0.039) -> -3.0 gross
        o3 = PaperOrder(
            order_id="o3",
            client_order_id="c3",
            bot_id="okx_perp_test",
            signal_id="s3",
            exchange=ExchangeId.OKX,
            symbol="AVAX/USDT",
            market_type=MarketType.PERP,
            side=Side.BUY,
            requested_qty=1.5,
            filled_qty=1.5,
            avg_fill_price=52.0,
            leverage=2.0,
            fee_usd=0.039,
            status=OrderStatus.FILLED,
            is_paper=True,
        )

        for o in (o1, o2, o3):
            with svc.db.transaction() as conn:
                svc.db.save_order(o, conn=conn)
                svc.db.save_fill(
                    PaperFill(
                        order_id=o.order_id,
                        bot_id=o.bot_id,
                        exchange=o.exchange,
                        symbol=o.symbol,
                        market_type=o.market_type,
                        side=o.side,
                        qty=o.filled_qty,
                        price=o.avg_fill_price,
                        fee_usd=o.fee_usd,
                        slippage_bps=1.0,
                        is_paper=True,
                    ),
                    conn=conn,
                )
                svc.paper_engine._apply_fill_to_position_and_account(o, o.avg_fill_price, conn=conn)

            if o.order_id == "o2":
                pos = svc.db.list_positions("okx_perp_test")[0]
                assert abs(pos.qty - 1.5) < 1e-6
                assert abs(pos.realized_pnl_usd - 1.0) < 1e-6
                acct = svc.db.get_account("okx_perp_test")
                assert abs(acct["reserved_margin_usd"] - 37.5) < 1e-6

        assert svc.db.list_positions("okx_perp_test") == []
        acct = svc.db.get_account("okx_perp_test")
        assert abs(acct["reserved_margin_usd"]) < 1e-6
        expected_net = round(1.0 - 3.0 - 0.05 - 0.012 - 0.039, 6)
        assert abs(acct["realized_pnl_usd"] - expected_net) < 1e-6

        svc.sync_paper_fill_fees_to_treasury()
        audit = svc.paper_pnl_audit()
        assert audit["recorded_evidence_coverage_ok"] is True
        assert abs(Decimal(audit["unexplained_usd"])) < Decimal("1e-9")
    finally:
        svc.db.close()
