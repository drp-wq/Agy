from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.core.config import EngineConfig, LoadedEnvironment
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.diagnostics import freshness, observation_status
from app.dashboard.server import create_dashboard_app
from app.service import TradingEngineService
from validation72.monitor import collect


@pytest.fixture
def service(tmp_path):
    svc = TradingEngineService(env=LoadedEnvironment(config=EngineConfig(
        base_dir=tmp_path, db_path=tmp_path / 'paper.sqlite3', log_dir=tmp_path / 'logs'), credentials={}))
    yield svc
    svc.db.close()


def test_authenticated_readonly_full_ledger(service):
    conn = service.db._conn
    now = datetime.now(timezone.utc).isoformat()
    # More records than the state endpoint's 50-row snapshot; two exchanges.
    for i in range(65):
        conn.execute('''INSERT INTO paper_orders
            (order_id,client_order_id,bot_id,signal_id,exchange,symbol,market_type,side,order_type,
             requested_qty,status,reject_reason,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (str(i),str(i),'test','sig','binance' if i < 60 else 'okx','BTC/USDT','spot','buy','market',1,'rejected','risk_limit' if i % 2 else None,now,now))
    conn.execute("INSERT INTO paper_accounts (bot_id,exchange,cash_usd,initial_equity_usd,realized_pnl_usd,updated_at) VALUES ('diagnostics','binance',100,100,-3,?)", (now,))
    for i in range(2):
        conn.execute('''INSERT INTO paper_fills
            (fill_id,order_id,bot_id,exchange,symbol,market_type,side,qty,price,fee_usd,slippage_bps,timestamp)
            VALUES (?,?,?,'binance','BTC/USDT','spot','buy',1,10,1.5,0,?)''', (str(i),str(i),'diagnostics',now))
    conn.commit()
    client = TestClient(create_dashboard_app(service, auth_config=DashboardAuthConfig('admin','password')))
    before = conn.total_changes
    for path in ['/api/diagnostics', '/api/miniapp/diagnostics']:
        assert client.get(path).status_code == 401
        assert client.get(path, headers={'X-Telegram-Init-Data': 'invalid'}).status_code in (401, 403, 503)
        response = client.get(path, auth=('admin','password'))
        assert response.status_code == 200
        assert response.headers['cache-control'] == 'no-store, private'
        data = response.json()
        assert data['mode'] == 'PAPER_ONLY' and data['read_only']
        b = data['exchanges']['binance']
        assert b['orders_by_status'] == {'rejected': 60}
        assert b['rejections_by_reason'] == {'risk_limit': 30, 'unspecified': 30}
        assert b['fills'] == 2 and b['fees_usd'] == 3
        assert b['realized_pnl_usd'] == -3
        assert data['exchanges']['okx']['orders_by_status'] == {'rejected': 5}
        assert data['exchanges']['bybit']['fills'] == 0
        assert data['system']['observation']['status'] == 'missing'
        assert client.post(path, auth=('admin','password')).status_code == 405
    assert conn.total_changes == before


def test_freshness_and_invalid_observations(tmp_path):
    now = datetime.now(timezone.utc)
    assert freshness(None, now)['status'] == 'missing'
    assert freshness(now, now)['status'] == 'fresh'
    assert freshness(now - timedelta(seconds=181), now)['status'] == 'stale'
    for value in ['bad', now.replace(tzinfo=None), now + timedelta(hours=1)]:
        assert freshness(value, now)['status'] == 'invalid'
    path = tmp_path / 'latest.json'
    for value in ['broken', '{}', '[]', '{"timestamp": null}', 'x' * 65537]:
        path.write_text(value)
        assert observation_status(path, now)['status'] == 'invalid'
    path.write_text('{"timestamp":"2000-01-01T00:00:00Z"}')
    assert observation_status(path, now)['status'] == 'stale'


def test_monitor_local_readonly_and_completed_window(service, tmp_path):
    root = tmp_path / 'validation72'
    root.mkdir()
    (root / 'window.json').write_text('{"end_utc":"2000-01-01T00:00:00Z"}')
    (root / 'latest.json').write_text('{"timestamp":"2000-01-01T00:00:00Z"}')
    before = service.config.db_path.read_bytes()
    record = collect(service.config.db_path, root)
    assert record['window_complete'] is True
    assert record['previous_observation']['status'] == 'stale'
    assert 'exchanges' in record
    assert 'public_health' not in record  # no network unless explicitly configured
    assert service.config.db_path.read_bytes() == before
    missing = tmp_path / 'missing.sqlite3'
    assert collect(missing, root)['ledger_error_type'] == 'OperationalError'
    assert not missing.exists()
