from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.violet_status import install_violet_status


def test_status_requires_existing_credentials_and_has_no_writes():
    app = FastAPI()
    install_violet_status(app, SimpleNamespace(state={'mode': 'PAPER_ONLY'}), DashboardAuthConfig('fixture', 'fixture-password'))
    with TestClient(app) as client:
        assert client.get('/api/violet/all-cap').status_code == 401
        assert client.get('/api/violet/all-cap', auth=('fixture', 'wrong')).status_code == 401
        assert client.get('/api/violet/all-cap', auth=('fixture', 'fixture-password')).json() == {'mode': 'PAPER_ONLY'}
        assert client.post('/api/violet/all-cap', auth=('fixture', 'fixture-password')).status_code == 405


def test_unified_account_and_simulated_closed_loop_transfer_endpoints():
    app = FastAPI()
    install_violet_status(app, SimpleNamespace(state={'mode': 'PAPER_ONLY'}), DashboardAuthConfig('fixture', 'fixture-password'))
    with TestClient(app) as client:
        assert client.get('/api/violet/unified-account').status_code == 401
        assert client.get('/api/violet/transfers').status_code == 401

        ua = client.get('/api/violet/unified-account', auth=('fixture', 'fixture-password'))
        assert ua.status_code == 200
        body = ua.json()
        assert body['mode'] == 'PAPER_SIMULATION_ONLY'
        assert body['live_execution_enabled'] is False
        assert body['active_cex_venues'] == ['cex_binance', 'cex_okx', 'cex_bybit']
        assert body['active_dex_venues'] == ['dex_solana']
        assert body['invariant']['conservation_ok'] is True

        sim = client.post(
            '/api/violet/transfers/simulate',
            auth=('fixture', 'fixture-password'),
            json={
                'source_venue': 'hub_binance_master',
                'destination_venue': 'cex_binance',
                'amount_usd': '250.00',
                'idempotency_key': 'test-sim-1',
                'reason': 'unit_test_rebalance',
            },
        )
        assert sim.status_code == 200
        sim_body = sim.json()
        assert sim_body['transfer']['state'] == 'SETTLED'
        assert sim_body['summary']['invariant']['conservation_ok'] is True

        bad = client.post(
            '/api/violet/transfers/simulate',
            auth=('fixture', 'fixture-password'),
            json={
                'source_venue': 'hub_binance_master',
                'destination_venue': 'external_wallet',
                'amount_usd': '100.00',
                'idempotency_key': 'test-sim-bad',
            },
        )
        assert bad.status_code == 400
