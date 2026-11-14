"""Deterministic time-bound campaign and public metadata persistence tests."""
import asyncio
import json
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from app.core.config import AllCapConfig
from app.core.models import ExchangeId, MarketType, OrderType
from app.exchanges.registry import ExchangeRegistry
from app.execution.all_cap_runner import AllCapPaperRunner, BANDS
from app.execution.paper_campaign import PaperCampaign
from app.execution.paper_engine import EXCHANGE_FEE_SCHEDULE
from app.scanner.market_cap import MarketCapProvider
from test_all_cap_paper import snapshot, signal


def test_campaign_deadline_survives_restart_and_cannot_rearm(tmp_path):
    r=AllCapPaperRunner(tmp_path,ExchangeRegistry(),AllCapConfig(enabled=True),campaign_hours=72)
    initial=r.campaign.status()
    assert initial['status']=='active'
    assert datetime.fromisoformat(initial['ends_at'])-datetime.fromisoformat(initial['started_at'])==timedelta(hours=72)
    deadline=datetime.fromisoformat(initial['ends_at'])
    r.campaign.clock=lambda:deadline-timedelta(microseconds=1)
    assert r.campaign.entries_allowed()
    r.campaign.clock=lambda:deadline
    assert not r.campaign.entries_allowed()
    asyncio.run(r.run_cycle())
    assert not r.db.list_orders() and r.campaign.status()['status']=='completed'
    assert json.loads((tmp_path/'72H_RUN_REPORT.json').read_text())['status']=='completed'
    r.close()
    r=AllCapPaperRunner(tmp_path,ExchangeRegistry(),AllCapConfig(enabled=True),campaign_hours=72)
    assert r.campaign.record['started_at']==initial['started_at']
    assert r.campaign.record['ends_at']==initial['ends_at']
    r.close()


def test_expiry_keeps_protective_exits_and_rejects_backwards_clock(tmp_path):
    r=AllCapPaperRunner(tmp_path,ExchangeRegistry(),AllCapConfig(enabled=True),campaign_hours=72)
    s=snapshot();sig=signal(s);bot=r.bot_id(BANDS[2])
    order=r.engine.submit_signal_order(bot,sig,r.size(sig,s,BANDS[2]),1,s,'synthetic-campaign',True)
    assert order.filled_qty>0
    end=datetime.fromisoformat(r.campaign.record['ends_at'])
    r.campaign.clock=lambda:end
    assert r.campaign.status()['status']=='protective_exits_only'
    assert not r.campaign.entries_allowed()
    exits=r.engine.mark_to_market_and_check_exits({'binance:spot:MIDCAP/USDT':s.model_copy(update={'last_price':18.9})})
    assert exits and r.campaign.status()['status']=='completed'
    start=datetime.fromisoformat(r.campaign.record['started_at'])
    r.campaign.clock=lambda:start-timedelta(seconds=1)
    assert not r.campaign.entries_allowed()
    r.close()


@pytest.mark.asyncio
async def test_cap_cache_restart_provenance_staleness_and_failure_backoff(tmp_path):
    stamp=datetime.now(timezone.utc).isoformat()
    calls=[]
    async def fetch(page,size):
        calls.append(page)
        return [dict(id='fixture',symbol='x',market_cap=1e9,circulating_supply=1e6,last_updated=stamp)]
    cache=tmp_path/'cache.json';cfg=AllCapConfig(market_cap_min_request_interval_sec=0)
    provider=MarketCapProvider(cfg,fetch,cache_path=cache)
    assert await provider.refresh()
    original=provider.classify_symbol('X')
    restored=MarketCapProvider(cfg,fetch,cache_path=cache)
    assert restored.cache_restored and await restored.refresh()
    assert len(calls)==1
    assert restored.classify_symbol('X').source_updated_at==original.source_updated_at
    assert restored.classify_symbol('X').fetched_at==original.fetched_at
    assert not restored.classify_symbol('X',datetime.now(timezone.utc)+timedelta(hours=2)).is_fresh
    async def fail(page,size): raise OSError('synthetic outage')
    failed=MarketCapProvider(cfg,fail,cache_path=cache)
    assert not await failed.refresh(force=True)
    again=MarketCapProvider(cfg,fail,cache_path=cache)
    assert again.status_summary()['retry_after_sec']>55
    assert again.classify_symbol('X').source_updated_at==original.source_updated_at
    cache.write_text('not JSON')
    invalid=MarketCapProvider(cfg,fetch,cache_path=cache)
    assert not invalid.classify_symbol('X').is_fresh


@pytest.mark.asyncio
async def test_instance_fee_model_applies_to_sizing_fills_exits_and_restart(tmp_path):
    r=AllCapPaperRunner(tmp_path,ExchangeRegistry(),AllCapConfig(enabled=True))
    class Adapter:
        exchange_id=ExchangeId.OKX
        async def get_instrument_spec(self,*args):
            return SimpleNamespace(maker_fee=.001,taker_fee=.0015)
    await r.prepare_fees(Adapter(),'MIDCAP/USDT')
    key=(ExchangeId.OKX,MarketType.SPOT,'MIDCAP/USDT')
    assert r.engine.fee_schedule is r.selector.fee_schedule
    assert r.engine.fee_schedule[key]['taker']==.0015
    assert EXCHANGE_FEE_SCHEDULE[(ExchangeId.OKX,MarketType.SPOT)]['taker']==.001
    s=snapshot().model_copy(update={'exchange':ExchangeId.OKX})
    sig=signal(s).model_copy(update={'estimated_fee_pct':.0015})
    notional=r.size(sig,s,BANDS[2])
    cheaper=r.size(sig.model_copy(update={'estimated_fee_pct':.001}),s,BANDS[2])
    assert notional<cheaper
    order=r.engine.submit_signal_order(r.bot_id(BANDS[2]),sig,notional,1,s,'fee-fixture',True,order_type=OrderType.LIMIT,limit_price=sig.entry_high)
    fills=r.db.list_fills()
    assert order.filled_qty>0
    assert abs(order.fee_usd-sum(f.price*f.qty*.0015 for f in fills))<.000002
    r.close()
    r=AllCapPaperRunner(tmp_path,ExchangeRegistry(),AllCapConfig(enabled=True))
    assert r.engine.fee_schedule[key]['taker']==.0015
    exits=r.engine.mark_to_market_and_check_exits({'okx:spot:MIDCAP/USDT':s.model_copy(update={'last_price':24.1})})
    assert len(exits)==1
    assert abs(exits[0].fee_usd-exits[0].filled_qty*exits[0].avg_fill_price*.0015)<.000002
    r.close()
