"""Tests for Decimal PnL tracker, append-only ledger, strategy intelligence, and 7-tier cap categories."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.accounting.pnl_tracker import build_pnl_report, classify_research_cap_category
from app.core.models import (
    Direction,
    ExchangeId,
    MarketType,
    OrderStatus,
    OrderType,
    PaperFill,
    PaperOrder,
    PaperPosition,
)
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.violet_status import install_violet_status
from app.persistence.database import PaperDatabase


def test_research_cap_categories():
    assert classify_research_cap_category(150_000_000_000) == "mega_100b_plus"
    assert classify_research_cap_category(25_000_000_000) == "large_10b_100b"
    assert classify_research_cap_category(2_000_000_000) == "mid_1b_10b"
    assert classify_research_cap_category(250_000_000) == "small_100m_1b"
    assert classify_research_cap_category(40_000_000) == "micro_10m_100m"
    assert classify_research_cap_category(5_000_000) == "nano_under_10m"
    assert classify_research_cap_category(None) == "unknown_unverified"


def test_pnl_tracker_decimal_accounting_partial_fills_and_reconciliation(tmp_path: Path):
    db_path = tmp_path / "pnl.sqlite3"
    db = PaperDatabase(db_path)
    bot_id = "violet_all_cap_mid_spot_paper"
    db.ensure_account(bot_id, "binance", 100.0)
    now = datetime.now(timezone.utc)

    db.append_ledger_event(
        event_id=f"alloc:{bot_id}",
        timestamp_utc=now.isoformat(),
        event_type="CAPITAL_ALLOCATION",
        bot_id=bot_id,
        exchange="binance",
        market_cap_band="mid",
        amount_usd="100.0",
        idempotency_key=f"alloc:{bot_id}",
    )
    # Duplicate idempotency key must be ignored
    assert (
        db.append_ledger_event(
            event_id=f"alloc:{bot_id}:dup",
            timestamp_utc=now.isoformat(),
            event_type="CAPITAL_ALLOCATION",
            bot_id=bot_id,
            exchange="binance",
            market_cap_band="mid",
            amount_usd="100.0",
            idempotency_key=f"alloc:{bot_id}",
        )
        is False
    )

    order = PaperOrder(
        order_id="ord-1",
        client_order_id="cid-1",
        signal_id="sig-1",
        bot_id=bot_id,
        exchange=ExchangeId.BINANCE,
        symbol="OP/USDT",
        market_type=MarketType.SPOT,
        side="buy",
        direction=Direction.LONG,
        order_type=OrderType.LIMIT,
        requested_qty=10.0,
        limit_price=2.0,
        notional_usd=20.0,
        leverage=1.0,
        status=OrderStatus.FILLED,
        filled_qty=10.0,
        avg_fill_price=2.0,
        strategy_name="trend_following",
        created_at=now,
        updated_at=now,
    )
    db.save_order(order)
    fill = PaperFill(
        fill_id="fill-1",
        order_id="ord-1",
        bot_id=bot_id,
        exchange=ExchangeId.BINANCE,
        symbol="OP/USDT",
        market_type=MarketType.SPOT,
        side="buy",
        direction=Direction.LONG,
        qty=10.0,
        price=2.0,
        fee_usd=0.02,
        slippage_bps=1.0,
        timestamp=now,
    )
    db.save_fill(fill)
    exit_order = PaperOrder(
        order_id="ord-1-exit",
        client_order_id="cid-1-exit",
        signal_id="sig-1-exit",
        bot_id=bot_id,
        exchange=ExchangeId.BINANCE,
        symbol="OP/USDT",
        market_type=MarketType.SPOT,
        side="sell",
        direction=Direction.LONG,
        order_type=OrderType.LIMIT,
        requested_qty=5.0,
        limit_price=2.4,
        notional_usd=12.0,
        leverage=1.0,
        status=OrderStatus.FILLED,
        filled_qty=5.0,
        avg_fill_price=2.4,
        fee_usd=0.012,
        strategy_name="trend_following",
        created_at=now,
        updated_at=now,
    )
    db.save_order(exit_order)
    db.record_paper_close(
        exit_order,
        close_qty=5.0,
        entry_price=2.0,
        gross_realized=2.0,
    )
    pos = PaperPosition(
        position_id="pos-1",
        bot_id=bot_id,
        exchange=ExchangeId.BINANCE,
        symbol="OP/USDT",
        market_type=MarketType.SPOT,
        direction=Direction.LONG,
        qty=5.0,
        entry_price=2.0,
        mark_price=2.4,
        stop_loss=1.8,
        take_profit_targets=[2.8],
        leverage=1.0,
        unrealized_pnl_usd=2.0,
        realized_pnl_usd=1.988,
        fees_usd=0.032,
        strategy_name="trend_following",
        opened_at=now,
        updated_at=now,
    )
    db.upsert_position(pos)

    report = build_pnl_report(db)
    ov = report["overview"]
    assert ov["starting_capital_usdt"] == 100.0
    assert ov["realized_pnl_usdt"] == 2.0
    assert ov["unrealized_pnl_usdt"] == 2.0
    assert ov["current_equity_usdt"] == 104.0
    assert ov["win_rate_pct"] == 100.0
    assert ov["active_positions"] == 1
    assert report["reconciliation"]["balanced"] is True
    assert report["reconciliation"]["drift_usd"] == 0.0
    assert len(report["assets"]) == 1
    assert report["assets"][0]["asset"] == "OP/USDT"
    assert report["assets"][0]["status"] == "open"
    db.close()

    # Restart persistence check
    db2 = PaperDatabase(db_path)
    report2 = build_pnl_report(db2)
    assert report2["overview"]["current_equity_usdt"] == 104.0
    assert len(report2["ledger"]) == 1
    db2.close()


def test_pnl_and_ledger_endpoints(tmp_path: Path):
    db = PaperDatabase(tmp_path / "ep.sqlite3")
    db.ensure_account("violet_all_cap_low_spot_paper", "binance", 100.0)
    app = FastAPI()
    auth = DashboardAuthConfig("trader", "pass-123")

    class Runner:
        def __init__(self, d):
            self.db = d
            self.state = {"cycles": 1}

        def status(self):
            return self.state

        def pnl_report(self, **kwargs):
            return build_pnl_report(self.db, **kwargs)

    install_violet_status(app, Runner(db), auth, state_path=tmp_path / "router.sqlite3")
    client = TestClient(app)

    assert client.get("/api/violet/pnl").status_code == 401
    r = client.get("/api/violet/pnl", auth=("trader", "pass-123"))
    assert r.status_code == 200
    assert r.json()["mode"] == "PAPER_ONLY"
    assert r.json()["live_accounts_excluded"] is True
    assert r.json()["overview"]["starting_capital_usdt"] == 100.0

    r2 = client.get("/api/violet/ledger", auth=("trader", "pass-123"))
    assert r2.status_code == 200
    assert r2.json()["reconciliation"]["balanced"] is True
    db.close()


def test_all_cap_runner_synthetic_full_lifecycle(tmp_path: Path):
    """Deterministic end-to-end test: precision alignment -> order -> fill -> position -> exit -> ledger -> restart."""
    import asyncio
    from datetime import timedelta
    from app.core.config import AllCapConfig
    from app.core.models import Candle, InstrumentSpec, MarketRegime, MarketSnapshot, OrderBookSnapshot, StrategySignal
    from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED
    from app.execution.all_cap_runner import AllCapPaperRunner
    from app.scanner.market_cap import MarketCapBand, MarketCapRecord
    from app.scanner.mid_low_cap_discovery import DiscoveredSpotMarket

    assert LIVE_EXECUTION_PERMANENTLY_DISABLED is True

    now = datetime.now(timezone.utc)
    ts_ms = int(now.timestamp() * 1000)

    def make_candles(interval_sec: int, count: int = 80) -> list[Candle]:
        end_ms = (ts_ms // (interval_sec * 1000)) * (interval_sec * 1000)
        return [
            Candle(
                timestamp=end_ms - (count - 1 - i) * interval_sec * 1000,
                open=99.8,
                high=100.5,
                low=99.5,
                close=100.0,
                volume=1000.0,
            )
            for i in range(count)
        ]

    class FakeAdapter:
        exchange_id = ExchangeId.BINANCE
        credentials = None

        def health_status(self):
            return {"ok": True}

        async def get_validated_spot_spec(self, symbol: str):
            return InstrumentSpec(
                exchange=ExchangeId.BINANCE,
                symbol=symbol,
                market_type=MarketType.SPOT,
                base="SOL",
                quote="USDT",
                price_precision=0.01,
                amount_precision=0.001,
                min_amount=0.001,
                min_cost=5.0,
                maker_fee=0.001,
                taker_fee=0.001,
            )

        async def fetch_ticker(self, symbol, market_type):
            return {"last": 100.0, "bid": 99.95, "ask": 100.05, "timestamp": ts_ms}

        async def fetch_order_book(self, symbol, market_type, limit=10):
            return OrderBookSnapshot(
                exchange=ExchangeId.BINANCE,
                symbol=symbol,
                market_type=market_type,
                timestamp=now,
                source_timestamp_ms=ts_ms,
                bids=[(99.95, 500.0)],
                asks=[(100.05, 500.0)],
            )

        async def fetch_ohlcv(self, symbol, market_type, timeframe, limit=100):
            return make_candles(900 if timeframe == "15m" else 3600, 80)

    class FakeRegistry:
        def __init__(self, adapter):
            self._a = adapter

        def all(self):
            return {ExchangeId.BINANCE: self._a}

        def get(self, ex):
            return self._a

    cap_rec = MarketCapRecord(
        base_symbol="SOL",
        market_cap_usd=40_000_000_000.0,
        circulating_supply=400_000_000.0,
        band=MarketCapBand.LARGE,
        source="coingecko",
        source_id="solana",
        source_updated_at=now,
        is_fresh=True,
    )

    class FakeProvider:
        last_error = None

        def classify_symbol(self, base, now_arg=None):
            return cap_rec

        def status_summary(self):
            return {"cached": 1}

    cfg = AllCapConfig(
        enabled=True,
        sleeve_capital_usd=100.0,
        max_risk_per_trade_pct=0.01,
        min_order_notional_usd=5.0,
        min_24h_volume_usd=150_000.0,
        min_order_book_depth_usd=5_000.0,
        max_spread_bps=35.0,
    )
    lab_dir = tmp_path / "violet_lab"
    runner = AllCapPaperRunner(lab_dir, FakeRegistry(FakeAdapter()), cfg, cap_provider=FakeProvider(), campaign_hours=72)

    async def fake_discover(adapter, force=False):
        return [
            DiscoveredSpotMarket(
                exchange=ExchangeId.BINANCE,
                symbol="SOL/USDT",
                base="SOL",
                quote="USDT",
                market_type=MarketType.SPOT,
                active=True,
                cap_record=cap_rec,
            )
        ]

    runner.discovery.discover_for_exchange = fake_discover
    runner.discovery.next_global_scan_batch = lambda fair: fair

    current_price = {"px": 100.0}

    async def fake_scan_pair(checked, symbol, mtype, include_funding):
        px = current_price["px"]
        return MarketSnapshot(
            exchange=ExchangeId.BINANCE,
            symbol="SOL/USDT",
            market_type=MarketType.SPOT,
            timestamp=datetime.now(timezone.utc),
            ticker_source_timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            book_source_timestamp_ms=int(datetime.now(timezone.utc).timestamp() * 1000),
            last_price=px,
            bid=px - 0.05,
            ask=px + 0.05,
            bids=[(px - 0.05, 500.0)],
            asks=[(px + 0.05, 500.0)],
            spread_bps=10.0,
            volume_24h_usd=50_000_000.0,
            order_book_depth_usd=100_000.0,
            relative_volume=1.5,
            volatility=0.02,
            trend_strength=28.0,
            rsi=58.0,
            macd=0.5,
            macd_signal=0.3,
            macd_hist=0.2,
            atr=1.5,
            ema_fast=px,
            ema_slow=px - 0.5,
            ema_trend=px - 1.0,
            ema_alignment="bullish",
            support=px - 3.0,
            resistance=px + 5.0,
            liquidity_score=0.95,
            regime=MarketRegime.TRENDING_BULLISH,
            candles_by_tf={"15m": make_candles(900), "1h": make_candles(3600)},
        )

    runner.scanner.scan_pair = fake_scan_pair

    # Intentionally unaligned entry_high (100.12345) to verify _cycle quantizes to 0.01 tick and 0.001 qty
    def fake_select_signals(snaps, caps):
        if current_price["px"] > 102.0:
            return [], []
        return [
            StrategySignal(
                signal_id="sig-sol-1",
                strategy_name="trend_following",
                exchange=ExchangeId.BINANCE,
                symbol="SOL/USDT",
                market_type=MarketType.SPOT,
                direction=Direction.LONG,
                entry_low=99.0,
                entry_high=100.12345,
                stop_loss=97.0,
                take_profit_targets=[108.0],
                confidence_score=0.75,
                risk_reward_ratio=2.5,
                invalidation_condition="close below 97",
                rationale="synthetic bullish test",
                regime=MarketRegime.TRENDING_BULLISH,
                estimated_fee_pct=0.001,
                estimated_slippage_pct=0.0005,
            )
        ], []

    runner.selector.select_signals = fake_select_signals

    st1 = asyncio.run(runner.run_cycle())
    assert len(st1["orders"]) == 1, st1["rejections"]
    assert st1["fills"] == 1
    assert st1["positions"] == 1

    # Duplicate-order prevention within same 1m bucket
    st_dup = asyncio.run(runner.run_cycle())
    assert st_dup["fills"] == 1

    # Trigger protective take-profit exit
    current_price["px"] = 108.0
    st2 = asyncio.run(runner.run_cycle())
    assert st2["positions"] == 0

    rep = runner.pnl_report()
    assert rep["reconciliation"]["balanced"] is True
    assert rep["reconciliation"]["drift_usd"] == 0.0
    assert rep["overview"]["realized_pnl_usdt"] > 0
    ev_types = {e["event_type"] for e in runner.db.list_ledger_events(limit=100)}
    assert {"CAPITAL_ALLOCATION", "ORDER_SUBMITTED", "FILL", "POSITION_CLOSE"}.issubset(ev_types)
    runner.close()

    # Restart recovery check
    runner2 = AllCapPaperRunner(lab_dir, FakeRegistry(FakeAdapter()), cfg, cap_provider=FakeProvider(), campaign_hours=72)
    rep2 = runner2.pnl_report()
    assert rep2["reconciliation"]["balanced"] is True
    assert rep2["overview"]["realized_pnl_usdt"] == rep["overview"]["realized_pnl_usdt"]
    runner2.close()
