"""Read-only evidence coverage report. Balanced journals != complete PnL."""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def detect_missing_paper_order_evidence(db) -> dict[str, Any]:
    """Detect filled paper orders lacking paper_fills or SL/TP closure evidence."""
    with db._lock:
        c = db._conn
        filled_orders = c.execute(
            "SELECT COUNT(*) FROM paper_orders WHERE status = 'filled' AND is_paper = 1"
        ).fetchone()[0]
        missing_fill_rows = c.execute(
            """
            SELECT o.order_id
            FROM paper_orders o
            WHERE o.status = 'filled'
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
            WHERE o.status = 'filled'
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
        "complete": len(missing_fill_ids) == 0 and len(missing_sltp_closure_ids) == 0,
    }


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
            (Decimal(str(r[0])) for r in c.execute("SELECT realized_pnl_usd FROM paper_accounts")),
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
        "unexplained_usd": str(gap),
        "recorded_evidence_coverage_ok": coverage,
        "historical_completeness_certified": False,
        "reason": "Legacy closes and funding may lack durable source events; no fabricated backfill",
    }
