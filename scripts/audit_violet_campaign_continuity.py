"""Read-only audit of isolated VIOLET campaign archives; never settles positions."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def audit_campaign(directory: Path) -> dict:
    report = json.loads((directory / "72H_RUN_REPORT.json").read_text(encoding="utf-8"))
    observations = sum(1 for line in (directory / "72h-observations.jsonl").open(encoding="utf-8") if line.strip())
    database = directory / "violet-all-cap.sqlite3"
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("paper_orders", "paper_fills", "paper_positions")
        }
    expected = {
        "observations": report["cycles_observed"],
        "paper_orders": counts["paper_orders"] - report["baseline_orders"],
        "paper_fills": counts["paper_fills"] - report["baseline_fills"],
        "paper_positions": counts["paper_positions"],
    }
    matches = (
        integrity == "ok"
        and observations == expected["observations"]
        and expected["paper_orders"] == report["paper_orders_during_run"]
        and expected["paper_fills"] == report["public_paper_fills_during_run"]
        and expected["paper_positions"] == report["open_positions"]
    )
    return {
        "campaign_id": report["campaign_id"],
        "status": report["status"],
        "started_at": report["started_at"],
        "ends_at": report["ends_at"],
        "last_observed_at": report["last_observed_at"],
        "observations": observations,
        "orders_since_baseline": expected["paper_orders"],
        "fills_since_baseline": expected["paper_fills"],
        "open_paper_positions": expected["paper_positions"],
        "sqlite_integrity": integrity,
        "report_consistent": matches,
        "requires_separate_paper_settlement": counts["paper_positions"] > 0,
    }


def audit(root: Path) -> dict:
    archived = root / "archived-campaigns" / "violet-all-cap-500-v1"
    active = root / "violet-all-cap-500-fresh-v2"
    v1, v2 = audit_campaign(archived), audit_campaign(active)
    independent = v1["campaign_id"] != v2["campaign_id"] and v1["started_at"] < v2["started_at"]
    return {
        "mode": "READ_ONLY_PAPER",
        "historical_archive": v1,
        "active_campaign": v2,
        "separate_campaigns": independent,
        "passed": independent and v1["report_consistent"] and v2["report_consistent"],
        "note": "Open archived paper positions remain isolated; this audit never settles or transfers them.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/root/paper-maintenance-preview-data"))
    args = parser.parse_args()
    result = audit(args.root)
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
