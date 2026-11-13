# Multi-Exchange Paper Trading Platform

Private source repository for the Binance, Bybit and OKX multi-exchange paper-trading engine and its React dashboard.

**Safety:** Live exchange execution is structurally disabled. Do not commit credentials, production databases, portfolio records or Telegram tokens.

- Backend: `app/`, `scripts/`, `tests/`
- Frontend: `frontend/`
- Validation monitor source: `validation72/monitor.py`
- Operational instructions: `OPERATIONS.md`, `SECURITY_AUDIT.md`

Backend tests: `.venv/bin/pytest -q`
Frontend checks: `cd frontend && npm ci && npm run lint && npm run build`

Production deployment is controlled separately by systemd and Caddy on the VPS. Source updates must be reviewed and tested before deployment.
