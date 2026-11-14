"""Timestamp provenance and fail-closed freshness regression tests."""
from datetime import datetime, timezone

import pytest
import ccxt

from app.core.models import MarketType
from app.exchanges.bybit import BybitAdapter
from app.exchanges.timestamps import source_timestamp_ms
from app.execution.all_cap_runner import source_age, AllCapPaperRunner
from app.exchanges.registry import ExchangeRegistry


@pytest.mark.parametrize('value,expected', [(1700000000,1700000000000),('1700000000.125',1700000000125),
    (1700000000123,1700000000123),(None,None),('',None),(True,None),(-1,None),
    (float('nan'),None),(float('inf'),None),(1700000000000000,None),('bad',None)])
def test_timestamp_units(value, expected):
    assert source_timestamp_ms(value) == expected


@pytest.mark.parametrize('offset,valid', [(0,True),(30,True),(31,False),(-5,True),(-6,False), (3600,False),(-3600,False)])
def test_stale_future_and_clock_skew(offset, valid):
    now = datetime(2026,11,13,tzinfo=timezone.utc)
    assert source_age((now.timestamp()-offset)*1000,now,30) is valid
    assert source_age(now.timestamp()-offset,now,30) is valid


@pytest.mark.asyncio
@pytest.mark.parametrize('timestamp',[None,1700000000,1700000000123])
async def test_bybit_uses_same_response_envelope(timestamp):
    adapter = BybitAdapter()
    client = adapter._public_client
    market = {'id':'BTCUSDT','symbol':'BTC/USDT','base':'BTC','quote':'USDT','spot':True,'type':'spot'}
    client.markets = {'BTC/USDT':market}
    adapter._markets_loaded = True
    client.publicGetV5MarketTickers = lambda request: {'retCode':0,'time':timestamp,
        'result':{'category':'spot','list':[{'symbol':'BTCUSDT','lastPrice':'20','bid1Price':'19','ask1Price':'21','volume24h':'10','turnover24h':'200'}]}}
    ticker = await adapter.fetch_ticker('BTC/USDT',MarketType.SPOT)
    assert ticker['last'] == 20
    assert ticker['timestamp'] == source_timestamp_ms(timestamp)
    assert ticker['source_timestamp_kind'] == 'bybit_v5_response_time'
    adapter.close()


def test_worker_exclusion(tmp_path):
    runner = AllCapPaperRunner(tmp_path,ExchangeRegistry())
    try:
        with pytest.raises(RuntimeError, match='already_active'):
            AllCapPaperRunner(tmp_path,ExchangeRegistry())
    finally:
        runner.close()
    other = AllCapPaperRunner(tmp_path,ExchangeRegistry())
    other.close()

@pytest.mark.asyncio
async def test_cap_rate_limit_backoff_preserves_rejection():
    import httpx
    from app.scanner.market_cap import MarketCapProvider
    calls=[]
    async def limited(page,size):
        calls.append(page)
        response=httpx.Response(429,headers={'Retry-After':'180'},request=httpx.Request('GET','https://example.test'))
        raise httpx.HTTPStatusError('rate limited',request=response.request,response=response)
    provider=MarketCapProvider(page_fetcher=limited)
    assert not await provider.refresh()
    assert not await provider.refresh(force=True)
    assert len(calls)==1
    assert provider.status_summary()['retry_after_sec'] > 175
    assert not provider.classify_symbol('BTC').is_fresh
