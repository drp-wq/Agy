"""Transactional SQLite ledger with migrations, WAL mode, crash recovery, and backups."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app.core.models import (
    Direction,
    ExchangeId,
    MarketType,
    OrderStatus,
    OrderType,
    PaperFill,
    PaperOrder,
    PaperPosition,
    Side,
)

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paper_accounts (
            bot_id TEXT PRIMARY KEY,
            exchange TEXT NOT NULL,
            cash_usd REAL NOT NULL,
            initial_equity_usd REAL NOT NULL,
            realized_pnl_usd REAL NOT NULL DEFAULT 0.0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paper_orders (
            order_id TEXT PRIMARY KEY,
            client_order_id TEXT UNIQUE NOT NULL,
            bot_id TEXT NOT NULL,
            signal_id TEXT NOT NULL,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_type TEXT NOT NULL,
            side TEXT NOT NULL,
            order_type TEXT NOT NULL,
            requested_qty REAL NOT NULL,
            filled_qty REAL NOT NULL DEFAULT 0.0,
            avg_fill_price REAL NOT NULL DEFAULT 0.0,
            limit_price REAL,
            stop_loss REAL,
            take_profit REAL,
            leverage REAL NOT NULL DEFAULT 1.0,
            fee_usd REAL NOT NULL DEFAULT 0.0,
            slippage_bps REAL NOT NULL DEFAULT 0.0,
            status TEXT NOT NULL,
            is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper = 1),
            reject_reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS paper_fills (
            fill_id TEXT PRIMARY KEY,
            order_id TEXT NOT NULL,
            bot_id TEXT NOT NULL,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_type TEXT NOT NULL,
            side TEXT NOT NULL,
            qty REAL NOT NULL,
            price REAL NOT NULL,
            fee_usd REAL NOT NULL,
            slippage_bps REAL NOT NULL,
            is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper = 1),
            timestamp TEXT NOT NULL,
            FOREIGN KEY (order_id) REFERENCES paper_orders(order_id)
        );

        CREATE TABLE IF NOT EXISTS paper_positions (
            position_id TEXT PRIMARY KEY,
            bot_id TEXT NOT NULL,
            exchange TEXT NOT NULL,
            symbol TEXT NOT NULL,
            market_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            qty REAL NOT NULL,
            entry_price REAL NOT NULL,
            mark_price REAL NOT NULL,
            leverage REAL NOT NULL DEFAULT 1.0,
            stop_loss REAL,
            take_profit REAL,
            unrealized_pnl_usd REAL NOT NULL DEFAULT 0.0,
            realized_pnl_usd REAL NOT NULL DEFAULT 0.0,
            is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper = 1),
            opened_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(bot_id, exchange, symbol, market_type)
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        """,
    ),
]


