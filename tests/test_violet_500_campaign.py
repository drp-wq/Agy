"""Verification tests for the 500 USDT VIOLET All-Cap paper campaign redeployment."""
from __future__ import annotations

import asyncio
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.config import AllCapConfig
from app.core.models import Direction, ExchangeId, MarketType, StrategySignal
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.violet_status import install_violet_status
from app.exchanges.registry import ExchangeRegistry
from app.execution.all_cap_runner import AllCapPaperRunner, BANDS
from app.execution.violet_campaign_config import resolve_violet_campaign_settings
from app.persistence.database import PaperDatabase
from app.scanner.market_cap import MarketCapBand, MarketCapProvider
from app.treasury.closed_loop_router import ClosedLoopAssetRouter
from test_all_cap_paper import signal, snapshot


def _make_500_config(**overrides) -> AllCapConfig:
    params = dict(
        enabled=True,
        sleeve_capital_usd=100.0,
        max_risk_per_trade_pct=0.01,
        min_order_notional_usd=5.0,
        market_cap_min_request_interval_sec=0.0,
    )
    params.update(overrides)
    return AllCapConfig(**params)


def test_500_total_five_100_sleeves_and_no_double_funding(tmp_path: Path):
    archive_dir = tmp_path / "violet-all-cap"
    archive_db = PaperDatabase(archive_dir / "violet-all-cap.sqlite3")
    for b in BANDS:
        archive_db.ensure_account(f"violet_all_cap_{b.value}_spot_paper", "binance", 1000.0)
    archive_db.close()

    campaign_dir = tmp_path / "violet-all-cap-500-v1"
    cfg = _make_500_config()
    runner = AllCapPaperRunner(
        campaign_dir,
        ExchangeRegistry(),
        cfg,
        campaign_hours=72,
        campaign_id="violet-all-cap-500-v1",
        historical_archive_dir=archive_dir,
    )
    try:
        status = runner.status()
        assert status["campaign_id"] == "violet-all-cap-500-v1"
        assert status["budget"]["total_initial_usdt"] == 500.0
        assert status["budget"]["sleeve_initial_usdt"] == 100.0
        assert status["budget"]["sleeve_count"] == 5
        assert status["budget"]["current_equity_usdt"] == 500.0
        assert status["budget"]["leverage"] == 0
        assert status["risk_budget"]["max_leverage_ratio"] == 1.0
        assert len(status["sleeves"]) == 5
        assert [s["band"] for s in status["sleeves"]] == ["micro", "low", "mid", "large", "mega"]
        assert all(s["initial_usdt"] == 100.0 and s["equity_usdt"] == 100.0 for s in status["sleeves"])
        assert status["historical_archive"]["initial_budget_usdt"] == 5000.0
        assert status["historical_archive"]["realized_pnl_usdt"] == 0.0

        async def _no_refresh(force=False):
            return False

        runner.provider.refresh = _no_refresh
        asyncio.run(runner.run_cycle())
        first_started = runner.campaign.record["started_at"]
        first_ends = runner.campaign.record["ends_at"]
    finally:
        runner.close()

    # Restarting in the same campaign dir preserves cycle count and window without double-funding.
    restarted = AllCapPaperRunner(
        campaign_dir,
        ExchangeRegistry(),
        cfg,
        campaign_hours=72,
        campaign_id="violet-all-cap-500-v1",
        historical_archive_dir=archive_dir,
    )
    try:
        st2 = restarted.status()
        assert st2["cycles"] == 1
        assert st2["budget"]["total_initial_usdt"] == 500.0
        assert st2["budget"]["current_equity_usdt"] == 500.0
        assert sum(a["cash_usd"] for a in restarted.db.list_accounts()) == 500.0
        assert restarted.campaign.record["started_at"] == first_started
        assert restarted.campaign.record["ends_at"] == first_ends
    finally:
        restarted.close()

    # Reverting to 1000/sleeve (5000 total) against the 500 DB fails closed.
    with pytest.raises(ValueError, match="sleeve_initial_equity_mismatch"):
        AllCapPaperRunner(
            campaign_dir,
            ExchangeRegistry(),
            AllCapConfig(enabled=True, sleeve_capital_usd=1000.0),
            campaign_hours=72,
        )


