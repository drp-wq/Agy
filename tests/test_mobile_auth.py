"""Native auth boundaries: no provider, operator credentials or production ledger I/O."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from app.dashboard.mobile_auth import AUDIENCE, REDIRECT, MobileSessionStore, challenge, validate_launch
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.server import create_dashboard_app
from app.core.config import EngineConfig, LoadedEnvironment
from app.service import TradingEngineService

EMAIL = 'operator@example.test'
VERIFIER = 'a' * 64


def launch():
    return {'app_id': AUDIENCE, 'redirect_uri': REDIRECT, 'state': 's' * 43,
            'code_challenge': challenge(VERIFIER), 'code_challenge_method': 'S256'}


def test_pkce_one_use_concurrency_restart_expiry_and_at_rest_secrecy(tmp_path):
    now = [1000.0]
    path = tmp_path / 'sessions.sqlite3'
    store = MobileSessionStore(path, EMAIL, lambda: now[0])
    pending = validate_launch(launch())
    code = store.issue_handoff(pending, EMAIL, 'Operator')
    with pytest.raises(HTTPException):
        store.exchange(code, 'b' * 64, REDIRECT, AUDIENCE)
    def exchange_once(_):
        try:
            return store.exchange(code, VERIFIER, REDIRECT, AUDIENCE)
        except HTTPException:
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(exchange_once, range(2)))
    assert sum(r is not None for r in results) == 1
    issued = next(r for r in results if r)
    assert store.verify(issued['session_token']) == EMAIL
    for secret in [code, issued['session_token'], issued['refresh_token']]:
        assert secret.encode() not in path.read_bytes()
    store = MobileSessionStore(path, EMAIL, lambda: now[0])
    assert store.verify(issued['session_token']) == EMAIL
    now[0] += store.ACCESS_SECONDS
    with pytest.raises(HTTPException): store.verify(issued['session_token'])
    renewed = store.refresh(issued['refresh_token'], AUDIENCE)
    assert store.verify(renewed['session_token']) == EMAIL
    with pytest.raises(HTTPException): store.refresh(issued['refresh_token'], AUDIENCE)
    with pytest.raises(HTTPException): store.verify(renewed['session_token'])
    with pytest.raises(HTTPException): store.refresh(renewed['refresh_token'], AUDIENCE)
    assert path.stat().st_mode & 0o077 == 0


def test_handoff_expiry_allowlist_audience_redirect_and_absolute_refresh_expiry(tmp_path):
    now = [1000.0]
    store = MobileSessionStore(tmp_path / 's.sqlite3', EMAIL, lambda: now[0])
    for field, value in [('app_id', 'evil'), ('redirect_uri', 'https://evil.test/'),
                         ('code_challenge_method', 'plain'), ('state', 'short')]:
        with pytest.raises(HTTPException):validate_launch(launch() | {field: value})
    with pytest.raises(HTTPException):store.issue_handoff(validate_launch(launch()), 'wrong@example.test', 'Wrong')
    code = store.issue_handoff(validate_launch(launch()), EMAIL, 'Operator')
    with pytest.raises(HTTPException):store.exchange(code, VERIFIER, REDIRECT, 'evil')
    with pytest.raises(HTTPException):store.exchange(code, VERIFIER, REDIRECT + '/evil', AUDIENCE)
    now[0] += 90
    with pytest.raises(HTTPException):store.exchange(code, VERIFIER, REDIRECT, AUDIENCE)
    session = store.issue_authenticated(EMAIL, 'Operator')
    now[0] += store.FAMILY_SECONDS
    with pytest.raises(HTTPException):store.refresh(session['refresh_token'], AUDIENCE)
    # An allowlist change invalidates existing otherwise valid sessions.
    session = store.issue_authenticated(EMAIL, 'Operator')
    store.allowed_email = 'other@example.test'
    with pytest.raises(HTTPException):store.verify(session['session_token'])


@pytest.fixture
def native_app(tmp_path, monkeypatch):
    from app.dashboard import google_auth
    monkeypatch.setattr(google_auth, 'load_config', lambda: {
        'GOOGLE_CLIENT_ID': 'fixture-client', 'GOOGLE_CLIENT_SECRET': 'fixture-secret',
        'GOOGLE_ALLOWED_EMAIL': EMAIL, 'SESSION_SECRET': 'x' * 48,
        'GOOGLE_OAUTH_CALLBACK_URL': 'https://testserver/auth/google/callback'})
    cfg = EngineConfig(base_dir=tmp_path, db_path=tmp_path/'paper.sqlite3', log_dir=tmp_path/'logs')
    svc = TradingEngineService(env=LoadedEnvironment(config=cfg, credentials={}), enable_telegram=False)
    app = create_dashboard_app(svc, DashboardAuthConfig('operator', 'fixture-password'))
    client = TestClient(app, base_url='https://testserver')
    yield app, client, svc
    svc.db.close()


def test_mobile_login_readonly_authorization_rotation_and_logout(native_app):
    app, client, svc = native_app
    payload = {'audience': AUDIENCE, 'platform': 'android'}
    assert client.post('/auth/login', json=payload).status_code == 401
    assert client.post('/auth/login', json=payload, auth=('operator', 'wrong')).status_code == 401
    assert client.post('/auth/login', json=payload | {'audience': 'evil'}, auth=('operator', 'fixture-password')).status_code == 400
    result = client.post('/auth/login', json=payload, auth=('operator', 'fixture-password'))
    assert result.status_code == 200
    session = result.json()
    headers = {'Authorization': 'Bearer ' + session['session_token']}
    assert client.get('/auth/mobile/session', headers=headers).json()['email'] == EMAIL
    assert client.get('/api/state', headers=headers).status_code == 200
    assert client.get('http://testserver/api/state', headers=headers).status_code == 400
    # Must not gain admin/paper-mutation privileges from a mobile read session.
    assert client.post('/api/risk/emergency-stop', json={'reason': 'fixture'}, headers=headers).status_code == 403
    from app.dashboard.violet_status import install_violet_status
    class Runner:
        state = {'campaign_id': 'fixture', 'mode': 'PAPER_ONLY'}
    install_violet_status(app, Runner(), DashboardAuthConfig('operator', 'fixture-password'))
    assert client.get('/api/violet/all-cap', headers=headers).status_code == 200
    before = svc.db._conn.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0]
    result = client.post('/auth/mobile/refresh', json={'audience': AUDIENCE, 'refresh_token': session['refresh_token']})
    assert result.status_code == 200
    renewed = result.json()
    assert client.get('/auth/mobile/session', headers=headers).status_code == 401
    new_headers = {'Authorization': 'Bearer ' + renewed['session_token']}
    assert client.post('/auth/logout', json={}, headers=new_headers).status_code == 200
    assert client.get('/auth/mobile/session', headers=new_headers).status_code == 401
    assert client.post('/auth/mobile/refresh', json={'audience': AUDIENCE, 'refresh_token': renewed['refresh_token']}).status_code == 401
    assert svc.db._conn.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0] == before
    assert client.post('http://testserver/auth/login', json=payload, auth=('operator', 'fixture-password')).status_code == 400


def test_native_google_flow_uses_verified_provider_identity_and_pkce(native_app, monkeypatch):
    app, client, svc = native_app
    # Find the captured OAuth client through the original callback closure. Stub only
    # provider I/O; our state handling, handoff exchange and authorization remain real.
    from app.dashboard import server
    # Rebuild with a fake provider implementation before app construction.
    class Google:
        async def authorize_redirect(self, request, callback):
            request.session['provider_test_state'] = 'fixture-provider-state'
            return RedirectResponse('https://accounts.google.com/fixture', status_code=302)
        async def authorize_access_token(self, request):
            if request.query_params.get('state') != request.session.pop('provider_test_state', None):
                raise ValueError('provider state mismatch')
            return {'id_token': 'provider-verified-fixture', 'userinfo': {'email': EMAIL, 'email_verified': True, 'name': 'Operator'}}
    from starlette.middleware.sessions import SessionMiddleware
    def install(app):
        app.add_middleware(SessionMiddleware, secret_key='x'*48, https_only=True)
        return {'GOOGLE_ALLOWED_EMAIL': EMAIL, 'GOOGLE_OAUTH_CALLBACK_URL': 'https://testserver/auth/google/callback'}, type('Provider', (), {'google': Google()})(), True
    monkeypatch.setattr(server, 'install_google', install)
    app = server.create_dashboard_app(svc, DashboardAuthConfig('operator','fixture-password'))
    client = TestClient(app, base_url='https://testserver')
    assert client.get('/auth/google', params=launch() | {'platform': 'android'}, follow_redirects=False).status_code == 302
    callback = client.get('/auth/google/callback?code=fixture-code&state=fixture-provider-state', follow_redirects=False)
    assert callback.status_code == 303
    destination = urlparse(callback.headers['location'])
    assert destination.scheme == 'tech.prottushjd.violet'
    query = parse_qs(destination.query)
    assert query['state'] == [launch()['state']]
    assert 'session_token' not in query and 'access_token' not in query
    exchange = {'code': query['code'][0], 'code_verifier': VERIFIER, 'redirect_uri': REDIRECT, 'audience': AUDIENCE}
    assert client.post('/auth/mobile/exchange', json=exchange | {'code_verifier': 'b'*64}).status_code == 401
    result = client.post('/auth/mobile/exchange', json=exchange)
    assert result.status_code == 200
    assert client.post('/auth/mobile/exchange', json=exchange).status_code == 401
    assert client.get('/auth/mobile/session', headers={'Authorization':'Bearer '+result.json()['session_token']}).status_code == 200


def test_mobile_rate_limit_and_pkce_log_redaction():
    from app.dashboard.mobile_auth import MobileAttemptLimiter
    from app.core.security import SecretRedactionFilter
    now = [100.0]
    limiter = MobileAttemptLimiter(limit=2, window=60, clock=lambda: now[0])
    limiter.check('peer')
    limiter.check('peer')
    with pytest.raises(HTTPException) as exc:limiter.check('peer')
    assert exc.value.status_code == 429
    now[0] += 60
    limiter.check('peer')
    text = SecretRedactionFilter().redact("code_verifier='privateverifier' callback?code=privatecode&state=public")
    assert 'privateverifier' not in text and 'privatecode' not in text