class PaperDatabase:
    """Thread-safe SQLite repository with WAL mode and transactional paper accounting."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self.migrate()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE;")
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def migrate(self) -> None:
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            applied = {
                row["version"]
                for row in self._conn.execute("SELECT version FROM schema_migrations").fetchall()
            }
            for version, sql in MIGRATIONS:
                if version not in applied:
                    self._conn.executescript(sql)
                    self._conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, datetime.now(timezone.utc).isoformat()),
                    )
                    self._conn.commit()

    def ensure_account(self, bot_id: str, exchange: str, initial_equity_usd: float) -> dict[str, Any]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM paper_accounts WHERE bot_id = ?", (bot_id,)
            ).fetchone()
            if row is None:
                now = datetime.now(timezone.utc).isoformat()
                conn.execute(
                    """
                    INSERT INTO paper_accounts (bot_id, exchange, cash_usd, initial_equity_usd, realized_pnl_usd, updated_at)
                    VALUES (?, ?, ?, ?, 0.0, ?)
                    """,
                    (bot_id, exchange, initial_equity_usd, initial_equity_usd, now),
                )
                row = conn.execute(
                    "SELECT * FROM paper_accounts WHERE bot_id = ?", (bot_id,)
                ).fetchone()
            return dict(row)

    def get_account(self, bot_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM paper_accounts WHERE bot_id = ?", (bot_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_accounts(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM paper_accounts ORDER BY bot_id").fetchall()
            return [dict(r) for r in rows]

    def update_account_cash(self, bot_id: str, cash_delta: float, realized_pnl_delta: float = 0.0) -> None:
        with self.transaction() as conn:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE paper_accounts
                SET cash_usd = cash_usd + ?,
                    realized_pnl_usd = realized_pnl_usd + ?,
                    updated_at = ?
                WHERE bot_id = ?
                """,
                (cash_delta, realized_pnl_delta, now, bot_id),
            )

    def get_order_by_client_id(self, client_order_id: str) -> PaperOrder | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM paper_orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
            return self._row_to_order(row) if row else None

    def save_order(self, order: PaperOrder) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO paper_orders (
                    order_id, client_order_id, bot_id, signal_id, exchange, symbol, market_type,
                    side, order_type, requested_qty, filled_qty, avg_fill_price, limit_price,
                    stop_loss, take_profit, leverage, fee_usd, slippage_bps, status, is_paper,
                    reject_reason, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(order_id) DO UPDATE SET
                    filled_qty = excluded.filled_qty,
                    avg_fill_price = excluded.avg_fill_price,
                    fee_usd = excluded.fee_usd,
                    slippage_bps = excluded.slippage_bps,
                    status = excluded.status,
                    reject_reason = excluded.reject_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    order.order_id,
                    order.client_order_id,
                    order.bot_id,
                    order.signal_id,
                    order.exchange.value,
                    order.symbol,
                    order.market_type.value,
                    order.side.value,
                    order.order_type.value,
                    order.requested_qty,
                    order.filled_qty,
                    order.avg_fill_price,
                    order.limit_price,
                    order.stop_loss,
                    order.take_profit,
                    order.leverage,
                    order.fee_usd,
                    order.slippage_bps,
                    order.status.value,
                    order.reject_reason,
                    order.created_at.isoformat(),
                    order.updated_at.isoformat(),
                ),
            )

    def save_fill(self, fill: PaperFill) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO paper_fills (
                    fill_id, order_id, bot_id, exchange, symbol, market_type, side,
                    qty, price, fee_usd, slippage_bps, is_paper, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    fill.fill_id,
                    fill.order_id,
                    fill.bot_id,
                    fill.exchange.value,
                    fill.symbol,
                    fill.market_type.value,
                    fill.side.value,
                    fill.qty,
                    fill.price,
                    fill.fee_usd,
                    fill.slippage_bps,
                    fill.timestamp.isoformat(),
                ),
            )

    def upsert_position(self, pos: PaperPosition) -> None:
        with self.transaction() as conn:
            if pos.qty <= 1e-10:
                conn.execute(
                    """
                    DELETE FROM paper_positions
                    WHERE bot_id = ? AND exchange = ? AND symbol = ? AND market_type = ?
                    """,
                    (pos.bot_id, pos.exchange.value, pos.symbol, pos.market_type.value),
                )
                return
            conn.execute(
                """
                INSERT INTO paper_positions (
                    position_id, bot_id, exchange, symbol, market_type, direction, qty,
                    entry_price, mark_price, leverage, stop_loss, take_profit,
                    unrealized_pnl_usd, realized_pnl_usd, is_paper, opened_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(bot_id, exchange, symbol, market_type) DO UPDATE SET
                    direction = excluded.direction,
                    qty = excluded.qty,
                    entry_price = excluded.entry_price,
                    mark_price = excluded.mark_price,
                    leverage = excluded.leverage,
                    stop_loss = excluded.stop_loss,
                    take_profit = excluded.take_profit,
                    unrealized_pnl_usd = excluded.unrealized_pnl_usd,
                    realized_pnl_usd = excluded.realized_pnl_usd,
                    updated_at = excluded.updated_at
                """,
                (
                    pos.position_id,
                    pos.bot_id,
                    pos.exchange.value,
                    pos.symbol,
                    pos.market_type.value,
                    pos.direction.value,
                    pos.qty,
                    pos.entry_price,
                    pos.mark_price,
                    pos.leverage,
                    pos.stop_loss,
                    pos.take_profit,
                    pos.unrealized_pnl_usd,
                    pos.realized_pnl_usd,
                    pos.opened_at.isoformat(),
                    pos.updated_at.isoformat(),
                ),
            )

    def list_positions(self, bot_id: str | None = None) -> list[PaperPosition]:
        with self._lock:
            if bot_id:
                rows = self._conn.execute(
                    "SELECT * FROM paper_positions WHERE bot_id = ? ORDER BY updated_at DESC",
                    (bot_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM paper_positions ORDER BY updated_at DESC"
                ).fetchall()
            return [self._row_to_position(r) for r in rows]

    def list_orders(self, limit: int = 100) -> list[PaperOrder]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM paper_orders ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_order(r) for r in rows]

    def list_fills(self, limit: int = 100) -> list[PaperFill]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM paper_fills ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
            return [self._row_to_fill(r) for r in rows]

    def list_incomplete_orders(self) -> list[PaperOrder]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM paper_orders WHERE status IN ('created', 'validated', 'submitted', 'partially_filled')"
            ).fetchall()
            return [self._row_to_order(r) for r in rows]

    def log_audit(self, event_type: str, actor: str, payload: dict[str, Any]) -> None:
        with self.transaction() as conn:
            conn.execute(
                "INSERT INTO audit_logs (timestamp, event_type, actor, payload_json) VALUES (?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), event_type, actor, json.dumps(payload, default=str)),
            )

    def list_audit_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    def backup_to(self, backup_path: Path) -> Path:
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            dst = sqlite3.connect(str(backup_path))
            try:
                self._conn.backup(dst)
            finally:
                dst.close()
        return backup_path

    @staticmethod
    def _row_to_order(row: sqlite3.Row) -> PaperOrder:
        return PaperOrder(
            order_id=row["order_id"],
            client_order_id=row["client_order_id"],
            bot_id=row["bot_id"],
            signal_id=row["signal_id"],
            exchange=ExchangeId(row["exchange"]),
            symbol=row["symbol"],
            market_type=MarketType(row["market_type"]),
            side=Side(row["side"]),
            order_type=OrderType(row["order_type"]),
            requested_qty=row["requested_qty"],
            filled_qty=row["filled_qty"],
            avg_fill_price=row["avg_fill_price"],
            limit_price=row["limit_price"],
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            leverage=row["leverage"],
            fee_usd=row["fee_usd"],
            slippage_bps=row["slippage_bps"],
            status=OrderStatus(row["status"]),
            is_paper=bool(row["is_paper"]),
            reject_reason=row["reject_reason"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _row_to_fill(row: sqlite3.Row) -> PaperFill:
        return PaperFill(
            fill_id=row["fill_id"],
            order_id=row["order_id"],
            bot_id=row["bot_id"],
            exchange=ExchangeId(row["exchange"]),
            symbol=row["symbol"],
            market_type=MarketType(row["market_type"]),
            side=Side(row["side"]),
            qty=row["qty"],
            price=row["price"],
            fee_usd=row["fee_usd"],
            slippage_bps=row["slippage_bps"],
            is_paper=bool(row["is_paper"]),
            timestamp=datetime.fromisoformat(row["timestamp"]),
        )

    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> PaperPosition:
        return PaperPosition(
            position_id=row["position_id"],
            bot_id=row["bot_id"],
            exchange=ExchangeId(row["exchange"]),
            symbol=row["symbol"],
            market_type=MarketType(row["market_type"]),
            direction=Direction(row["direction"]),
            qty=row["qty"],
            entry_price=row["entry_price"],
            mark_price=row["mark_price"],
            leverage=row["leverage"],
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            unrealized_pnl_usd=row["unrealized_pnl_usd"],
            realized_pnl_usd=row["realized_pnl_usd"],
            is_paper=bool(row["is_paper"]),
            opened_at=datetime.fromisoformat(row["opened_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
