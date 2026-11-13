"""Contract, security, and isolation tests for the V7 Mini App API presenter."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from app.core.config import EngineConfig, ExchangeCredentials
from app.core.models import (
    Candle,
    Direction,
    ExchangeId,
    MarketRegime,
    MarketSnapshot,
    MarketType,
    PaperPosition,
)
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.server import create_dashboard_app
from app.exchanges.registry import ExchangeRegistry
from app.service import TradingEngineService
from app.telegram.config import TelegramConfig
from datetime import datetime, timezone
from app.exchanges.base import BaseExchangeAdapter


class _FakeAdapter(BaseExchangeAdapter):
    def __init__(self, exchange_id: ExchangeId, credentials: ExchangeCredentials | None = None) -> None:
        self.exchange_id = exchange_id
        super().__init__(credentials=credentials, client_factory=lambda opts: object())

    def _create_raw_client(self, opts):
        return object()


def _sign_init_data(bot_token: str, user_id: int, auth_date: int | None = None) -> str:
    ts = auth_date if auth_date is not None else int(time.time())
    user_json = json.dumps({"id": user_id, "first_name": "Owner", "username": "owner"})
    fields = {"auth_date": str(ts), "query_id": "AAHdF6IQAAAAAN0XohDhrOrc", "user": user_json}
    data_check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    sig = hmac.new(secret, data_check.encode("utf-8"), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": sig})


def _make_v7_service(tmp_path: Path) -> tuple[TradingEngineService, TelegramConfig]:
    cfg = EngineConfig(db_path=tmp_path / "paper.db", log_dir=tmp_path / "logs")
    tg_cfg = TelegramConfig(
        bot_token="777777:AAFakeV7TestToken",
        chat_id="555000111",
        state_path=tmp_path / "tg_state.json",
    )
    svc = TradingEngineService(config=cfg, telegram_config=tg_cfg, enable_telegram=True)
    okx = _FakeAdapter(ExchangeId.OKX, ExchangeCredentials(exchange="okx", api_key="k", api_secret="s", passphrase="p"))
    bybit = _FakeAdapter(ExchangeId.BYBIT, ExchangeCredentials(exchange="bybit"))
    binance = _FakeAdapter(ExchangeId.BINANCE, ExchangeCredentials(exchange="binance"))
    svc.registry = ExchangeRegistry({ExchangeId.OKX: okx, ExchangeId.BYBIT: bybit, ExchangeId.BINANCE: binance})
    svc.auth_verification = {
        "okx": {"status": "ok", "configured": True, "asset_count": 2},
        "bybit": {"status": "unconfigured", "configured": False, "asset_count": 0},
        "binance": {"status": "unconfigured", "configured": False, "asset_count": 0},
    }
    return svc, tg_cfg


def test_v7_miniapp_state_health_and_candles_contract(tmp_path: Path) -> None:
    svc, tg_cfg = _make_v7_service(tmp_path)

    # Seed real scanner snapshots across exchanges for cross-exchange spread calculation
    svc.scanner.latest_snapshots["okx:perp:BTC/USDT"] = MarketSnapshot(
        exchange=ExchangeId.OKX,
        symbol="BTC/USDT",
        market_type=MarketType.PERP,
        timestamp=datetime.now(timezone.utc),
        last_price=90050.0,
        bid=90045.0,
        ask=90055.0,
        spread_bps=1.1,
        volume_24h_usd=500_000_000.0,
        regime=MarketRegime.TRENDING_BULLISH,
        opportunity_score=72.5,
        candles_by_tf={
            "15m": [
                Candle(timestamp=1_700_000_000_000, open=90000.0, high=90100.0, low=89950.0, close=90050.0, volume=120.5)
            ]
        },
    )
    svc.scanner.latest_snapshots["binance:spot:BTC/USDT"] = MarketSnapshot(
        exchange=ExchangeId.BINANCE,
        symbol="BTC/USDT",
        market_type=MarketType.SPOT,
        timestamp=datetime.now(timezone.utc),
        last_price=90000.0,
        bid=89995.0,
        ask=90005.0,
        spread_bps=1.1,
        volume_24h_usd=900_000_000.0,
        regime=MarketRegime.RANGING,
        opportunity_score=60.0,
    )

    # Seed a paper futures position on OKX
    svc.db.upsert_position(
        PaperPosition(
            bot_id="okx_perp_alpha",
            exchange=ExchangeId.OKX,
            symbol="BTC/USDT",
            market_type=MarketType.PERP,
            direction=Direction.LONG,
            qty=0.1,
            entry_price=89500.0,
            mark_price=90050.0,
            leverage=3.0,
            stop_loss=88000.0,
            take_profit=92000.0,
            unrealized_pnl_usd=55.0,
        )
    )

    app = create_dashboard_app(svc, auth_config=DashboardAuthConfig("admin", "secret"))
    client = TestClient(app)
    valid_init = _sign_init_data(tg_cfg.bot_token, int(tg_cfg.chat_id))

    # 1. State contract & real-vs-demo check
    res = client.get("/api/miniapp/state", headers={"X-Telegram-Init-Data": valid_init})
    assert res.status_code == 200
    data = res.json()
    assert data["is_demo"] is False
    assert data["mode"] == "PAPER_ONLY"
    assert data["live_execution_disabled"] is True
    assert data["live_execution_permanently_disabled"] is True
    assert data["telegram_owner"]["auth_method"] == "signed_init_data"

    # 2. Exchange & auth isolation check (OKX private auth ok, Bybit/Binance unconfigured)
    assert set(data["exchanges"].keys()) == {"okx", "bybit", "binance"}
    assert data["exchanges"]["okx"]["private_auth_ok"] is True
    assert data["exchanges"]["bybit"]["private_auth_ok"] is False
    assert data["exchanges"]["binance"]["private_auth_ok"] is False
    assert data["exchanges"]["bybit"]["public_feed_ok"] is True

    # 3. Position isolation & derivatives fields
    assert len(data["futures_positions"]) == 1
    pos = data["futures_positions"][0]
    assert pos["exchange"] == "okx"
    assert pos["market_type"] == "futures"
    assert pos["leverage"] == 3.0
    assert pos["liquidation_price"] is not None

    # 4. Cross-exchange spread computed from real snapshots
    assert len(data["cross_exchange_spreads"]) == 1
    assert data["cross_exchange_spreads"][0]["symbol"] == "BTC/USDT"

    # 5. Health contract
    h_res = client.get("/api/miniapp/health", headers={"X-Telegram-Init-Data": valid_init})
    assert h_res.status_code == 200
    h_data = h_res.json()
    assert h_data["live_execution_permanently_disabled"] is True
    assert h_data["telegram_auth_verified"] is True
    assert h_data["adapters"]["okx"]["private_auth_ok"] is True
    assert h_data["adapters"]["binance"]["private_auth_ok"] is False

    # 6. Candles endpoint maps 'futures' -> 'perp' and normalizes 'time' in seconds
    c_res = client.get("/api/miniapp/candles/okx/futures/BTC/USDT/15m", headers={"X-Telegram-Init-Data": valid_init})
    assert c_res.status_code == 200
    c_data = c_res.json()
    assert len(c_data["candles"]) == 1
    assert c_data["candles"][0]["time"] == 1_700_000_000
