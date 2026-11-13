# Live Readiness Checklist

> **Current Status**: `LIVE EXECUTION: DISABLED` (Paper-Only Deployment). Live trading must NOT be enabled without explicit final operator authorization.

- [x] OKX and Bybit adapters implemented with rate limits, retry backoff, and outage isolation
- [x] Live-order submission paths structurally blocked (`LiveExecutionBlockedError`) and verified by automated tests
- [x] Credentials loaded from `0600` `/root/trading-secrets/exchanges.env` with secret redaction
- [x] Bybit read-only authentication verified (`18` assets)
- [x] OKX authenticated balance (`8` assets) & positions (`0`) verified over IPv4 (`187.52.118.25`); note OKX key `perm` is `"read_only,trade"` on exchange side (recommend restricting key to pure read-only on OKX dashboard if desired; local engine structurally blocks all writes)
- [x] Global Market Scanner running across OKX & Bybit Spot & Perp
- [x] 10 modular strategies + AI Orchestrator + walk-forward OOS calibration implemented
- [x] Global Risk Guardian enforced on all paper orders
- [x] Simultaneous multi-bot paper execution + SQLite WAL persistence + crash recovery verified
- [x] Authenticated neon/cyberpunk dashboard verified on `127.0.0.1:8788`
- [ ] Extended multi-day paper soak test & operator sign-off (required before any future live discussion)

## 2026-11-13 Operator Handoff (23:00 Asia/Dhaka)
- [x] Public HTTPS dashboard: https://trade.187-52-118-25.sslip.io
- [x] HTTPS /api/health returns 200 with validated TLS; unauthenticated /api/state returns 401
- [x] Caddy and multi-exchange-paper systemd services enabled and active
- [x] 18 automated tests pass (1 dependency deprecation warning)
- [x] Read-only 5-minute readiness monitor installed as multi-exchange-readiness.timer
- [ ] Multi-day soak still required; 23:00 inspection is NOT a live authorization
- [ ] Exchange-side OKX API key retains trade permission; restrict on OKX portal
- [ ] Confirm simulated fills, PnL, risk triggers and reconciliation with fresh evidence at inspection
- [ ] Final operator authorization for any future live trading must be separate

Inspection command:
  systemctl status multi-exchange-paper multi-exchange-readiness.timer --no-pager
  cat /root/multi-exchange-paper/data/readiness_watch.json
  cd /root/multi-exchange-paper && .venv/bin/pytest -q

NOTE: Existing application-level order/write blocks remain in force.
