import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import AllCapConfig, MidLowCapConfig
from app.core.models import Direction, ExchangeId, MarketRegime, MarketType, StrategySignal
from app.exchanges.registry import ExchangeRegistry
from app.execution.all_cap_runner import AllCapPaperRunner, BANDS, CheckedPublicAdapter
from app.scanner.market_cap import MarketCapBand, MarketCapProvider, classify_market_cap_band
from app.scanner.mid_low_cap_discovery import DynamicSpotMarketDiscoverer
from test_mid_low_cap_autotrade import _make_snapshot, _fresh_cap, _make_trending_candles


def snapshot():
    s = _make_snapshot(depth_usd=1_000_000, spread_bps=6)
    now_ms = int(datetime.now(timezone.utc).timestamp()*1000)
    s.candles_by_tf['1h'] = [c.model_copy(update={'timestamp': now_ms-(90-i)*3600000}) for i,c in enumerate(_make_trending_candles(90))]
    s.ticker_source_timestamp_ms = s.book_source_timestamp_ms = datetime.now(timezone.utc).timestamp()*1000
    s.bids = [(s.bid, 10000)]
    s.asks = [(s.ask, 10000)]
    return s


def signal(s):
    return StrategySignal(strategy_name='trend_following', exchange=s.exchange, symbol=s.symbol,
        market_type=MarketType.SPOT, direction=Direction.LONG, entry_low=19.9, entry_high=20.1,
        stop_loss=19, take_profit_targets=[24], invalidation_condition='stop',
        estimated_fee_pct=.001, estimated_slippage_pct=.0005, risk_reward_ratio=4,
        confidence_score=.8, rationale='synthetic verification fixture')


@pytest.fixture
def runner(tmp_path):
    r = AllCapPaperRunner(tmp_path, ExchangeRegistry(), AllCapConfig(enabled=True))
    yield r
    r.close()


@pytest.mark.parametrize('cap,band', [(999999,'unknown'),(1000000,'micro'),(49999999,'micro'),
    (50000000,'low'),(299999999,'low'),(300000000,'mid'),(10000000000,'mid'),
    (10000000001,'large'),(199999999999,'large'),(200000000000,'mega'),(float('nan'),'unknown')])
def test_boundaries(cap, band):
    assert classify_market_cap_band(cap, AllCapConfig()).value == band
    assert classify_market_cap_band(cap, AllCapConfig(), is_fresh=False) == MarketCapBand.UNKNOWN


def test_policy_validation_and_legacy():
    for kwargs in [dict(low_cap_max_usd=1e9), dict(mega_cap_min_usd=1e8), dict(allow_short=True), dict(max_leverage=2), dict(min_oos_net_expectancy_r=-.1)]:
        with pytest.raises(ValidationError):
            AllCapConfig(**kwargs)
    assert classify_market_cap_band(1e12, MidLowCapConfig()) == MarketCapBand.LARGE
    cfg = AllCapConfig(micro_cap_min_usd=2e6, low_cap_min_usd=60e6, mega_cap_min_usd=300e9)
    assert classify_market_cap_band(55e6, cfg) == MarketCapBand.MICRO
    assert classify_market_cap_band(250e9, cfg) == MarketCapBand.LARGE


def test_stale_invalid_and_liquidity_rejections(runner):
    s = snapshot()
    cap = _fresh_cap('MIDCAP', 1e9, MarketCapBand.MID)
    assert runner.safety_reasons(s, cap) == []
    assert 'stale_market_data' in '|'.join(runner.safety_reasons(s.model_copy(update={'timestamp': s.timestamp-timedelta(minutes=5)}), cap))
    assert 'nonfinite_market_data' in runner.safety_reasons(s.model_copy(update={'atr': float('nan')}), cap)
    assert 'spread_too_wide' in '|'.join(runner.safety_reasons(s.model_copy(update={'spread_bps': 50}), cap))
    assert 'insufficient_depth' in '|'.join(runner.safety_reasons(s.model_copy(update={'order_book_depth_usd': 100}), cap))
    assert 'insufficient_24h_volume' in '|'.join(runner.safety_reasons(s.model_copy(update={'volume_24h_usd': 100}), cap))
    assert 'missing_or_stale_cap_source_timestamp' in runner.safety_reasons(s, cap.model_copy(update={'source_updated_at': None}))
    s.candles_by_tf['15m'] = [c.model_copy(update={'timestamp': c.timestamp-86400000}) for c in s.candles_by_tf['15m']]
    assert 'missing_or_stale_candles:15m' in runner.safety_reasons(s, cap)


