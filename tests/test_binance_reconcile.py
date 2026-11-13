"""Deterministic mock-based tests for read-only Binance reconciliation engine."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.core.config import ExchangeCredentials
from app.core.models import ExchangeId
from app.core.security import LiveExecutionBlockedError, enforce_read_only_exchange
from app.exchanges.binance import BinanceAdapter
from app.intelligence.binance_intel import assess_reconciliation_coverage
from app.intelligence.binance_reconcile import (
    BinanceReconcileEngine,
    BinanceReconcileStore,
)


class _FakeBinanceClient:
    """Deterministic mock CCXT Binance client supporting spot or futures."""

    def __init__(self, opts: dict[str, Any], state: dict[str, Any]) -> None:
        self.opts = opts
        self.default_type = (opts.get("options") or {}).get("defaultType", "spot")
        self.state = state
        self.closed = False
        if self.default_type == "future":
            self.markets = {
                "BTC/USDT:USDT": {"symbol": "BTC/USDT:USDT", "contract": True, "linear": True},
                "ETH/USDT:USDT": {"symbol": "ETH/USDT:USDT", "contract": True, "linear": True},
                "SOL/USDT:USDT": {"symbol": "SOL/USDT:USDT", "contract": True, "linear": True},
            }
            self.markets_by_id = {
                "BTCUSDT": [{"symbol": "BTC/USDT:USDT", "contract": True, "linear": True}],
                "ETHUSDT": [{"symbol": "ETH/USDT:USDT", "contract": True, "linear": True}],
                "SOLUSDT": [{"symbol": "SOL/USDT:USDT", "contract": True, "linear": True}],
            }
        else:
            self.markets = {
                "BTC/USDT": {"symbol": "BTC/USDT", "spot": True},
                "ETH/USDT": {"symbol": "ETH/USDT", "spot": True},
                "ADA/USDT": {"symbol": "ADA/USDT", "spot": True},
            }
            self.markets_by_id = {
                "BTCUSDT": [{"symbol": "BTC/USDT", "spot": True}],
                "ETHUSDT": [{"symbol": "ETH/USDT", "spot": True}],
                "ADAUSDT": [{"symbol": "ADA/USDT", "spot": True}],
            }

    # Blocked write methods to verify enforce_read_only_exchange
    def create_order(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("Should be blocked by enforce_read_only_exchange")

    def cancel_order(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("Should be blocked by enforce_read_only_exchange")

    def private_post_order(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("Should be blocked by enforce_read_only_exchange")

    def fapiprivate_post_order(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("Should be blocked by enforce_read_only_exchange")

    def load_markets(self) -> dict[str, Any]:
        return self.markets

    def fetch_balance(self) -> dict[str, Any]:
        return self.state.get("spot_balance", {"total": {"USDT": 100.0, "ADA": 50.0}})

    def fetch_positions(self) -> list[dict[str, Any]]:
        return self.state.get("futures_positions", [])

    def private_get_openorders(self) -> list[dict[str, Any]]:
        if self.state.get("spot_open_orders_error"):
            raise RuntimeError("spot open orders failed")
        return self.state.get("spot_native_open_orders", [])

    def fapiprivate_get_openorders(self) -> list[dict[str, Any]]:
        if self.state.get("futures_open_orders_error"):
            raise RuntimeError("futures open orders failed")
        return self.state.get("futures_native_open_orders", [])

    def fetch_open_orders(self, symbol: str, limit: int = 100) -> list[dict[str, Any]]:
        key = "futures_open_by_symbol" if self.default_type == "future" else "spot_open_by_symbol"
        return list(self.state.get(key, {}).get(symbol, []))[:limit]

    def fetch_orders(self, symbol: str, since: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if symbol in self.state.get("error_order_symbols", set()):
            raise ConnectionError(f"Simulated error for {symbol}")
        key = "futures_orders" if self.default_type == "future" else "spot_orders"
        rows = list(self.state.get(key, {}).get(symbol, []))
        if since is not None:
            rows = [r for r in rows if int(r.get("timestamp") or 0) >= since]
        rows.sort(key=lambda r: int(r.get("timestamp") or 0))
        return rows[:limit]

    def fetch_my_trades(self, symbol: str, since: int | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if symbol in self.state.get("error_trade_symbols", set()):
            raise TimeoutError(f"Simulated trade timeout for {symbol}")
        key = "futures_trades" if self.default_type == "future" else "spot_trades"
        rows = list(self.state.get(key, {}).get(symbol, []))
        if since is not None:
            rows = [r for r in rows if int(r.get("timestamp") or 0) >= since]
        rows.sort(key=lambda r: int(r.get("timestamp") or 0))
        return rows[:limit]

    def close(self) -> None:
        self.closed = True


def _make_adapter(state: dict[str, Any], configured: bool = True) -> BinanceAdapter:
    creds = (
        ExchangeCredentials(exchange=ExchangeId.BINANCE, api_key="test_key_1234", api_secret="test_secret_5678")
        if configured
        else None
    )
    return BinanceAdapter(
        credentials=creds,
        max_retries=0,
        client_factory=lambda opts: _FakeBinanceClient(opts, state),
    )


def _create_paper_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(
        """
        CREATE TABLE paper_orders (
            order_id TEXT PRIMARY KEY,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_type TEXT NOT NULL,
            status TEXT NOT NULL,
            is_paper INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE paper_fills (
            fill_id TEXT PRIMARY KEY,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_type TEXT NOT NULL,
            is_paper INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE paper_positions (
            position_id TEXT PRIMARY KEY,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_type TEXT NOT NULL,
            is_paper INTEGER NOT NULL DEFAULT 1
        );
        INSERT INTO paper_orders VALUES ('po1', 'binance', 'BTC/USDT', 'spot', 'OPEN', 1);
        INSERT INTO paper_orders VALUES ('po2', 'binance', 'ETH/USDT', 'spot', 'FILLED', 1);
        INSERT INTO paper_fills VALUES ('pf1', 'binance', 'ETH/USDT', 'spot', 1);
        """
    )
    conn.commit()
    conn.close()


def test_read_only_guard_blocks_writes_and_allows_native_get() -> None:
    client = enforce_read_only_exchange(_FakeBinanceClient({}, {}))
    with pytest.raises(LiveExecutionBlockedError):
        client.create_order()
    with pytest.raises(LiveExecutionBlockedError):
        client.cancel_order()
    with pytest.raises(LiveExecutionBlockedError):
        client.private_post_order()
    with pytest.raises(LiveExecutionBlockedError):
        client.fapiprivate_post_order()
    # Read-only native GETs remain callable
    assert client.private_get_openorders() == []
    assert client.fapiprivate_get_openorders() == []


@pytest.mark.asyncio
async def test_unconfigured_adapter_returns_unconfigured(tmp_path: Path) -> None:
    store = BinanceReconcileStore(tmp_path / "reconcile.sqlite3")
    engine = BinanceReconcileEngine(store=store)
    adapter = _make_adapter({}, configured=False)
    try:
        res = await engine.run(adapter, mode="health_check")
        assert res["status"] == "UNCONFIGURED"
        assert res["writes_allowed"] is False
        assert res["live_execution_disabled"] is True
        assert res["complete_account_reconciliation"] is False
    finally:
        store.close()
        adapter.close()


@pytest.mark.asyncio
async def test_account_wide_open_orders_discovery_and_paper_separation(tmp_path: Path) -> None:
    paper_db = tmp_path / "paper.sqlite3"
    _create_paper_db(paper_db)

    state = {
        "spot_balance": {"total": {"USDT": 500.0, "ADA": 25.0}},
        "spot_native_open_orders": [
            {"id": "spot-open-1", "symbol": "ETHUSDT", "side": "buy", "amount": 0.1, "price": 3000.0, "timestamp": 1000}
        ],
        "futures_positions": [
            {"symbol": "SOL/USDT:USDT", "contracts": 2.0, "info": {"positionAmt": "2.0"}}
        ],
        "futures_native_open_orders": [
            {"id": "fut-open-1", "symbol": "BTCUSDT", "side": "sell", "amount": 0.05, "price": 90000.0, "timestamp": 2000}
        ],
        "spot_orders": {
            "BTC/USDT": [{"id": "so1", "timestamp": 1000, "side": "buy", "amount": 0.01, "price": 80000.0, "status": "closed"}]
        },
        "futures_orders": {
            "BTC/USDT:USDT": [{"id": "fo1", "timestamp": 2000, "side": "sell", "amount": 0.05, "price": 90000.0, "status": "open"}]
        },
        "futures_trades": {},  # SOL/USDT:USDT has open position but 0 trades -> mismatch!
    }

    store = BinanceReconcileStore(tmp_path / "reconcile.sqlite3")
    engine = BinanceReconcileEngine(
        store=store,
        paper_db_path=paper_db,
        spot_watchlist=("BTC/USDT",),
        perp_watchlist=("BTC/USDT:USDT",),
        page_limit=10,
    )
    adapter = _make_adapter(state)
    try:
        res = await engine.run(adapter, mode="health_check")
        assert res["status"] == "PARTIAL_SNAPSHOT"
        assert res["complete_account_reconciliation"] is False
        assert res["writes_allowed"] is False
        assert res["spot_account_open_orders"]["count"] == 1
        assert res["futures"]["account_open_orders"]["count"] == 1
        assert res["coverage"]["account_wide_open_orders_complete"] is True

        # Implicated markets discovered from balances, open orders, and positions
        assert "ADA/USDT" in res["implicated_markets"]["spot_from_balances"]
        assert "ETH/USDT" in res["implicated_markets"]["spot_from_open_orders"]
        assert "SOL/USDT:USDT" in res["implicated_markets"]["futures_from_positions"]
        assert "BTC/USDT:USDT" in res["implicated_markets"]["futures_from_open_orders"]
        assert "ADA/USDT" in res["symbols"]
        assert "ETH/USDT" in res["symbols"]
        assert "SOL/USDT:USDT" in res["futures"]["symbols"]

        # Paper ledger is strictly separated
        pl = res["paper_ledger"]
        assert pl["is_paper"] is True
        assert pl["paper_equated_with_real"] is False
        assert pl["binance_paper_orders"] == 2
        assert pl["open_paper_orders"] == 1
        assert pl["binance_paper_fills"] == 1

        categories = {m["category"] for m in res["mismatches"]}
        assert "EXCHANGE_POSITION_WITHOUT_FILL_HISTORY" in categories
        assert "PAPER_STALE_OPEN_ORDERS" in categories
        assert "PAPER_EXCHANGE_SEPARATION_INFO" in categories

        # Coverage assessment must NOT allow completeness claim
        cov = assess_reconciliation_coverage(res)
        assert cov.completeness_claim_allowed is False
        assert cov.complete_account_reconciliation is False
    finally:
        store.close()
        adapter.close()


@pytest.mark.asyncio
async def test_pagination_deduplication_and_durable_resume(tmp_path: Path) -> None:
    # 5 orders across timestamps 1000..5000 with page_limit=2 -> requires 3 pages to exhaust
    orders = [
        {"id": f"ord-{i}", "timestamp": i * 1000, "side": "buy", "amount": 1.0, "price": 100.0, "status": "closed"}
        for i in range(1, 6)
    ]
    state = {
        "spot_balance": {"total": {"USDT": 100.0}},
        "spot_orders": {"BTC/USDT": orders},
        "spot_trades": {"BTC/USDT": []},
    }
    db_path = tmp_path / "reconcile.sqlite3"
    store1 = BinanceReconcileStore(db_path)
    engine1 = BinanceReconcileEngine(
        store=store1,
        spot_watchlist=("BTC/USDT",),
        perp_watchlist=(),
        page_limit=2,
    )
    adapter = _make_adapter(state)
    try:
        # Run 1: bounded to max_pages=1 -> fetches ord-1, ord-2, hits max_pages_bound_reached
        res1 = await engine1.run(adapter, mode="backfill", max_pages=1, request_delay_sec=0.0)
        ep1 = res1["symbols"]["BTC/USDT"]["recent_orders"]
        assert ep1["status"] == "ok"
        assert ep1["count"] == 2
        assert ep1["newly_inserted"] == 2
        assert ep1["exhausted"] is False
        assert ep1["has_pagination_gap"] is True
        assert ep1["gap_reason"] == "max_pages_bound_reached"
        assert ep1["latest_timestamp"] == 2000
    finally:
        store1.close()

    # Run 2: new store instance (proves durability), max_pages=5 -> resumes from ts=2000,
    # deduplicates ord-2, and exhausts through ord-5!
    store2 = BinanceReconcileStore(db_path)
    engine2 = BinanceReconcileEngine(
        store=store2,
        spot_watchlist=("BTC/USDT",),
        perp_watchlist=(),
        page_limit=2,
    )
    try:
        res2 = await engine2.run(adapter, mode="backfill", max_pages=5, request_delay_sec=0.0)
        ep2 = res2["symbols"]["BTC/USDT"]["recent_orders"]
        assert ep2["status"] == "ok"
        assert ep2["resumed_from_timestamp"] == 2000
        assert ep2["exhausted"] is True
        assert ep2["has_pagination_gap"] is False
        assert ep2["total_stored"] == 5
        assert ep2["newly_inserted"] == 3
    finally:
        store2.close()
        adapter.close()


@pytest.mark.asyncio
async def test_error_handling_and_mismatch_reporting(tmp_path: Path) -> None:
    state = {
        "spot_balance": {"total": {"USDT": 100.0}},
        "spot_native_open_orders": [
            {"id": "o1", "symbol": "BTCUSDT", "side": "buy", "amount": 0.1, "price": 50000.0, "timestamp": 1000}
        ],
        "error_order_symbols": {"BTC/USDT"},
        "error_trade_symbols": {"BTC/USDT"},
    }
    store = BinanceReconcileStore(tmp_path / "reconcile.sqlite3")
    engine = BinanceReconcileEngine(
        store=store,
        spot_watchlist=("BTC/USDT",),
        perp_watchlist=(),
    )
    adapter = _make_adapter(state)
    try:
        res = await engine.run(adapter, mode="health_check", request_delay_sec=0.0)
        btc = res["symbols"]["BTC/USDT"]
        assert btc["recent_orders"]["status"] == "error"
        assert btc["recent_orders"]["error_type"] == "ConnectionError"
        assert btc["recent_trades"]["status"] == "error"
        assert btc["recent_trades"]["error_type"] == "TimeoutError"

        categories = {m["category"] for m in res["mismatches"]}
        assert "EXCHANGE_ENDPOINT_ERROR" in categories
        assert "EXCHANGE_OPEN_ORDER_HISTORY_GAP" in categories
    finally:
        store.close()
        adapter.close()
