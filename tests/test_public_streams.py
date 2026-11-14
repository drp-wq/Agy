"""Synthetic protocol fixtures. No private API, network or exchange orders."""
import json
from types import SimpleNamespace
import pytest
import ccxt
from app.exchanges.public_streams import BinanceDepth, binance_timestamped_book, gate_timestamped_ticker
from app.exchanges.gateio import GateIOAdapter
from app.exchanges.registry import ExchangeRegistry
from app.core.models import ExchangeId, MarketType
from app.core.security import LiveExecutionBlockedError


def snapshot():
    return {'nonce':100, 'bids':[[99,10],[98,10],[97,10]],'asks':[[101,10],[102,10],[103,10]]}


def event(**updates):
    return dict({'e':'depthUpdate','E':1794607273874,'s':'BTCUSDT','U':101,'u':102,'b':[['99','11']],'a':[['101','0']]},**updates)


def test_binance_sequence_sync_deletions_and_timestamp():
    book=BinanceDepth(snapshot(),'BTCUSDT')
    assert book.apply(event(U=99,u=100),2) is None
    result=book.apply(event(),2)
    assert result['bids'][0]==(99,11)
    assert result['asks'][0]==(102,10)
    assert result['timestamp']==1794607273874 and result['nonce']==102
    assert book.apply(event(U=103,u=104,a=[]),2)['nonce']==104


@pytest.mark.parametrize('updates', [dict(s='ETHUSDT'),dict(e='bookTicker'),dict(U=105,u=106),
    dict(U=103,u=102),dict(E=None),dict(E=float('nan')),dict(b=[['99','-1']]),dict(a=[['90','5']]),
    dict(a=[['101','0'],['102','0'],['103','0']])])
def test_binance_invalid_or_unsynced_data_rejected(updates):
    with pytest.raises(ValueError):
        BinanceDepth(snapshot(),'BTCUSDT').apply(event(**updates),2)


class Socket:
    def __init__(self, messages): self.messages=iter(messages); self.sent=[]
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def recv(self,timeout): return json.dumps(next(self.messages))
    def send(self,message): self.sent.append(json.loads(message))


def test_binance_connects_before_rest_snapshot_and_never_sends_messages():
    sock=Socket([event(U=99,u=100),event()])
    sequence=[]
    def connect(uri,**kwargs):
        assert uri=='wss://stream.binance.com:443/ws/btcusdt@depth@100ms'
        sequence.append('connect');return sock
    client=SimpleNamespace(market=lambda s:{'id':'BTCUSDT'})
    def fetch(symbol,limit): sequence.append('snapshot');return snapshot()
    client.fetch_order_book=fetch
    result=binance_timestamped_book(client,'BTC/USDT',2,connect)
    assert sequence==['connect','snapshot'] and not sock.sent
    assert result['timestamp']==1794607273874


def gate_client():
    return SimpleNamespace(market=lambda s:{'id':'BTC_USDT'},
        parse_ticker=lambda row,m:{'last':float(row['last']),'symbol':'BTC/USDT'},iso8601=lambda ts:None)


def gate_event(**updates):
    return dict({'event':'update','channel':'spot.tickers','time_ms':1794607273874,
                 'result':{'currency_pair':'BTC_USDT','last':'20'}},**updates)


def test_gate_ticker_timestamp_and_public_only_subscription():
    sock=Socket([{'event':'subscribe'},gate_event()])
    result=gate_timestamped_ticker(gate_client(),'BTC/USDT',lambda *args,**kwargs:sock)
    assert result['timestamp']==1794607273874 and result['last']==20
    assert len(sock.sent)==1
    assert sock.sent[0]['channel']=='spot.tickers' and sock.sent[0]['event']=='subscribe'
    assert 'auth' not in sock.sent[0]


@pytest.mark.parametrize('updates',[dict(time_ms=None),dict(channel='spot.orders'),
    dict(result={'currency_pair':'OTHER_USDT','last':'20'}),dict(error={'code':1})])
def test_gate_untrusted_ticker_rejected(updates):
    sock=Socket([gate_event(**updates)])
    with pytest.raises(ValueError):
        gate_timestamped_ticker(gate_client(),'BTC/USDT',lambda *args,**kwargs:sock)


def test_lab_registry_credentials_and_write_lock():
    registry=ExchangeRegistry.violet_public()
    try:
        assert set(registry.all())==set(ExchangeId)
        assert all(a.credentials is None for a in registry.all().values())
        for adapter in registry.all().values():
            with pytest.raises(LiveExecutionBlockedError):
                adapter._public_client.create_order('BTC/USDT','market','buy',1)
            with pytest.raises(LiveExecutionBlockedError):
                adapter._public_client.request('orders', 'private', 'POST')
    finally: registry.close_all()
    with pytest.raises(ValueError,match='credentials_forbidden'):
        GateIOAdapter(credentials=object())


@pytest.mark.asyncio
async def test_gate_book_uses_update_timestamp_and_missing_stays_missing():
    adapter=GateIOAdapter()
    adapter._markets_loaded=True
    adapter._public_client.market=lambda norm:{'id':'BTC_USDT'}
    response={'id':100,'update':1794607273874,'current':1794607273999,'bids':[[19,3]],'asks':[[21,4]]}
    adapter._public_client.publicSpotGetOrderBook=lambda request:response
    book=await adapter.fetch_order_book('BTC/USDT',MarketType.SPOT)
    assert book.source_timestamp_ms==1794607273874
    response.pop('update')
    book=await adapter.fetch_order_book('BTC/USDT',MarketType.SPOT)
    assert book.source_timestamp_ms is None
    adapter.close()
