"""Economic funding checks using committed engine executions, not supplied quantities."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.core.config import RiskPolicyConfig, UnifiedTreasuryConfig
from app.core.models import ExchangeId, MarketType, OrderStatus, PaperOrder, Side
from app.execution.paper_engine import PaperExecutionEngine
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.treasury import paper_exposure
from app.treasury.funding_coordinator import settle_captured_funding
from app.treasury.ledger import UnifiedTreasuryLedger


BOUNDARY = datetime(2026, 11, 14, 8, tzinfo=timezone.utc)


def setup_db(tmp_path):
    db = PaperDatabase(tmp_path/'captured.sqlite3')
    db.ensure_account('bot', 'okx', 500)
    return db, PaperExecutionEngine(db, GlobalRiskGuardian(RiskPolicyConfig())), UnifiedTreasuryLedger(db, UnifiedTreasuryConfig(enabled=True))


def execution(db, engine, name, qty, side, at, market=MarketType.PERP):
    order = PaperOrder(order_id=name, client_order_id=name, bot_id='bot', signal_id=name,
        exchange=ExchangeId.OKX, symbol='BTC/USDT', market_type=market, side=side,
        requested_qty=qty, filled_qty=qty, avg_fill_price=100, fee_usd=qty*.1,
        leverage=2 if market==MarketType.PERP else 1, status=OrderStatus.FILLED,
        created_at=at, updated_at=at, is_paper=True)
    with db.transaction() as conn:
        db.save_order(order, conn=conn)
        engine._apply_fill_to_position_and_account(order, 100, conn=conn)
    return order


def settle(db, treasury, **overrides):
    return settle_captured_funding(db, treasury, **(dict(bot_id='bot', exchange='okx',
        symbol='BTC/USDT', settlement_at=BOUNDARY, interval_seconds=28800,
        rate='.001', mark_price='100', source={'endpoint':'fixture://funding',
        'venue_event_id':'synthetic-0800'}, now=BOUNDARY+timedelta(days=1)) | overrides))


@pytest.mark.parametrize('side,qty_sign', [(Side.BUY, 1), (Side.SELL, -1)])
@pytest.mark.parametrize('rate', ['.001', '-.001', '0'])
def test_closed_position_missed_settlement_restart_repeat(tmp_path,side,qty_sign,rate):
    db, engine, treasury = setup_db(tmp_path)
    execution(db, engine, 'open', 2, side, BOUNDARY-timedelta(seconds=1))
    execution(db, engine, 'close', 2, Side.SELL if side==Side.BUY else Side.BUY, BOUNDARY)
    assert db.list_positions('bot')==[]
    assert paper_exposure.keys(db)[0]['coverage_from']==(BOUNDARY-timedelta(seconds=1)).isoformat()
    before=dict(db.get_account('bot'))
    result=settle(db,treasury,rate=rate)
    expected=-Decimal(qty_sign)*2*100*Decimal(rate)
    assert Decimal(result['amount_usd'])==expected
    after=db.get_account('bot')
    assert after['cash_usd']==pytest.approx(before['cash_usd']+float(expected))
    assert after['realized_pnl_usd']==pytest.approx(before['realized_pnl_usd']+float(expected))
    assert after['reserved_margin_usd']==before['reserved_margin_usd']==0
    events=db.list_ledger_events()
    assert len(events)==1 and Decimal(events[0]['funding_usd'])==expected
    assert Decimal(events[0]['realized_pnl_usd'])==0
    db.close()
    db, engine, treasury=setup_db(tmp_path)
    assert settle(db,treasury,rate=rate)['duplicate']
    assert db.get_account('bot')['cash_usd']==after['cash_usd']
    assert len(db.list_ledger_events())==1
    # Fully closed exposure still covers the next missed interval, with zero cash.
    result=settle(db,treasury,rate=rate,settlement_at=BOUNDARY+timedelta(hours=8))
    assert Decimal(result['amount_usd'])==0
    db.close()


def test_resize_reverse_same_timestamp_ordering_and_exact_boundary(tmp_path):
    db, engine, treasury=setup_db(tmp_path)
    execution(db,engine,'open',2,Side.BUY,BOUNDARY-timedelta(seconds=3))
    execution(db,engine,'resize',1,Side.BUY,BOUNDARY-timedelta(seconds=2))
    execution(db,engine,'partial',1,Side.SELL,BOUNDARY-timedelta(seconds=2))
    execution(db,engine,'reverse',5,Side.SELL,BOUNDARY-timedelta(seconds=1))
    execution(db,engine,'at-boundary',1,Side.BUY,BOUNDARY)
    assert settle(db,treasury)['signed_qty']=='-3'
    later=paper_exposure.exposure_at(db,bot_id='bot',exchange='okx',symbol='BTC/USDT',at=BOUNDARY+timedelta(microseconds=1))
    assert later['signed_qty']=='-2'
    db.close()


def test_first_execution_on_boundary_has_zero_prior_exposure(tmp_path):
    db,engine,treasury=setup_db(tmp_path)
    execution(db,engine,'open',2,Side.BUY,BOUNDARY)
    assert Decimal(settle(db,treasury)['amount_usd'])==0
    with pytest.raises(ValueError,match='coverage'):
        settle(db,treasury,settlement_at=BOUNDARY-timedelta(hours=8))
    db.close()


def test_execution_replay_conflict_and_out_of_order_fail_atomically(tmp_path):
    db,engine,treasury=setup_db(tmp_path)
    original=execution(db,engine,'open',2,Side.BUY,BOUNDARY-timedelta(seconds=1))
    account=dict(db.get_account('bot'))
    engine._apply_fill_to_position_and_account(original,100)
    assert db.get_account('bot')==account
    with pytest.raises(ValueError,match='Conflicting'):
        engine._apply_fill_to_position_and_account(original.model_copy(update={'fee_usd':3}),100)
    with pytest.raises(ValueError,match='Conflicting'):
        engine._apply_fill_to_position_and_account(original.model_copy(update={'updated_at':BOUNDARY}),100)
    with pytest.raises(ValueError,match='precedes'):
        execution(db,engine,'older',1,Side.BUY,BOUNDARY-timedelta(seconds=2))
    assert db.get_account('bot')==account and db._conn.execute("SELECT 1 FROM paper_orders WHERE order_id='older'").fetchone() is None
    db.close()


def test_exposure_capture_failure_rolls_back_finances(tmp_path,monkeypatch):
    db,engine,treasury=setup_db(tmp_path)
    before=dict(db.get_account('bot'))
    def crash(*args):raise RuntimeError('exposure storage failure')
    monkeypatch.setattr(paper_exposure,'after_execution',crash)
    with pytest.raises(RuntimeError):execution(db,engine,'open',2,Side.BUY,BOUNDARY)
    assert db.get_account('bot')==before and not db.list_positions('bot')
    assert not paper_exposure.keys(db) and db._conn.execute("SELECT 1 FROM paper_orders WHERE order_id='open'").fetchone() is None
    db.close()


def test_journal_failure_rolls_back_funding_cash_and_attribution(tmp_path,monkeypatch):
    db,engine,treasury=setup_db(tmp_path)
    execution(db,engine,'open',2,Side.BUY,BOUNDARY-timedelta(seconds=1))
    before=dict(db.get_account('bot'))
    original=db.append_ledger_event
    def crash(**kwargs):raise RuntimeError('journal unavailable')
    monkeypatch.setattr(db,'append_ledger_event',crash)
    with pytest.raises(RuntimeError):settle(db,treasury)
    assert db.get_account('bot')==before
    assert treasury.get_attribution_summary('exchange')=={}
    monkeypatch.setattr(db,'append_ledger_event',original)
    assert not settle(db,treasury)['duplicate']
    db.close()


def test_rounding_source_conflict_future_and_missing_history(tmp_path):
    db,engine,treasury=setup_db(tmp_path)
    with pytest.raises(ValueError,match='history'):settle(db,treasury)
    execution(db,engine,'open',1,Side.BUY,BOUNDARY-timedelta(seconds=1))
    result=settle(db,treasury,rate='.00000000005',mark_price='100')
    assert Decimal(result['amount_usd'])==0  # Half-even at exactly half a USDT quantum.
    assert Decimal(result['rounding_delta_usd'])==Decimal('.000000005')
    assert settle(db,treasury,rate='0.000000000050',mark_price='100.0')['duplicate']
    before=dict(db.get_account('bot'))
    with pytest.raises(ValueError,match='Conflicting'):
        settle(db,treasury,rate='.00000000005',source={'endpoint':'fixture://different','venue_event_id':'synthetic-0800'})
    with pytest.raises(ValueError,match='already'):
        settle(db,treasury,settlement_at=BOUNDARY+timedelta(days=2))
    assert db.get_account('bot')==before
    db.close()


def test_late_execution_cannot_rewrite_settled_exposure(tmp_path):
    db,engine,treasury=setup_db(tmp_path)
    execution(db,engine,'open',1,Side.BUY,BOUNDARY-timedelta(seconds=10))
    settle(db,treasury)
    account=dict(db.get_account('bot'))
    with pytest.raises(ValueError,match='already settled'):
        execution(db,engine,'late',1,Side.BUY,BOUNDARY-timedelta(seconds=1))
    assert db.get_account('bot')==account
    # Boundary changes follow the sampled exposure and do not invalidate it.
    execution(db,engine,'exact',1,Side.BUY,BOUNDARY)
    assert settle(db,treasury)['duplicate']
    db.close()


def test_open_margin_and_unrealized_unchanged_by_funding(tmp_path):
    db,engine,treasury=setup_db(tmp_path)
    execution(db,engine,'open',1,Side.BUY,BOUNDARY-timedelta(seconds=1))
    positions=db.list_positions('bot')
    reserved=db.get_account('bot')['reserved_margin_usd']
    settle(db,treasury)
    assert db.list_positions('bot')==positions
    assert db.get_account('bot')['reserved_margin_usd']==reserved
    db.close()


def test_concurrent_independent_connections_post_only_once(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    db,engine,treasury=setup_db(tmp_path)
    execution(db,engine,'open',1,Side.BUY,BOUNDARY-timedelta(seconds=1))
    other,_,other_treasury=setup_db(tmp_path)
    before=db.get_account('bot')['cash_usd']
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda pair:settle(*pair), [(db,treasury),(other,other_treasury)]))
    assert sorted(r['duplicate'] for r in results)==[False,True]
    assert db.get_account('bot')['cash_usd']==pytest.approx(before-.1)
    assert len(db.list_ledger_events())==1
    db.close();other.close()


def test_spot_execution_does_not_create_funding_coverage(tmp_path):
    db,engine,treasury=setup_db(tmp_path)
    execution(db,engine,'spot',1,Side.BUY,BOUNDARY-timedelta(seconds=1),MarketType.SPOT)
    assert paper_exposure.keys(db)==[]
    with pytest.raises(ValueError,match='history'):settle(db,treasury)
    db.close()


def test_conflicting_existing_journal_cannot_hide_funding(tmp_path):
    db,engine,treasury=setup_db(tmp_path)
    execution(db,engine,'open',1,Side.BUY,BOUNDARY-timedelta(seconds=1))
    event_id=f'funding:linear:okx:bot:BTC/USDT:{BOUNDARY.isoformat()}'
    db.append_ledger_event(event_id=event_id, idempotency_key=event_id,
        timestamp_utc=BOUNDARY.isoformat(), event_type='FUNDING',bot_id='bot',
        exchange='okx',symbol='BTC/USDT',funding_usd='99')
    before=dict(db.get_account('bot'))
    with pytest.raises(ValueError,match='dashboard journal'):settle(db,treasury)
    assert db.get_account('bot')==before
    assert treasury.get_attribution_summary('exchange')=={}
    assert db._conn.execute('SELECT COUNT(*) FROM paper_exposure_watermarks').fetchone()[0]==0
    db.close()
