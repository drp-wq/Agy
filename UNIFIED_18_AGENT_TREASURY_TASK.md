# Unified 18-agent capital allocation and attribution (PAPER-FIRST)
Build within /root/multi-exchange-paper after mid-low-cap-paper-build completes. User requests 18 automated agents, a unified USD/USDT treasury view, allocations by user/agent to exchange/account/strategy, and accurate profit/loss attribution by origin.

Security/custody:
- Unified wallet is an accounting dashboard, NOT pooled cross-exchange custody. Never claim funds move merely by changing ledger entries.
- Default all transfers to MANUAL instructions or recorded external transfer evidence. Agent can propose transfers and trade allocations, but must NEVER call exchange withdrawal, internal transfer, wallet signing, or trading write APIs without a separate explicit authorization and hardened approval workflow. Do not change credentials, live order flags, or exchange permissions.
- No real fund movement or live orders in this task. Paper-only test transfers may be simulated.
- Require explicit account ownership verification and user approval for future live transfers. Never expose keys or addresses in logs.
Architecture:
- Configurable 18 agent slots with IDs, strategy, exchange, account/subaccount, allowed symbols, capital budget, risk limits and status. Do not assert 18 are already active.
- Double-entry append-only accounting journal with unique transfer IDs and idempotency, per-asset and per-exchange/account balances, immutable audit trail, decimal arithmetic and reconciliation. USD valuation must distinguish USDT/USD and record FX conversion/time; do not treat USD and USDT as exactly identical.
- Explicit lifecycle: treasury unallocated -> reserved -> transfer proposed -> user approved -> external transfer pending -> confirmed from exchange read-only evidence -> agent available -> paper trading; reject double counting, negative available funds and duplicate transfers.
- For paper transfers, simulate ledger movements separately from real exchange balance; never report paper as real.
- Attribution: realized/unrealized PnL by agent, exchange, strategy, symbol, account, plus trading fees, funding, slippage, transfer fees and external deposits/withdrawals. Transfers/deposits are NOT trading profit. Reconcile source and destination balances.
- Agent strategy allocator may propose/rebalance budgets using measured risk-adjusted performance with loss caps, concentration limits and cooldowns, but no agent may override the global risk guardian.
- API/dashboard read-only treasury views and controlled paper-only simulation endpoints if consistent with existing auth; never expose unauthenticated writes.
- Migration must preserve existing SQLite paper ledger, current bot IDs, and service availability. Do not restart production service without explicit approval. New feature flag defaults off until tested.
- Unit/integration tests for accounting conservation, idempotency, transfer lifecycle, PnL, USD/USDT FX, agent risk budgets, 18-agent slots, permissions, crash recovery and no-live invariants.
- Deliver UNIFIED_TREASURY_IMPLEMENTATION_REPORT.md with exact implemented vs deferred capabilities and evidence. Run tests.

## User clarification — one unified account (authoritative)
The user explicitly chose ONE unified capital account shared by all 18 agents, not 18 independent funded accounts. Implement a single master capital pool with 18 virtual agent allocations/reservations and per-agent PnL attribution. All agents draw from the same available budget subject to aggregate risk and concurrency caps. No double spending: total agent reservations plus free capital must reconcile to the single master account. Exchange-specific accounts are execution destinations/linked subledgers, not separate independent agent treasuries. Funds remain physically on their actual exchange/wallet until externally transferred and verified. No real transfers or live orders.
