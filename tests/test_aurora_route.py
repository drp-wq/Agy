"""Integration tests for the additive authenticated /aurora V7 route and treasury audit payload."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import EngineConfig, ExchangeCredentials, LoadedEnvironment
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.server import create_dashboard_app
from app.service import TradingEngineService


def test_aurora_route_auth_and_treasury_payload(tmp_path: Path) -> None:
    cfg = EngineConfig(
        base_dir=tmp_path,
        db_path=tmp_path / "aurora.sqlite3",
        log_dir=tmp_path / "logs",
    )
    env = LoadedEnvironment(
        config=cfg,
        credentials={
            "okx": ExchangeCredentials("okx"),
            "bybit": ExchangeCredentials("bybit"),
            "binance": ExchangeCredentials("binance"),
        },
    )
    svc = TradingEngineService(env=env)
    auth_cfg = DashboardAuthConfig("admin", "strong_pass_123")
    app = create_dashboard_app(svc, auth_config=auth_cfg)
    client = TestClient(app)

    # Unauthenticated /aurora is blocked
    unauth = client.get("/aurora")
    assert unauth.status_code == 401

    # Authenticated /aurora serves Midnight Aurora V7 HTML with /aurora/assets/
    auth_res = client.get("/aurora", auth=("admin", "strong_pass_123"))
    assert auth_res.status_code == 200
    assert "/aurora/assets/" in auth_res.text
    assert auth_res.headers["x-frame-options"] == "DENY"
    assert auth_res.headers["x-content-type-options"] == "nosniff"

    # Legacy / and /miniapp remain intact
    assert client.get("/", auth=("admin", "strong_pass_123")).status_code == 200
    assert client.get("/miniapp").status_code == 200

    # Empty X-Telegram-Init-Data with valid Basic Auth does not trigger spurious 401
    state_res = client.get(
        "/api/miniapp/state",
        headers={"X-Telegram-Init-Data": ""},
        auth=("admin", "strong_pass_123"),
    )
    assert state_res.status_code == 200
    payload = state_res.json()
    assert payload["mode"] == "PAPER_ONLY"
    assert payload["live_execution_permanently_disabled"] is True
    assert "unified_treasury" in payload
    assert "treasury_reconciliation" in payload
    assert "paper_pnl_audit" in payload

    # Invalid non-empty X-Telegram-Init-Data is still strictly rejected (401/503)
    bad_tg = client.get(
        "/api/miniapp/state",
        headers={"X-Telegram-Init-Data": "invalid=1&hash=0000"},
        auth=("admin", "strong_pass_123"),
    )
    assert bad_tg.status_code in (401, 503)
