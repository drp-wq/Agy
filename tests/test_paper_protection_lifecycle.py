"""Economic/recovery tests through the real risk -> fill -> position -> exit path."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import json

import pytest

from app.core.config import RiskPolicyConfig
from app.core.models import Direction, ExchangeId, MarketType, MarketRegime, MarketSnapshot, StrategySignal, OrderStatus
from app.execution.paper_engine import PaperExecutionEngine
from app.execution.paper_protection import PREFIX
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian


def quote(price=100, market=MarketType.SPOT, depth_qty=100):
    return MarketSnapshot(exchange=ExchangeId.OKX, symbol='TEST/USDT', market_type=market,
        timestamp=datetime.now(timezone.utc), last_price=price,
        bid=price, ask=price, spread_bps=2, volume_24h_usd=1e8,
        order_book_depth_usd=100000, liquidity_score=1,
        regime=MarketRegime.TRENDING_BULLISH,
        bids=[(price,depth_qty)],asks=[(price,depth_qty)])


def signal(direction=Direction.LONG, market=MarketType.SPOT, targets=None, trail=None):
    long = direction == Direction.LONG
    return StrategySignal(strategy_name='protection_fixture',exchange=ExchangeId.OKX,
        symbol='TEST/USDT',market_type=market,direction=direction,entry_low=99,entry_high=101,
        stop_loss=90 if long else 110,
        take_profit_targets=targets if targets is not None else ([110,120] if long else [90,80]),
        invalidation_condition='fixture stop',estimated_fee_pct=.001,
        estimated_slippage_pct=0,risk_reward_ratio=2,confidence_score=.8,
        rationale='synthetic paper lifecycle fixture',metadata={} if trail is None else {'trailing_stop_pct':trail})


def start(tmp_path, capital=500):
    db=PaperDatabase(tmp_path/'protection.sqlite3')
    engine=PaperExecutionEngine(db,GlobalRiskGuardian(RiskPolicyConfig()))
    engine.ensure_bot_account('bot',ExchangeId.OKX,capital)
    return db,engine


def open_trade(engine, sig, qty=1):
    snap=quote(market=sig.market_type)
    order=engine.submit_signal_order(bot_id='bot',signal=sig,requested_notional_usd=qty*100,
        leverage=2 if sig.market_type==MarketType.PERP else 1,snapshot=snap,client_order_id='entry')
    assert order.status==OrderStatus.FILLED,order.reject_reason
    return order


def mark(engine, snap):
    return engine.mark_to_market_and_check_exits({f'okx:{snap.market_type.value}:TEST/USDT':snap})


def assert_economics(db):
    # Independently reconstruct the cash equation from immutable fill/closure rows.
    account=db.get_account('bot')
    fees=sum((Decimal(str(r[0])) for r in db._conn.execute("SELECT fee_usd FROM paper_fills WHERE bot_id='bot'")),Decimal(0))
    gross=sum((Decimal(str(r[0])) for r in db._conn.execute("SELECT gross_realized_usd FROM paper_realized_closures WHERE bot_id='bot'")),Decimal(0))
    expected=Decimal(str(account['initial_equity_usd']))+gross-fees
    assert float(account['cash_usd'])==pytest.approx(float(expected),abs=1e-6)
    assert float(account['realized_pnl_usd'])==pytest.approx(float(gross-fees),abs=1e-6)
    positions=db.list_positions('bot')
    margin=sum(p.qty*p.entry_price/p.leverage for p in positions)
    assert account['reserved_margin_usd']==pytest.approx(margin,abs=2e-6)
    for p in positions:
        signed=1 if p.direction==Direction.LONG else -1
        assert p.unrealized_pnl_usd==pytest.approx(round(signed*(p.mark_price-p.entry_price)*p.qty,4),abs=1e-7)
    filled_orders=db._conn.execute("SELECT order_id,filled_qty,fee_usd FROM paper_orders WHERE filled_qty>0").fetchall()
    for order in filled_orders:
        fills=db.list_fills_for_order(order['order_id'])
        assert sum(f.qty for f in fills)==pytest.approx(order['filled_qty'],abs=1e-6)
        assert sum(f.fee_usd for f in fills)==pytest.approx(order['fee_usd'],abs=1e-6)


@pytest.mark.parametrize('direction,market',[(Direction.LONG,MarketType.SPOT),(Direction.LONG,MarketType.PERP),(Direction.SHORT,MarketType.PERP)])
def test_multiple_levels_restart_repeat_and_economic_conservation(tmp_path,direction,market):
    db,engine=start(tmp_path)
    opened=open_trade(engine,signal(direction,market))
    first=110 if direction==Direction.LONG else 90
    tick=quote(first,market)
    closed=mark(engine,tick)
    assert len(closed)==1 and closed[0].filled_qty==.5
    assert db.list_positions('bot')[0].qty==.5
    assert mark(engine,tick)==[]
    assert_economics(db)
    db.close()
    db=PaperDatabase(tmp_path/'protection.sqlite3');engine=PaperExecutionEngine(db,GlobalRiskGuardian(RiskPolicyConfig()))
    engine.recover_on_startup()
    assert mark(engine,tick)==[]
    final=mark(engine,quote(120 if direction==Direction.LONG else 80,market))
    assert len(final)==1 and final[0].filled_qty==.5
    assert not db.list_positions('bot')
    assert sum(o.filled_qty for o in closed+final)==opened.filled_qty
    assert len(db.list_realized_closures())==2
    assert_economics(db);db.close()


def test_gap_crossing_levels_consumes_one_book_once_and_recovers_partial_exit(tmp_path):
    db,engine=start(tmp_path)
    open_trade(engine,signal())
    tick=quote(130,depth_qty=.3)
    exited=mark(engine,tick)
    assert len(exited)==1 and exited[0].status==OrderStatus.PARTIALLY_FILLED
    assert exited[0].filled_qty==.3 and db.list_positions('bot')[0].qty==.7
    assert mark(engine,tick)==[]
    assert_economics(db)
    db.close();db=PaperDatabase(tmp_path/'protection.sqlite3')
    engine=PaperExecutionEngine(db,GlobalRiskGuardian(RiskPolicyConfig()));engine.recover_on_startup()
    exits=mark(engine,quote(130))
    assert len(exits)==1 and exits[0].filled_qty==.7
    assert not db.list_positions('bot')
    assert_economics(db);db.close()


@pytest.mark.parametrize('direction', [Direction.LONG,Direction.SHORT])
def test_trailing_anchor_never_recedes_and_stop_survives_restart(tmp_path,direction):
    db,engine=start(tmp_path)
    market=MarketType.PERP
    open_trade(engine,signal(direction,market,targets=[200] if direction==Direction.LONG else [10],trail=.05))
    favorable=110 if direction==Direction.LONG else 90
    assert mark(engine,quote(favorable,market))==[]
    p=db.list_positions('bot')[0]
    assert p.stop_loss==pytest.approx(104.5 if direction==Direction.LONG else 94.5)
    db.close();db=PaperDatabase(tmp_path/'protection.sqlite3');engine=PaperExecutionEngine(db,GlobalRiskGuardian(RiskPolicyConfig()))
    mark(engine,quote(108 if direction==Direction.LONG else 92,market))
    assert db.list_positions('bot')[0].stop_loss==p.stop_loss
    closed=mark(engine,quote(104 if direction==Direction.LONG else 95,market))
    assert len(closed)==1 and not db.list_positions('bot')
    assert_economics(db);db.close()


@pytest.mark.parametrize('direction,adverse',[(Direction.LONG,.001),(Direction.SHORT,201)])
def test_extreme_gap_cross_margin_liquidation_records_loss_without_bailout(tmp_path,direction,adverse):
    db,engine=start(tmp_path,100)
    for i in range(4):engine.ensure_bot_account(f'other-{i}',ExchangeId.OKX,100)
    opened=open_trade(engine,signal(direction,MarketType.PERP))
    closed=mark(engine,quote(adverse,MarketType.PERP))
    assert len(closed)==1 and not db.list_positions('bot')
    reason=json.loads(db._conn.execute("SELECT payload_json FROM audit_logs WHERE event_type='PAPER_PROTECTION_EXECUTION' ORDER BY id DESC LIMIT 1").fetchone()[0])['reason']
    assert reason=='paper_liquidation_cross_margin'
    assert db.get_account('bot')['cash_usd']<0  # Gap insolvency is explicit; no fabricated insurance credit.
    assert_economics(db);db.close()


def test_protection_crash_rolls_back_order_fills_cash_and_plan(tmp_path,monkeypatch):
    db,engine=start(tmp_path);open_trade(engine,signal())
    before=db._conn.execute("SELECT value_json FROM risk_state WHERE key LIKE 'paper_protection:%'").fetchone()[0]
    account=dict(db.get_account('bot'));fills=len(db.list_fills())
    original=db.record_paper_close
    def crash(*args,**kwargs):raise RuntimeError('synthetic crash before realized settlement')
    monkeypatch.setattr(db,'record_paper_close',crash)
    tick=quote(110)
    with pytest.raises(RuntimeError):mark(engine,tick)
    assert db.get_account('bot')==account and len(db.list_fills())==fills
    assert db._conn.execute("SELECT value_json FROM risk_state WHERE key LIKE 'paper_protection:%'").fetchone()[0]==before
    monkeypatch.setattr(db,'record_paper_close',original)
    assert len(mark(engine,tick))==1
    assert_economics(db);db.close()


def test_parallel_delivery_with_two_engine_instances_posts_one_exit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    db,engine=start(tmp_path);open_trade(engine,signal())
    second_db=PaperDatabase(tmp_path/'protection.sqlite3')
    second=PaperExecutionEngine(second_db,GlobalRiskGuardian(RiskPolicyConfig()))
    tick=quote(110)
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(mark,engine,tick);b=pool.submit(mark,second,tick)
        assert len(a.result())+len(b.result())==1
    assert db.list_positions('bot')[0].qty==.5
    assert db.list_positions('bot')[0].take_profit==120
    assert_economics(db);second_db.close();db.close()


def test_cancel_partial_entry_is_idempotent_and_preserves_executed_position(tmp_path):
    db,engine=start(tmp_path)
    snap=quote(depth_qty=.4)
    entry=engine.submit_signal_order(bot_id='bot',signal=signal(),requested_notional_usd=100,
        leverage=1,snapshot=snap,client_order_id='partial-entry')
    assert entry.status==OrderStatus.PARTIALLY_FILLED and entry.filled_qty==.4
    before=dict(db.get_account('bot'));positions=db.list_positions('bot')
    canceled=engine.cancel_paper_order('partial-entry')
    assert canceled.status==OrderStatus.CANCELED
    assert engine.cancel_paper_order('partial-entry').order_id==entry.order_id
    assert db.get_account('bot')==before and db.list_positions('bot')==positions
    assert_economics(db);db.close()


def test_new_spot_short_and_missing_account_are_rejected_without_fills(tmp_path):
    db,engine=start(tmp_path)
    rejected=engine.submit_signal_order(bot_id='bot',signal=signal(Direction.SHORT),
        requested_notional_usd=100,leverage=1,snapshot=quote(),client_order_id='short-spot')
    assert rejected.status==OrderStatus.REJECTED and rejected.reject_reason=='spot_short_or_oversell_not_supported'
    sig=signal();sig.strategy_name='missing-account-fixture'
    rejected=engine.submit_signal_order(bot_id='missing',signal=sig,requested_notional_usd=100,
        leverage=1,snapshot=quote(),client_order_id='missing')
    assert rejected.status==OrderStatus.REJECTED and rejected.reject_reason=='paper_account_required'
    assert not db.list_fills() and not db.list_positions()
    assert_economics(db);db.close()


def test_reconciler_detects_drift_instead_of_counting_paper_flags_only(tmp_path):
    db,engine=start(tmp_path);open_trade(engine,signal())
    assert engine.reconcile_positions()['reconciled']
    with db.transaction() as conn:conn.execute("UPDATE paper_accounts SET reserved_margin_usd=1 WHERE bot_id='bot'")
    report=engine.reconcile_positions()
    assert not report['reconciled']
    assert 'reserved_margin_equation' in [e['reason'] for e in report['equation_errors']]
    assert not report['historical_completeness_certified']
    db.close()


def test_actual_book_cost_cannot_overdraw_margin_even_when_mark_check_passes(tmp_path):
    db,engine=start(tmp_path,100)
    for i in range(4):engine.ensure_bot_account(f'other-{i}',ExchangeId.OKX,100)
    snap=quote();snap.asks=[(110,100)]
    result=engine.submit_signal_order(bot_id='bot',signal=signal(),requested_notional_usd=99,
        leverage=1,snapshot=snap,client_order_id='cost-shock')
    assert result.status==OrderStatus.REJECTED and result.reject_reason=='insufficient_actual_execution_margin'
    assert not db.list_fills() and not db.list_positions()
    assert db.get_account('bot')['cash_usd']==100
    assert_economics(db);db.close()


def test_manual_reduction_rescales_remaining_tp_without_rearming_completed_level(tmp_path):
    db,engine=start(tmp_path);open_trade(engine,signal())
    assert mark(engine,quote(110))[0].filled_qty==.5
    reduction=engine.submit_signal_order(bot_id='bot',signal=signal(Direction.SHORT),
        requested_notional_usd=27.75,leverage=1,snapshot=quote(111),client_order_id='reduce')
    assert reduction.status==OrderStatus.FILLED and reduction.filled_qty==.25
    assert db.list_positions('bot')[0].qty==.25
    assert_economics(db)
    assert mark(engine,quote(115))==[]
    final=mark(engine,quote(120))
    assert len(final)==1 and final[0].filled_qty==.25 and not db.list_positions('bot')
    assert_economics(db);db.close()


def test_size_increase_updates_cost_margin_and_keeps_trailing_policy(tmp_path):
    db,engine=start(tmp_path)
    first=open_trade(engine,signal(trail=.05),qty=.5)
    mark(engine,quote(105))
    add_signal=signal();add_signal.strategy_name='scale-in-fixture'
    added=engine.submit_signal_order(bot_id='bot',signal=add_signal,requested_notional_usd=27.5,
        leverage=1,snapshot=quote(110),client_order_id='scale-in')
    assert added.status==OrderStatus.FILLED and added.filled_qty==.25
    pos=db.list_positions('bot')[0]
    assert pos.qty==.75 and pos.entry_price==pytest.approx((50+27.5)/.75,abs=1e-6)
    assert db.get_account('bot')['reserved_margin_usd']==77.5
    plan=json.loads(db._conn.execute('SELECT value_json FROM risk_state WHERE key=?',(PREFIX+pos.position_id,)).fetchone()[0])
    assert plan['trailing_stop_pct']==.05 and plan['anchor']==110
    assert_economics(db)
    assert len(mark(engine,quote(103)))==1
    assert not db.list_positions('bot')
    assert_economics(db);db.close()


def test_canceled_partial_execution_is_included_in_replay_and_missing_evidence_checks(tmp_path):
    from app.treasury.legacy_pnl_backfill import replay_paper_orders
    from app.treasury.reconciliation_audit import detect_missing_paper_order_evidence
    db,engine=start(tmp_path)
    order=engine.submit_signal_order(bot_id='bot',signal=signal(),requested_notional_usd=100,
        leverage=1,snapshot=quote(depth_qty=.4),client_order_id='partial-proof')
    assert order.status==OrderStatus.PARTIALLY_FILLED
    engine.cancel_paper_order('partial-proof')
    replay=replay_paper_orders(db._conn)
    assert replay.replayed_orders==1
    assert replay.per_bot_replayed['bot']['cash_usd']==db.get_account('bot')['cash_usd']
    assert detect_missing_paper_order_evidence(db)['complete']
    # Corrupt an isolated fixture to prove canceled-order evidence is not ignored.
    with db.transaction() as conn:conn.execute('DELETE FROM paper_fills WHERE order_id=?',(order.order_id,))
    report=detect_missing_paper_order_evidence(db)
    assert not report['complete'] and report['missing_fill_order_ids']==[order.order_id]
    db.close()


def test_immediate_marketable_limit_uses_taker_fee_and_unfilled_limit_charges_none(tmp_path):
    from app.core.models import OrderType
    db,engine=start(tmp_path)
    filled=engine.submit_signal_order(bot_id='bot',signal=signal(),requested_notional_usd=100,
        leverage=1,snapshot=quote(),client_order_id='marketable-limit',order_type=OrderType.LIMIT,limit_price=100)
    assert filled.status==OrderStatus.FILLED and filled.fee_usd==.1
    other=signal();other.strategy_name='unfilled-limit-fixture';other.symbol='OTHER/USDT'
    other_snap=quote();other_snap.symbol=other.symbol
    before=dict(db.get_account('bot'));fill_count=len(db.list_fills())
    pending=engine.submit_signal_order(bot_id='bot',signal=other,requested_notional_usd=20,
        leverage=1,snapshot=other_snap,client_order_id='passive-limit',order_type=OrderType.LIMIT,limit_price=99)
    assert pending.status==OrderStatus.SUBMITTED and pending.filled_qty==0 and pending.fee_usd==0
    engine.cancel_paper_order(pending.client_order_id)
    assert len(db.list_fills())==fill_count and db.get_account('bot')==before
    assert_economics(db);db.close()


@pytest.mark.parametrize('bad_input', ['nan_notional','nan_mark','infinite_leverage','future_timestamp'])
def test_invalid_inputs_are_persisted_as_rejections_without_financial_side_effects(tmp_path,bad_input):
    db,engine=start(tmp_path);snap=quote();notional,lev=100,1
    if bad_input=='nan_notional':notional=float('nan')
    elif bad_input=='nan_mark':snap.last_price=float('nan')
    elif bad_input=='infinite_leverage':lev=float('inf')
    else:snap.timestamp=datetime.now(timezone.utc)+timedelta(minutes=1)
    rejected=engine.submit_signal_order(bot_id='bot',signal=signal(),requested_notional_usd=notional,
        leverage=lev,snapshot=snap,client_order_id='invalid-input')
    assert rejected.status==OrderStatus.REJECTED and rejected.reject_reason=='invalid_execution_inputs'
    assert not db.list_fills() and not db.list_positions()
    assert db.get_account('bot')['cash_usd']==500
    assert_economics(db);db.close()


def test_fixed_stop_dominating_a_configured_trail_keeps_correct_exit_reason(tmp_path):
    db,engine=start(tmp_path)
    sig=signal(targets=[200],trail=.05);sig.stop_loss=99.5
    open_trade(engine,sig)
    assert len(mark(engine,quote(99.4)))==1
    payload=json.loads(db._conn.execute("SELECT payload_json FROM audit_logs WHERE event_type='PAPER_PROTECTION_EXECUTION' ORDER BY id DESC LIMIT 1").fetchone()[0])
    assert payload['reason']=='stop_loss'
    assert_economics(db);db.close()


def test_concurrent_entry_intent_returns_the_same_committed_order(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    db,first=start(tmp_path)
    second_db=PaperDatabase(tmp_path/'protection.sqlite3');second=PaperExecutionEngine(second_db,GlobalRiskGuardian(RiskPolicyConfig()))
    barrier=Barrier(2)
    for engine in [first,second]:
        evaluate=engine.risk.evaluate
        def simultaneous(*args,_evaluate=evaluate,**kwargs):
            result=_evaluate(*args,**kwargs);barrier.wait(timeout=5);return result
        engine.risk.evaluate=simultaneous
    with ThreadPoolExecutor(2) as pool:
        a=pool.submit(open_trade,first,signal());b=pool.submit(open_trade,second,signal())
        assert a.result().order_id==b.result().order_id
    assert len(db.list_orders())==1 and len(db.list_positions('bot'))==1
    assert_economics(db);second_db.close();db.close()


def test_explicit_full_close_retires_durable_protection_state(tmp_path):
    db,engine=start(tmp_path);open_trade(engine,signal())
    position=db.list_positions('bot')[0]
    close=engine.submit_signal_order(bot_id='bot',signal=signal(Direction.SHORT),requested_notional_usd=100,
        leverage=1,snapshot=quote(),client_order_id='full-close')
    assert close.status==OrderStatus.FILLED and not db.list_positions('bot')
    plan=json.loads(db._conn.execute('SELECT value_json FROM risk_state WHERE key=?',(PREFIX+position.position_id,)).fetchone()[0])
    assert plan['closed'] and all(level['remaining_qty']==0 for level in plan['targets'])
    assert_economics(db);db.close()
