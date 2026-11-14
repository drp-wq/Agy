"""Read-only history pagination, timestamp policy, worker recovery and entry gate."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.config import RiskPolicyConfig, UnifiedTreasuryConfig
from app.core.models import (Direction, ExchangeId, MarketType, OrderStatus, PaperOrder, Side,
                             MarketSnapshot, MarketRegime, StrategySignal)
from app.execution.paper_engine import PaperExecutionEngine
from app.exchanges.funding_history import FundingEvidenceBlocked, fetch_funding_evidence, milliseconds
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.treasury.funding_worker import PaperFundingWorker
from app.treasury.ledger import UnifiedTreasuryLedger

T=datetime(2026,11,14,8,tzinfo=timezone.utc)


def row(at,rate='.001',mark='100'):
    return dict(symbol='BTCUSDT',fundingTime=milliseconds(at),fundingRate=rate,markPrice=mark)


class Adapter:
    exchange_id=ExchangeId.BINANCE
    def __init__(self,records):
        self.records=records; self.requests=[]; self.failure=None; self.transform=lambda x:x
        self._public_client=SimpleNamespace(fapiPublicGetFundingRate=self.get)
    def get(self,params):
        self.requests.append(dict(params))
        if self.failure:raise self.failure
        selected=[r for r in self.records if params['startTime']<=r['fundingTime']<=params['endTime']]
        return self.transform(selected[:params['limit']])
    async def load_markets(self):
        return {'BTC/USDT:USDT':{'swap':True,'linear':True,'settle':'USDT','id':'BTCUSDT'}}
    def normalize_symbol(self,symbol,market):return symbol+':USDT'
    async def _run(self,fn,name):return fn()


async def fetch(adapter,**kwargs):
    return await fetch_funding_evidence(adapter,symbol='BTC/USDT',**(
        dict(coverage_from=T-timedelta(seconds=1),until=T+timedelta(hours=9)) | kwargs))


@pytest.mark.asyncio
async def test_paginated_exhaustion_preserves_native_milliseconds_and_interval():
    a=Adapter([row(T-timedelta(hours=8)),row(T+timedelta(milliseconds=1)),row(T+timedelta(hours=8,milliseconds=4))])
    result=await fetch(a,page_size=2)
    assert result.pages==2 and result.rows==3 and len(result.events)==2
    assert result.events[0].at==T+timedelta(milliseconds=1)
    assert result.events[1].interval_seconds==28800.003
    assert result.events[1].interval_evidence['previous_settlement_record']['fundingTime']==milliseconds(T)+1
    assert a.requests[1]['startTime']==milliseconds(T)+2
    assert all(p['symbol']=='BTCUSDT' for p in a.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['truncated','symbol','rate','mark','duplicates','out_of_order','ignored_start','missing_predecessor','inverse','unsupported'])
async def test_incomplete_or_invalid_evidence_blocks_before_posting(kind):
    a=Adapter([row(T-timedelta(hours=8)),row(T),row(T+timedelta(hours=8))])
    kwargs={}
    if kind=='truncated':kwargs=dict(page_size=1,max_pages=1)
    if kind=='symbol':a.records[1]['symbol']='ETHUSDT'
    if kind=='rate':a.records[1]['fundingRate']='NaN'
    if kind=='mark':a.records[1]['markPrice']='0'
    if kind=='duplicates':a.transform=lambda rows:rows+[rows[-1]]
    if kind=='out_of_order':a.transform=lambda rows:list(reversed(rows))
    if kind=='ignored_start':a.transform=lambda rows:[row(T-timedelta(days=8))]+rows
    if kind=='missing_predecessor':a.records=a.records[1:]
    if kind=='inverse':
        async def markets():return {'BTC/USDT:USDT':{'swap':True,'linear':False,'settle':'BTC','id':'BTCUSD'}}
        a.load_markets=markets
    if kind=='unsupported':a.exchange_id=ExchangeId.OKX
    with pytest.raises(FundingEvidenceBlocked):await fetch(a,**kwargs)


@pytest.mark.asyncio
async def test_empty_response_is_empty_history_not_a_zero_cash_posting():
    result=await fetch(Adapter([]))
    assert result.events==() and result.rows==0 and result.pages==1


@pytest.mark.asyncio
async def test_worker_empty_history_is_not_entry_readiness(tmp_path):
    db,engine,treasury=database(tmp_path)
    clock=[T+timedelta(minutes=1)]
    w=worker(db,treasury,Adapter([]),clock,probe_keys={('bot','binance','BTC/USDT')})
    report=await w.run_once()
    assert report['status']=='BLOCKED'
    assert report['keys']['bot:binance:BTC/USDT']['reason']=='no_historical_funding_context'
    assert not w.can_increase('bot','binance','BTC/USDT')
    assert db.get_account('bot')['cash_usd']==500 and not db.list_ledger_events()
    db.close()


def database(tmp_path):
    db=PaperDatabase(tmp_path/'worker.sqlite3');db.ensure_account('bot','binance',500)
    engine=PaperExecutionEngine(db,GlobalRiskGuardian(RiskPolicyConfig()))
    treasury=UnifiedTreasuryLedger(db,UnifiedTreasuryConfig(enabled=True))
    return db,engine,treasury


def execute(db,engine,name,qty,side,at):
    o=PaperOrder(order_id=name,client_order_id=name,bot_id='bot',signal_id=name,
        exchange=ExchangeId.BINANCE,symbol='BTC/USDT',market_type=MarketType.PERP,
        side=side,requested_qty=qty,filled_qty=qty,avg_fill_price=100,fee_usd=qty*.1,
        leverage=2,status=OrderStatus.FILLED,created_at=at,updated_at=at,is_paper=True)
    with db.transaction() as conn:
        db.save_order(o,conn=conn);engine._apply_fill_to_position_and_account(o,100,conn=conn)
    return o


def worker(db,treasury,adapter,clock,**kwargs):
    registry=SimpleNamespace(get=lambda key:adapter)
    return PaperFundingWorker(db,treasury,registry,clock=lambda:clock[0],**kwargs)


@pytest.mark.asyncio
async def test_real_provider_worker_recovers_closed_exposure_and_missed_native_events(tmp_path):
    db,engine,treasury=database(tmp_path)
    event=T+timedelta(milliseconds=1)
    # This opening occurs after the nominal 08:00 schedule but before the
    # official native event. The documented paper model uses native event time.
    execute(db,engine,'open',2,Side.BUY,T+timedelta(microseconds=500))
    execute(db,engine,'close',2,Side.SELL,event)
    cash=db.get_account('bot')['cash_usd']
    a=Adapter([row(T-timedelta(hours=8)),row(event),row(T+timedelta(hours=8,milliseconds=4))])
    clock=[T+timedelta(hours=9)]
    w=worker(db,treasury,a,clock)
    report=await w.run_once()
    state=report['keys']['bot:binance:BTC/USDT']
    assert state['posted']==2 and state['status']=='VERIFIED_CAPTURED_RANGE'
    assert db.get_account('bot')['cash_usd']==pytest.approx(cash-.2)
    assert len(db.list_ledger_events())==2
    assert db.list_positions()==[]
    assert w.can_increase('bot',ExchangeId.BINANCE,'BTC/USDT')
    db.close();db,engine,treasury=database(tmp_path)
    restarted=worker(db,treasury,a,clock)
    assert not restarted.can_increase('bot','binance','BTC/USDT')
    report=await restarted.run_once()
    assert report['keys']['bot:binance:BTC/USDT']['duplicates']==2
    assert db.get_account('bot')['cash_usd']==pytest.approx(cash-.2)
    # Periodic replay catches a delayed publication earlier than the last query.
    a.records.insert(2,row(T+timedelta(hours=4),rate='-.002'))
    # Preserve accepted settlement records; append new observed predecessor
    # evidence without repeating the existing event's cash effect.
    clock[0]+=timedelta(seconds=121)
    report=await restarted.run_once()
    assert report['status']=='VERIFIED_CAPTURED_RANGE'
    assert report['keys']['bot:binance:BTC/USDT']['posted']==1
    assert report['keys']['bot:binance:BTC/USDT']['duplicates']==2
    assert db._conn.execute('SELECT COUNT(*) FROM paper_funding_interval_observations').fetchone()[0]==4
    assert db.get_account('bot')['cash_usd']==pytest.approx(cash-.2)
    db.close()


@pytest.mark.asyncio
async def test_network_failure_throttle_and_stale_green_gate(tmp_path):
    db,engine,treasury=database(tmp_path)
    a=Adapter([row(T-timedelta(hours=8))]);clock=[T+timedelta(minutes=1)]
    w=worker(db,treasury,a,clock,probe_keys={('bot','binance','BTC/USDT')})
    assert not w.can_increase('bot','binance','BTC/USDT')
    await w.run_once()
    assert w.can_increase('bot','binance','BTC/USDT')
    calls=len(a.requests)
    await w.run_once();assert len(a.requests)==calls
    clock[0]+=timedelta(seconds=241)
    assert not w.can_increase('bot','binance','BTC/USDT')
    a.failure=RuntimeError('upstream sensitive details must not escape')
    report=await w.run_once()
    assert report['status']=='BLOCKED' and 'sensitive' not in str(report)
    assert not w.can_increase('bot','binance','BTC/USDT')
    assert db.list_ledger_events()==[]
    db.close()


@pytest.mark.asyncio
async def test_legacy_uncaptured_order_blocks_new_exposure_without_reset(tmp_path):
    db,engine,treasury=database(tmp_path)
    # Historical source orders are preserved; capture is intentionally absent.
    order=PaperOrder(order_id='legacy',client_order_id='legacy',bot_id='bot',signal_id='legacy',
        exchange=ExchangeId.BINANCE,symbol='BTC/USDT',market_type=MarketType.PERP,
        side=Side.BUY,requested_qty=1,filled_qty=1,avg_fill_price=100,fee_usd=.1,status=OrderStatus.FILLED)
    db.save_order(order)
    a=Adapter([row(T-timedelta(hours=8))]);clock=[T+timedelta(minutes=1)]
    w=worker(db,treasury,a,clock,probe_keys={('bot','binance','BTC/USDT')})
    before=dict(db.get_account('bot'))
    report=await w.run_once()
    assert report['status']=='BLOCKED'
    assert report['keys']['bot:binance:BTC/USDT']['unmapped_historical_orders']==1
    assert not w.can_increase('bot','binance','BTC/USDT')
    assert db.get_account('bot')==before
    db.close()


def test_execution_gate_blocks_entry_and_reversal_but_preserves_reductions_and_protection(tmp_path):
    db,engine,treasury=database(tmp_path)
    def quote(price=100):
        return MarketSnapshot(exchange=ExchangeId.BINANCE,symbol='BTC/USDT',market_type=MarketType.PERP,
            timestamp=datetime.now(timezone.utc),last_price=price,bid=price,ask=price,spread_bps=2,
            volume_24h_usd=1e8,order_book_depth_usd=100000,liquidity_score=1,
            regime=MarketRegime.TRENDING_BULLISH,bids=[(price,100)],asks=[(price,100)])
    def signal(direction=Direction.LONG):
        return StrategySignal(strategy_name='funding_gate_fixture',exchange=ExchangeId.BINANCE,
            symbol='BTC/USDT',market_type=MarketType.PERP,direction=direction,entry_low=99,entry_high=101,
            stop_loss=90 if direction==Direction.LONG else 110,
            take_profit_targets=[120] if direction==Direction.LONG else [80],
            invalidation_condition='synthetic stop',estimated_fee_pct=.001,estimated_slippage_pct=0,
            risk_reward_ratio=2,confidence_score=.8,rationale='isolated gate fixture')
    def submit(name,qty=1,direction=Direction.LONG):
        return engine.submit_signal_order('bot',signal(direction),qty*100,2,quote(),name)
    engine.funding_gate=lambda *args:False
    assert submit('blocked').reject_reason=='funding_evidence_unavailable_for_new_exposure'
    engine.funding_gate=lambda *args:True
    opened=submit('open')
    assert opened.status==OrderStatus.FILLED,opened.reject_reason
    engine.funding_gate=lambda *args:False
    assert submit('reversal',2,Direction.SHORT).status==OrderStatus.REJECTED
    assert submit('reduce',.5,Direction.SHORT).status==OrderStatus.FILLED
    assert db.list_positions('bot')[0].qty==.5
    stopped=engine.mark_to_market_and_check_exits({'binance:perp:BTC/USDT':quote(89)})
    assert len(stopped)==1 and not db.list_positions('bot')
    db.close()


@pytest.mark.asyncio
async def test_worker_failure_after_one_event_recovers_remaining_without_reposting(tmp_path,monkeypatch):
    import app.treasury.funding_worker as module
    db,engine,treasury=database(tmp_path)
    execute(db,engine,'open',2,Side.SELL,T-timedelta(seconds=1))
    a=Adapter([row(T-timedelta(hours=8)),row(T),row(T+timedelta(hours=8),rate='-.002')])
    clock=[T+timedelta(hours=9)];w=worker(db,treasury,a,clock)
    cash=db.get_account('bot')['cash_usd']
    original=module.settle_captured_funding
    def interrupted(*args,**kwargs):
        if kwargs['settlement_at']>T:raise RuntimeError('simulated worker interruption')
        return original(*args,**kwargs)
    monkeypatch.setattr(module,'settle_captured_funding',interrupted)
    report=await w.run_once()
    assert report['status']=='BLOCKED'
    assert db.get_account('bot')['cash_usd']==pytest.approx(cash+.2)
    assert len(db.list_ledger_events())==1
    monkeypatch.setattr(module,'settle_captured_funding',original)
    report=await w.run_once(force=True)
    assert report['status']=='VERIFIED_CAPTURED_RANGE'
    state=report['keys']['bot:binance:BTC/USDT']
    assert state['posted']==1 and state['duplicates']==1
    assert db.get_account('bot')['cash_usd']==pytest.approx(cash+.2-.4)
    assert len(db.list_ledger_events())==2
    db.close()


@pytest.mark.asyncio
async def test_late_nonzero_event_appends_without_rewriting_prior_settlement(tmp_path):
    db,engine,treasury=database(tmp_path)
    execute(db,engine,'open',1,Side.BUY,T-timedelta(seconds=1))
    a=Adapter([row(T-timedelta(hours=8)),row(T),row(T+timedelta(hours=8))])
    clock=[T+timedelta(hours=9)];w=worker(db,treasury,a,clock)
    cash=db.get_account('bot')['cash_usd']
    await w.run_once()
    original=[dict(r) for r in db._conn.execute('SELECT * FROM paper_funding_settlements ORDER BY settlement_id')]
    a.records.insert(2,row(T+timedelta(hours=4),rate='-.002'))
    report=await w.run_once(force=True)
    assert report['status']=='VERIFIED_CAPTURED_RANGE'
    assert db.get_account('bot')['cash_usd']==pytest.approx(cash-.1+.2-.1)
    current={r['settlement_id']:dict(r) for r in db._conn.execute('SELECT * FROM paper_funding_settlements')}
    assert all(current[r['settlement_id']]==r for r in original)
    assert db._conn.execute('SELECT COUNT(*) FROM paper_funding_interval_observations').fetchone()[0]==4
    # An actual rate correction is economically different and must block.
    a.records[-1]['fundingRate']='.004'
    report=await w.run_once(force=True)
    assert report['status']=='BLOCKED'
    assert db.get_account('bot')['cash_usd']==pytest.approx(cash)
    db.close()


@pytest.mark.asyncio
async def test_service_cycle_posts_funding_before_scanning_and_exposes_status(tmp_path):
    from unittest.mock import AsyncMock
    from app.core.config import EngineConfig, LoadedEnvironment
    from app.service import TradingEngineService
    cfg=EngineConfig(db_path=tmp_path/'service.sqlite3',log_dir=tmp_path/'logs',
                     unified_treasury=UnifiedTreasuryConfig(enabled=True))
    cfg.mid_low_cap.enabled=False
    service=TradingEngineService(env=LoadedEnvironment(config=cfg),enable_telegram=False)
    service.db.ensure_account('bot','binance',500)
    execute(service.db,service.paper_engine,'open',1,Side.BUY,T-timedelta(seconds=1))
    a=Adapter([row(T-timedelta(hours=8)),row(T)])
    w=service.funding_worker
    w.registry=SimpleNamespace(get=lambda key:a)
    w.clock=lambda:T+timedelta(minutes=2)
    w.probe_keys=set()
    async def scan(**kwargs):
        # Real cycle ordering: funding has posted before market data/signals.
        assert service.db.get_account('bot')['cash_usd']==pytest.approx(499.8)
        return []
    service.scanner.scan_all=scan
    service.orchestrator.generate_signals=lambda snapshots:[]
    service.bot_manager.process_signals_concurrently=AsyncMock(return_value=[])
    result=await service.run_cycle()
    assert result['funding']['status']=='VERIFIED_CAPTURED_RANGE'
    assert service.state_snapshot()['paper_funding']==result['funding']
    assert service.paper_engine.funding_gate.__self__ is w
    await service.shutdown()


@pytest.mark.asyncio
async def test_cancellation_clears_readiness_and_recovers_without_cash_mutation(tmp_path):
    import asyncio
    db,engine,treasury=database(tmp_path)
    a=Adapter([row(T-timedelta(hours=8))]);clock=[T+timedelta(minutes=1)]
    w=worker(db,treasury,a,clock,probe_keys={('bot','binance','BTC/USDT')})
    await w.run_once()
    assert w.can_increase('bot','binance','BTC/USDT')
    entered=asyncio.Event()
    async def hanging_provider(*args,**kwargs):
        entered.set()
        await asyncio.Event().wait()
    w.provider=hanging_provider
    task=asyncio.create_task(w.run_once(force=True))
    await entered.wait()
    assert not w.can_increase('bot','binance','BTC/USDT')
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert db.get_account('bot')['cash_usd']==500 and not db.list_ledger_events()
    w.provider=fetch_funding_evidence
    await w.run_once(force=True)
    assert w.can_increase('bot','binance','BTC/USDT')
    db.close()
