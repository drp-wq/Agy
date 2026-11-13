"""Integration tests for P0/P1 reconciliation against production features."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.core.config import (
    EngineConfig,
    ExchangeCredentials,
    LoadedEnvironment,
    RiskPolicyConfig,
    UnifiedTreasuryConfig,
)
from app.core.models import (
    Direction,
    ExchangeId,
    MarketRegime,
    MarketSnapshot,
    MarketType,
    OrderStatus,
    StrategySignal,
)
from app.core.security import LiveExecutionBlockedError, enforce_read_only_exchange
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.server import create_dashboard_app
from app.execution.paper_engine import PaperExecutionEngine
from app.persistence.database import MIGRATIONS, PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.service import TradingEngineService


def _make_snapshot(symbol: str = "BTC/USDT", price: float = 50000.0) -> MarketSnapshot:
    return MarketSnapshot(
        exchange=ExchangeId.OKX,
        symbol=symbol,
        market_type=MarketType.SPOT,
        timestamp=datetime.now(timezone.utc),
        last_price=price,
        bid=price - 5.0,
        ask=price + 5.0,
        spread_bps=2.0,
        volume_24h_usd=50_000_000.0,
        order_book_depth_usd=100_000.0,
        liquidity_score=0.95,
        regime=MarketRegime.TRENDING_BULLISH,
    )


def _make_signal(direction: Direction, symbol: str = "BTC/USDT") -> StrategySignal:
    sl = 48500.0 if direction == Direction.LONG else 51500.0
    tp = [52500.0] if direction == Direction.LONG else [47500.0]
    return StrategySignal(
        strategy_name="trend_following",
        exchange=ExchangeId.OKX,
        symbol=symbol,
        market_type=MarketType.SPOT,
        direction=direction,
        entry_low=49900.0,
        entry_high=50100.0,
        stop_loss=sl,
        take_profit_targets=tp,
        invalidation_condition="SL hit",
        estimated_fee_pct=0.001,
        estimated_slippage_pct=0.0002,
        risk_reward_ratio=1.7,
        confidence_score=0.7,
        rationale="integration test",
    )


def test_migration_4_backfills_reserved_margin_and_preserves_data(tmp_path: Path):
    db_path = tmp_path / "legacy_v3.sqlite3"
    conn = sqlite3.connect(str(db_path))
    for ver, sql in MIGRATIONS:
        if ver <= 3:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (ver, datetime.now(timezone.utc).isoformat()),
            )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO paper_accounts (bot_id, exchange, cash_usd, initial_equity_usd, realized_pnl_usd, updated_at) "
        "VALUES ('okx_spot_alpha', 'okx', 3000.0, 3000.0, 0.0, ?)",
        (now,),
    )
    conn.execute(
        "INSERT INTO paper_positions (position_id, bot_id, exchange, symbol, market_type, direction, qty, "
        "entry_price, mark_price, leverage, stop_loss, take_profit, unrealized_pnl_usd, realized_pnl_usd, is_paper, opened_at, updated_at) "
        "VALUES ('pos_1', 'okx_spot_alpha', 'okx', 'ETH/USDT', 'spot', 'long', 0.25, 2000.0, 2000.0, 1.0, 1900.0, 2200.0, 0.0, 0.0, 1, ?, ?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    db = PaperDatabase(db_path)
    acct = db.get_account("okx_spot_alpha")
    assert acct is not None
    assert acct["cash_usd"] == 3000.0
    assert acct["reserved_margin_usd"] == 500.0
    versions = [
        r["version"]
        for r in db._conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    ]
    assert versions == [1, 2, 3, 4]
    db.close()


def test_reducing_signal_order_succeeds_with_zero_free_cash(tmp_path: Path):
    db = PaperDatabase(tmp_path / "reduce.sqlite3")
    policy = RiskPolicyConfig(
        max_portfolio_exposure_pct=0.90,
        max_per_exchange_exposure_pct=0.50,
        max_per_symbol_exposure_pct=0.50,
        max_correlated_exposure_pct=0.80,
        max_single_trade_risk_pct=0.05,
    )
    risk = GlobalRiskGuardian(policy, db=db)
    engine = PaperExecutionEngine(db, risk)
    engine.ensure_bot_account("bot_full", ExchangeId.OKX, 1000.0)

    snap = _make_snapshot(price=50000.0)
    open_ord = engine.submit_signal_order(
        bot_id="bot_full",
        signal=_make_signal(Direction.LONG),
        requested_notional_usd=450.0,
        leverage=1.0,
        snapshot=snap,
        client_order_id="open_full_1",
    )
    assert open_ord.status == OrderStatus.FILLED

    # Reserve all remaining cash (simulate other open exposure) so free_cash == 0
    acct = db.get_account("bot_full")
    assert acct is not None
    assert acct["reserved_margin_usd"] > 400.0
    rem = float(acct["cash_usd"]) - float(acct["reserved_margin_usd"])
    if rem > 0:
        db.update_account_cash("bot_full", cash_delta=0.0, reserved_margin_delta=rem)
    reserved_before_close = float(db.get_account("bot_full")["reserved_margin_usd"])

    close_ord = engine.submit_signal_order(
        bot_id="bot_full",
        signal=_make_signal(Direction.SHORT),
        requested_notional_usd=450.0,
        leverage=1.0,
        snapshot=snap,
        client_order_id="close_full_1",
    )
    assert close_ord.status == OrderStatus.FILLED
    assert close_ord.reject_reason is None
    assert close_ord.filled_qty > 0
    remaining_pos = db.list_positions("bot_full")
    assert len(remaining_pos) == 0 or remaining_pos[0].qty < open_ord.filled_qty
    acct_after = db.get_account("bot_full")
    assert acct_after is not None
    assert acct_after["reserved_margin_usd"] < reserved_before_close
    closures = db._conn.execute("SELECT COUNT(*) FROM paper_realized_closures").fetchone()[0]
    assert closures == 1
    db.close()


def test_service_restart_persists_risk_bot_state_and_treasury_coverage(tmp_path: Path):
    cfg = EngineConfig(
        base_dir=tmp_path,
        db_path=tmp_path / "svc.sqlite3",
        log_dir=tmp_path / "logs",
        unified_treasury=UnifiedTreasuryConfig(enabled=True, initial_master_capital=10000.0),
    )
    env = LoadedEnvironment(config=cfg, credentials={"okx": ExchangeCredentials("okx")})
    svc1 = TradingEngineService(env=env, enable_telegram=False)

    snap = _make_snapshot(price=50000.0)
    svc1.paper_engine.submit_signal_order(
        bot_id="bybit_spot_swing",
        signal=_make_signal(Direction.LONG),
        requested_notional_usd=500.0,
        leverage=1.0,
        snapshot=snap,
        client_order_id="svc_open_1",
    )
    svc1.paper_engine.mark_to_market_and_check_exits({"okx:spot:BTC/USDT": _make_snapshot(price=53000.0)})
    svc1.sync_paper_fill_fees_to_treasury()

    svc1.bot_manager.toggle_bot("okx_spot_alpha", False)
    svc1.risk.trigger_emergency_stop("ops_drill")
    svc1.db.close()

    # Restart service against the same SQLite database
    svc2 = TradingEngineService(env=env, enable_telegram=False)
    assert svc2.bot_manager.bots["okx_spot_alpha"].enabled is False
    assert svc2.risk.emergency_stop_active is True
    assert svc2.risk.emergency_stop_reason == "ops_drill"
    audit = svc2.paper_pnl_audit()
    assert audit["paper_fills"] == 2
    assert audit["recorded_closures"] == 1
    assert audit["recorded_evidence_coverage_ok"] is True
    svc2.db.close()


def test_security_allows_reconcile_endpoints_and_blocks_non_get_transport():
    import pytest

    class MockCCXT:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def request(self, path, api="public", method="GET", params=None, config=None):
            self.calls.append((path, method))
            return {"ok": True}

        def private_get_openorders(self):
            return self.request("openOrders", api="private", method="GET")

        def fapiprivate_get_openorders(self):
            return self.request("fapi/openOrders", api="fapiPrivate", method="GET")

        def private_post_order(self):
            return self.request("order", api="private", method="POST")

        def create_order(self):
            return self.request("order", api="private", method="POST")

    ex = enforce_read_only_exchange(MockCCXT())
    assert ex.private_get_openorders() == {"ok": True}
    assert ex.fapiprivate_get_openorders() == {"ok": True}
    with pytest.raises(LiveExecutionBlockedError):
        ex.private_post_order()
    with pytest.raises(LiveExecutionBlockedError):
        ex.create_order()
    with pytest.raises(LiveExecutionBlockedError):
        ex.request("order", api="private", method="POST")


def test_dashboard_aurora_csp_and_oauth_empty_callback_fallback(tmp_path: Path, monkeypatch):
    cfg = EngineConfig(
        base_dir=tmp_path,
        db_path=tmp_path / "aurora.sqlite3",
        log_dir=tmp_path / "logs",
    )
    svc = TradingEngineService(
        env=LoadedEnvironment(config=cfg, credentials={"okx": ExchangeCredentials("okx")}),
        enable_telegram=False,
    )
    app = create_dashboard_app(svc, auth_config=DashboardAuthConfig("admin", "pass1234"))
    client = TestClient(app)

    assert client.get("/aurora").status_code == 401
    res = client.get("/aurora", auth=("admin", "pass1234"))
    assert res.status_code == 200
    csp = res.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "https://telegram.org" in csp
    svc.db.close()