def test_viable_sizing_zero_leverage_and_blocked_min_notional(tmp_path: Path):
    cfg = _make_500_config()
    runner = AllCapPaperRunner(tmp_path / "v500", ExchangeRegistry(), cfg, campaign_hours=72)
    try:
        s = snapshot()
        sig = signal(s)
        sizes = [runner.size(sig, s, b) for b in BANDS]
        assert sizes == sorted(sizes)
        assert sizes[0] < sizes[-1]
        assert all(5.0 <= val <= 20.0 for val in sizes)

        # Guardian enforces 0x borrowed leverage (max_leverage=1.0).
        assert runner.engine.risk.policy.max_leverage == 1.0
        perp_sig = sig.model_copy(update={"market_type": MarketType.PERP})
        decision = runner.engine.risk.evaluate(
            signal=perp_sig,
            requested_notional_usd=12.0,
            leverage=2.0,
            total_equity_usd=500.0,
            positions=[],
            snapshot=s,
        )
        assert not decision.approved
        assert "leverage_cap_exceeded" in decision.reason
    finally:
        runner.close()

    # Wide stop forces risk-based notional below min_order_notional_usd (5.0) -> BLOCKED, never rounded up.
    async def fetch(page, size):
        return [
            dict(
                id="midcap",
                symbol="midcap",
                market_cap=1e9,
                circulating_supply=1e6,
                last_updated=datetime.now(timezone.utc).isoformat(),
            )
        ]

    class Adapter:
        exchange_id = ExchangeId.BINANCE
        credentials = None

        async def get_instrument_spec(self, *args):
            return SimpleNamespace(
                price_precision=0.01,
                amount_precision=0.000001,
                min_amount=0.000001,
                min_cost=5.0,
                maker_fee=0.001,
                taker_fee=0.001,
            )

        async def load_markets(self, **kwargs):
            return {"MIDCAP/USDT": dict(symbol="MIDCAP/USDT", base="MIDCAP", quote="USDT", spot=True, active=True)}

    reg = ExchangeRegistry({ExchangeId.BINANCE: Adapter()})
    r2 = AllCapPaperRunner(tmp_path / "blocked", reg, cfg, MarketCapProvider(cfg, fetch), campaign_hours=72)
    try:
        s = snapshot()
        wide_stop_sig = StrategySignal(
            strategy_name="trend_following",
            exchange=s.exchange,
            symbol=s.symbol,
            market_type=MarketType.SPOT,
            direction=Direction.LONG,
            entry_low=19.9,
            entry_high=20.1,
            stop_loss=14.0,  # ~30% stop -> 0.60 / 0.306 = $1.96 notional < $5 min
            take_profit_targets=[38.0],
            invalidation_condition="stop",
            estimated_fee_pct=0.001,
            estimated_slippage_pct=0.0005,
            risk_reward_ratio=3.0,
            confidence_score=0.8,
            rationale="min notional block fixture",
        )

        async def scan(*args, **kwargs):
            return s

        r2.scanner.scan_pair = scan
        r2.selector.select_signals = lambda snaps, caps: ([wide_stop_sig], [])
        state = asyncio.run(r2.run_cycle())
        assert state["orders"] == []
        assert state["fills"] == 0
        assert any(r.get("reason") == "blocked_below_min_order_notional" for r in state["rejections"])
        assert state["entry_gate"]["status"] == "BLOCKED"
        assert state["entry_gate"]["min_notional_blocked_count"] >= 1
    finally:
        r2.close()


