"""Read-only evidence coverage report. Balanced journals != complete PnL."""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def detect_missing_paper_order_evidence(db) -> dict[str, Any]:
    """Detect filled paper orders lacking paper_fills or SL/TP closure evidence."""
    with db._lock:
        c = db._conn
        filled_orders = c.execute(
            "SELECT COUNT(*) FROM paper_orders WHERE filled_qty > 0 AND is_paper = 1"
        ).fetchone()[0]
        missing_fill_rows = c.execute(
            """
            SELECT o.order_id
            FROM paper_orders o
            WHERE o.filled_qty > 0
              AND o.is_paper = 1
              AND NOT EXISTS (
                  SELECT 1 FROM paper_fills f WHERE f.order_id = o.order_id
              )
            ORDER BY o.created_at ASC, o.order_id ASC
            """
        ).fetchall()
        missing_sltp_closure_rows = c.execute(
            """
            SELECT o.order_id
            FROM paper_orders o
            WHERE o.filled_qty > 0
              AND o.is_paper = 1
              AND o.signal_id = 'sl_tp_exit'
              AND NOT EXISTS (
                  SELECT 1 FROM paper_realized_closures cl WHERE cl.order_id = o.order_id
              )
            ORDER BY o.created_at ASC, o.order_id ASC
            """
        ).fetchall()

    missing_fill_ids = [str(r[0]) for r in missing_fill_rows]
    missing_sltp_closure_ids = [str(r[0]) for r in missing_sltp_closure_rows]
    return {
        "filled_orders": int(filled_orders),
        "missing_fills_count": len(missing_fill_ids),
        "missing_fill_order_ids": missing_fill_ids,
        "missing_sltp_closures_count": len(missing_sltp_closure_ids),
        "missing_sltp_closure_order_ids": missing_sltp_closure_ids,
        "order_scope": "executed quantity, including partial/canceled orders",
        "complete": len(missing_fill_ids) == 0 and len(missing_sltp_closure_ids) == 0,
    }


def _real_to_decimal(value: Any) -> Decimal:
    """Convert SQLite REAL float to Decimal without binary float64 ULP noise."""
    return Decimal(str(round(float(value), 8)))


def _format_gap(gap: Decimal) -> str:
    """Return '0' for exact-zero Decimal gaps instead of scientific/trailing-zero noise."""
    return "0" if gap == Decimal("0") else str(gap)


def audit_per_bot_paper_pnl(db) -> list[dict[str, Any]]:
    """Return read-only per-account PnL, attribution, and evidence completeness breakdown."""
    with db._lock:
        c = db._conn
        accounts = [
            dict(r)
            for r in c.execute(
                "SELECT bot_id, exchange, initial_equity_usd, cash_usd, realized_pnl_usd, reserved_margin_usd "
                "FROM paper_accounts ORDER BY bot_id ASC"
            ).fetchall()
        ]
        attr_rows = c.execute(
            """
            SELECT agent_id, event_category, amount
            FROM treasury_attribution_events
            WHERE event_category IN ('trade_realized_pnl', 'trading_fee', 'funding')
            """
        ).fetchall()
        fill_counts = {
            r[0]: int(r[1])
            for r in c.execute(
                "SELECT bot_id, COUNT(*) FROM paper_fills WHERE is_paper = 1 GROUP BY bot_id"
            ).fetchall()
        }
        closure_counts = {
            r[0]: int(r[1])
            for r in c.execute(
                "SELECT bot_id, COUNT(*) FROM paper_realized_closures WHERE is_paper = 1 GROUP BY bot_id"
            ).fetchall()
        }
        missing_fill_counts = {
            r[0]: int(r[1])
            for r in c.execute(
                """
                SELECT o.bot_id, COUNT(*)
                FROM paper_orders o
                WHERE o.filled_qty > 0 AND o.is_paper = 1
                  AND NOT EXISTS (SELECT 1 FROM paper_fills f WHERE f.order_id = o.order_id)
                GROUP BY o.bot_id
                """
            ).fetchall()
        }
        missing_sltp_counts = {
            r[0]: int(r[1])
            for r in c.execute(
                """
                SELECT o.bot_id, COUNT(*)
                FROM paper_orders o
                WHERE o.filled_qty > 0 AND o.is_paper = 1 AND o.signal_id = 'sl_tp_exit'
                  AND NOT EXISTS (SELECT 1 FROM paper_realized_closures cl WHERE cl.order_id = o.order_id)
                GROUP BY o.bot_id
                """
            ).fetchall()
        }

    by_bot_attr: dict[str, dict[str, Any]] = {}
    for row in attr_rows:
        bot = row["agent_id"]
        bucket = by_bot_attr.setdefault(
            bot,
            {
                "fee_sum": Decimal("0"),
                "fee_cnt": 0,
                "closure_sum": Decimal("0"),
                "closure_cnt": 0,
                "funding_sum": Decimal("0"),
                "funding_cnt": 0,
            },
        )
        amt = Decimal(row["amount"])
        cat = row["event_category"]
        if cat == "trading_fee":
            bucket["fee_sum"] += amt
            bucket["fee_cnt"] += 1
        elif cat == "trade_realized_pnl":
            bucket["closure_sum"] += amt
            bucket["closure_cnt"] += 1
        elif cat == "funding":
            bucket["funding_sum"] += amt
            bucket["funding_cnt"] += 1

    results: list[dict[str, Any]] = []
    for acct in accounts:
        bot = acct["bot_id"]
        b = by_bot_attr.get(
            bot,
            {
                "fee_sum": Decimal("0"),
                "fee_cnt": 0,
                "closure_sum": Decimal("0"),
                "closure_cnt": 0,
                "funding_sum": Decimal("0"),
                "funding_cnt": 0,
            },
        )
        acct_pnl = _real_to_decimal(acct["realized_pnl_usd"])
        attr_total = b["fee_sum"] + b["closure_sum"] + b["funding_sum"]
        gap = acct_pnl - attr_total
        fills_cnt = fill_counts.get(bot, 0)
        closures_cnt = closure_counts.get(bot, 0)
        miss_fills = missing_fill_counts.get(bot, 0)
        miss_sltp = missing_sltp_counts.get(bot, 0)
        reconciled = (
            gap == Decimal("0")
            and miss_fills == 0
            and miss_sltp == 0
            and fills_cnt == b["fee_cnt"]
            and closures_cnt == b["closure_cnt"]
        )
        results.append(
            {
                "bot_id": bot,
                "exchange": acct["exchange"],
                "initial_equity_usd": str(_real_to_decimal(acct["initial_equity_usd"])),
                "cash_usd": str(_real_to_decimal(acct["cash_usd"])),
                "realized_pnl_usd": str(acct_pnl),
                "reserved_margin_usd": str(_real_to_decimal(acct.get("reserved_margin_usd") or 0.0)),
                "fills_count": fills_cnt,
                "fee_attributions_count": b["fee_cnt"],
                "closures_count": closures_cnt,
                "closure_attributions_count": b["closure_cnt"],
                "funding_attributions_count": b["funding_cnt"],
                "orders_missing_fills": miss_fills,
                "sltp_orders_missing_closures": miss_sltp,
                "attributed_fees_usd": str(b["fee_sum"]),
                "attributed_closures_usd": str(b["closure_sum"]),
                "attributed_funding_usd": str(b["funding_sum"]),
                "attributed_total_usd": str(attr_total),
                "unexplained_usd": _format_gap(gap),
                "reconciled": reconciled,
            }
        )
    return results


