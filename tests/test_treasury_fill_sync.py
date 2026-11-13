"""Idempotent paper fee attribution; no fabricated profit or exchange writes."""
from decimal import Decimal
from app.core.config import EngineConfig, UnifiedTreasuryConfig
from app.core.models import PaperFill, PaperOrder, ExchangeId, MarketType, Side
from app.service import TradingEngineService


def test_paper_fill_fee_sync_is_idempotent(tmp_path):
    cfg = EngineConfig(db_path=tmp_path / 'paper.sqlite3', log_dir=tmp_path / 'logs',
                       unified_treasury=UnifiedTreasuryConfig(enabled=True))
    service = TradingEngineService(config=cfg, enable_telegram=False)
    service.treasury.initialize_default_slots(18)
    fill = PaperFill(order_id='order_test', bot_id='bot_test', exchange=ExchangeId.BINANCE,
                     symbol='BTC/USDT', market_type=MarketType.SPOT, side=Side.BUY,
                     qty=0.01, price=50000, fee_usd=0.5, slippage_bps=2)
    service.db.save_order(PaperOrder(order_id="order_test", client_order_id="client_test",
                                     bot_id="bot_test", signal_id="signal_test",
                                     exchange=ExchangeId.BINANCE, symbol="BTC/USDT",
                                     market_type=MarketType.SPOT, side=Side.BUY, requested_qty=0.01))
    service.db.save_fill(fill)
    first = service.sync_paper_fill_fees_to_treasury()
    second = service.sync_paper_fill_fees_to_treasury()
    assert first['fees_processed_idempotently'] == second['fees_processed_idempotently'] == 1
    with service.db._lock:
        rows = service.db._conn.execute("SELECT amount FROM treasury_attribution_events WHERE idempotency_key=?",(f'paper_fill_fee:{fill.fill_id}',)).fetchall()
    assert len(rows) == 1 and Decimal(rows[0]['amount']) == Decimal('-0.50000000')
    assert service.treasury.reconcile()['balanced'] is True
    assert service.treasury.reconcile()['realized_pnl_complete'] is False
    service.db.close()


def test_realized_close_evidence_and_idempotent_attribution(tmp_path):
    from app.core.models import PaperPosition, Direction, OrderStatus
    cfg = EngineConfig(db_path=tmp_path/'close.sqlite3', log_dir=tmp_path/'logs',
                       unified_treasury=UnifiedTreasuryConfig(enabled=True))
    svc = TradingEngineService(config=cfg, enable_telegram=False)
    svc.db.ensure_account('bot_close', 'binance', 1000.0)
    opening = PaperOrder(order_id='opening',client_order_id='opening_client',bot_id='bot_close',
                         signal_id='open',exchange=ExchangeId.BINANCE,symbol='BTC/USDT',
                         market_type=MarketType.SPOT,side=Side.BUY,requested_qty=1,filled_qty=1,
                         avg_fill_price=100,status=OrderStatus.FILLED)
    svc.db.save_order(opening)
    svc.db.upsert_position(PaperPosition(bot_id='bot_close',exchange=ExchangeId.BINANCE,
                            symbol='BTC/USDT',market_type=MarketType.SPOT,
                            direction=Direction.LONG,qty=1,entry_price=100,mark_price=100))
    closing = PaperOrder(order_id='closing',client_order_id='closing_client',bot_id='bot_close',
                         signal_id='close',exchange=ExchangeId.BINANCE,symbol='BTC/USDT',
                         market_type=MarketType.SPOT,side=Side.SELL,requested_qty=1,filled_qty=1,
                         avg_fill_price=110,fee_usd=0.11,status=OrderStatus.FILLED)
    svc.db.save_order(closing)
    svc.paper_engine._apply_fill_to_position_and_account(closing,110)
    assert abs(svc.db.get_account('bot_close')['realized_pnl_usd']-9.89)<1e-6
    assert svc.db._conn.execute('SELECT COUNT(*) FROM paper_realized_closures').fetchone()[0]==1
    svc.sync_paper_fill_fees_to_treasury()
    svc.sync_paper_fill_fees_to_treasury()
    rows=svc.db._conn.execute("SELECT amount FROM treasury_attribution_events WHERE idempotency_key='paper_close_pnl:closing'").fetchall()
    assert len(rows)==1 and Decimal(rows[0]['amount'])==Decimal('10.00000000')
    assert svc.treasury.reconcile()['realized_pnl_complete'] is False
    svc.db.close()
