# Paper treasury persistence handoff

The router writes balances, retained transfer idempotency records, USD double-entry
journal entries, and brain cycle reports in one SQLite transaction. Journal entries
use the existing treasury tables with `closed_loop:` accounts, independently of the
strategy master pool. Cross-process writers refresh under `BEGIN IMMEDIATE`;
failed nested transfers roll back to savepoints. Restart restores the last report;
replaying a cycle with different metrics is rejected.

Preview integration is implemented locally in `preview_integration/preview_app.py`.
It shares `service.risk` (already backed by the preview service database), uses
`/root/paper-maintenance-preview-data/closed-loop-paper/router.sqlite3`, and installs
the same router and brain instances into authenticated status endpoints. A minute
loop records skipped reports when service metrics are unavailable. Shutdown cancels
the loop. HTTP mutations remain disabled.

The preview application was untracked before this task and depends on unrelated,
uncommitted Violet modules and config changes. To preserve that work, the narrow
commit includes `preview_integration/treasury-persistence.patch` rather than adding
the entire pre-existing application and its dependencies. The patch contains only
this task's wiring, already applied locally. Once AGY's baseline preview application
and dependencies are committed, apply it with `git apply --unidiff-zero` (do not reapply locally).
This dependency handoff blocks a standalone preview deployment from this commit.

Metrics read only this service's paper accounts and reconciled ledger targets.
Fresh service cycles and at least four distinct observations with nonzero return
variance are required. Open positions without a verified current valuation are
excluded. Solana research and Violet campaign metrics are never substituted.
The current read-only legacy preview does not advance service cycles, so the loop
correctly remains fail-closed until a fresh service metrics source is available.
Neither `violet-all-cap` nor `/opt/solana-quant-bot` is opened by the router or metrics
collector. `LIVE_EXECUTION_PERMANENTLY_DISABLED` remains True.

Legacy router snapshots with transfer history but no journal fail reconciliation;
they require a separately reviewed migration, not an invented journal history.
Runtime SQLite files and credentials are never included in the commit.

Validation: required backend gate passed (19 tests), including restart replay,
risk halt persistence, journal reconciliation/drift, report-write rollback,
observed metrics and idempotency beyond 200 transfers. Frontend lint, tests (25),
and build passed. The sandbox hangs inside Starlette TestClient even for an empty
FastAPI app; the backend gate passes outside that sandbox.
