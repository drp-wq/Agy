Violet Lab 72-hour PAPER_ONLY campaign

Active entry window: 2026-11-13 22:24:10 UTC to 2026-11-16 22:24:10 UTC.
The durable SQLite campaign record survives restarts and does not rearm.
After expiry, new entries stop and existing positions retain protective checks.

Only multi-exchange-paper-maintenance-preview.service was restarted.
The independent all-cap ledger uses one locked worker and four validated
credential-free public-data adapters. The legacy preview is protective-only:
its 16 existing positions are preserved; its entry-generating loop is disabled.
Production is unchanged. Live exchange orders remain structurally disabled.

GET /api/violet/all-cap requires existing dashboard Basic credentials.
Use it for campaign state; the old Aurora panels describe the legacy ledger.
Run outputs:
/root/paper-maintenance-preview-data/violet-all-cap/72H_RUN_REPORT.json
/root/paper-maintenance-preview-data/violet-all-cap/72h-observations.jsonl

See ../verification/72h/VALIDATION_AND_RUN_REPORT.md for tests, public evidence,
limitations, source archive and rollback instructions.
Canonical report: /root/paper-maintenance-preview-data/FINAL_VALIDATION_REPORT.md

Tests:
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:verification /root/multi-exchange-paper/.venv/bin/python -m pytest -q -p no:cacheprovider -p sandbox_loop_pulse

Synthetic retained fixtures (never actual campaign trades):
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:tests /root/multi-exchange-paper/.venv/bin/python scripts/verify_synthetic_execution.py

Bounded public probes (host network access):
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. /root/multi-exchange-paper/.venv/bin/python scripts/validate_public_adapters.py
