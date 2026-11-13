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
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS treasury_master_pool (
            pool_id TEXT PRIMARY KEY,
            asset TEXT NOT NULL,
            total_capital TEXT NOT NULL,
            free_capital TEXT NOT NULL,
            reserved_capital TEXT NOT NULL,
            ledger_scope TEXT NOT NULL DEFAULT 'paper_simulated',
            is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper = 1),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS treasury_agent_slots (
            agent_id TEXT PRIMARY KEY,
            slot_index INTEGER UNIQUE NOT NULL CHECK (slot_index >= 1 AND slot_index <= 18),
            name TEXT NOT NULL,
            strategy TEXT NOT NULL,
            exchange TEXT NOT NULL,
            account_id TEXT NOT NULL,
            allowed_symbols_json TEXT NOT NULL,
            capital_budget TEXT NOT NULL DEFAULT '0',
            reserved_capital TEXT NOT NULL DEFAULT '0',
            available_capital TEXT NOT NULL DEFAULT '0',
            trading_capital TEXT NOT NULL DEFAULT '0',
            max_risk_per_trade_pct TEXT NOT NULL,
            daily_loss_limit_pct TEXT NOT NULL,
            max_drawdown_pct TEXT NOT NULL,
            max_leverage TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'idle',
            is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper = 1),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS treasury_fx_rates (
            snapshot_id TEXT PRIMARY KEY,
            base_asset TEXT NOT NULL,
            quote_asset TEXT NOT NULL,
            rate TEXT NOT NULL,
            source TEXT NOT NULL,
            as_of TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS treasury_journal_entries (
            entry_id TEXT PRIMARY KEY,
            idempotency_key TEXT UNIQUE NOT NULL,
            event_type TEXT NOT NULL,
            reference_id TEXT NOT NULL,
            ledger_scope TEXT NOT NULL,
            is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper = 1),
            fx_rate_to_usd TEXT NOT NULL,
            fx_timestamp TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS treasury_journal_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entry_id TEXT NOT NULL,
            account_code TEXT NOT NULL,
            asset TEXT NOT NULL,
            debit TEXT NOT NULL,
            credit TEXT NOT NULL,
            usd_value TEXT NOT NULL,
            FOREIGN KEY (entry_id) REFERENCES treasury_journal_entries(entry_id)
        );

        CREATE TRIGGER IF NOT EXISTS trg_treasury_journal_entries_no_update
        BEFORE UPDATE ON treasury_journal_entries
        BEGIN
            SELECT RAISE(ABORT, 'treasury_journal_entries is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_treasury_journal_entries_no_delete
        BEFORE DELETE ON treasury_journal_entries
        BEGIN
            SELECT RAISE(ABORT, 'treasury_journal_entries is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_treasury_journal_lines_no_update
        BEFORE UPDATE ON treasury_journal_lines
        BEGIN
            SELECT RAISE(ABORT, 'treasury_journal_lines is append-only');
        END;

        CREATE TRIGGER IF NOT EXISTS trg_treasury_journal_lines_no_delete
        BEFORE DELETE ON treasury_journal_lines
        BEGIN
            SELECT RAISE(ABORT, 'treasury_journal_lines is append-only');
        END;

        CREATE TABLE IF NOT EXISTS treasury_transfers (
            transfer_id TEXT PRIMARY KEY,
            idempotency_key TEXT UNIQUE NOT NULL,
            agent_id TEXT NOT NULL,
            source_account TEXT NOT NULL,
            destination_account TEXT NOT NULL,
            asset TEXT NOT NULL,
            amount TEXT NOT NULL,
            fee_amount TEXT NOT NULL DEFAULT '0',
            fx_rate_to_usd TEXT NOT NULL,
            fx_timestamp TEXT NOT NULL,
            state TEXT NOT NULL,
            ledger_scope TEXT NOT NULL,
            is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper = 1),
            ownership_verified INTEGER NOT NULL DEFAULT 0,
            approved_by TEXT,
            readonly_evidence_ref TEXT,
            manual_instruction TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS treasury_attribution_events (
            attribution_id TEXT PRIMARY KEY,
            idempotency_key TEXT UNIQUE NOT NULL,
            agent_id TEXT NOT NULL,
            exchange TEXT NOT NULL,
            account_id TEXT NOT NULL,
            strategy TEXT NOT NULL,
            symbol TEXT NOT NULL,
            asset TEXT NOT NULL,
            event_category TEXT NOT NULL,
            amount TEXT NOT NULL,
            fx_rate_to_usd TEXT NOT NULL,
            usd_value TEXT NOT NULL,
            is_trading_profit INTEGER NOT NULL,
            is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper = 1),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS treasury_rebalance_proposals (
            proposal_id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            current_budget TEXT NOT NULL,
            proposed_budget TEXT NOT NULL,
            risk_adjusted_score TEXT NOT NULL,
            reason TEXT NOT NULL,
            approved INTEGER NOT NULL DEFAULT 0,
            blocked_reason TEXT,
            created_at TEXT NOT NULL
        );
        """,
    ),
    (3, """
        CREATE TABLE IF NOT EXISTS paper_realized_closures (
            order_id TEXT PRIMARY KEY REFERENCES paper_orders(order_id),
            bot_id TEXT NOT NULL, exchange TEXT NOT NULL, symbol TEXT NOT NULL,
            market_type TEXT NOT NULL, close_qty TEXT NOT NULL,
            entry_price TEXT NOT NULL, exit_price TEXT NOT NULL,
            gross_realized_usd TEXT NOT NULL, exit_fee_usd TEXT NOT NULL,
            is_paper INTEGER NOT NULL DEFAULT 1 CHECK (is_paper=1),
            created_at TEXT NOT NULL
        );
    """),
    (4, """
        CREATE TABLE IF NOT EXISTS risk_state (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS bot_configs_state (
            bot_id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
    """),
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
            acct_cols = {
                r["name"] for r in self._conn.execute("PRAGMA table_info(paper_accounts)").fetchall()
            }
            if "reserved_margin_usd" not in acct_cols:
                self._conn.execute(
                    "ALTER TABLE paper_accounts ADD COLUMN reserved_margin_usd REAL NOT NULL DEFAULT 0.0"
                )
                # Backfill reserved margin from pre-existing open positions so closes do not underflow.
                self._conn.execute(
                    """
                    UPDATE paper_accounts
                    SET reserved_margin_usd = COALESCE(
                        (
                            SELECT ROUND(SUM((qty * entry_price) / CASE WHEN leverage > 1.0 THEN leverage ELSE 1.0 END), 6)
                            FROM paper_positions
                            WHERE paper_positions.bot_id = paper_accounts.bot_id
                        ),
                        0.0
                    )
                    """
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

    def record_paper_close(
        self,
        order: PaperOrder,
        close_qty: float,
        entry_price: float,
        gross_realized: float,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        """Atomically post account realized PnL and durable close evidence."""
        if not order.is_paper or close_qty <= 0:
            raise ValueError("Only positive paper position closures are allowed")

        def _exec(c: sqlite3.Connection) -> None:
            existing = c.execute(
                "SELECT 1 FROM paper_realized_closures WHERE order_id=?", (order.order_id,)
            ).fetchone()
            if existing:
                return
            now = datetime.now(timezone.utc).isoformat()
            c.execute(
                """INSERT INTO paper_realized_closures
                (order_id,bot_id,exchange,symbol,market_type,close_qty,entry_price,exit_price,
                 gross_realized_usd,exit_fee_usd,created_at)
                 VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    order.order_id,
                    order.bot_id,
                    order.exchange.value,
                    order.symbol,
                    order.market_type.value,
                    str(close_qty),
                    str(entry_price),
                    str(order.avg_fill_price),
                    str(gross_realized),
                    str(order.fee_usd),
                    now,
                ),
            )
            c.execute(
                """UPDATE paper_accounts SET cash_usd=cash_usd+?,
                realized_pnl_usd=realized_pnl_usd+?,updated_at=? WHERE bot_id=?""",
                (gross_realized, gross_realized, now, order.bot_id),
            )

        if conn is not None:
            _exec(conn)
        else:
            with self.transaction() as c:
                _exec(c)

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

    def update_account_cash(
        self,
        bot_id: str,
        cash_delta: float,
        realized_pnl_delta: float = 0.0,
        reserved_margin_delta: float = 0.0,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        def _exec(c: sqlite3.Connection) -> None:
            now = datetime.now(timezone.utc).isoformat()
            c.execute(
                """
                UPDATE paper_accounts
                SET cash_usd = cash_usd + ?,
                    realized_pnl_usd = realized_pnl_usd + ?,
                    reserved_margin_usd = MAX(0.0, reserved_margin_usd + ?),
                    updated_at = ?
                WHERE bot_id = ?
                """,
                (cash_delta, realized_pnl_delta, reserved_margin_delta, now, bot_id),
            )

        if conn is not None:
            _exec(conn)
        else:
            with self.transaction() as c:
                _exec(c)

    def save_risk_state(self, key: str, value: Any) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO risk_state (key, value_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json = excluded.value_json,
                    updated_at = excluded.updated_at
                """,
                (key, json.dumps(value), datetime.now(timezone.utc).isoformat()),
            )

    def load_risk_state(self, key: str) -> Any:
        with self._lock:
            row = self._conn.execute(
                "SELECT value_json FROM risk_state WHERE key = ?", (key,)
            ).fetchone()
            return json.loads(row["value_json"]) if row else None

    def save_bot_enabled(self, bot_id: str, enabled: bool) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO bot_configs_state (bot_id, enabled, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(bot_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (bot_id, 1 if enabled else 0, datetime.now(timezone.utc).isoformat()),
            )

    def load_bot_enabled(self, bot_id: str) -> bool | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT enabled FROM bot_configs_state WHERE bot_id = ?", (bot_id,)
            ).fetchone()
            return bool(row["enabled"]) if row else None

    def get_order_by_client_id(self, client_order_id: str) -> PaperOrder | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM paper_orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
            return self._row_to_order(row) if row else None

    def save_order(self, order: PaperOrder, conn: sqlite3.Connection | None = None) -> None:
        def _exec(c: sqlite3.Connection) -> None:
            c.execute(
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

        if conn is not None:
            _exec(conn)
        else:
            with self.transaction() as c:
                _exec(c)

    def save_fill(self, fill: PaperFill, conn: sqlite3.Connection | None = None) -> None:
        def _exec(c: sqlite3.Connection) -> None:
            c.execute(
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

        if conn is not None:
            _exec(conn)
        else:
            with self.transaction() as c:
                _exec(c)

    def upsert_position(self, pos: PaperPosition, conn: sqlite3.Connection | None = None) -> None:
        def _exec(c: sqlite3.Connection) -> None:
            if pos.qty <= 1e-10:
                c.execute(
                    """
                    DELETE FROM paper_positions
                    WHERE bot_id = ? AND exchange = ? AND symbol = ? AND market_type = ?
                    """,
                    (pos.bot_id, pos.exchange.value, pos.symbol, pos.market_type.value),
                )
                return
            c.execute(
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

        if conn is not None:
            _exec(conn)
        else:
            with self.transaction() as c:
                _exec(c)

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

    def list_fills_for_order(self, order_id: str) -> list[PaperFill]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM paper_fills WHERE order_id = ? ORDER BY timestamp ASC", (order_id,)
            ).fetchall()
            return [self._row_to_fill(r) for r in rows]

    def list_incomplete_orders(self) -> list[PaperOrder]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM paper_orders WHERE status IN ('created', 'validated', 'submitted', 'partially_filled')"
            ).fetchall()
            return [self._row_to_order(r) for r in rows]

    def list_realized_closures(self, limit: int | None = None) -> list[dict[str, Any]]:
        with self._lock:
            sql = "SELECT * FROM paper_realized_closures ORDER BY created_at ASC, order_id ASC"
            if limit is not None:
                rows = self._conn.execute(f"{sql} LIMIT ?", (limit,)).fetchall()
            else:
                rows = self._conn.execute(sql).fetchall()
            return [dict(r) for r in rows]

    def list_all_fills_asc(self) -> list[PaperFill]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM paper_fills ORDER BY timestamp ASC, fill_id ASC"
            ).fetchall()
            return [self._row_to_fill(r) for r in rows]

    def list_treasury_funding_events(self) -> list[dict[str, Any]]:
        with self._lock:
            table_row = self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='treasury_attribution_events'"
            ).fetchone()
            if not table_row:
                return []
            rows = self._conn.execute(
                """
                SELECT exchange, amount, usd_value, created_at
                FROM treasury_attribution_events
                WHERE event_category = 'funding'
                ORDER BY created_at ASC, attribution_id ASC
                """
            ).fetchall()
            return [dict(r) for r in rows]

    def log_audit(
        self,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
        conn: sqlite3.Connection | None = None,
    ) -> None:
        def _exec(c: sqlite3.Connection) -> None:
            c.execute(
                "INSERT INTO audit_logs (timestamp, event_type, actor, payload_json) VALUES (?, ?, ?, ?)",
                (datetime.now(timezone.utc).isoformat(), event_type, actor, json.dumps(payload, default=str)),
            )

        if conn is not None:
            _exec(conn)
        else:
            with self.transaction() as c:
                _exec(c)

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
