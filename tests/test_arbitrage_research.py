from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.dashboard.arbitrage_research as arb_mod
from app.dashboard.arbitrage_research import (
    MIN_NET_SPREAD_PCT,
    clear_arbitrage_cache,
    evaluate_pair,
    scan_public_books,
)
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.violet_status import install_violet_status


def test_unverified_costs_never_mark_executable():
    r = evaluate_pair({'exchange': 'binance', 'ask': 100}, {'exchange': 'bybit', 'bid': 102})
    assert r['net_spread_pct'] is None
    assert not r['candidate']
    assert not r['executable']
    assert r['gross_spread_bps'] == 200.0
    assert 'fees_unverified' in r['blockers']
    assert 'slippage_unverified' in r['blockers']
    assert 'inventory_not_verified' in r['blockers']
    assert 'no_order_routing' in r['blockers']


def test_verified_costs_threshold_and_no_execution():
    r = evaluate_pair(
        {'exchange': 'binance', 'ask': 100, 'freshness': 'fresh', 'age_seconds': 1.2},
        {'exchange': 'bybit', 'bid': 101, 'freshness': 'fresh', 'age_seconds': 2.0},
        fee_buy_pct=0.1,
        fee_sell_pct=0.1,
        slippage_pct=0.1,
    )
    assert r['net_spread_pct'] == 0.7
    assert r['candidate']
    assert not r['executable']
    assert r['quote_age_diff_seconds'] == 0.8


def test_below_threshold_rejected():
    r = evaluate_pair(
        {'exchange': 'binance', 'ask': 100},
        {'exchange': 'bybit', 'bid': 100.3},
        fee_buy_pct=0.1,
        fee_sell_pct=0.1,
        slippage_pct=0.1,
    )
    assert not r['candidate']
    assert MIN_NET_SPREAD_PCT == 0.35


def test_scan_public_books_error_isolation_freshness_and_no_credentials(monkeypatch):
    clear_arbitrage_cache()
    created_configs = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    stale_ms = int((datetime.now(timezone.utc) - timedelta(seconds=45)).timestamp() * 1000)

    class FakeExchange:
        def __init__(self, ex_id, cfg):
            self.ex_id = ex_id
            created_configs.append((ex_id, cfg))

        async def fetch_order_book(self, symbol, limit=5):
            assert symbol == 'BTC/USDT'
            assert limit == 5
            if self.ex_id == 'binance':
                return {'timestamp': now_ms - 1000, 'asks': [[65000.0, 0.5]], 'bids': [[64995.0, 0.8]]}
            if self.ex_id == 'bybit':
                return {'timestamp': stale_ms, 'asks': [[65100.0, 0.4]], 'bids': [[65090.0, 0.6]]}
            raise TimeoutError('okx public timeout')

        async def close(self):
            return None

    monkeypatch.setattr(arb_mod.ccxt, 'binance', lambda cfg: FakeExchange('binance', cfg))
    monkeypatch.setattr(arb_mod.ccxt, 'bybit', lambda cfg: FakeExchange('bybit', cfg))
    monkeypatch.setattr(arb_mod.ccxt, 'okx', lambda cfg: FakeExchange('okx', cfg))

    result = asyncio.run(scan_public_books('BTC/USDT', use_cache=True))
    assert result['mode'] == 'READ_ONLY_RESEARCH'
    assert result['live_execution'] is False
    assert result['cached'] is False
    assert len(result['books']) == 3
    by_ex = {b['exchange']: b for b in result['books']}
    assert by_ex['binance']['status'] == 'ok'
    assert by_ex['binance']['freshness'] == 'fresh'
    assert by_ex['bybit']['status'] == 'ok'
    assert by_ex['bybit']['freshness'] == 'stale'
    assert by_ex['okx']['status'] == 'unavailable'
    assert by_ex['okx']['error_type'] == 'TimeoutError'
    assert len(result['comparisons']) == 2
    assert all(c['executable'] is False and c['net_spread_pct'] is None for c in result['comparisons'])
    assert 'partial_exchange_unavailability' in result['blockers']
    assert 'stale_or_untimestamped_quote' in result['blockers']

    for _ex_id, cfg in created_configs:
        assert cfg['enableRateLimit'] is True
        assert 'apiKey' not in cfg
        assert 'secret' not in cfg
        assert 'password' not in cfg

    # Second call within TTL hits short cache without re-querying exchanges
    cached_result = asyncio.run(scan_public_books('BTC/USDT', use_cache=True))
    assert cached_result['cached'] is True
    assert len(created_configs) == 3
    clear_arbitrage_cache()


def test_arbitrage_endpoint_auth_validation_and_readonly():
    app = FastAPI()
    install_violet_status(app, SimpleNamespace(state={'mode': 'PAPER_ONLY'}), DashboardAuthConfig('u', 'p'))
    with TestClient(app) as client:
        assert client.get('/api/violet/arbitrage').status_code == 401
        assert client.get('/api/violet/arbitrage?symbol=DOGE/USDT', auth=('u', 'p')).status_code == 422
        assert client.post('/api/violet/arbitrage', auth=('u', 'p')).status_code == 405
