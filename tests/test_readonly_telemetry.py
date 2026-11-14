"""Tests for isolated read-only exchange telemetry and dashboard integration."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.core.config import EngineConfig, LoadedEnvironment, MidLowCapConfig, WatchOnlyWalletsConfig
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.server import create_dashboard_app
from app.exchanges.readonly_telemetry import (
    FORBIDDEN_WRITE_METHODS,
    ReadOnlyExchangeTelemetry,
    _classify_error,
    _sanitize_error,
)
from app.service import TradingEngineService


def test_readonly_telemetry_missing_credentials_and_secret_masking(tmp_path: Path):
    assert LIVE_EXECUTION_PERMANENTLY_DISABLED is True
    assert {"create_order", "cancel_order", "withdraw", "transfer", "set_leverage"}.issubset(
        FORBIDDEN_WRITE_METHODS
    )

    env_file = tmp_path / "empty.env"
    env_file.write_text("# no keys\n", encoding="utf-8")
    tel = ReadOnlyExchangeTelemetry(env_path=env_file)
    snap = asyncio.run(tel.refresh(force=True))
    for ex in ("binance", "bybit", "okx"):
        assert snap[ex]["private_auth_ok"] is False
        assert snap[ex]["auth_state"] == "missing_credentials"
        assert snap[ex]["live_readonly_snapshot"]["status"] == "error"

    masked = _sanitize_error(RuntimeError("bad key SUPERSECRET12345"), ["SUPERSECRET12345"])
    assert "SUPERSECRET12345" not in masked
    assert "***" in masked
    assert _classify_error(RuntimeError("PermissionDenied 50110")) == "invalid_permissions"
    assert _classify_error(TimeoutError("network timeout")) == "exchange_error"


def test_readonly_telemetry_stale_detection(tmp_path: Path):
    env_file = tmp_path / "empty.env"
    env_file.write_text("", encoding="utf-8")
    tel = ReadOnlyExchangeTelemetry(env_path=env_file, stale_after_sec=10.0)
    tel._cache["binance"] = {
        "exchange": "binance",
        "public_feed_ok": True,
        "private_auth_ok": True,
        "auth_state": "authenticated",
        "private_auth_error": None,
        "last_sync_ts": int(time.time()) - 120,
        "latency_ms": 42.0,
        "freshness": "fresh",
        "account_status": "read_only_verified",
        "api_permissions": {"read_only": True},
        "live_readonly_snapshot": {"status": "ok", "timestamp": int(time.time()) - 120, "balances": {"USDT": 10.0}, "positions": []},
    }
    snap = tel.snapshot()
    assert snap["binance"]["freshness"] == "stale"
    assert snap["binance"]["auth_state"] == "stale_data"


def test_miniapp_state_overlays_readonly_telemetry_without_mixing_paper(tmp_path: Path):
    cfg = EngineConfig(
        mid_low_cap=MidLowCapConfig(enabled=False),
        base_dir=tmp_path,
        db_path=tmp_path / "preview.sqlite3",
        backup_dir=tmp_path / "backups",
        log_dir=tmp_path / "logs",
        watch_only_wallets=WatchOnlyWalletsConfig(registry_path=tmp_path / "empty-wallets.json"),
    )
    svc = TradingEngineService(env=LoadedEnvironment(config=cfg, credentials={}), enable_telegram=False)
    auth = DashboardAuthConfig("trader", "secret-pass-123")
    app = create_dashboard_app(svc, auth_config=auth)

    calls = {"force": 0}

    class StubTelemetry:
        def snapshot(self):
            now = int(time.time())
            return {
                "binance": {
                    "exchange": "binance",
                    "public_feed_ok": True,
                    "private_auth_ok": True,
                    "auth_state": "authenticated",
                    "private_auth_error": None,
                    "last_sync_ts": now,
                    "latency_ms": 111.5,
                    "freshness": "fresh",
                    "account_status": "read_only_verified",
                    "api_permissions": {"read_only": True},
                    "live_readonly_snapshot": {"status": "ok", "timestamp": now, "balances": {"USDT": 123.45}, "positions": []},
                },
                "bybit": {
                    "exchange": "bybit",
                    "public_feed_ok": True,
                    "private_auth_ok": True,
                    "auth_state": "authenticated",
                    "private_auth_error": None,
                    "last_sync_ts": now,
                    "latency_ms": 222.0,
                    "freshness": "fresh",
                    "account_status": "UTA_status_6",
                    "api_permissions": None,
                    "live_readonly_snapshot": {"status": "ok", "timestamp": now, "balances": {"USDT": 50.0}, "positions": []},
                },
                "okx": {
                    "exchange": "okx",
                    "public_feed_ok": True,
                    "private_auth_ok": True,
                    "auth_state": "authenticated",
                    "private_auth_error": None,
                    "last_sync_ts": now,
                    "latency_ms": 180.0,
                    "freshness": "fresh",
                    "account_status": "acctLv_1",
                    "api_permissions": None,
                    "live_readonly_snapshot": {"status": "ok", "timestamp": now, "balances": {"USDT": 75.0}, "positions": []},
                },
            }

        async def refresh(self, force: bool = False):
            if force:
                calls["force"] += 1
            return self.snapshot()

    app.state.readonly_telemetry = StubTelemetry()
    client = TestClient(app)

    # Unauthenticated -> 401
    assert client.get("/api/miniapp/state").status_code == 401

    # Authenticated without refresh
    r = client.get("/api/miniapp/state", auth=("trader", "secret-pass-123"))
    assert r.status_code == 200
    data = r.json()
    assert data["mode"] == "PAPER_ONLY"
    assert data["live_execution_disabled"] is True
    assert data["exchanges"]["binance"]["private_auth_ok"] is True
    assert data["exchanges"]["binance"]["api_latency_ms"] == 111.5
    assert data["exchanges"]["binance"]["live_readonly_snapshot"]["balances"] == {"USDT": 123.45}
    assert data["exchanges"]["bybit"]["exchange_meta"]["account_status"] == "UTA_status_6"
    assert data["exchanges"]["okx"]["exchange_meta"]["account_status"] == "acctLv_1"
    assert calls["force"] == 0

    # Sync Telemetry (?refresh=1) triggers forced refresh
    r2 = client.get("/api/miniapp/state?refresh=1", auth=("trader", "secret-pass-123"))
    assert r2.status_code == 200
    assert calls["force"] == 1


def test_position_failure_is_explicit_and_never_certifies_empty_account(tmp_path, monkeypatch):
    env_file = tmp_path / 'empty.env'
    env_file.write_text('')
    tel = ReadOnlyExchangeTelemetry(env_path=env_file)
    tel._creds['bybit'] = {'apiKey': 'fixture-key', 'secret': 'fixture-secret'}
    class Client:
        async def fetch_ticker(self, *args): return {}
        async def fetch_balance(self, *args): return {'total': {'USDT': 1}}
        async def fetch_positions(self, *args): raise TimeoutError('fixture network failure')
        async def private_get_v5_user_query_api(self): raise PermissionError('fixture denied')
        async def close(self): pass
    monkeypatch.setattr(tel, '_build_client', lambda exchange: (Client(), None))
    result = asyncio.run(tel._fetch_one('bybit'))
    assert result['private_auth_ok']
    assert result['endpoint_status']['positions'] == 'BLOCKED:TimeoutError'
    assert result['endpoint_status']['permissions'] == 'NOT_TESTED'
    assert result['api_permissions'] is None
    assert result['account_status'] != 'read_only_verified'