def test_sizing_and_sleeve_isolation(runner):
    s = snapshot(); sig = signal(s)
    assert len(runner.db.list_accounts()) == 5
    assert len({runner.bot_id(b) for b in BANDS}) == 5
    values = [runner.size(sig, s, b) for b in BANDS]
    assert values == sorted(values)
    assert values[0] < values[-1]
    assert runner.size(sig, s, MarketCapBand.MID) == runner.size(sig, s, MarketCapBand.MID)
    for update in [dict(stop_loss=21),dict(stop_loss=0),dict(take_profit_targets=[20.1]),dict(direction=Direction.SHORT),dict(estimated_fee_pct=-1)]:
        assert runner.size(sig.model_copy(update=update), s, MarketCapBand.MID) == 0
    order = runner.engine.submit_signal_order(runner.bot_id(MarketCapBand.MID),sig,values[2],1,s,'isolation',True)
    assert order.is_paper and order.leverage == 1 and order.filled_qty > 0
    assert runner.db.list_fills()
    assert not runner.db.list_positions(runner.bot_id(MarketCapBand.MICRO))
    assert runner.size(sig,s,MarketCapBand.MID) <= values[2]
    exit_snap = s.model_copy(update={'last_price': 24.1})
    exits = runner.engine.mark_to_market_and_check_exits({'binance:spot:MIDCAP/USDT':exit_snap})
    assert exits and all(o.is_paper for o in exits)
    assert not runner.db.list_positions()


def test_checked_source_timestamps():
    class Adapter:
        exchange_id = ExchangeId.BINANCE
        async def fetch_ticker(self,*args): return {'timestamp': None}
        async def fetch_order_book(self,*args): return SimpleNamespace(source_timestamp_ms=None)
    checked = CheckedPublicAdapter(Adapter(),30)
    with pytest.raises(ValueError, match='ticker_source'):
        asyncio.run(checked.fetch_ticker('X/USDT',MarketType.SPOT))
    with pytest.raises(ValueError, match='book_source'):
        asyncio.run(checked.fetch_order_book('X/USDT'))


def test_cycle_heartbeat_persistence_and_rejection(tmp_path):
    r = AllCapPaperRunner(tmp_path,ExchangeRegistry(),AllCapConfig(enabled=True))
    async def refresh(force=False): return False
    r.provider.refresh = refresh
    state = asyncio.run(r.run_cycle())
    assert state['cycles'] == 1 and state['completed_at']
    assert state['rejections'][0]['reason'] == 'no_verified_markets'
    r.close()
    r = AllCapPaperRunner(tmp_path,ExchangeRegistry(),AllCapConfig(enabled=True))
    assert r.state['cycles'] == 1
    r.close()


def test_round_robin_discovery_all_caps(tmp_path):
    cfg = AllCapConfig(enabled=True,max_discovered_symbols_per_exchange=5,scan_batch_size=2, market_cap_min_request_interval_sec=0)
    caps = [10e6,100e6,1e9,50e9,1e12]
    async def fetch(page, per_page):
        return [dict(id=f'coin{i}',symbol=f'c{i}',market_cap=cap,circulating_supply=100,
                     last_updated=datetime.now(timezone.utc).isoformat()) for i,cap in enumerate(caps)]
    class Adapter:
        exchange_id=ExchangeId.BINANCE
        async def load_markets(self, **kw):
            return {f'C{i}/USDT':dict(symbol=f'C{i}/USDT',base=f'C{i}',quote='USDT',spot=True,active=True) for i in range(5)}
    provider=MarketCapProvider(cfg,fetch)
    discovery=DynamicSpotMarketDiscoverer(provider,cfg);discovery.target_bands=BANDS
    markets=asyncio.run(discovery.discover_for_exchange(Adapter()))
    assert {m.cap_record.band for m in markets} == set(BANDS)
    assert {m.cap_record.band for m in asyncio.run(discovery.discover_global([Adapter()]))} == set(BANDS)
    first=discovery.next_global_scan_batch(markets)
    second=discovery.next_global_scan_batch(markets)
    assert len(first)==len(second)==2 and not set(m.symbol for m in first)&set(m.symbol for m in second)


