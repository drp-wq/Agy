# Unified 18-Agent Capital Allocation and Attribution Implementation Report

## 1. Summary & Security / Custody Invariants
Implemented a feature-flagged **paper-only** unified 18-agent treasury, double-entry append-only accounting journal, USD/USDT FX valuation, explicit transfer lifecycle, PnL attribution, and risk-adjusted budget allocator in `/root/multi-exchange-paper`:

- **Single master capital pool model**: Implemented ONE unified master capital pool (`treasury_master_pool`) shared across up to 18 configurable virtual agent slots (`treasury_agent_slots`). Total agent allocations/reservations plus free capital reconcile strictly to the single master pool; no double spending is permitted.
- **No pooled cross-exchange custody & no real transfers**: The unified treasury is an accounting and paper-simulation layer only. Transfer proposals generate manual instructions (`MANUAL PAPER INSTRUCTION`) and require explicit ownership verification (`ownership_verified=True`), user approval (`approved_by`), and read-only evidence references (`readonly_evidence_ref`).
- **Live execution permanently disabled**: `LIVE_EXECUTION_PERMANENTLY_DISABLED = True`, `UnifiedTreasuryConfig(paper_only=False)` raises `ValueError`, `enforce_read_only_exchange` blocks `withdraw`, `transfer`, and all order-submission/write methods, and no credentials or live flags were modified.
- **Production service & existing ledger preserved**: Migration v2 in `app/persistence/database.py` adds the `treasury_*` tables while preserving all v1 tables (`paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `audit_logs`) and existing bot IDs. `UnifiedTreasuryConfig.enabled` defaults to `False` (toggleable via `UNIFIED_TREASURY_ENABLED=true`). The production service was not restarted.

## 2. Implemented Capabilities

1. **Configuration & Feature Flag (`app/core/config.py`)**
   - Added `UnifiedTreasuryConfig` (`enabled=False`, `max_agent_slots=18`, `master_pool_currency="USDT"`, `initial_master_capital=10000.0`, `default_usdt_usd_rate=0.9995`, `max_single_agent_concentration_pct=0.25`, `max_aggregate_allocated_pct=0.85`, `rebalance_cooldown_sec=300.0`, `agent_daily_loss_cap_pct=0.02`, `require_ownership_verification=True`, `paper_only=True`).
   - Wired `unified_treasury` into `EngineConfig` and `load_environment()` (`UNIFIED_TREASURY_ENABLED`).

2. **Database Schema & Append-Only Triggers (`app/persistence/database.py`)**
   - Added Migration v2 with `treasury_master_pool`, `treasury_agent_slots`, `treasury_fx_rates`, `treasury_journal_entries`, `treasury_journal_lines`, `treasury_transfers`, `treasury_attribution_events`, and `treasury_rebalance_proposals`.
   - Added SQLite `BEFORE UPDATE` and `BEFORE DELETE` triggers on `treasury_journal_entries` and `treasury_journal_lines` (`RAISE(ABORT, ...)`), enforcing append-only immutability at the database layer.
   - Monetary amounts are stored as `TEXT` representing quantized `Decimal` values.

3. **Domain Models & Decimal / FX Helpers (`app/treasury/models.py`)**
   - `to_decimal()` with 8-decimal quantization (`QUANT_8`), `FxRateSnapshot`, `MasterPoolState`, `AgentSlotConfig`, `AgentSlotStatus` (`idle`, `active`, `paused`, `disabled`), `TransferLifecycleState`, `VALID_TRANSFER_TRANSITIONS`, `JournalEntry`, `JournalEntryLine`, `AttributionCategory`, `AttributionRecord`, and `RebalanceProposal`.

4. **Unified Treasury Ledger & Transfer Lifecycle (`app/treasury/ledger.py`)**
   - `UnifiedTreasuryLedger`:
     - **18 configurable agent slots**: `initialize_default_slots()` and `configure_agent_slot()` manage slots 1..18 without assuming all 18 are active.
     - **Double-entry journal**: `post_journal_entry()` validates per-asset `sum(debit) == sum(credit)` and `idempotency_key` uniqueness.
     - **Explicit transfer lifecycle**: `reserve_capital()` -> `propose_transfer()` -> `approve_transfer()` -> `mark_external_transfer_pending()` -> `confirm_from_readonly_evidence()` -> `release_to_agent_available()` -> `allocate_to_paper_trading()`, plus `cancel_or_reject_transfer()` and `simulate_paper_transfer_e2e()` (`ledger_scope="paper_simulated"`, `is_paper=True`).
     - **USD vs USDT FX distinction**: `record_fx_rate()`, `get_latest_fx_rate()`, and `value_in_usd()` record FX conversion rates and timestamps rather than treating USD and USDT as identical.
     - **Attribution & external funding**: `record_attribution()`, `record_external_deposit()`, `record_external_withdrawal()`, and `get_attribution_summary(group_by=...)` across `agent_id`, `exchange`, `account_id`, `strategy`, and `symbol`. External deposits, withdrawals, and transfer fees are marked `is_trading_profit=False`.
     - **Reconciliation**: `reconcile()` verifies journal debit/credit balance, master pool conservation (`free + reserved == total` and `reserved == sum(slot_allocated)`), and strict separation of `paper_simulated` vs `exchange_readonly` balances.

5. **Risk-Adjusted Strategy Allocator (`app/treasury/allocator.py`)**
   - `AgentStrategyAllocator.propose_rebalance()` computes risk-adjusted scores from attribution data and enforces `GlobalRiskGuardian` supremacy (emergency stop / circuit breaker cannot be overridden), per-agent loss caps, single-agent concentration limits, aggregate allocation caps, and rebalance cooldowns.

6. **Service & Authenticated Dashboard API (`app/service.py`, `app/dashboard/server.py`)**
   - Added `TradingEngineService.treasury` and `TradingEngineService.treasury_allocator` (instantiated only when `unified_treasury.enabled` is `True`) and exposed `unified_treasury` in `state_snapshot()`.
   - Added authenticated read-only endpoints (`/api/treasury/overview`, `/api/treasury/agents`, `/api/treasury/transfers`, `/api/treasury/journal`, `/api/treasury/attribution`, `/api/treasury/reconciliation`) and controlled paper-only simulation endpoints (`/api/treasury/paper/init-slots`, `/api/treasury/paper/simulate-transfer`, `/api/treasury/paper/rebalance`), all gated by `require_auth` and returning `503` when the feature flag is disabled.

## 3. Deferred / Out-of-Scope Capabilities
- **Live exchange transfers, withdrawals, and live order routing**: Permanently disabled (`LIVE_EXECUTION_PERMANENTLY_DISABLED = True`). Future live activation would require separate explicit authorization, hardened multi-party approval, and removal of structural locks.
- **Automatic live FX feed polling**: Currently uses recorded FX snapshots (`record_fx_rate`) and `default_usdt_usd_rate` fallback; live external FX feed polling is deferred.
- **Frontend UI panels for the 18-agent treasury**: Backend API endpoints and `state_snapshot()` payloads are implemented; dedicated HTML/JS panels in `index.html` / `miniapp.html` are deferred.
- **Automatic per-cycle paper execution wiring from `PaperExecutionEngine` fills into `UnifiedTreasuryLedger`**: `UnifiedTreasuryLedger` provides `record_attribution()` and `simulate_paper_transfer_e2e()`; automatic hookup into every `PaperExecutionEngine` fill is deferred until the feature flag is enabled after validation.

## 4. Verification & Test Evidence
- Added `tests/test_unified_treasury.py` (8 test cases) covering:
  1. Configurable 18-agent slots without assuming all 18 are active.
  2. Accounting conservation, double-entry balance validation, single-master-pool no-double-spending, and SQLite append-only triggers.
  3. Idempotency and duplicate/mismatched transfer rejection.
  4. Full explicit transfer lifecycle, invalid transition rejection, ownership verification & approval requirements, and cancellation reserve release.
  5. USD vs USDT FX rate/timestamp distinction and PnL/cost attribution (excluding external deposits from trading profit).
  6. `AgentStrategyAllocator` loss caps, concentration limits, cooldowns, and `GlobalRiskGuardian` supremacy.
  7. Crash recovery preserving both existing `paper_accounts` and new `treasury_*` state.
  8. Dashboard auth (401 unauthenticated, 503 when feature flag off, 200 authenticated paper simulation) and no-live invariants.
- Executed `.venv/bin/pytest -v` after final edits: **43 passed, 2 warnings in 6.01s**.