def test_stale_feeds_and_daily_circuit_breaker_block_entries(tmp_path: Path):
    cfg = _make_500_config()
    runner = AllCapPaperRunner(tmp_path / "stale", ExchangeRegistry(), cfg, campaign_hours=72)
    try:
        # Simulate stale completed_at timestamp -> entry_gate must report STALE.
        runner.state["cycles"] = 3
        runner.state["completed_at"] = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        st = runner.status()
        assert st["worker_health"] == "stale_or_not_started"
        assert st["entry_gate"]["status"] == "STALE"

        # Trip all 5 sleeve daily loss breakers -> entry_gate must report BLOCKED.
        runner.state["completed_at"] = datetime.now(timezone.utc).isoformat()
        runner.state["worker_health"] = "running"
        today = datetime.now(timezone.utc).date().isoformat()
        for band in BANDS:
            runner.db.save_risk_state(f"{runner.bot_id(band)}:daily", {"day": today, "start": 200.0, "tripped": False})
            runner.sleeve_daily_state(band)
        runner._populate_summary_fields()
        st_blocked = runner.status()
        assert st_blocked["entry_gate"]["status"] == "BLOCKED"
        assert "all_sleeves_daily_loss_tripped" in st_blocked["entry_gate"]["reasons"]
    finally:
        runner.close()


def test_campaign_pointer_persistence_and_router_isolation(tmp_path: Path):
    archive_dir = tmp_path / "violet-all-cap"
    archive_dir.mkdir()
    settings = resolve_violet_campaign_settings(tmp_path, environ={}, persist_pointer=True)
    assert settings.campaign_id == "violet-all-cap-500-v1"
    assert settings.total_budget_usdt == 500.0
    assert settings.sleeve_capital_usdt == 100.0
    assert settings.campaign_dir == (tmp_path / "violet-all-cap-500-v1").resolve()
    assert settings.pointer_path.exists()
    assert stat.S_IMODE(os.stat(settings.pointer_path).st_mode) == 0o600

    # Subsequent calls without env still resolve to the persisted 500 campaign dir.
    reloaded = resolve_violet_campaign_settings(tmp_path, environ={}, persist_pointer=False)
    assert reloaded == settings

    # Refuses pointing active campaign to the historical archive directory.
    with pytest.raises(ValueError, match="active_campaign_dir_cannot_equal_historical_archive_dir"):
        resolve_violet_campaign_settings(
            tmp_path,
            environ={"VIOLET_CAMPAIGN_DIR": str(archive_dir)},
            persist_pointer=False,
        )

    # Closed-loop router remains completely isolated (defaults to $27,500 independent of 500 USDT campaign).
    router = ClosedLoopAssetRouter(state_path=tmp_path / "closed-loop-paper" / "router.sqlite3")
    summary = router.summary()
    assert summary["invariant"]["conservation_ok"] is True
    assert float(summary["invariant"]["current_venue_total_usd"]) == 27500.0
    assert float(summary["invariant"]["current_venue_total_usd"]) != settings.total_budget_usdt


def test_violet_500_research_endpoint_auth_and_readonly_405(tmp_path: Path):
    cfg = _make_500_config()
    runner = AllCapPaperRunner(
        tmp_path / "violet-all-cap-500-v1",
        ExchangeRegistry(),
        cfg,
        campaign_hours=72,
        campaign_id="violet-all-cap-500-v1",
    )
    try:
        app = FastAPI()

        @app.middleware("http")
        async def preview_readonly_guard(request, call_next):
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                return JSONResponse({"detail": "Independent paper preview: HTTP mutations disabled"}, status_code=405)
            return await call_next(request)

        install_violet_status(
            app,
            runner,
            DashboardAuthConfig("violet_user", "violet_secret_pw"),
            state_path=tmp_path / "router.sqlite3",
        )

        with TestClient(app) as client:
            assert client.get("/api/violet/research").status_code == 401
            assert client.get("/api/violet/research", auth=("violet_user", "wrong")).status_code == 401
            assert client.post("/api/violet/research", auth=("violet_user", "violet_secret_pw")).status_code == 405

            res = client.get("/api/violet/research", auth=("violet_user", "violet_secret_pw"))
            assert res.status_code == 200
            body = res.json()
            assert body["live_execution_enabled"] is False
            assert body["accounting"] == "SEPARATE_PAPER_LEDGERS_NO_TRANSFERS"
            assert body["violet"]["campaign_id"] == "violet-all-cap-500-v1"
            assert body["violet"]["budget"]["total_initial_usdt"] == 500.0
            assert len(body["violet"]["sleeves"]) == 5
            assert all(s["initial_usdt"] == 100.0 for s in body["violet"]["sleeves"])
            assert body["violet"]["risk_budget"]["leverage"] == 0
    finally:
        runner.close()
