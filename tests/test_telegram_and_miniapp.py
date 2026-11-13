"""Tests for Telegram alerts, read-only commands, initData HMAC auth, dedup/restart, and Mini App UI."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import EngineConfig
from app.core.models import (
    Direction,
    ExchangeId,
    MarketType,
    OrderStatus,
    OrderType,
    PaperOrder,
)
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.server import create_dashboard_app
from app.service import TradingEngineService
from app.telegram.adapter import TelegramAlertAdapter
from app.telegram.client import TelegramAPIError, TelegramClient
from app.telegram.commands import TelegramCommandHandler
from app.telegram.config import TelegramConfig
from app.telegram.formatter import format_alert_html
from app.telegram.models import AlertEventType, AlertSeverity, TelegramAlertEvent
from app.telegram.poller import TelegramPollingWorker
from app.telegram.state import TelegramStateStore
from app.telegram.webapp_auth import verify_telegram_init_data


def _sign_init_data(
    bot_token: str,
    user_id: int,
    auth_date: int | None = None,
    username: str = "owner",
) -> str:
    ts = int(time.time()) if auth_date is None else auth_date
    user_json = json.dumps({"id": user_id, "first_name": "Trader", "username": username})
    params = {
        "auth_date": str(ts),
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": user_json,
    }
    data_check_string = "\n".join(f"{k}={params[k]}" for k in sorted(params.keys()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    sig = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()
    params["hash"] = sig
    return urlencode(params)


def _make_service(tmp_path: Path, tg_cfg: TelegramConfig | None = None) -> TradingEngineService:
    cfg = EngineConfig(
        db_path=tmp_path / "paper.sqlite3",
        log_dir=tmp_path / "logs",
    )
    return TradingEngineService(config=cfg, telegram_config=tg_cfg, enable_telegram=tg_cfg is not None)


def test_secret_redaction_and_html_escaping(tmp_path: Path) -> None:
    token = "9999999:AAFakeSecretTokenValueForTest12345"
    cfg = TelegramConfig(
        bot_token=token,
        chat_id="123456789",
        state_path=tmp_path / "tg_state.json",
    )
    assert token not in repr(cfg)
    assert token not in str(cfg)

    ev = TelegramAlertEvent(
        event_type=AlertEventType.ORDER_FILL,
        severity=AlertSeverity.INFO,
        title="Paper <script>alert(1)</script> Fill",
        exchange="okx",
        symbol="BTC/USDT",
        market_type="spot",
        side="buy",
        price=65000.5,
        quantity=0.01,
        details="Bot <okx_spot> & test",
    )
    rendered = format_alert_html(ev, dashboard_url="https://trade.187-52-118-25.sslip.io")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "PAPER_ONLY" in rendered
    assert "https://trade.187-52-118-25.sslip.io" in rendered


def test_state_dedup_rate_limit_and_restart_persistence(tmp_path: Path) -> None:
    state_path = tmp_path / "tg_state.json"
    store1 = TelegramStateStore(state_path)
    store1.set_update_offset(42)
    ok1, r1 = store1.check_and_reserve_send("opp:okx:BTC/USDT", dedup_window_sec=120.0, rate_limit_per_min=5, now_epoch=1000.0)
    ok2, r2 = store1.check_and_reserve_send("opp:okx:BTC/USDT", dedup_window_sec=120.0, rate_limit_per_min=5, now_epoch=1010.0)
    assert ok1 is True and r1 is None
    assert ok2 is False and r2 == "deduplicated"

    # Simulate process restart with a fresh TelegramStateStore instance
    store2 = TelegramStateStore(state_path)
    assert store2.get_update_offset() == 42
    ok3, r3 = store2.check_and_reserve_send("opp:okx:BTC/USDT", dedup_window_sec=120.0, rate_limit_per_min=5, now_epoch=1020.0)
    assert ok3 is False and r3 == "deduplicated"

    # Rate limit check
    for i in range(4):
        ok, _ = store2.check_and_reserve_send(f"k:{i}", dedup_window_sec=10.0, rate_limit_per_min=5, now_epoch=1030.0)
        assert ok is True
    ok_rl, r_rl = store2.check_and_reserve_send("k:overflow", dedup_window_sec=10.0, rate_limit_per_min=5, now_epoch=1031.0)
    assert ok_rl is False and r_rl == "rate_limited"


def test_authorized_and_unauthorized_commands(tmp_path: Path) -> None:
    tg_cfg = TelegramConfig(
        bot_token="12345:AAFakeToken",
        chat_id="555000111",
        state_path=tmp_path / "tg_state.json",
    )
    svc = _make_service(tmp_path, tg_cfg=tg_cfg)
    store = TelegramStateStore(tg_cfg.state_path)
    handler = TelegramCommandHandler(svc, tg_cfg, store)

    # Unauthorized user -> silently rejected (None)
    unauth = handler.handle_update({
        "update_id": 1,
        "message": {"chat": {"id": 999999, "type": "private"}, "from": {"id": 999999}, "text": "/status"},
    })
    assert unauth is None
    assert store.get_metrics()["unauthorized_rejected"] == 1

    # Authorized commands
    for cmd in ("/start", "/help", "/status", "/exchanges", "/alerts", "/risk", "/pnl", "/positions"):
        res = handler.handle_update({
            "update_id": 2,
            "message": {"chat": {"id": 555000111, "type": "private"}, "from": {"id": 555000111}, "text": cmd},
        })
        assert res is not None
        text, markup = res
        assert len(text) > 10
        if cmd in ("/start", "/help"):
            assert markup is not None
            assert markup["inline_keyboard"][0][0]["web_app"]["url"] == tg_cfg.miniapp_url


@pytest.mark.asyncio
async def test_telegram_api_failure_redacts_token_and_recovers(tmp_path: Path) -> None:
    token = "888888:AASecretShouldNotLeak"
    cfg = TelegramConfig(bot_token=token, chat_id="555000111", state_path=tmp_path / "tg_state.json")
    store = TelegramStateStore(cfg.state_path)

    attempts = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(500, json={"ok": False, "description": f"error with {token}"})

    client = TelegramClient(cfg, transport=httpx.MockTransport(handler), max_retries=2, base_backoff_sec=0.01)
    adapter = TelegramAlertAdapter(cfg, state_store=store, client=client)

    sent = await adapter.emit(
        TelegramAlertEvent(
            event_type=AlertEventType.RISK_ALERT,
            severity=AlertSeverity.HIGH,
            title="Test Failure Handling",
            details="Simulated 500",
        )
    )
    assert sent is False
    assert attempts["count"] == 2
    metrics = store.get_metrics()
    assert metrics["failed_count"] == 1
    assert token not in (metrics.get("last_error") or "")
    await client.close()


@pytest.mark.asyncio
async def test_poller_persists_offset_and_replies(tmp_path: Path) -> None:
    token = "777777:AAFakePollToken"
    cfg = TelegramConfig(bot_token=token, chat_id="555000111", state_path=tmp_path / "tg_state.json")
    svc = _make_service(tmp_path, tg_cfg=cfg)
    store = TelegramStateStore(cfg.state_path)
    sent_messages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={
                "ok": True,
                "result": [
                    {
                        "update_id": 100,
                        "message": {"chat": {"id": 555000111, "type": "private"}, "from": {"id": 555000111}, "text": "/status"},
                    },
                    {
                        "update_id": 101,
                        "message": {"chat": {"id": 111222, "type": "private"}, "from": {"id": 111222}, "text": "/pnl"},
                    },
                ],
            })
        if request.url.path.endswith("/sendMessage"):
            body = json.loads(request.content.decode("utf-8"))
            sent_messages.append(body["text"])
            return httpx.Response(200, json={"ok": True, "result": {"message_id": 501}})
        return httpx.Response(200, json={"ok": True, "result": True})

    client = TelegramClient(cfg, transport=httpx.MockTransport(handler))
    cmd_handler = TelegramCommandHandler(svc, cfg, store)
    poller = TelegramPollingWorker(client, cmd_handler, store, poll_timeout_sec=1)

    count = await poller.poll_once()
    assert count == 2
    assert store.get_update_offset() == 102
    assert len(sent_messages) == 1
    assert "Engine Status" in sent_messages[0]
    await client.close()


def test_webapp_init_data_validation_and_miniapp_api_auth(tmp_path: Path) -> None:
    token = "666666:AAFakeMiniAppToken"
    owner_id = "555000111"
    tg_cfg = TelegramConfig(bot_token=token, chat_id=owner_id, state_path=tmp_path / "tg_state.json")
    svc = _make_service(tmp_path, tg_cfg=tg_cfg)
    auth_cfg = DashboardAuthConfig("admin", "secretpass")
    app = create_dashboard_app(svc, auth_config=auth_cfg)
    client = TestClient(app)

    # 1. Mobile UI shell loads with no-store & Telegram SDK
    ui = client.get("/miniapp")
    assert ui.status_code == 200
    assert "no-store" in ui.headers.get("cache-control", "")
    assert "telegram-web-app.js" in ui.text
    for tab_label in ("Overview", "Markets", "Opportunities", "Positions / Paper", "Risk", "Alerts", "Exchange Status"):
        assert tab_label in ui.text
    assert token not in ui.text

    # 2. Unauthenticated Mini App API is rejected
    assert client.get("/api/miniapp/state").status_code == 401
    assert client.get("/api/miniapp/health").status_code == 401

    # 3. Forged signature -> 401
    valid_init = _sign_init_data(token, int(owner_id))
    forged = valid_init[:-4] + "dead"
    assert client.get("/api/miniapp/state", headers={"X-Telegram-Init-Data": forged}).status_code == 401

    # 4. Expired auth_date -> 401
    expired = _sign_init_data(token, int(owner_id), auth_date=int(time.time()) - 3600)
    assert client.get("/api/miniapp/state", headers={"X-Telegram-Init-Data": expired}).status_code == 401

    # 5. Wrong user ID -> 403
    wrong_user = _sign_init_data(token, 999888777)
    assert client.get("/api/miniapp/state", headers={"X-Telegram-Init-Data": wrong_user}).status_code == 403

    # 6. Valid owner initData -> 200
    ok_resp = client.get("/api/miniapp/state", headers={"X-Telegram-Init-Data": valid_init})
    assert ok_resp.status_code == 200
    payload = ok_resp.json()
    assert payload["mode"] == "PAPER_ONLY"
    assert payload["live_execution_disabled"] is True

    # 7. Existing /api/state still requires dashboard auth
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", auth=("admin", "secretpass")).status_code == 200
