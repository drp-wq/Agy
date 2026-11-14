from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sqlite3
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import RiskPolicyConfig
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.persistence_health import persistence_health
from app.dashboard.violet_status import install_violet_status
from app.risk.guardian import GlobalRiskGuardian
from app.treasury.closed_loop_router import ClosedLoopAssetRouter


def setup_app(path):
    guardian = GlobalRiskGuardian(RiskPolicyConfig())
    router = ClosedLoopAssetRouter(state_path=path, risk_guardian=guardian)
    app = FastAPI()
    install_violet_status(app, SimpleNamespace(state={}), DashboardAuthConfig('fixture', 'fixture-password'),
                          closed_loop_router=router, risk_guardian=guardian)
    return app


def test_authenticated_read_only_health(tmp_path):
    app = setup_app(tmp_path / 'router.db')
    with TestClient(app) as client:
        url = '/api/violet/persistence/health'
        assert client.get(url).status_code == 401
        assert client.get(url, auth=('fixture', 'wrong')).status_code == 401
        response = client.get(url, auth=('fixture', 'fixture-password'))
        assert response.status_code == 503
        assert response.json()['scheduler']['status'] == 'unavailable'
        assert client.post(url, auth=('fixture', 'fixture-password')).status_code == 405


def test_restart_and_read_only_snapshot(tmp_path):
    path = tmp_path / 'router.db'
    first = setup_app(path)
    report = first.state.unified_brain.step([], 'restart-cycle')
    app = setup_app(path)
    before = path.read_bytes()
    app.state.persistence_scheduler_heartbeat = datetime.now(timezone.utc).isoformat()
    result = persistence_health(app)
    assert result['status'] == 'ok'
    assert result['last_brain_cycle_timestamp'] == report.timestamp_utc
    assert result['sqlite']['reconciliation']['balanced'] is True
    assert result['live_execution_permanently_disabled'] is True
    assert path.read_bytes() == before
    app.state.persistence_scheduler_heartbeat = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    assert persistence_health(app)['scheduler']['status'] == 'stale'
    app.state.unified_brain.risk_guardian.trigger_emergency_stop('test')
    assert persistence_health(app)['risk_guardian']['status'] == 'halted'
    assert persistence_health(app)['status'] == 'unavailable'


def test_missing_corrupt_and_drift_fail_closed(tmp_path):
    path = tmp_path / 'router.db'
    app = setup_app(path)
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE closed_loop_state SET payload_json='{}'")
    assert persistence_health(app)['sqlite']['status'] == 'unavailable'
    path.write_bytes(b'corrupt')
    assert persistence_health(app)['sqlite']['status'] == 'unavailable'
    path.unlink()
    assert persistence_health(app)['status'] == 'unavailable'
    assert not path.exists()


def test_journal_drift_and_missing_guardian(tmp_path):
    app = setup_app(tmp_path / 'router.db')
    router = app.state.closed_loop_router
    router.simulate_transfer(source_venue='hub_binance_master', destination_venue='cex_binance',
                             amount_usd='100', idempotency_key='drift')
    with sqlite3.connect(router.state_path) as conn:
        payload = json.loads(conn.execute('SELECT payload_json FROM closed_loop_state').fetchone()[0])
        payload['balances']['cex_binance'] = '999'
        conn.execute('UPDATE closed_loop_state SET payload_json=?', (json.dumps(payload),))
    assert persistence_health(app)['sqlite']['status'] == 'unavailable'
    app.state.unified_brain.risk_guardian = None
    assert persistence_health(app)['risk_guardian']['status'] == 'unavailable'