def test_regime_selector_preserved(runner):
    assert {s.name for s in runner.selector._allowed_for_regime(MarketRegime.TRENDING_BULLISH)} == {'trend_following','breakout_retest'}
    assert 'mean_reversion' in {s.name for s in runner.selector._allowed_for_regime(MarketRegime.RANGING)}


def test_full_cycle_local_orders_fills_and_restart(tmp_path):
    cfg = AllCapConfig(enabled=True,market_cap_min_request_interval_sec=0)
    async def fetch(page, size):
        return [dict(id='midcap',symbol='midcap',market_cap=1e9,circulating_supply=1e6,
                     last_updated=datetime.now(timezone.utc).isoformat())]
    class Adapter:
        exchange_id=ExchangeId.BINANCE
        credentials=None
        async def get_instrument_spec(self,*args):
            return SimpleNamespace(price_precision=.01,amount_precision=.000001,min_amount=.000001,min_cost=5,maker_fee=.001,taker_fee=.001)
        async def load_markets(self, **kwargs):
            return {'MIDCAP/USDT':dict(symbol='MIDCAP/USDT',base='MIDCAP',quote='USDT',spot=True,active=True)}
    registry=ExchangeRegistry({ExchangeId.BINANCE:Adapter()})
    r=AllCapPaperRunner(tmp_path,registry,cfg,MarketCapProvider(cfg,fetch))
    async def scan(*args,**kwargs): return snapshot()
    r.scanner.scan_pair=scan
    # Synthetic signal isolates orchestration from strategy evidence, tested by legacy selector tests.
    r.selector.select_signals=lambda snaps,caps: ([signal(snaps[0])],[])
    state=asyncio.run(r.run_cycle())
    assert len(state['orders']) == 1 and state['fills'] == 1
    assert state['orders'][0]['order_type'] == 'limit'
    assert state['orders'][0]['is_paper'] and state['orders'][0]['leverage'] == 1
    assert r.db.list_positions()[0].bot_id == r.bot_id(MarketCapBand.MID)
    state=asyncio.run(r.run_cycle())
    assert state['cycles']==2 and not state['orders']
    assert any('concentration' in '|'.join(d.get('reasons',[])) for d in state['rejections'])
    r.close()
    r=AllCapPaperRunner(tmp_path,registry,cfg)
    assert r.state['cycles']==2 and len(r.db.list_fills())==1
    assert len(r.db.list_accounts())==5
    r.close()


def test_credentials_and_production_paths_rejected(tmp_path):
    with pytest.raises(ValueError,match='production_path'):
        AllCapPaperRunner('/root/multi-exchange-paper-audit-fixes',ExchangeRegistry())
    adapter=SimpleNamespace(credentials=object())
    with pytest.raises(ValueError,match='credentials_forbidden'):
        AllCapPaperRunner(tmp_path,ExchangeRegistry({ExchangeId.BINANCE:adapter}))


def test_small_duplicate_ticker_is_not_assumed_canonical():
    from app.execution.all_cap_runner import VioletMarketCapProvider
    cfg=AllCapConfig(market_cap_min_request_interval_sec=0)
    async def fetch(page,size):
        return [dict(id=identity,symbol='dup',market_cap=cap,circulating_supply=100,
                     last_updated=datetime.now(timezone.utc).isoformat())
                for identity,cap in [('canonical',1e12),('collision',1e6)]]
    provider=VioletMarketCapProvider(cfg,fetch)
    asyncio.run(provider.refresh())
    assert provider.classify_symbol('DUP').ambiguous
    assert provider.classify_symbol('DUP').band == MarketCapBand.UNKNOWN


