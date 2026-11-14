# AGENTS.md — Unified AGY + Codex Governance Contract

This repository (`multi-exchange-paper-maintenance-20261114` / `drp-wq/Agy`) is maintained jointly by **AGY (Antigravity)** and **Codex**. Both agents MUST follow these rules to share skills, preserve safety invariants, and avoid conflicting edits.

## 1. Non-Negotiable Safety Invariants
1. **Paper-First / Live-Execution Gate**: `app/core/security.py` (`LIVE_EXECUTION_PERMANENTLY_DISABLED`) and `GlobalRiskGuardian` (`app/risk/guardian.py`) are supreme. Never enable real-money order placement or open external withdrawals in code without explicit Phase 4 user sign-off.
2. **Closed-Loop Custody Rule**: Asset transfers are restricted strictly to the signed `ClosedLoopAllowlist` (`hub_binance_master`, `cex_binance`, `cex_okx`, `cex_bybit`, `dex_solana`). Any external address or non-allowlisted venue MUST be rejected.
3. **Campaign & Ledger Isolation**: Keep the 72-hour VIOLET paper campaign ledger and `/opt/solana-quant-bot` research ledger isolated from simulated closed-loop tests.
4. **No Secrets in Git or Archives**: Never commit `.env*`, `*.key`, `*.pem`, `*.sqlite3*`, or credentials.

## 2. Role Split (AGY vs. Codex)
- **AGY (Antigravity)**:
  - Strategy & Solana research diagnostics (`violet_solana_bridge.py`, `SolanaResearchView.tsx`)
  - Scarlet Terminal UI/UX (`frontend/src/**`)
  - Multi-agent orchestration, deployment, and live endpoint verification
- **Codex**:
  - Execution engines (`app/execution/**`), exchange adapters (`app/exchanges/**`)
  - Double-entry treasury & closed-loop router (`app/treasury/**`)
  - Reconciliation & backend invariant test suites (`tests/**`)

Both agents share `.agents/skills/` and MUST run the verification gates below before committing.

## 3. Mandatory Verification Gates
- **Backend**:
  ```bash
  /root/multi-exchange-paper/.venv/bin/pytest tests/test_closed_loop_router.py tests/test_violet_status.py tests/test_violet_solana_bridge.py -q
  ```
- **Frontend**:
  ```bash
  cd frontend && npm run lint && npm test && npm run build
  ```
- **Narrow Git Commits**: Stage only files touched by your task; preserve unrelated local working-tree files.
