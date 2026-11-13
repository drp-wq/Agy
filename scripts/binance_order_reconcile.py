#!/usr/bin/env python3
"""Read-only, durable, resumable Binance Spot & USDT-M Futures reconciliation runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from app.core.config import load_environment
from app.core.security import assert_paper_only
from app.exchanges.binance import BinanceAdapter
from app.intelligence.binance_reconcile import (
    BinanceReconcileEngine,
    BinanceReconcileStore,
)

ROOT = Path("/root/multi-exchange-paper")
OUT = ROOT / "data" / "binance_order_reconcile.json"
RECONCILE_DB = ROOT / "data" / "binance_reconcile.sqlite3"
PAPER_DB = ROOT / "data" / "paper_engine.sqlite3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Binance order/trade reconciliation.")
    parser.add_argument(
        "--mode",
        choices=("health_check", "backfill"),
        default="health_check",
        help="health_check for 15-min lightweight checks; backfill for slow resumable pagination.",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="Override max pagination pages per symbol.")
    parser.add_argument("--skip-history", action="store_true", help="Only run account-wide open orders, balances, and positions.")
    return parser.parse_args()


async def main() -> None:
    assert_paper_only("paper")
    args = parse_args()
    env = load_environment(require_secrets_file=True)
    adapter = BinanceAdapter(credentials=env.credentials.get("binance"), max_retries=1)
    store = BinanceReconcileStore(RECONCILE_DB)
    engine = BinanceReconcileEngine(store=store, paper_db_path=PAPER_DB)
    try:
        result = await engine.run(
            adapter,
            mode=args.mode,
            max_pages=args.max_pages,
            skip_history=args.skip_history,
        )
    finally:
        store.close()
        adapter.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, OUT)
    print(
        json.dumps(
            {
                "status": result["status"],
                "run_mode": result["run_mode"],
                "spot_symbols_checked": len(result.get("symbols", {})),
                "futures_symbols_checked": len(result.get("futures", {}).get("symbols", {})),
                "account_wide_open_orders_complete": result.get("coverage", {}).get("account_wide_open_orders_complete"),
                "complete_account_reconciliation": result["complete_account_reconciliation"],
                "mismatches_count": len(result.get("mismatches", [])),
                "writes_allowed": result["writes_allowed"],
                "checked_at_utc": result["checked_at_utc"],
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