def test_all_cap_daily_breaker_rejects_synthetic_signal(tmp_path):
    cfg=AllCapConfig(enabled=True,market_cap_min_request_interval_sec=0)
    async def fetch(page,size):
        return [dict(id='midcap',symbol='midcap',market_cap=1e9,circulating_supply=1e6,
                     last_updated=datetime.now(timezone.utc).isoformat())]
    class Adapter:
        exchange_id=ExchangeId.BINANCE
        credentials=None
        async def get_instrument_spec(self,*args):
            return SimpleNamespace(price_precision=.01,amount_precision=.000001,min_amount=.000001,min_cost=5,maker_fee=.001,taker_fee=.001)
        async def load_markets(self,**kwargs):
            return {'MIDCAP/USDT':dict(symbol='MIDCAP/USDT',base='MIDCAP',quote='USDT',spot=True,active=True)}
    registry=ExchangeRegistry({ExchangeId.BINANCE:Adapter()})
    r=AllCapPaperRunner(tmp_path,registry,cfg,MarketCapProvider(cfg,fetch))
    bot=r.bot_id(MarketCapBand.MID)
    # Recorded start above current equity deterministically trips the actual sleeve gate.
    key=f'{bot}:daily'
    r.db.save_risk_state(key,{'day':datetime.now(timezone.utc).date().isoformat(),'start':2000,'tripped':False})
    async def scan(*args,**kwargs): return snapshot()
    r.scanner.scan_pair=scan
    r.selector.select_signals=lambda snaps,caps: ([signal(snaps[0])],[])
    state=asyncio.run(r.run_cycle())
    assert any(d.get('reason')=='sleeve_daily_loss_or_position_limit' for d in state['rejections'])
    assert not r.db.list_orders() and not r.db.list_fills()
    assert r.db.load_risk_state(key)['tripped']
    r.close()
    r=AllCapPaperRunner(tmp_path,registry,cfg)
    assert r.db.load_risk_state(key)['tripped']
    r.close()


def test_protective_exit_does_not_require_entry_liquidity_or_cap(runner):
    s=snapshot()
    sig=signal(s)
    bot=runner.bot_id(MarketCapBand.MID)
    order=runner.engine.submit_signal_order(bot,sig,runner.size(sig,s,MarketCapBand.MID),1,s,'synthetic-stop',True)
    assert order.filled_qty > 0
    stressed=s.model_copy(update={'last_price':18.9,'volume_24h_usd':1,'order_book_depth_usd':1})
    cap=_fresh_cap('MIDCAP',1e9,MarketCapBand.MID).model_copy(update={'is_fresh':False})
    assert runner.safety_reasons(stressed,cap)
    assert runner.safe_for_protective_exit(stressed)
    assert not runner.safe_for_protective_exit(stressed.model_copy(update={'ticker_source_timestamp_ms':None}))
    assert not runner.safe_for_protective_exit(stressed.model_copy(update={'last_price':float('nan')}))
    assert runner.engine.mark_to_market_and_check_exits({'binance:spot:MIDCAP/USDT':stressed})
    assert not runner.db.list_positions()


@pytest.mark.asyncio
async def test_instrument_fee_minimum_and_precision_gates(runner):
    s=snapshot(); sig=signal(s)
    class Adapter:
        async def get_instrument_spec(self,*args):
            return self.spec
    adapter=Adapter()
    base=dict(price_precision=.01,amount_precision=.000001,min_amount=.000001,min_cost=5,maker_fee=.001,taker_fee=.001)
    adapter.spec=SimpleNamespace(**base)
    assert not await runner.instrument_reasons(adapter,sig,s,20)
    adapter.spec=SimpleNamespace(**dict(base,taker_fee=.0015))
    assert 'instrument_fees' in (await runner.instrument_reasons(adapter,sig,s,20))[0]
    adapter.spec=SimpleNamespace(**dict(base,min_cost=100))
    assert 'instrument_minimum' in (await runner.instrument_reasons(adapter,sig,s,20))[0]
    adapter.spec=SimpleNamespace(**dict(base,amount_precision=3))
    assert 'instrument_precision' in (await runner.instrument_reasons(adapter,sig,s,20))[0]
