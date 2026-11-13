#!/usr/bin/env python3
"""Safe, deterministic, idempotent CLI for legacy paper SL/TP PnL evidence backfill."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.treasury.legacy_pnl_backfill import backfill_legacy_paper_pnl


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deterministic, idempotent backfill for missing legacy paper SL/TP fills and closures."
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        required=True,
        help="Path to the SQLite database copy to inspect or backfill.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is read-only dry-run).",
    )
    parser.add_argument(
        "--no-treasury-sync",
        action="store_true",
        help="Skip syncing missing treasury_attribution_events rows.",
    )
    parser.add_argument(
        "--actor",
        default="legacy_pnl_backfill",
        help="Actor recorded in audit_logs when backfill rows are inserted.",
    )
    parser.add_argument(
        "--flip-reversal-residual",
        action="store_true",
        help="Use post-a78a6f0 reversal residual flip semantics during replay.",
    )
    parser.add_argument(
        "--allow-production-db",
        action="store_true",
        help="Explicitly allow targeting the live production DB path (disabled by default).",
    )
    args = parser.parse_args()

    resolved = args.db_path.resolve()
    prod_db = Path("/root/multi-exchange-paper/data/paper_engine.sqlite3").resolve()
    if resolved == prod_db and args.apply and not args.allow_production_db:
        raise SystemExit(
            "Refusing to mutate production DB without --allow-production-db. Target a staging copy instead."
        )

    result = backfill_legacy_paper_pnl(
        resolved,
        actor=args.actor,
        dry_run=not args.apply,
        sync_treasury=not args.no_treasury_sync,
        flip_reversal_residual=args.flip_reversal_residual,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
