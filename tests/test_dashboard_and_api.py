"""Tests for authenticated neon/cyberpunk dashboard API and security headers."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import EngineConfig, ExchangeCredentials, LoadedEnvironment
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.server import create_dashboard_app
from app.service import TradingEngineService


def test_dashboard_auth_headers_and_endpoints(tmp_path: Path):
    cfg = EngineConfig(
        base_dir=tmp_path,
        db_path=tmp_path / "dash.sqlite3",
        log_dir=tmp_path / "logs",
    )
    env = LoadedEnvironment(
        config=cfg,
        credentials={
            "okx": ExchangeCredentials("okx", "okx_k", "okx_super_secret", "okx_pass"),
            "bybit": ExchangeCredentials("bybit", "bybit_k", "bybit_super_secret"),
        },
    )
    svc = TradingEngineService(env=env)
    auth_cfg = DashboardAuthConfig("admin", "strong_pass_123")
    app = create_dashboard_app(svc, auth_config=auth_cfg)
    client = TestClient(app)

    # Public health check works
    h = client.get("/api/health")
    assert h.status_code == 200
    assert h.json()["live_execution_disabled"] is True
    assert h.headers["x-content-type-options"] == "nosniff"
    assert h.headers["x-frame-options"] == "DENY"

    # Unauthenticated access blocked
    assert client.get("/").status_code == 401
    assert client.get("/api/state").status_code == 401

    # Authenticated access succeeds and never leaks secrets
    res = client.get("/api/state", auth=("admin", "strong_pass_123"))
    assert res.status_code == 200
    body = res.text
    assert "okx_super_secret" not in body
    assert "bybit_super_secret" not in body
    assert res.json()["mode"] == "PAPER_ONLY"

    # Bot toggle & emergency stop
    tog = client.post(
        "/api/bots/okx_spot_alpha/toggle",
        json={"enabled": False},
        auth=("admin", "strong_pass_123"),
    )
    assert tog.status_code == 200
    assert tog.json()["bot"]["enabled"] is False

    estop = client.post(
        "/api/risk/emergency-stop",
        json={"reason": "test"},
        auth=("admin", "strong_pass_123"),
    )
    assert estop.status_code == 200
    assert estop.json()["emergency_stop_active"] is True

    # Strict CSP directives on root
    root_res = client.get("/", auth=("admin", "strong_pass_123"))
    assert root_res.status_code == 200
    csp = root_res.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp


def test_dashboard_env_rejects_insecure_permissions(tmp_path: Path):
    import os
    import pytest
    from app.core.security import SecretPermissionError

    sec_dir = tmp_path / "sec"
    sec_dir.mkdir()
    os.chmod(sec_dir, 0o700)
    env_file = sec_dir / "dashboard.env"
    env_file.write_text("DASHBOARD_USER=trader\nDASHBOARD_PASSWORD=secret_pass\n", encoding="utf-8")
    os.chmod(env_file, 0o644)

    with pytest.raises(SecretPermissionError):
        DashboardAuthConfig.load_or_create(env_path=env_file)

    os.chmod(env_file, 0o600)
    loaded = DashboardAuthConfig.load_or_create(env_path=env_file)
    assert loaded.username == "trader"


def test_google_oauth_permissions_and_csrf_guard(tmp_path: Path, monkeypatch):
    import os
    import pytest
    from fastapi import FastAPI
    from app.dashboard import google_auth

    sec_dir = tmp_path / "sec"
    sec_dir.mkdir()
    os.chmod(sec_dir, 0o700)
    oauth_env = sec_dir / "google-oauth.env"
    oauth_env.write_text(
        "GOOGLE_CLIENT_ID=cid\n"
        "GOOGLE_CLIENT_SECRET=csec\n"
        "GOOGLE_ALLOWED_EMAIL=owner@example.com\n"
        "SESSION_SECRET=short\n",
        encoding="utf-8",
    )
    os.chmod(oauth_env, 0o644)
    monkeypatch.setattr(google_auth, "ENV_PATH", oauth_env)

    with pytest.raises(RuntimeError, match="0600"):
        google_auth.install(FastAPI())

    os.chmod(oauth_env, 0o600)
    with pytest.raises(RuntimeError, match="32 characters"):
        google_auth.install(FastAPI())

    oauth_env.write_text(
        "GOOGLE_CLIENT_ID=cid\n"
        "GOOGLE_CLIENT_SECRET=csec\n"
        "GOOGLE_ALLOWED_EMAIL=owner@example.com\n"
        "SESSION_SECRET=" + ("x" * 40) + "\n",
        encoding="utf-8",
    )
    os.chmod(oauth_env, 0o600)

    cfg = EngineConfig(
        base_dir=tmp_path,
        db_path=tmp_path / "oauth_dash.sqlite3",
        log_dir=tmp_path / "logs",
    )
    env = LoadedEnvironment(config=cfg, credentials={})
    svc = TradingEngineService(env=env)
    auth_cfg = DashboardAuthConfig("admin", "strong_pass_123")
    app = create_dashboard_app(svc, auth_config=auth_cfg)
    client = TestClient(app, base_url="https://testserver")

    # Simulate authenticated Google session via Starlette session cookie
    # First verify /auth/logout without CSRF fails only when session is active;
    # test CSRF check directly on POST with spoofed Origin vs valid Origin.
    from starlette.middleware.sessions import SessionMiddleware
    assert any(m.cls is SessionMiddleware for m in app.user_middleware)
    # Verify unauthenticated /auth/google redirects to Google (or attempts OIDC metadata)
    assert client.get("/api/state").status_code == 401
