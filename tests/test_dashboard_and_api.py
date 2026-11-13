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
