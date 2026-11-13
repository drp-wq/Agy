# Paper diagnostics maintenance

This change is prepared in this maintenance worktree only. Deployment is a separate operator action. Do not change running services, production databases, credentials, frozen tags, or other worktrees during verification.

## Local verification

Use an existing Python environment containing the application dependencies. Set `PYTHONDONTWRITEBYTECODE=1` when using an environment outside this worktree; do not install into that environment.

```sh
PYTHONDONTWRITEBYTECODE=1 /path/to/python -m pytest -q
cd frontend
npm ci --ignore-scripts --no-audit --no-fund
npm test
npm run lint
npm run build
```

Tests use temporary ledgers. Never point tests or a new application instance at the production database: application startup performs migrations and recovery writes.

## Monitor

`validation72/monitor.py` defaults to this checkout's `validation72` output directory and `data/paper_engine.sqlite3`. It does not load credentials or probe services. A missing ledger is reported as an error and is never created. Optional HTTP probes run only with `--base-url` or `PAPER_MONITOR_BASE_URL`; use an isolated local server for verification.

```sh
/path/to/python validation72/monitor.py --db /tmp/paper-check/fixture.sqlite3 --root /tmp/paper-check/observations
```

The ledger connection uses SQLite `mode=ro` and `query_only`. Only the selected output directory receives observations. Choose an output directory inside this worktree or `/tmp`. `window.json` is optional; an expired window records a final observation and completion flag rather than leaving the previous observation as the latest result. Missing or malformed windows do not prevent ledger diagnostics. An old completion flag does not suppress later samples.

## API and UI contract

Authenticated GET `/api/diagnostics` uses dashboard authentication; GET `/api/miniapp/diagnostics` also accepts the existing Telegram owner authentication. Both have private/no-store responses and no mutation handlers. They perform SELECT queries against the existing service ledger and inspect cached engine state, with no exchange calls, scans, reconciliation, or migrations.

Each exchange reports all-time paper order statuses and rejected orders grouped by `reject_reason`, fill count, execution fees, account net realized PnL, position unrealized PnL, and separate account/order/fill freshness. Null or empty rejection reasons are `unspecified`. Realized PnL already includes execution fees: do not subtract the reported fees again. Treasury transfers and externally observed account balances are excluded. Order rejection reasons are execution outcomes, not screening decisions.

Observation freshness reads only the timestamp from `service.config.base_dir/validation72/latest.json`. The monitor output must be in that directory for the API to observe it. A valid timestamp older than 180 seconds is stale; missing data is missing, malformed/oversized/timezone-free/future data is invalid. Quiet orders/fills can be stale while the engine is healthy; inspect cycle freshness separately. An expired observation window can remain stale by design. API generation time does not refresh source timestamps.

React diagnostics appear in System Health, Paper Orders, Portfolio Analytics, Market Scanner, Opportunities, and Risk. Panels poll every 15 seconds while visible, cancel requests on unmount, and show errors without demo fallback. Layouts collapse to one column on small screens.

## Future deployment and rollback

Before any separately authorized deployment, review the commit, retain the previous application artifact, build this exact revision, and validate in an isolated PAPER_ONLY instance with a disposable database and independent output directory. Verify unauthenticated diagnostics return 401, authenticated data is exchange-separated, and missing/stale observations are explicit. Inspect layouts at mobile and desktop widths.

Deploy only through the existing approved release process; no service edits, database migrations/backfills, credential changes, tag moves, or copying artifacts into another worktree are part of this maintenance task. Roll back by selecting the previous application artifact through that process. This diagnostics change requires no database schema changes or data restoration. Do not delete or replace a running ledger during rollback.
