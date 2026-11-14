"""Read-only campaign audit regression tests with isolated synthetic SQLite files."""
import json
import sqlite3
from pathlib import Path

from scripts.audit_violet_campaign_continuity import audit, audit_campaign


def campaign(directory: Path, campaign_id: str, start: str, positions: int = 0, mismatch: bool = False):
    directory.mkdir(parents=True)
    report = {
        "campaign_id": campaign_id, "status": "active", "started_at": start,
        "ends_at": "2026-11-17T00:00:00+00:00", "last_observed_at": start,
        "cycles_observed": 2 if mismatch else 1, "baseline_orders": 0,
        "baseline_fills": 0, "paper_orders_during_run": 1,
        "public_paper_fills_during_run": 1, "open_positions": positions,
    }
    (directory / "72H_RUN_REPORT.json").write_text(json.dumps(report))
    (directory / "72h-observations.jsonl").write_text('{"cycle":1}\n')
    with sqlite3.connect(directory / "violet-all-cap.sqlite3") as db:
        for name in ("paper_orders", "paper_fills", "paper_positions"):
            db.execute(f"CREATE TABLE {name} (id INTEGER)")
        db.execute("INSERT INTO paper_orders VALUES (1)")
        db.execute("INSERT INTO paper_fills VALUES (1)")
        for i in range(positions):
            db.execute("INSERT INTO paper_positions VALUES (?)", (i,))


def test_archive_position_is_reported_not_mutated(tmp_path):
    p = tmp_path / "archived-campaigns/violet-all-cap-500-v1"
    campaign(p, "violet-all-cap-500-v1", "2026-11-14T07:00:00+00:00", positions=1)
    before = (p / "violet-all-cap.sqlite3").read_bytes()
    result = audit_campaign(p)
    assert result["report_consistent"]
    assert result["requires_separate_paper_settlement"]
    assert (p / "violet-all-cap.sqlite3").read_bytes() == before


def test_both_campaigns_must_reconcile(tmp_path):
    campaign(tmp_path / "archived-campaigns/violet-all-cap-500-v1", "v1", "2026-11-14T07:00:00+00:00", 1)
    campaign(tmp_path / "violet-all-cap-500-fresh-v2", "v2", "2026-11-14T09:00:00+00:00")
    assert audit(tmp_path)["passed"]
    report = tmp_path / "violet-all-cap-500-fresh-v2/72H_RUN_REPORT.json"
    data = json.loads(report.read_text())
    data["cycles_observed"] = 99
    report.write_text(json.dumps(data))
    assert not audit(tmp_path)["passed"]
