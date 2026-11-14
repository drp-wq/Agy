from types import SimpleNamespace
import pytest
from app.core.models import ExchangeId, MarketType
from app.exchanges.registry import ExchangeRegistry
from app.execution.legacy_preview_protection import protect_legacy_preview
from test_all_cap_paper import snapshot


@pytest.mark.asyncio
async def test_legacy_protection_only_uses_existing_positions_and_fresh_data():
    positions=[SimpleNamespace(exchange=ExchangeId.BINANCE,symbol='MIDCAP/USDT',market_type=MarketType.SPOT),
               SimpleNamespace(exchange=ExchangeId.OKX,symbol='OTHER/USDT',market_type=MarketType.SPOT)]
    scans=[];exits=[]
    async def scan(adapter,symbol,market_type,all_tfs):
        scans.append(symbol)
        return snapshot() if symbol=='MIDCAP/USDT' else None
    service=SimpleNamespace(db=SimpleNamespace(list_positions=lambda:positions),
        scanner=SimpleNamespace(scan_pair=scan),
        paper_engine=SimpleNamespace(mark_to_market_and_check_exits=lambda snaps:exits.append(list(snaps))))
    registry=ExchangeRegistry({ExchangeId.BINANCE:object(),ExchangeId.OKX:object()})
    state=await protect_legacy_preview(service,registry,lambda snap:True)
    assert sorted(scans)==['MIDCAP/USDT','OTHER/USDT']
    assert exits==[['binance:spot:MIDCAP/USDT']]
    assert state['mode']=='PROTECTIVE_EXITS_ONLY' and state['fresh_instruments']==1
    assert state['failures']==['okx:OTHER/USDT']
