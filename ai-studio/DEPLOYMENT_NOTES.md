# Deployment notes — sanitized development snapshot

- Real source directory: `/root/multi-exchange-paper-maintenance-20261114`.
- Production-independent preview data: `/root/paper-maintenance-preview-data` (EXCLUDED from archive).
- Systemd unit: `multi-exchange-paper-maintenance-preview.service`, enabled, listens `127.0.0.3:8790`.
- Caddy public host: `preview.trade.187-52-118-25.sslip.io`; Aurora path `/aurora/`.
- External environment-specific `preview_app.py` is excluded; it constructs `TradingEngineService` with empty credentials and isolated database, registers `install_violet_status(app, all_cap, auth_config)` and runs 72-hour all-cap paper campaign.
- Separate Solana host `solana.187-52-118-25.sslip.io` and read-only loopback `127.0.0.1:8795` (`solana-quant-dashboard.service`).
- Build frontend with `cd frontend && npm ci && npm run lint && npm test && npm run build`, then copy `frontend/dist/assets/*` to `app/dashboard/static/aurora/assets/` and `frontend/dist/index.html` to `app/dashboard/templates/aurora.html` (retaining a rollback backup first).
- Validate `/root/multi-exchange-paper/.venv/bin/pytest tests/test_violet_solana_bridge.py tests/test_violet_status.py tests/test_aurora_route.py -q`, Vitest tests, and unauthenticated 401 + asset 200 checks.
- No credentials, database, logs, runtime state, backups, `.env`, sqlite, or private keys are included.
