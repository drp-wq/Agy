"""Read-only, durable, resumable Binance Spot & USDT-M Futures reconciliation engine.

Strictly read-only: never submits orders, never enables live execution, never
exposes credentials, and never equates simulated paper orders with real exchange records.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from app.core.security import (
    LIVE_EXECUTION_PERMANENTLY_DISABLED,
    assert_paper_only,
    enforce_read_only_exchange,
)
from app.exchanges.binance import BinanceAdapter

DEFAULT_SPOT_WATCHLIST: tuple[str, ...] = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "BMT/USDT",
    "ONE/USDT",
)
DEFAULT_PERP_WATCHLIST: tuple[str, ...] = (
    "BTC/USDT:USDT",
    "ETH/USDT:USDT",
    "SOL/USDT:USDT",
    "BNB/USDT:USDT",
)
QUOTE_ASSETS_EXCLUDED_FROM_SYMBOL_DISCOVERY: frozenset[str] = frozenset(
    {"USDT", "USDC", "FDUSD", "BUSD", "TUSD", "DAI"}
)
FUTURES_RETENTION_DAYS = 90
SPOT_RETENTION_NOTE = "Symbol-specific allOrders/myTrades endpoints; retention and rate limits apply."

RunMode = Literal["health_check", "backfill"]


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(val: Any, default: float | None = None) -> float | None:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _canonical_order_id(row: dict[str, Any]) -> str:
    oid = row.get("id") or row.get("orderId")
    if oid is not None and str(oid) != "":
        return str(oid)
    ts = _safe_int(row.get("timestamp") or row.get("time"))
    side = str(row.get("side") or "")
    amt = str(row.get("amount") or row.get("origQty") or "")
    price = str(row.get("price") or "")
    return f"anon:{ts}:{side}:{amt}:{price}"


def _canonical_trade_id(row: dict[str, Any]) -> str:
    tid = row.get("id")
    if tid is not None and str(tid) != "":
        return str(tid)
    ts = _safe_int(row.get("timestamp"))
    oid = str(row.get("order") or "")
    side = str(row.get("side") or "")
    amt = str(row.get("amount") or "")
    price = str(row.get("price") or "")
    return f"anon:{ts}:{oid}:{side}:{amt}:{price}"


def _resolve_market_symbol(markets_by_id: dict[str, Any], market_id: str, market_type: Literal["spot", "perp"]) -> str | None:
    if not market_id:
        return None
    entry = markets_by_id.get(market_id)
    if isinstance(entry, list):
        if market_type == "spot":
            match = next((m for m in entry if m.get("spot")), None)
        else:
            match = next((m for m in entry if m.get("contract") or m.get("swap") or m.get("linear")), None)
        entry = match or (entry[0] if entry else None)
    if isinstance(entry, dict) and entry.get("symbol"):
        return str(entry["symbol"])
    return None


@dataclass
class WatermarkState:
    exchange: str
    market_type: str
    symbol: str
    stream_type: str
    last_timestamp_ms: int = 0
    last_record_id: str | None = None
    oldest_timestamp_ms: int | None = None
    total_records: int = 0
    pages_fetched: int = 0
    exhausted: bool = False
    has_pagination_gap: bool = False
    gap_reason: str | None = None
    retention_window_days: int | None = None
    last_run_at_utc: str | None = None
    last_status: str | None = None
    last_error_type: str | None = None


class BinanceReconcileStore:
    """Durable SQLite persistence for Binance reconciliation watermarks, orders, trades, and runs."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS reconcile_watermarks (
                exchange TEXT NOT NULL,
                market_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                stream_type TEXT NOT NULL,
                last_timestamp_ms INTEGER NOT NULL DEFAULT 0,
                last_record_id TEXT,
                oldest_timestamp_ms INTEGER,
                total_records INTEGER NOT NULL DEFAULT 0,
                pages_fetched INTEGER NOT NULL DEFAULT 0,
                exhausted INTEGER NOT NULL DEFAULT 0,
                has_pagination_gap INTEGER NOT NULL DEFAULT 0,
                gap_reason TEXT,
                retention_window_days INTEGER,
                last_run_at_utc TEXT,
                last_status TEXT,
                last_error_type TEXT,
                PRIMARY KEY (exchange, market_type, symbol, stream_type)
            );

            CREATE TABLE IF NOT EXISTS exchange_orders (
                exchange TEXT NOT NULL,
                market_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                order_id TEXT NOT NULL,
                client_order_id TEXT,
                status TEXT,
                side TEXT,
                order_type TEXT,
                price REAL,
                amount REAL,
                filled REAL,
                remaining REAL,
                timestamp_ms INTEGER,
                datetime_utc TEXT,
                provenance TEXT NOT NULL,
                account_scope TEXT NOT NULL,
                raw_json TEXT,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (exchange, market_type, symbol, order_id)
            );

            CREATE INDEX IF NOT EXISTS idx_exchange_orders_ts
                ON exchange_orders (exchange, market_type, symbol, timestamp_ms);

            CREATE TABLE IF NOT EXISTS exchange_trades (
                exchange TEXT NOT NULL,
                market_type TEXT NOT NULL,
                symbol TEXT NOT NULL,
                trade_id TEXT NOT NULL,
                order_id TEXT,
                side TEXT,
                price REAL,
                amount REAL,
                cost REAL,
                fee_cost REAL,
                fee_currency TEXT,
                timestamp_ms INTEGER,
                datetime_utc TEXT,
                provenance TEXT NOT NULL,
                account_scope TEXT NOT NULL,
                raw_json TEXT,
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (exchange, market_type, symbol, trade_id)
            );

            CREATE INDEX IF NOT EXISTS idx_exchange_trades_ts
                ON exchange_trades (exchange, market_type, symbol, timestamp_ms);

            CREATE TABLE IF NOT EXISTS reconcile_runs (
                run_id TEXT PRIMARY KEY,
                run_mode TEXT NOT NULL,
                started_at_utc TEXT NOT NULL,
                finished_at_utc TEXT NOT NULL,
                status TEXT NOT NULL,
                summary_json TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def get_watermark(
        self, exchange: str, market_type: str, symbol: str, stream_type: str
    ) -> WatermarkState | None:
        row = self._conn.execute(
            """
            SELECT * FROM reconcile_watermarks
            WHERE exchange=? AND market_type=? AND symbol=? AND stream_type=?
            """,
            (exchange, market_type, symbol, stream_type),
        ).fetchone()
        if not row:
            return None
        return WatermarkState(
            exchange=row["exchange"],
            market_type=row["market_type"],
            symbol=row["symbol"],
            stream_type=row["stream_type"],
            last_timestamp_ms=int(row["last_timestamp_ms"] or 0),
            last_record_id=row["last_record_id"],
            oldest_timestamp_ms=row["oldest_timestamp_ms"],
            total_records=int(row["total_records"] or 0),
            pages_fetched=int(row["pages_fetched"] or 0),
            exhausted=bool(row["exhausted"]),
            has_pagination_gap=bool(row["has_pagination_gap"]),
            gap_reason=row["gap_reason"],
            retention_window_days=row["retention_window_days"],
            last_run_at_utc=row["last_run_at_utc"],
            last_status=row["last_status"],
            last_error_type=row["last_error_type"],
        )

    def save_watermark(self, wm: WatermarkState) -> None:
        self._conn.execute(
            """
            INSERT INTO reconcile_watermarks (
                exchange, market_type, symbol, stream_type,
                last_timestamp_ms, last_record_id, oldest_timestamp_ms,
                total_records, pages_fetched, exhausted, has_pagination_gap,
                gap_reason, retention_window_days, last_run_at_utc,
                last_status, last_error_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(exchange, market_type, symbol, stream_type) DO UPDATE SET
                last_timestamp_ms=excluded.last_timestamp_ms,
                last_record_id=excluded.last_record_id,
                oldest_timestamp_ms=excluded.oldest_timestamp_ms,
                total_records=excluded.total_records,
                pages_fetched=excluded.pages_fetched,
                exhausted=excluded.exhausted,
                has_pagination_gap=excluded.has_pagination_gap,
                gap_reason=excluded.gap_reason,
                retention_window_days=excluded.retention_window_days,
                last_run_at_utc=excluded.last_run_at_utc,
                last_status=excluded.last_status,
                last_error_type=excluded.last_error_type
            """,
            (
                wm.exchange,
                wm.market_type,
                wm.symbol,
                wm.stream_type,
                wm.last_timestamp_ms,
                wm.last_record_id,
                wm.oldest_timestamp_ms,
                wm.total_records,
                wm.pages_fetched,
                1 if wm.exhausted else 0,
                1 if wm.has_pagination_gap else 0,
                wm.gap_reason,
                wm.retention_window_days,
                wm.last_run_at_utc,
                wm.last_status,
                wm.last_error_type,
            ),
        )
        self._conn.commit()

    def upsert_orders(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        orders: list[dict[str, Any]],
        provenance: str,
        account_scope: str,
    ) -> int:
        if not orders:
            return 0
        now_iso = _utc_iso()
        inserted = 0
        for row in orders:
            oid = _canonical_order_id(row)
            exists = self._conn.execute(
                "SELECT 1 FROM exchange_orders WHERE exchange=? AND market_type=? AND symbol=? AND order_id=?",
                (exchange, market_type, symbol, oid),
            ).fetchone()
            if not exists:
                inserted += 1
            ts = _safe_int(row.get("timestamp") or row.get("time"))
            self._conn.execute(
                """
                INSERT INTO exchange_orders (
                    exchange, market_type, symbol, order_id, client_order_id,
                    status, side, order_type, price, amount, filled, remaining,
                    timestamp_ms, datetime_utc, provenance, account_scope,
                    raw_json, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, market_type, symbol, order_id) DO UPDATE SET
                    client_order_id=COALESCE(excluded.client_order_id, exchange_orders.client_order_id),
                    status=COALESCE(excluded.status, exchange_orders.status),
                    filled=COALESCE(excluded.filled, exchange_orders.filled),
                    remaining=COALESCE(excluded.remaining, exchange_orders.remaining),
                    timestamp_ms=COALESCE(excluded.timestamp_ms, exchange_orders.timestamp_ms),
                    datetime_utc=COALESCE(excluded.datetime_utc, exchange_orders.datetime_utc),
                    provenance=excluded.provenance,
                    raw_json=excluded.raw_json,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    exchange,
                    market_type,
                    symbol,
                    oid,
                    row.get("clientOrderId") or row.get("client_order_id"),
                    row.get("status"),
                    row.get("side"),
                    row.get("type"),
                    _safe_float(row.get("price")),
                    _safe_float(row.get("amount") or row.get("origQty")),
                    _safe_float(row.get("filled") or row.get("executedQty")),
                    _safe_float(row.get("remaining")),
                    ts,
                    row.get("datetime"),
                    provenance,
                    account_scope,
                    json.dumps(row, default=str),
                    now_iso,
                ),
            )
        self._conn.commit()
        return inserted

    def upsert_trades(
        self,
        *,
        exchange: str,
        market_type: str,
        symbol: str,
        trades: list[dict[str, Any]],
        provenance: str,
        account_scope: str,
    ) -> int:
        if not trades:
            return 0
        now_iso = _utc_iso()
        inserted = 0
        for row in trades:
            tid = _canonical_trade_id(row)
            exists = self._conn.execute(
                "SELECT 1 FROM exchange_trades WHERE exchange=? AND market_type=? AND symbol=? AND trade_id=?",
                (exchange, market_type, symbol, tid),
            ).fetchone()
            if not exists:
                inserted += 1
            fee = row.get("fee") or {}
            ts = _safe_int(row.get("timestamp"))
            self._conn.execute(
                """
                INSERT INTO exchange_trades (
                    exchange, market_type, symbol, trade_id, order_id,
                    side, price, amount, cost, fee_cost, fee_currency,
                    timestamp_ms, datetime_utc, provenance, account_scope,
                    raw_json, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(exchange, market_type, symbol, trade_id) DO UPDATE SET
                    order_id=COALESCE(excluded.order_id, exchange_trades.order_id),
                    fee_cost=COALESCE(excluded.fee_cost, exchange_trades.fee_cost),
                    fee_currency=COALESCE(excluded.fee_currency, exchange_trades.fee_currency),
                    provenance=excluded.provenance,
                    raw_json=excluded.raw_json,
                    updated_at_utc=excluded.updated_at_utc
                """,
                (
                    exchange,
                    market_type,
                    symbol,
                    tid,
                    row.get("order"),
                    row.get("side"),
                    _safe_float(row.get("price")),
                    _safe_float(row.get("amount")),
                    _safe_float(row.get("cost")),
                    _safe_float(fee.get("cost") if isinstance(fee, dict) else None),
                    fee.get("currency") if isinstance(fee, dict) else None,
                    ts,
                    row.get("datetime"),
                    provenance,
                    account_scope,
                    json.dumps(row, default=str),
                    now_iso,
                ),
            )
        self._conn.commit()
        return inserted

    def count_records(self, table: Literal["exchange_orders", "exchange_trades"], market_type: str, symbol: str | None = None) -> int:
        if symbol is not None:
            row = self._conn.execute(
                f"SELECT count(*) FROM {table} WHERE exchange='binance' AND market_type=? AND symbol=?",
                (market_type, symbol),
            ).fetchone()
        else:
            row = self._conn.execute(
                f"SELECT count(*) FROM {table} WHERE exchange='binance' AND market_type=?",
                (market_type,),
            ).fetchone()
        return int(row[0]) if row else 0

    def record_run(self, run_id: str, run_mode: str, started_at_utc: str, finished_at_utc: str, status: str, summary: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO reconcile_runs (run_id, run_mode, started_at_utc, finished_at_utc, status, summary_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_id, run_mode, started_at_utc, finished_at_utc, status, json.dumps(summary, default=str)),
        )
        self._conn.commit()


class BinanceReconcileEngine:
    """Bounded, resumable, read-only Binance Spot and USDT-M Futures reconciliation engine."""

    def __init__(
        self,
        *,
        store: BinanceReconcileStore,
        paper_db_path: Path | None = None,
        spot_watchlist: tuple[str, ...] = DEFAULT_SPOT_WATCHLIST,
        perp_watchlist: tuple[str, ...] = DEFAULT_PERP_WATCHLIST,
        page_limit: int = 100,
    ) -> None:
        self.store = store
        self.paper_db_path = paper_db_path
        self.spot_watchlist = spot_watchlist
        self.perp_watchlist = perp_watchlist
        self.page_limit = page_limit

    async def _paginate_stream(
        self,
        *,
        run_fn: Callable[[Callable[[], Any], str], Any],
        fetch_page: Callable[[int | None, int], Any],
        market_type: Literal["spot", "perp"],
        symbol: str,
        stream_type: Literal["orders", "trades"],
        provenance: str,
        account_scope: str,
        max_pages: int,
        request_delay_sec: float,
    ) -> dict[str, Any]:
        prior = self.store.get_watermark("binance", market_type, symbol, stream_type)
        resumed_from = prior.last_timestamp_ms if prior and prior.last_timestamp_ms > 0 else None
        since_ms = resumed_from
        retention_days = FUTURES_RETENTION_DAYS if market_type == "perp" else None
        has_gap = False
        gap_reason: str | None = None

        if market_type == "perp" and since_ms is not None:
            now_ms = int(time.time() * 1000)
            min_allowed_ms = now_ms - (FUTURES_RETENTION_DAYS * 86_400_000)
            if since_ms < min_allowed_ms:
                since_ms = min_allowed_ms
                has_gap = True
                gap_reason = "futures_retention_window_90d"

        seen_ids: set[str] = set()
        pages_fetched = 0
        newly_inserted = 0
        latest_ts = prior.last_timestamp_ms if prior else 0
        latest_id = prior.last_record_id if prior else None
        oldest_ts = prior.oldest_timestamp_ms if prior else None
        exhausted = False
        error_type: str | None = None

        try:
            for page_idx in range(max_pages):
                rows = await run_fn(
                    lambda s=since_ms, lim=self.page_limit: fetch_page(s, lim),
                    f"{market_type}:{stream_type}:{symbol}:p{page_idx}",
                )
                if not isinstance(rows, list):
                    rows = list(rows or [])
                pages_fetched += 1

                if stream_type == "orders":
                    newly_inserted += self.store.upsert_orders(
                        exchange="binance",
                        market_type=market_type,
                        symbol=symbol,
                        orders=rows,
                        provenance=provenance,
                        account_scope=account_scope,
                    )
                    id_fn = _canonical_order_id
                else:
                    newly_inserted += self.store.upsert_trades(
                        exchange="binance",
                        market_type=market_type,
                        symbol=symbol,
                        trades=rows,
                        provenance=provenance,
                        account_scope=account_scope,
                    )
                    id_fn = _canonical_trade_id

                page_stamps: list[int] = []
                for r in rows:
                    rid = id_fn(r)
                    seen_ids.add(rid)
                    ts = _safe_int(r.get("timestamp") or r.get("time"))
                    if ts > 0:
                        page_stamps.append(ts)
                        if ts >= latest_ts:
                            latest_ts = ts
                            latest_id = rid
                        if oldest_ts is None or ts < oldest_ts:
                            oldest_ts = ts

                if len(rows) < self.page_limit:
                    exhausted = True
                    break

                if not page_stamps:
                    has_gap = True
                    gap_reason = "missing_timestamps_at_page_limit"
                    break

                max_page_ts = max(page_stamps)
                if since_ms is not None and max_page_ts <= since_ms:
                    has_gap = True
                    gap_reason = "timestamp_stall_at_page_limit"
                    break

                since_ms = max_page_ts
                if page_idx == max_pages - 1:
                    has_gap = True
                    gap_reason = "max_pages_bound_reached"
                elif request_delay_sec > 0:
                    await asyncio.sleep(request_delay_sec)
        except Exception as exc:
            error_type = type(exc).__name__
            has_gap = True
            gap_reason = f"api_error:{error_type}"

        table: Literal["exchange_orders", "exchange_trades"] = (
            "exchange_orders" if stream_type == "orders" else "exchange_trades"
        )
        total_stored = self.store.count_records(table, market_type, symbol)
        status = "ok" if error_type is None else ("partial" if pages_fetched > 0 else "error")

        wm = WatermarkState(
            exchange="binance",
            market_type=market_type,
            symbol=symbol,
            stream_type=stream_type,
            last_timestamp_ms=latest_ts,
            last_record_id=latest_id,
            oldest_timestamp_ms=oldest_ts,
            total_records=total_stored,
            pages_fetched=(prior.pages_fetched if prior else 0) + pages_fetched,
            exhausted=exhausted,
            has_pagination_gap=has_gap,
            gap_reason=gap_reason,
            retention_window_days=retention_days,
            last_run_at_utc=_utc_iso(),
            last_status=status,
            last_error_type=error_type,
        )
        self.store.save_watermark(wm)

        out: dict[str, Any] = {
            "status": status,
            "count": len(seen_ids),
            "newly_inserted": newly_inserted,
            "total_stored": total_stored,
            "pages": pages_fetched,
            "exhausted": exhausted,
            "has_pagination_gap": has_gap,
            "gap_reason": gap_reason,
            "latest_timestamp": latest_ts,
            "resumed_from_timestamp": resumed_from,
            "provenance": provenance,
        }
        if error_type:
            out["error_type"] = error_type
        return out

    def _read_paper_ledger(self) -> dict[str, Any]:
        if not self.paper_db_path or not self.paper_db_path.exists():
            return {
                "status": "missing",
                "provenance_source": "local_paper_sqlite",
                "is_paper": True,
                "account_scope": "paper_simulation",
                "paper_equated_with_real": False,
                "binance_paper_orders": 0,
                "binance_paper_fills": 0,
                "binance_paper_positions": 0,
                "open_paper_orders": 0,
                "comparison": "Paper and exchange orders are strictly separate and never equated.",
            }
        db = sqlite3.connect(f"file:{self.paper_db_path}?mode=ro", uri=True)
        try:
            orders_total = db.execute(
                "SELECT count(*) FROM paper_orders WHERE exchange='binance' AND is_paper=1"
            ).fetchone()[0]
            open_paper_orders = db.execute(
                "SELECT count(*) FROM paper_orders WHERE exchange='binance' AND is_paper=1 AND upper(status) IN ('OPEN', 'PENDING', 'NEW', 'PARTIALLY_FILLED')"
            ).fetchone()[0]
            fills_total = db.execute(
                "SELECT count(*) FROM paper_fills WHERE exchange='binance' AND is_paper=1"
            ).fetchone()[0]
            positions_total = db.execute(
                "SELECT count(*) FROM paper_positions WHERE exchange='binance' AND is_paper=1"
            ).fetchone()[0]
            by_market = db.execute(
                "SELECT market_type, status, count(*) FROM paper_orders WHERE exchange='binance' AND is_paper=1 GROUP BY market_type, status"
            ).fetchall()
            return {
                "status": "ok",
                "provenance_source": "local_paper_sqlite",
                "is_paper": True,
                "account_scope": "paper_simulation",
                "paper_equated_with_real": False,
                "binance_paper_orders": int(orders_total),
                "open_paper_orders": int(open_paper_orders),
                "binance_paper_fills": int(fills_total),
                "binance_paper_positions": int(positions_total),
                "orders_by_market_status": {f"{mt}:{st}": int(cnt) for mt, st, cnt in by_market},
                "comparison": "Paper and exchange orders are strictly separate and never equated.",
            }
        except Exception as exc:
            return {
                "status": "error",
                "error_type": type(exc).__name__,
                "provenance_source": "local_paper_sqlite",
                "is_paper": True,
                "paper_equated_with_real": False,
            }
        finally:
            db.close()

    def _build_mismatches(
        self,
        *,
        spot_symbols_result: dict[str, Any],
        futures_result: dict[str, Any],
        spot_open_order_symbols: set[str],
        futures_open_order_symbols: set[str],
        futures_position_symbols: set[str],
        paper_ledger: dict[str, Any],
    ) -> list[dict[str, Any]]:
        mismatches: list[dict[str, Any]] = []

        for sym, entry in spot_symbols_result.items():
            for stream_key in ("recent_orders", "recent_trades"):
                ep = entry.get(stream_key) or {}
                if ep.get("status") == "error":
                    mismatches.append(
                        {
                            "category": "EXCHANGE_ENDPOINT_ERROR",
                            "severity": "error",
                            "account_scope": "spot",
                            "symbol": sym,
                            "message": f"Spot {stream_key} query failed ({ep.get('error_type')}).",
                        }
                    )
                elif ep.get("has_pagination_gap"):
                    mismatches.append(
                        {
                            "category": "EXCHANGE_PAGINATION_GAP",
                            "severity": "warning",
                            "account_scope": "spot",
                            "symbol": sym,
                            "message": f"Spot {stream_key} has pagination gap ({ep.get('gap_reason')}).",
                        }
                    )
            if sym in spot_open_order_symbols:
                orders_ep = entry.get("recent_orders") or {}
                if orders_ep.get("status") != "ok" or orders_ep.get("has_pagination_gap"):
                    mismatches.append(
                        {
                            "category": "EXCHANGE_OPEN_ORDER_HISTORY_GAP",
                            "severity": "warning",
                            "account_scope": "spot",
                            "symbol": sym,
                            "message": "Spot symbol has account-wide open order(s) but order history is incomplete.",
                        }
                    )

        fut_symbols = futures_result.get("symbols") or {}
        for sym, entry in fut_symbols.items():
            for stream_key in ("recent_orders", "recent_trades"):
                ep = entry.get(stream_key) or {}
                if ep.get("status") == "error":
                    mismatches.append(
                        {
                            "category": "EXCHANGE_ENDPOINT_ERROR",
                            "severity": "error",
                            "account_scope": "usdt_m_futures",
                            "symbol": sym,
                            "message": f"Futures {stream_key} query failed ({ep.get('error_type')}).",
                        }
                    )
                elif ep.get("has_pagination_gap"):
                    mismatches.append(
                        {
                            "category": "EXCHANGE_PAGINATION_GAP",
                            "severity": "warning",
                            "account_scope": "usdt_m_futures",
                            "symbol": sym,
                            "message": f"Futures {stream_key} has pagination gap ({ep.get('gap_reason')}).",
                        }
                    )
            if sym in futures_position_symbols:
                trades_ep = entry.get("recent_trades") or {}
                if trades_ep.get("status") == "ok" and int(trades_ep.get("total_stored") or 0) == 0:
                    mismatches.append(
                        {
                            "category": "EXCHANGE_POSITION_WITHOUT_FILL_HISTORY",
                            "severity": "warning",
                            "account_scope": "usdt_m_futures",
                            "symbol": sym,
                            "message": "Open Futures position has 0 stored exchange trades (retention window or missing history).",
                        }
                    )
            if sym in futures_open_order_symbols:
                orders_ep = entry.get("recent_orders") or {}
                if orders_ep.get("status") != "ok" or orders_ep.get("has_pagination_gap"):
                    mismatches.append(
                        {
                            "category": "EXCHANGE_OPEN_ORDER_HISTORY_GAP",
                            "severity": "warning",
                            "account_scope": "usdt_m_futures",
                            "symbol": sym,
                            "message": "Futures symbol has account-wide open order(s) but order history is incomplete.",
                        }
                    )

        if paper_ledger.get("open_paper_orders", 0) > 0:
            mismatches.append(
                {
                    "category": "PAPER_STALE_OPEN_ORDERS",
                    "severity": "info",
                    "account_scope": "paper_simulation",
                    "symbol": None,
                    "message": f"{paper_ledger['open_paper_orders']} simulated paper order(s) remain open in local paper ledger.",
                }
            )

        mismatches.append(
            {
                "category": "PAPER_EXCHANGE_SEPARATION_INFO",
                "severity": "info",
                "account_scope": "cross_scope",
                "symbol": None,
                "message": (
                    f"Paper ledger ({paper_ledger.get('binance_paper_orders', 0)} orders, "
                    f"{paper_ledger.get('binance_paper_fills', 0)} fills) reconciled separately from real exchange records."
                ),
            }
        )
        return mismatches

    async def run(
        self,
        adapter: BinanceAdapter,
        *,
        mode: RunMode = "health_check",
        max_pages: int | None = None,
        request_delay_sec: float | None = None,
        skip_history: bool = False,
    ) -> dict[str, Any]:
        """Execute a strictly read-only Binance reconciliation pass."""
        assert_paper_only("paper")
        if not LIVE_EXECUTION_PERMANENTLY_DISABLED:
            raise RuntimeError("Live execution lock must remain permanently enabled.")
        health = adapter.health_status()
        if not health.get("read_only_enforced", False):
            raise RuntimeError("Binance adapter must enforce read-only boundaries.")

        run_id = str(uuid.uuid4())
        started_at = _utc_iso()
        effective_max_pages = max_pages if max_pages is not None else (1 if mode == "health_check" else 5)
        effective_delay = request_delay_sec if request_delay_sec is not None else (0.05 if mode == "health_check" else 0.25)

        result: dict[str, Any] = {
            "run_id": run_id,
            "run_mode": mode,
            "checked_at_utc": started_at,
            "mode": "READ_ONLY",
            "live_execution_disabled": True,
            "writes_allowed": False,
            "scope": "open_orders_account_wide_history_bounded",
            "complete_account_reconciliation": False,
            "futures_checked": False,
            "transfers_checked": False,
            "history_scope": f"bounded_resumable_max_pages_{effective_max_pages}" if not skip_history else "open_orders_only",
            "symbols": {},
            "futures": {},
            "implicated_markets": {
                "spot_from_balances": [],
                "spot_from_open_orders": [],
                "futures_from_positions": [],
                "futures_from_open_orders": [],
            },
            "api_limitations": {
                "spot_history_symbol_required": True,
                "futures_history_symbol_required": True,
                "futures_query_window_days": 7,
                "futures_retention_days": FUTURES_RETENTION_DAYS,
                "spot_retention_note": SPOT_RETENTION_NOTE,
                "all_markets_polled": False,
                "transfers_deposits_withdrawals_checked": False,
            },
        }

        if not adapter.credentials or not adapter.credentials.is_configured:
            result["status"] = "UNCONFIGURED"
            result["paper_ledger"] = self._read_paper_ledger()
            result["mismatches"] = []
            result["coverage"] = {
                "spot_symbols_checked": 0,
                "spot_discovery_ok": False,
                "account_open_orders_ok": False,
                "futures_account_open_orders_ok": False,
                "historical_account_coverage": "UNCONFIGURED",
                "futures_history_complete": False,
            }
            self.store.record_run(run_id, mode, started_at, _utc_iso(), "UNCONFIGURED", result)
            return result

        # --- 1. Spot discovery & account-wide open orders ---
        spot_symbols = set(self.spot_watchlist)
        spot_from_balances: set[str] = set()
        spot_from_open_orders: set[str] = set()
        spot_markets: dict[str, Any] = {}

        try:
            balances = await adapter.fetch_read_only_balance()
            assets = [
                str(asset)
                for asset in (balances.get("balances") or {}).keys()
                if str(asset) not in QUOTE_ASSETS_EXCLUDED_FROM_SYMBOL_DISCOVERY
            ]
            spot_markets = await adapter._run(lambda: adapter._auth_client.load_markets(), "spot_market_discovery")
            for asset in assets:
                candidate = f"{asset}/USDT"
                if candidate in spot_markets and spot_markets[candidate].get("spot"):
                    spot_symbols.add(candidate)
                    spot_from_balances.add(candidate)
            result["spot_discovery"] = {
                "status": "ok",
                "asset_symbols": len(assets),
                "selected_symbols": len(spot_symbols),
            }
        except Exception as exc:
            result["spot_discovery"] = {"status": "error", "error_type": type(exc).__name__}

        try:
            raw_spot_open = await adapter._run(
                lambda: adapter._auth_client.private_get_openorders(),
                "spot_all_open_orders_native",
            )
            if not isinstance(raw_spot_open, list):
                raise TypeError("Unexpected spot open-orders response")
            if not spot_markets:
                spot_markets = await adapter._run(lambda: adapter._auth_client.load_markets(), "spot_markets_for_open")
            markets_by_id = getattr(adapter._auth_client, "markets_by_id", {}) or {}
            orders_by_sym: dict[str, list[dict[str, Any]]] = {}
            for order in raw_spot_open:
                market_id = str(order.get("symbol") or "")
                unified = _resolve_market_symbol(markets_by_id, market_id, "spot") or market_id
                if unified:
                    spot_symbols.add(unified)
                    spot_from_open_orders.add(unified)
                    orders_by_sym.setdefault(unified, []).append(order)
            for sym, sym_orders in orders_by_sym.items():
                self.store.upsert_orders(
                    exchange="binance",
                    market_type="spot",
                    symbol=sym,
                    orders=sym_orders,
                    provenance="binance_spot_native_open_orders",
                    account_scope="spot",
                )
            result["spot_account_open_orders"] = {
                "status": "ok",
                "count": len(raw_spot_open),
                "account_wide": True,
                "source": "binance_spot_native_open_orders",
            }
        except Exception as exc:
            result["spot_account_open_orders"] = {
                "status": "error",
                "account_wide": True,
                "error_type": type(exc).__name__,
            }

        # --- 2. Spot per-symbol bounded pagination ---
        for symbol in sorted(spot_symbols):
            entry: dict[str, Any] = {}
            try:
                open_rows = await adapter._run(
                    lambda s=symbol: adapter._auth_client.fetch_open_orders(s, limit=self.page_limit),
                    f"spot:fetch_open_orders:{symbol}",
                )
                self.store.upsert_orders(
                    exchange="binance",
                    market_type="spot",
                    symbol=symbol,
                    orders=list(open_rows or []),
                    provenance="binance_spot_ccxt_fetch_open_orders",
                    account_scope="spot",
                )
                entry["open_orders"] = {
                    "status": "ok",
                    "count": len(open_rows or []),
                    "provenance": "binance_spot_ccxt_fetch_open_orders",
                }
            except Exception as exc:
                entry["open_orders"] = {"status": "error", "error_type": type(exc).__name__}

            if not skip_history:
                entry["recent_orders"] = await self._paginate_stream(
                    run_fn=adapter._run,
                    fetch_page=lambda since_ms, lim, s=symbol: adapter._auth_client.fetch_orders(s, since=since_ms, limit=lim),
                    market_type="spot",
                    symbol=symbol,
                    stream_type="orders",
                    provenance="binance_spot_ccxt_fetch_orders",
                    account_scope="spot",
                    max_pages=effective_max_pages,
                    request_delay_sec=effective_delay,
                )
                entry["recent_trades"] = await self._paginate_stream(
                    run_fn=adapter._run,
                    fetch_page=lambda since_ms, lim, s=symbol: adapter._auth_client.fetch_my_trades(s, since=since_ms, limit=lim),
                    market_type="spot",
                    symbol=symbol,
                    stream_type="trades",
                    provenance="binance_spot_ccxt_fetch_my_trades",
                    account_scope="spot",
                    max_pages=effective_max_pages,
                    request_delay_sec=effective_delay,
                )
            result["symbols"][symbol] = entry

        # --- 3. USDT-M Futures discovery, account-wide open orders & bounded pagination ---
        futures_from_positions: set[str] = set()
        futures_from_open_orders: set[str] = set()
        perp_symbols = set(self.perp_watchlist)

        try:
            futures_opts = adapter._build_ccxt_options(authenticated=True)
            futures_opts["options"] = {"defaultType": "future"}
            raw_future_client = (
                adapter._client_factory(futures_opts)
                if adapter._client_factory
                else adapter._create_raw_client(futures_opts)
            )
            future_client = enforce_read_only_exchange(raw_future_client)
            try:
                fut_markets = await adapter._run(lambda: future_client.load_markets(), "futures_load_markets")
                fut_markets_by_id = getattr(future_client, "markets_by_id", {}) or {}

                positions = await adapter._run(lambda: future_client.fetch_positions(), "futures_positions")
                open_pos_count = 0
                for pos in positions or []:
                    contracts = _safe_float(pos.get("contracts"), 0.0) or 0.0
                    pos_amt = _safe_float((pos.get("info") or {}).get("positionAmt"), 0.0) or 0.0
                    if contracts != 0.0 or pos_amt != 0.0:
                        open_pos_count += 1
                        psym = pos.get("symbol")
                        if psym:
                            perp_symbols.add(str(psym))
                            futures_from_positions.add(str(psym))

                try:
                    raw_fut_open = await adapter._run(
                        lambda: future_client.fapiprivate_get_openorders(),
                        "futures_all_open_orders_native",
                    )
                    if not isinstance(raw_fut_open, list):
                        raise TypeError("Unexpected futures open-orders response")
                    fut_orders_by_sym: dict[str, list[dict[str, Any]]] = {}
                    for order in raw_fut_open:
                        market_id = str(order.get("symbol") or "")
                        unified = _resolve_market_symbol(fut_markets_by_id, market_id, "perp") or market_id
                        if unified:
                            perp_symbols.add(unified)
                            futures_from_open_orders.add(unified)
                            fut_orders_by_sym.setdefault(unified, []).append(order)
                    for sym, sym_orders in fut_orders_by_sym.items():
                        self.store.upsert_orders(
                            exchange="binance",
                            market_type="perp",
                            symbol=sym,
                            orders=sym_orders,
                            provenance="binance_futures_native_open_orders",
                            account_scope="usdt_m_futures",
                        )
                    fut_open_meta: dict[str, Any] = {
                        "status": "ok",
                        "count": len(raw_fut_open),
                        "account_wide": True,
                        "source": "binance_futures_native_open_orders",
                    }
                except Exception as exc:
                    fut_open_meta = {
                        "status": "error",
                        "account_wide": True,
                        "error_type": type(exc).__name__,
                    }

                futures_symbols_out: dict[str, Any] = {}
                for sym in sorted(perp_symbols):
                    if fut_markets and sym not in fut_markets:
                        continue
                    item: dict[str, Any] = {}
                    try:
                        f_open = await adapter._run(
                            lambda s=sym: future_client.fetch_open_orders(s, limit=self.page_limit),
                            f"futures:fetch_open_orders:{sym}",
                        )
                        self.store.upsert_orders(
                            exchange="binance",
                            market_type="perp",
                            symbol=sym,
                            orders=list(f_open or []),
                            provenance="binance_futures_ccxt_fetch_open_orders",
                            account_scope="usdt_m_futures",
                        )
                        item["open_orders"] = {
                            "status": "ok",
                            "count": len(f_open or []),
                            "provenance": "binance_futures_ccxt_fetch_open_orders",
                        }
                    except Exception as exc:
                        item["open_orders"] = {"status": "error", "error_type": type(exc).__name__}

                    if not skip_history:
                        item["recent_orders"] = await self._paginate_stream(
                            run_fn=adapter._run,
                            fetch_page=lambda since_ms, lim, s=sym: future_client.fetch_orders(s, since=since_ms, limit=lim),
                            market_type="perp",
                            symbol=sym,
                            stream_type="orders",
                            provenance="binance_futures_ccxt_fetch_orders",
                            account_scope="usdt_m_futures",
                            max_pages=effective_max_pages,
                            request_delay_sec=effective_delay,
                        )
                        item["recent_trades"] = await self._paginate_stream(
                            run_fn=adapter._run,
                            fetch_page=lambda since_ms, lim, s=sym: future_client.fetch_my_trades(s, since=since_ms, limit=lim),
                            market_type="perp",
                            symbol=sym,
                            stream_type="trades",
                            provenance="binance_futures_ccxt_fetch_my_trades",
                            account_scope="usdt_m_futures",
                            max_pages=effective_max_pages,
                            request_delay_sec=effective_delay,
                        )
                    futures_symbols_out[sym] = item

                orders_trades_ok = bool(futures_symbols_out) and all(
                    v.get("status") == "ok"
                    for item in futures_symbols_out.values()
                    for v in item.values()
                )
                result["futures"] = {
                    "status": "ok",
                    "open_positions": open_pos_count,
                    "account_open_orders": fut_open_meta,
                    "symbols": futures_symbols_out,
                    "orders_and_trades_checked": orders_trades_ok and not skip_history,
                }
                result["futures_checked"] = True
            finally:
                future_client.close()
        except Exception as exc:
            result["futures"] = {
                "status": "error",
                "error_type": type(exc).__name__,
                "orders_and_trades_checked": False,
            }

        result["implicated_markets"] = {
            "spot_from_balances": sorted(spot_from_balances),
            "spot_from_open_orders": sorted(spot_from_open_orders),
            "futures_from_positions": sorted(futures_from_positions),
            "futures_from_open_orders": sorted(futures_from_open_orders),
        }

        paper_ledger = self._read_paper_ledger()
        result["paper_ledger"] = paper_ledger
        result["exchange_ledger"] = {
            "provenance_source": "binance_api",
            "is_paper": False,
            "spot_stored_orders": self.store.count_records("exchange_orders", "spot"),
            "spot_stored_trades": self.store.count_records("exchange_trades", "spot"),
            "futures_stored_orders": self.store.count_records("exchange_orders", "perp"),
            "futures_stored_trades": self.store.count_records("exchange_trades", "perp"),
        }
        result["mismatches"] = self._build_mismatches(
            spot_symbols_result=result["symbols"],
            futures_result=result["futures"],
            spot_open_order_symbols=spot_from_open_orders,
            futures_open_order_symbols=futures_from_open_orders,
            futures_position_symbols=futures_from_positions,
            paper_ledger=paper_ledger,
        )

        spot_open_ok = result.get("spot_account_open_orders", {}).get("status") == "ok"
        fut_open_ok = result.get("futures", {}).get("account_open_orders", {}).get("status") == "ok"
        any_spot_ok = any(
            v.get("status") == "ok"
            for s in result["symbols"].values()
            for v in s.values()
        )
        result["coverage"] = {
            "spot_symbols_checked": len(result["symbols"]),
            "futures_symbols_checked": len(result.get("futures", {}).get("symbols", {})),
            "spot_discovery_ok": result.get("spot_discovery", {}).get("status") == "ok",
            "account_open_orders_ok": spot_open_ok,
            "futures_account_open_orders_ok": fut_open_ok,
            "account_wide_open_orders_complete": spot_open_ok and fut_open_ok,
            "historical_account_coverage": "BOUNDED_SYMBOL_SCOPED",
            "futures_history_complete": False,
        }
        result["status"] = "PARTIAL_SNAPSHOT" if (any_spot_ok or spot_open_ok or fut_open_ok) else "FAILED"
        self.store.record_run(run_id, mode, started_at, _utc_iso(), result["status"], result)
        return result
