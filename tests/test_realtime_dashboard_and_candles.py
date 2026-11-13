"""Comprehensive tests for real-time market charts, candle resilience, dashboard metrics, and responsive UI."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil
import subprocess
import time

import ccxt
from fastapi.testclient import TestClient

from app.core.config import EngineConfig, ExchangeCredentials, LoadedEnvironment
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
from app.dashboard.candles import CandleFeedService
from app.dashboard.server import create_dashboard_app
from app.exchanges.base import BaseExchangeAdapter, ExchangeOutageError
from app.exchanges.registry import ExchangeRegistry
from app.service import TradingEngineService


class _StubCandleClient:
    def __init__(self, rows: list[list[float]] | None = None, exc: Exception | None = None) -> None:
        self.rows = rows or []
        self.exc = exc
        self.calls: list[dict[str, object]] = []

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "15m",
        since: int | None = None,
        limit: int = 150,
    ) -> list[list[float]]:
        self.calls.append({"symbol": symbol, "timeframe": timeframe, "since": since, "limit": limit})
        if self.exc is not None:
            raise self.exc
        out = [r for r in self.rows if since is None or int(r[0]) >= since]
        return out[-limit:]


class _StubAdapter(BaseExchangeAdapter):
    def __init__(
        self,
        exchange_id: ExchangeId,
        client: _StubCandleClient,
        credentials: ExchangeCredentials | None = None,
    ) -> None:
        self.exchange_id = exchange_id
        self._stub_client = client
        super().__init__(
            credentials=credentials,
            max_retries=0,
            outage_threshold=3,
            client_factory=lambda _opts: self._stub_client,
        )

    def _create_raw_client(self, opts):
        return self._stub_client


def _make_dashboard_service(
    tmp_path: Path,
    clients: dict[ExchangeId, _StubCandleClient] | None = None,
) -> tuple[TradingEngineService, TestClient, tuple[str, str]]:
    cfg = EngineConfig(
        base_dir=tmp_path,
        db_path=tmp_path / "rt_dash.sqlite3",
        log_dir=tmp_path / "logs",
    )
    env = LoadedEnvironment(
        config=cfg,
        credentials={
            "binance": ExchangeCredentials("binance", "bin_key", "bin_secret_value"),
            "bybit": ExchangeCredentials("bybit", "byb_key", "byb_secret_value"),
            "okx": ExchangeCredentials("okx", "okx_key", "okx_secret_value", "okx_pass"),
        },
    )
    svc = TradingEngineService(env=env, enable_telegram=False)
    default_rows = [
        [1_700_000_000_000, 90000.0, 90100.0, 89900.0, 90050.0, 10.0],
        [1_700_000_900_000, 90050.0, 90200.0, 90000.0, 90150.0, 12.5],
        [1_700_001_800_000, 90150.0, 90300.0, 90100.0, 90250.0, 15.0],
    ]
    adapters = {}
    for ex_id in (ExchangeId.BINANCE, ExchangeId.BYBIT, ExchangeId.OKX):
        c = (clients or {}).get(ex_id) or _StubCandleClient(rows=list(default_rows))
        adapters[ex_id] = _StubAdapter(ex_id, c, credentials=env.credentials[ex_id.value])
    svc.registry = ExchangeRegistry(adapters)

    auth_cfg = DashboardAuthConfig("admin", "strong_pass_123")
    app = create_dashboard_app(svc, auth_config=auth_cfg)
    return svc, TestClient(app), ("admin", "strong_pass_123")


def test_candle_endpoint_all_exchanges_incremental_dedup_and_throttling(tmp_path: Path) -> None:
    # Include duplicate timestamp and unsorted rows to verify deduplication & sorting
    okx_client = _StubCandleClient(
        rows=[
            [1_700_001_800_000, 90150.0, 90300.0, 90100.0, 90220.0, 14.0],
            [1_700_000_000_000, 90000.0, 90100.0, 89900.0, 90050.0, 10.0],
            [1_700_000_900_000, 90050.0, 90200.0, 90000.0, 90150.0, 12.5],
            [1_700_001_800_000, 90150.0, 90320.0, 90100.0, 90280.0, 16.0],  # duplicate latest update
        ]
    )
    svc, client, auth = _make_dashboard_service(tmp_path, {ExchangeId.OKX: okx_client})

    # Unauthenticated blocked
    assert client.get("/api/candles/okx/spot/BTC/USDT/15m").status_code == 401

    # Supported across all 3 exchanges + multiple symbols (BTC, ETH, SOL, AVAX)
    for ex in ("binance", "bybit", "okx"):
        for sym in ("BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT"):
            r = client.get(f"/api/candles/{ex}/spot/{sym}/15m", auth=auth)
            assert r.status_code == 200
            body = r.json()
            assert body["exchange"] == ex
            assert body["symbol"] == sym
            assert body["timeframe"] == "15m"
            assert body["stale"] is False
            assert body["source"] in {"exchange_rest", "live_cache"}
            ts_list = [c["timestamp"] for c in body["candles"]]
            assert ts_list == sorted(set(ts_list))
            assert len(ts_list) == 3

    # Verify latest duplicate bar overwrote earlier one and cache throttles immediate repeat poll
    r1 = client.get("/api/candles/okx/spot/BTC/USDT/15m", auth=auth)
    assert r1.status_code == 200
    assert r1.json()["candles"][-1]["close"] == 90280.0
    calls_before = len(okx_client.calls)
    r2 = client.get("/api/candles/okx/spot/BTC/USDT/15m", auth=auth)
    assert r2.status_code == 200
    assert r2.json()["source"] == "live_cache"
    assert len(okx_client.calls) == calls_before  # Throttled within 4s TTL

    # Incremental update via since param after cache expiry
    svc_feed = CandleFeedService(cache_ttl_sec=0.0)
    first = asyncio.run(svc_feed.get_candles(svc, "okx", "spot", "BTC", "USDT", "15m"))
    assert len(first["candles"]) == 3
    okx_client.rows.append([1_700_002_700_000, 90280.0, 90400.0, 90200.0, 90350.0, 20.0])
    inc = asyncio.run(
        svc_feed.get_candles(svc, "okx", "spot", "BTC", "USDT", "15m", since=1_700_001_800_000)
    )
    assert inc["incremental"] is True
    assert [c["timestamp"] for c in inc["candles"]] == [1_700_001_800_000, 1_700_002_700_000]


def test_candle_timeout_ratelimit_disconnect_and_stale_fallback(tmp_path: Path) -> None:
    timeout_client = _StubCandleClient(exc=ccxt.RequestTimeout("timed out"))
    rate_client = _StubCandleClient(exc=ccxt.RateLimitExceeded("429 slow down"))
    net_client = _StubCandleClient(exc=ccxt.NetworkError("connection reset"))

    svc, client, auth = _make_dashboard_service(
        tmp_path,
        {
            ExchangeId.BINANCE: timeout_client,
            ExchangeId.BYBIT: rate_client,
            ExchangeId.OKX: net_client,
        },
    )

    # 1. Timeout -> 504
    r_to = client.get("/api/candles/binance/spot/BTC/USDT/5m", auth=auth)
    assert r_to.status_code == 504
    assert r_to.json()["detail"]["error_code"] == "request_timeout"

    # 2. Rate-limit -> 429 with Retry-After header
    r_rl = client.get("/api/candles/bybit/spot/ETH/USDT/5m", auth=auth)
    assert r_rl.status_code == 429
    assert r_rl.headers.get("retry-after") == "15"
    assert r_rl.json()["detail"]["error_code"] == "rate_limited"

    # 3. Network disconnect -> 503
    r_net = client.get("/api/candles/okx/perp/SOL/USDT/5m", auth=auth)
    assert r_net.status_code == 503
    assert r_net.json()["detail"]["error_code"] == "exchange_unavailable"

    # 4. Outage circuit open -> 503
    okx_adapter = svc.registry.get(ExchangeId.OKX)
    okx_adapter._circuit_open_until = time.monotonic() + 30.0
    r_cb = client.get("/api/candles/okx/perp/SOL/USDT/5m", auth=auth)
    assert r_cb.status_code == 503
    assert r_cb.json()["detail"]["error_code"] == "exchange_circuit_open"

    # 5. Stale scanner snapshot fallback when allow_stale=true
    svc.scanner.latest_snapshots["binance:spot:BTC/USDT"] = MarketSnapshot(
        exchange=ExchangeId.BINANCE,
        symbol="BTC/USDT",
        market_type=MarketType.SPOT,
        timestamp=datetime.now(timezone.utc) - timedelta(seconds=30),
        last_price=90000.0,
        bid=89995.0,
        ask=90005.0,
        spread_bps=1.1,
        volume_24h_usd=500_000_000.0,
        candles_by_tf={
            "5m": [
                Candle(timestamp=1_700_000_000_000, open=90000, high=90100, low=89900, close=90050, volume=5.0)
            ]
        },
    )
    r_stale = client.get("/api/candles/binance/spot/BTC/USDT/5m?allow_stale=true", auth=auth)
    assert r_stale.status_code == 200
    stale_body = r_stale.json()
    assert stale_body["stale"] is True
    assert stale_body["source"] == "stale_cache"
    assert len(stale_body["candles"]) == 1

    # 6. Invalid timeframe rejected cleanly
    assert client.get("/api/candles/binance/spot/BTC/USDT/7m", auth=auth).status_code == 400


def test_dashboard_pnl_drawdown_paper_live_balance_separation(tmp_path: Path) -> None:
    svc, client, auth = _make_dashboard_service(tmp_path)

    # Seed an open paper position with SL/TP and unrealized PnL
    svc.db.upsert_position(
        PaperPosition(
            bot_id="okx_perp_momentum",
            exchange=ExchangeId.OKX,
            symbol="BTC/USDT",
            market_type=MarketType.PERP,
            direction=Direction.LONG,
            qty=0.1,
            entry_price=90000.0,
            mark_price=91000.0,
            leverage=2.0,
            stop_loss=88500.0,
            take_profit=93000.0,
            unrealized_pnl_usd=100.0,
        )
    )
    svc.db.update_account_cash("okx_perp_momentum", cash_delta=-25.0, realized_pnl_delta=-25.0, reserved_margin_delta=4500.0)
    svc.risk.update_equity_watermarks(14100.0, initial_equity_usd=14000.0)

    # Seed isolated read-only real exchange snapshot (must NEVER mix into paper equity)
    svc.readonly_account_snapshots = {
        "binance": {"status": "ok", "balances": {"USDT": 999999.0}, "positions": [], "timestamp": int(time.time())},
        "bybit": {"status": "ok", "balances": {"BTC": 1.25}, "positions": [], "timestamp": int(time.time())},
        "okx": {"status": "error", "balances": {}, "positions": [], "timestamp": None, "error_type": "NetworkError"},
    }

    r = client.get("/api/state", auth=auth)
    assert r.status_code == 200
    d = r.json()

    assert d["mode"] == "PAPER_ONLY"
    assert d["live_execution_disabled"] is True
    assert "bin_secret_value" not in r.text
    assert "byb_secret_value" not in r.text
    assert "okx_secret_value" not in r.text

    port = d["portfolio"]
    assert port["realized_pnl_usd"] == -25.0
    assert port["unrealized_pnl_usd"] == 100.0
    # Realized +999999 from read-only snapshot MUST NOT contaminate paper equity
    assert port["total_equity_usd"] < 20000.0
    assert d["risk"]["current_drawdown_pct"] is not None
    assert d["risk"]["max_drawdown_limit_pct"] > 0
    assert len(d["available_pairs"]) >= 3
    assert d["readonly_account_snapshots"]["binance"]["balances"]["USDT"] == 999999.0


def test_dashboard_html_realtime_chart_responsiveness_and_js_syntax(tmp_path: Path) -> None:
    _svc, client, auth = _make_dashboard_service(tmp_path)
    res = client.get("/", auth=auth)
    assert res.status_code == 200
    html = res.text

    # Verify required UI sections, labels, responsiveness, and security controls
    required_markers = [
        "PAPER / SIMULATED DATA",
        "EXCHANGE READ-ONLY",
        "WATCH-ONLY · READ-ONLY",
        "Paper Realized PnL",
        "Paper Unrealized PnL",
        "Drawdown (vs HWM)",
        "Open Paper Positions &amp; Stop-Loss / Take-Profit",
        "Real-Time Multi-Exchange Market Chart",
        "CHART_BASE_REFRESH_MS = 6000",
        "AbortController",
        "document.visibilityState === 'visible'",
        "overflow-x: hidden",
        "minmax(min(100%, 360px), 1fr)",
        "@media (max-width: 1024px)",
        "@media (max-width: 680px)",
        "devicePixelRatio",
        "ResizeObserver",
    ]
    for marker in required_markers:
        assert marker in html, f"Missing expected marker in index.html: {marker}"

    # Verify inline script parses cleanly with node --check if node is installed
    match = re.search(r"<script>(.*)</script>", html, re.DOTALL)
    assert match is not None
    if shutil.which("node"):
        js_file = tmp_path / "dash_inline.js"
        js_file.write_text(match.group(1), encoding="utf-8")
        proc = subprocess.run(["node", "--check", str(js_file)], capture_output=True, text=True)
        assert proc.returncode == 0, f"Inline JS syntax error: {proc.stderr}"

    # Verify Aurora / Miniapp ProMarketChart + chartRefresh helper includes auto-refresh, cancellation, and backoff
    aurora_chart = (
        Path("/root/multi-exchange-paper/frontend/src/components/charts/ProMarketChart.tsx").read_text(encoding="utf-8")
        + "\n"
        + Path("/root/multi-exchange-paper/frontend/src/utils/chartRefresh.ts").read_text(encoding="utf-8")
    )
    for marker in ("CHART_REFRESH_MS = 6000", "AbortController", "visibilityState", "backoffMsRef"):
        assert marker in aurora_chart
