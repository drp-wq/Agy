"""Deterministic, audit-traceable, idempotent backfill for legacy paper SL/TP fills and closures."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.core.config import UnifiedTreasuryConfig
from app.core.security import assert_paper_only
from app.persistence.database import PaperDatabase
from app.treasury.ledger import UnifiedTreasuryLedger
from app.treasury.models import AttributionCategory, AttributionRecord, to_decimal
from app.treasury.reconciliation_audit import audit_paper_pnl_coverage


class LegacyReplayMismatchError(RuntimeError):
    """Raised when chronological order replay diverges from paper_accounts or paper_positions."""


@dataclass(frozen=True)
class MissingFillRecord:
    fill_id: str
    order_id: str
    bot_id: str
    exchange: str
    symbol: str
    market_type: str
    side: str
    qty: float
    price: float
    fee_usd: float
    slippage_bps: float
    is_paper: int
    timestamp: str


@dataclass(frozen=True)
class MissingClosureRecord:
    order_id: str
    bot_id: str
    exchange: str
    symbol: str
    market_type: str
    close_qty: str
    entry_price: str
    exit_price: str
    gross_realized_usd: str
    exit_fee_usd: str
    is_paper: int
    created_at: str


@dataclass(frozen=True)
class ReplaySummary:
    replayed_orders: int
    per_bot_replayed: dict[str, dict[str, float]]
    missing_fills: list[MissingFillRecord]
    missing_closures: list[MissingClosureRecord]
    missing_fees_usd: str
    missing_gross_usd: str


def replay_paper_orders(
    conn: sqlite3.Connection,
    *,
    flip_reversal_residual: bool = False,
    allow_non_sltp: bool = False,
    tolerance: float = 1e-6,
) -> ReplaySummary:
    """Replay filled paper_orders chronologically and verify exact match with accounts and positions."""
    conn.row_factory = sqlite3.Row
    accounts = {
        r["bot_id"]: dict(r)
        for r in conn.execute("SELECT * FROM paper_accounts ORDER BY bot_id").fetchall()
    }
    db_positions = {
        (r["bot_id"], r["exchange"], r["symbol"], r["market_type"]): dict(r)
        for r in conn.execute("SELECT * FROM paper_positions").fetchall()
    }
    orders = [
        dict(r)
        for r in conn.execute(
            """
            SELECT o.*,
                   (SELECT COUNT(*) FROM paper_fills f WHERE f.order_id = o.order_id) AS fill_cnt,
                   (SELECT COUNT(*) FROM paper_realized_closures c WHERE c.order_id = o.order_id) AS closure_cnt
            FROM paper_orders o
            WHERE o.status = 'filled' AND o.is_paper = 1
            ORDER BY o.created_at ASC, o.order_id ASC
            """
        ).fetchall()
    ]

    positions: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    replayed_realized: dict[str, float] = {b: 0.0 for b in accounts}
    replayed_cash: dict[str, float] = {
        b: float(a["initial_equity_usd"]) for b, a in accounts.items()
    }

    missing_fills: list[MissingFillRecord] = []
    missing_closures: list[MissingClosureRecord] = []

    for o in orders:
        bot_id = o["bot_id"]
        if bot_id not in accounts:
            raise LegacyReplayMismatchError(
                f"Unknown bot_id in filled paper order {o['order_id']}: {bot_id}"
            )
        key = (bot_id, o["exchange"], o["symbol"], o["market_type"])
        direction = "long" if o["side"] == "buy" else "short"
        qty = float(o["filled_qty"])
        px = float(o["avg_fill_price"])
        fee = float(o["fee_usd"])
        lev = float(o["leverage"])
        if qty <= 0 or px <= 0 or fee < 0:
            raise LegacyReplayMismatchError(f"Invalid order economics for {o['order_id']}")

        replayed_cash[bot_id] -= fee
        replayed_realized[bot_id] -= fee

        if o["fill_cnt"] == 0:
            if not allow_non_sltp and o["signal_id"] != "sl_tp_exit":
                raise LegacyReplayMismatchError(
                    f"Unexpected non-sl_tp_exit order missing paper_fills: {o['order_id']} ({o['signal_id']})"
                )
            missing_fills.append(
                MissingFillRecord(
                    fill_id=f"fill_backfill_{o['order_id']}",
                    order_id=o["order_id"],
                    bot_id=bot_id,
                    exchange=o["exchange"],
                    symbol=o["symbol"],
                    market_type=o["market_type"],
                    side=o["side"],
                    qty=qty,
                    price=px,
                    fee_usd=fee,
                    slippage_bps=float(o["slippage_bps"]),
                    is_paper=1,
                    timestamp=o["created_at"],
                )
            )

        pos = positions.get(key)
        if pos is None:
            positions[key] = {
                "direction": direction,
                "qty": qty,
                "entry_price": px,
                "leverage": lev,
            }
        elif pos["direction"] == direction:
            new_qty = pos["qty"] + qty
            pos["entry_price"] = round(
                ((pos["entry_price"] * pos["qty"]) + (px * qty)) / new_qty, 6
            )
            pos["qty"] = round(new_qty, 6)
        else:
            close_qty = min(pos["qty"], qty)
            residual_qty = round(qty - close_qty, 6)

            pnl_per_unit = (
                (px - pos["entry_price"])
                if pos["direction"] == "long"
                else (pos["entry_price"] - px)
            )
            realized = round(pnl_per_unit * close_qty, 6)
            replayed_cash[bot_id] += realized
            replayed_realized[bot_id] += realized

            if o["closure_cnt"] == 0:
                if not allow_non_sltp and o["signal_id"] != "sl_tp_exit":
                    raise LegacyReplayMismatchError(
                        f"Unexpected non-sl_tp_exit closure missing paper_realized_closures: {o['order_id']} ({o['signal_id']})"
                    )
                missing_closures.append(
                    MissingClosureRecord(
                        order_id=o["order_id"],
                        bot_id=bot_id,
                        exchange=o["exchange"],
                        symbol=o["symbol"],
                        market_type=o["market_type"],
                        close_qty=str(close_qty),
                        entry_price=str(pos["entry_price"]),
                        exit_price=str(px),
                        gross_realized_usd=str(realized),
                        exit_fee_usd=str(fee),
                        is_paper=1,
                        created_at=o["created_at"],
                    )
                )

            remaining = round(pos["qty"] - close_qty, 6)
            if remaining <= 1e-8:
                del positions[key]
                if flip_reversal_residual and residual_qty > 1e-8:
                    positions[key] = {
                        "direction": direction,
                        "qty": residual_qty,
                        "entry_price": px,
                        "leverage": lev,
                    }
            else:
                pos["qty"] = remaining

    replayed_margin: dict[str, float] = {b: 0.0 for b in accounts}
    for (bot_id, _, _, _), p in positions.items():
        replayed_margin[bot_id] += (p["qty"] * p["entry_price"]) / max(p["leverage"], 1.0)
    for bot_id in replayed_margin:
        replayed_margin[bot_id] = round(replayed_margin[bot_id], 6)

    per_bot_replayed: dict[str, dict[str, float]] = {}
    for bot_id, acct in accounts.items():
        if abs(replayed_realized[bot_id] - float(acct["realized_pnl_usd"])) > tolerance:
            raise LegacyReplayMismatchError(
                f"Replay realized mismatch for {bot_id}: replay={replayed_realized[bot_id]} db={acct['realized_pnl_usd']}"
            )
        if abs(replayed_cash[bot_id] - float(acct["cash_usd"])) > tolerance:
            raise LegacyReplayMismatchError(
                f"Replay cash mismatch for {bot_id}: replay={replayed_cash[bot_id]} db={acct['cash_usd']}"
            )
        db_margin = float(acct.get("reserved_margin_usd") or 0.0)
        if abs(replayed_margin[bot_id] - db_margin) > tolerance:
            raise LegacyReplayMismatchError(
                f"Replay reserved_margin mismatch for {bot_id}: replay={replayed_margin[bot_id]} db={db_margin}"
            )
        per_bot_replayed[bot_id] = {
            "cash_usd": round(replayed_cash[bot_id], 6),
            "realized_pnl_usd": round(replayed_realized[bot_id], 6),
            "reserved_margin_usd": round(replayed_margin[bot_id], 6),
        }

    if set(positions.keys()) != set(db_positions.keys()):
        raise LegacyReplayMismatchError(
            f"Replay open position keys differ from DB: replay={sorted(positions.keys())} db={sorted(db_positions.keys())}"
        )
    for key, rp in positions.items():
        dp = db_positions[key]
        if rp["direction"] != dp["direction"]:
            raise LegacyReplayMismatchError(f"Position direction mismatch for {key}")
        if abs(rp["qty"] - float(dp["qty"])) > tolerance:
            raise LegacyReplayMismatchError(
                f"Position qty mismatch for {key}: replay={rp['qty']} db={dp['qty']}"
            )
        if abs(rp["entry_price"] - float(dp["entry_price"])) > tolerance:
            raise LegacyReplayMismatchError(
                f"Position entry_price mismatch for {key}: replay={rp['entry_price']} db={dp['entry_price']}"
            )

    missing_fees = sum((Decimal(str(f.fee_usd)) for f in missing_fills), Decimal("0"))
    missing_gross = sum((Decimal(c.gross_realized_usd) for c in missing_closures), Decimal("0"))
    return ReplaySummary(
        replayed_orders=len(orders),
        per_bot_replayed=per_bot_replayed,
        missing_fills=missing_fills,
        missing_closures=missing_closures,
        missing_fees_usd=str(missing_fees),
        missing_gross_usd=str(missing_gross),
    )


def _snapshot_invariants(conn: sqlite3.Connection) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    accounts = [
        dict(r)
        for r in conn.execute(
            "SELECT bot_id, exchange, cash_usd, initial_equity_usd, realized_pnl_usd, reserved_margin_usd "
            "FROM paper_accounts ORDER BY bot_id"
        ).fetchall()
    ]
    orders_total = conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]
    orders_filled = conn.execute(
        "SELECT COUNT(*) FROM paper_orders WHERE status = 'filled' AND is_paper = 1"
    ).fetchone()[0]
    journal_entries = conn.execute("SELECT COUNT(*) FROM treasury_journal_entries").fetchone()[0]
    journal_lines = conn.execute("SELECT COUNT(*) FROM treasury_journal_lines").fetchone()[0]
    positions = [
        dict(r)
        for r in conn.execute(
            "SELECT position_id, bot_id, exchange, symbol, market_type, direction, qty, entry_price "
            "FROM paper_positions ORDER BY position_id"
        ).fetchall()
    ]
    master_pool = [
        dict(r)
        for r in conn.execute(
            "SELECT pool_id, total_capital, free_capital, reserved_capital "
            "FROM treasury_master_pool ORDER BY pool_id"
        ).fetchall()
    ]
    return {
        "accounts": accounts,
        "positions": positions,
        "orders_total": int(orders_total),
        "orders_filled": int(orders_filled),
        "journal_entries": int(journal_entries),
        "journal_lines": int(journal_lines),
        "master_pool": master_pool,
    }


def _verify_db_integrity_and_uniqueness(conn: sqlite3.Connection) -> dict[str, Any]:
    integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    fk_violations = [list(r) for r in conn.execute("PRAGMA foreign_key_check").fetchall()]
    dup_fill_ids = [
        r[0]
        for r in conn.execute(
            "SELECT fill_id FROM paper_fills GROUP BY fill_id HAVING COUNT(*) > 1"
        ).fetchall()
    ]
    dup_fill_orders = [
        r[0]
        for r in conn.execute(
            "SELECT order_id FROM paper_fills GROUP BY order_id HAVING COUNT(*) > 1"
        ).fetchall()
    ]
    dup_closure_orders = [
        r[0]
        for r in conn.execute(
            "SELECT order_id FROM paper_realized_closures GROUP BY order_id HAVING COUNT(*) > 1"
        ).fetchall()
    ]
    dup_attr_keys = [
        r[0]
        for r in conn.execute(
            "SELECT idempotency_key FROM treasury_attribution_events GROUP BY idempotency_key HAVING COUNT(*) > 1"
        ).fetchall()
    ]
    return {
        "integrity_check": integrity,
        "foreign_key_violations": fk_violations,
        "duplicate_fill_ids": dup_fill_ids,
        "duplicate_fill_orders": dup_fill_orders,
        "duplicate_closure_orders": dup_closure_orders,
        "duplicate_attribution_keys": dup_attr_keys,
        "ok": (
            integrity == "ok"
            and not fk_violations
            and not dup_fill_ids
            and not dup_fill_orders
            and not dup_closure_orders
            and not dup_attr_keys
        ),
    }


def _insert_attribution_direct(
    conn: sqlite3.Connection,
    *,
    idempotency_key: str,
    agent_id: str,
    exchange: str,
    account_id: str,
    strategy: str,
    symbol: str,
    category: AttributionCategory,
    amount: Decimal,
) -> int:
    dec_amt = to_decimal(amount)
    rec = AttributionRecord(
        idempotency_key=idempotency_key,
        agent_id=agent_id,
        exchange=exchange,
        account_id=account_id,
        strategy=strategy,
        symbol=symbol,
        asset="USD",
        event_category=category,
        amount=dec_amt,
        fx_rate_to_usd=to_decimal(1),
        usd_value=dec_amt,
        is_trading_profit=True,
    )
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO treasury_attribution_events (
            attribution_id, idempotency_key, agent_id, exchange, account_id,
            strategy, symbol, asset, event_category, amount, fx_rate_to_usd,
            usd_value, is_trading_profit, is_paper, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            rec.attribution_id,
            rec.idempotency_key,
            rec.agent_id,
            rec.exchange,
            rec.account_id,
            rec.strategy,
            rec.symbol,
            rec.asset,
            rec.event_category.value,
            str(rec.amount),
            str(rec.fx_rate_to_usd),
            str(rec.usd_value),
            1 if rec.is_trading_profit else 0,
            rec.created_at.isoformat(),
        ),
    )
    return cur.rowcount


def _record_missing_attribution(
    db: PaperDatabase,
    ledger: UnifiedTreasuryLedger | None,
    *,
    idempotency_key: str,
    agent_id: str,
    exchange: str,
    strategy: str,
    symbol: str,
    category: AttributionCategory,
    amount: Decimal,
) -> int:
    account_id = f"{exchange}:paper"
    if ledger is not None:
        ledger.record_attribution(
            idempotency_key=idempotency_key,
            agent_id=agent_id,
            exchange=exchange,
            account_id=account_id,
            strategy=strategy,
            symbol=symbol,
            asset="USD",
            category=category,
            amount=amount,
        )
        return 1
    with db.transaction() as conn:
        return _insert_attribution_direct(
            conn,
            idempotency_key=idempotency_key,
            agent_id=agent_id,
            exchange=exchange,
            account_id=account_id,
            strategy=strategy,
            symbol=symbol,
            category=category,
            amount=amount,
        )


def _sync_missing_treasury_attributions(db: PaperDatabase) -> dict[str, int]:
    """Idempotently attribute paper_fills fees and paper_realized_closures without mutating journals."""
    with db._lock:
        has_pool = (
            db._conn.execute(
                "SELECT 1 FROM treasury_master_pool WHERE pool_id = 'master_pool'"
            ).fetchone()
            is not None
        )
        existing_keys = {
            r[0]
            for r in db._conn.execute(
                "SELECT idempotency_key FROM treasury_attribution_events"
            ).fetchall()
        }
        fills = db._conn.execute(
            "SELECT * FROM paper_fills WHERE is_paper = 1 ORDER BY timestamp ASC, fill_id ASC"
        ).fetchall()
        closures = db._conn.execute(
            "SELECT * FROM paper_realized_closures WHERE is_paper = 1 ORDER BY created_at ASC, order_id ASC"
        ).fetchall()

    ledger = (
        UnifiedTreasuryLedger(db, UnifiedTreasuryConfig(enabled=True))
        if has_pool
        else None
    )

    fee_inserted = 0
    for row in fills:
        key = f"paper_fill_fee:{row['fill_id']}"
        if key in existing_keys:
            continue
        fee_inserted += _record_missing_attribution(
            db,
            ledger,
            idempotency_key=key,
            agent_id=row["bot_id"],
            exchange=row["exchange"],
            strategy="paper_fill_unmapped_strategy",
            symbol=row["symbol"],
            category=AttributionCategory.TRADING_FEE,
            amount=-Decimal(str(row["fee_usd"])),
        )

    closure_inserted = 0
    for row in closures:
        key = f"paper_close_pnl:{row['order_id']}"
        if key in existing_keys:
            continue
        closure_inserted += _record_missing_attribution(
            db,
            ledger,
            idempotency_key=key,
            agent_id=row["bot_id"],
            exchange=row["exchange"],
            strategy="paper_close_unmapped_strategy",
            symbol=row["symbol"],
            category=AttributionCategory.TRADE_REALIZED_PNL,
            amount=Decimal(str(row["gross_realized_usd"])),
        )

    return {
        "inserted_fee_attributions": fee_inserted,
        "inserted_closure_attributions": closure_inserted,
    }


def backfill_legacy_paper_pnl(
    db_path: Path,
    *,
    actor: str = "legacy_pnl_backfill",
    dry_run: bool = False,
    sync_treasury: bool = True,
    flip_reversal_residual: bool = False,
    allow_non_sltp: bool = False,
) -> dict[str, Any]:
    """Deterministically and idempotently backfill missing paper_fills, closures, and attributions."""
    assert_paper_only("paper")
    db = PaperDatabase(db_path)
    try:
        before_integrity = _verify_db_integrity_and_uniqueness(db._conn)
        if not before_integrity["ok"]:
            raise RuntimeError(f"Pre-flight DB integrity check failed: {before_integrity}")

        before_invariants = _snapshot_invariants(db._conn)
        before_audit = audit_paper_pnl_coverage(db)
        replay = replay_paper_orders(
            db._conn,
            flip_reversal_residual=flip_reversal_residual,
            allow_non_sltp=allow_non_sltp,
        )

        if dry_run:
            return {
                "dry_run": True,
                "replayed_orders": replay.replayed_orders,
                "missing_fills_found": len(replay.missing_fills),
                "missing_closures_found": len(replay.missing_closures),
                "missing_fees_usd": replay.missing_fees_usd,
                "missing_gross_usd": replay.missing_gross_usd,
                "missing_fill_order_ids": [f.order_id for f in replay.missing_fills],
                "missing_closure_order_ids": [c.order_id for c in replay.missing_closures],
                "before_audit": before_audit,
                "per_bot_replayed": replay.per_bot_replayed,
            }

        inserted_fills = 0
        inserted_closures = 0
        with db.transaction() as conn:
            for f in replay.missing_fills:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO paper_fills (
                        fill_id, order_id, bot_id, exchange, symbol, market_type,
                        side, qty, price, fee_usd, slippage_bps, is_paper, timestamp
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f.fill_id,
                        f.order_id,
                        f.bot_id,
                        f.exchange,
                        f.symbol,
                        f.market_type,
                        f.side,
                        f.qty,
                        f.price,
                        f.fee_usd,
                        f.slippage_bps,
                        f.is_paper,
                        f.timestamp,
                    ),
                )
                inserted_fills += cur.rowcount

            for c in replay.missing_closures:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO paper_realized_closures (
                        order_id, bot_id, exchange, symbol, market_type,
                        close_qty, entry_price, exit_price, gross_realized_usd,
                        exit_fee_usd, is_paper, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        c.order_id,
                        c.bot_id,
                        c.exchange,
                        c.symbol,
                        c.market_type,
                        c.close_qty,
                        c.entry_price,
                        c.exit_price,
                        c.gross_realized_usd,
                        c.exit_fee_usd,
                        c.is_paper,
                        c.created_at,
                    ),
                )
                inserted_closures += cur.rowcount

            if inserted_fills > 0 or inserted_closures > 0:
                per_bot_delta: dict[str, dict[str, str]] = {}
                for f in replay.missing_fills:
                    b = per_bot_delta.setdefault(
                        f.bot_id, {"fees_usd": "0", "gross_realized_usd": "0", "count": "0"}
                    )
                    b["fees_usd"] = str(Decimal(b["fees_usd"]) + Decimal(str(f.fee_usd)))
                    b["count"] = str(int(b["count"]) + 1)
                for c in replay.missing_closures:
                    b = per_bot_delta.setdefault(
                        c.bot_id, {"fees_usd": "0", "gross_realized_usd": "0", "count": "0"}
                    )
                    b["gross_realized_usd"] = str(
                        Decimal(b["gross_realized_usd"]) + Decimal(c.gross_realized_usd)
                    )

                payload = {
                    "provenance": "deterministic_chronological_paper_orders_replay",
                    "inserted_fills": inserted_fills,
                    "inserted_closures": inserted_closures,
                    "order_ids": [c.order_id for c in replay.missing_closures],
                    "fill_ids": [f.fill_id for f in replay.missing_fills],
                    "reconstructed_exit_fees_usd": replay.missing_fees_usd,
                    "reconstructed_gross_realized_usd": replay.missing_gross_usd,
                    "per_bot_delta": per_bot_delta,
                }
                conn.execute(
                    "INSERT INTO audit_logs (timestamp, event_type, actor, payload_json) VALUES (?, ?, ?, ?)",
                    (
                        datetime.now(timezone.utc).isoformat(),
                        "HISTORICAL_SLTP_EVIDENCE_BACKFILLED",
                        actor,
                        json.dumps(payload, sort_keys=True),
                    ),
                )

        attr_sync = (
            _sync_missing_treasury_attributions(db)
            if sync_treasury
            else {"inserted_fee_attributions": 0, "inserted_closure_attributions": 0}
        )

        after_invariants = _snapshot_invariants(db._conn)
        if after_invariants != before_invariants:
            raise RuntimeError(
                f"Immutable ledger invariant violated! before={before_invariants} after={after_invariants}"
            )
        after_integrity = _verify_db_integrity_and_uniqueness(db._conn)
        if not after_integrity["ok"]:
            raise RuntimeError(f"Post-backfill integrity check failed: {after_integrity}")

        after_audit = audit_paper_pnl_coverage(db)
        return {
            "dry_run": False,
            "replayed_orders": replay.replayed_orders,
            "missing_fills_found": len(replay.missing_fills),
            "missing_closures_found": len(replay.missing_closures),
            "inserted_fills": inserted_fills,
            "inserted_closures": inserted_closures,
            "missing_fees_usd": replay.missing_fees_usd,
            "missing_gross_usd": replay.missing_gross_usd,
            "treasury_attribution_sync": attr_sync,
            "before_audit": before_audit,
            "after_audit": after_audit,
            "invariants_unchanged": True,
            "integrity": after_integrity,
            "per_bot_replayed": replay.per_bot_replayed,
            "missing_fills_detail": [asdict(f) for f in replay.missing_fills],
            "missing_closures_detail": [asdict(c) for c in replay.missing_closures],
        }
    finally:
        db.close()
