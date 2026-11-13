# AGY TASK: Phantom watch-only wallet + unified 18-agent trading system full-stack integration

Work in /root/multi-exchange-paper. Existing service is active and must not be interrupted. Preserve all existing changes, files, accounts, services, paper ledger and authentication. Read existing architecture, UNIFIED_TREASURY_IMPLEMENTATION_REPORT.md, MID_LOW_CAP_IMPLEMENTATION_REPORT.md, config/watch_only_wallets.json and app/dashboard/server.py first.

GOAL: Integrate the existing unified single-master-capital-pool, 18 virtual agent allocation/attribution treasury and the registered Phantom Solana public wallet into BOTH backend and polished responsive frontend. The wallet is NOT a trading account or automatically spendable master pool. Distinguish actual exchange balances, paper simulated balances and externally held watch-only assets; never count watched wallet funds as available trading cash without verified transfer/reconciliation.

Backend:
- Create secure read-only Solana mainnet balance monitor for FWTfn9ijTN9PE9H4Wcy1LKqFKgoQrxhBQDh13Lf57h5T, using public Solana JSON RPC with bounded timeouts, retries/backoff, rate limiting, caching and staleness/error indicators. Read SOL lamports and SPL USDT only when token mint verified against known Solana USDT mint; never use arbitrary token symbol to identify USDT. Handle token accounts and decimals correctly, unknown balances, unavailable RPC and no USDT accounts. Never request private keys or seed phrases.
- Wire watch-only wallet registry to authenticated read-only dashboard API endpoints with sanitized responses; protect access via existing auth; no secret or credentials exposure, no unauthenticated wallet data, no signing, wallet connect signature, transfers, swaps, external writes or live trading.
- Expose authenticated treasury snapshot, 18 agents' virtual allocation/reservation/PnL, aggregate risk limits, exchange-linked paper subledgers and accounting reconciliation. Use existing ledger/allocator APIs rather than fabricate metrics. Guard against double counting and stale state. Separate simulated and actual funds visibly.
- Ensure errors cannot crash the trading engine; read-only data access and existing safety invariants. Never silently activate feature flags or modify production keys.
Frontend:
- Extend existing dashboard (mobile, iPad, desktop) with Wallet panel showing Phantom Solana address, SOL and SPL USDT, freshness, RPC health, read-only state, copy address and Solscan link.
- Unified Treasury panel with one master PAPER pool, free/reserved/allocated balances, 18 agent cards with per-agent status, PnL and risk, exchange allocation view, reconciliation status and clear no-live/no-transfer indicators.
- Integrate with existing market scanner, strategies, paper orders, positions and risk dashboard; no disconnected mock-only UI, no placeholder values represented as real balances. Good empty/error/loading states; accessible neon/cyberpunk styling.
- No operational live order/withdraw/deposit/transfer buttons; any future operations represented as disabled informational controls.
Testing and verification:
- Add tests for Solana RPC responses, malformed/timeout/rate-limit cases, SPL mint/decimals, auth enforcement, balance freshness, 18-agent allocation and treasury conservation, API contracts, UI render smoke where possible, live-disabled invariant.
- Run full pytest, static/syntax checks, safe authenticated API smoke tests if possible. No production service restart or change without explicit approval. Do not activate new feature flags without approval.
- Produce PHANTOM_UNIFIED_INTEGRATION_REPORT.md with files, tests, limitations, activation plan, and clear distinctions: implemented vs deployed. Record any blockers. Do not claim real wallet balances unless independently fetched.

Maintain live_order_submission=false, external_writes=false, signing_enabled=false, transfers_enabled=false. Never store private keys.