def audit_paper_pnl_coverage(db) -> dict[str, Any]:
    """Return read-only coverage metrics linking paper_orders, evidence tables, and treasury attributions."""
    evidence = detect_missing_paper_order_evidence(db)
    with db._lock:
        c = db._conn
        scalar = lambda sql: c.execute(sql).fetchone()[0]
        fills = scalar("SELECT COUNT(*) FROM paper_fills WHERE is_paper=1")
        fees = scalar(
            "SELECT COUNT(*) FROM treasury_attribution_events WHERE idempotency_key LIKE 'paper_fill_fee:%'"
        )
        closures = scalar("SELECT COUNT(*) FROM paper_realized_closures WHERE is_paper=1")
        closure_attr = scalar(
            "SELECT COUNT(*) FROM treasury_attribution_events WHERE idempotency_key LIKE 'paper_close_pnl:%'"
        )
        funding_table = scalar(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='paper_funding_settlements'"
        )
        funding = scalar("SELECT COUNT(*) FROM paper_funding_settlements WHERE is_paper=1") if funding_table else 0
        funding_attr = scalar(
            "SELECT COUNT(*) FROM treasury_attribution_events WHERE idempotency_key LIKE 'paper_funding:%'"
        )
        account_pnl = sum(
            (_real_to_decimal(r[0]) for r in c.execute("SELECT realized_pnl_usd FROM paper_accounts")),
            Decimal("0"),
        )
        attributed_pnl = sum(
            (
                Decimal(r[0])
                for r in c.execute(
                    "SELECT amount FROM treasury_attribution_events "
                    "WHERE event_category IN ('trade_realized_pnl','trading_fee','funding')"
                )
            ),
            Decimal("0"),
        )
    gap = account_pnl - attributed_pnl
    coverage = (
        fees == fills
        and closure_attr == closures
        and funding_attr == funding
        and evidence["complete"]
    )
    return {
        "filled_orders": evidence["filled_orders"],
        "orders_missing_fills": evidence["missing_fills_count"],
        "sltp_orders_missing_closures": evidence["missing_sltp_closures_count"],
        "orders_evidence_complete": evidence["complete"],
        "paper_fills": fills,
        "fee_attributions": fees,
        "recorded_closures": closures,
        "closure_attributions": closure_attr,
        "funding_settlements": funding,
        "funding_attributions": funding_attr,
        "account_realized_usd": str(account_pnl),
        "attributed_realized_usd": str(attributed_pnl),
        "unexplained_usd": _format_gap(gap),
        "recorded_evidence_coverage_ok": coverage,
        "historical_completeness_certified": False,
        "reason": "Legacy closes and funding may lack durable source events; no fabricated backfill",
    }
