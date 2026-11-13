# Supervised remediation: Binance reconciliation + Phantom integration
You are AGY acting as implementation agent. Work only in /root/multi-exchange-paper. Preserve running services, credentials, existing uncommitted user work, and all other projects. Do not deploy live trading, submit orders, transfer funds, change wallets/keys, relax read-only enforcement, or use --dangerously-skip-permissions.

Goals:
1. Complete read-only Binance Spot and USDT-M Futures account-wide open-order discovery (native signed read-only endpoints if needed); enumerate markets actually implicated by positions, balances, and orders. Avoid claiming full account coverage when API limitations exist.
2. Implement durable, bounded, resumable historical order/trade pagination with timestamp/ID deduplication, watermarks, pagination gaps, and explicit completeness/provenance flags. Account for API retention windows, symbol-specific endpoint requirements, and rate limits. Avoid heavy all-market polling; schedule slow backfills separately from 15-minute lightweight health checks.
3. Reconcile exchange-side records separately from simulated paper ledger, with correct account/exchange/market provenance and explicit mismatch categories. Never equate paper orders with real orders.
4. Fix the two failing tests in tests/test_phantom_unified_integration.py: RPC timeout exception-type reporting and missing agent-01 slot in treasury smoke test. Fix underlying implementation/test setup without weakening safety or masking failures.
5. Add deterministic mock-based tests for Binance account-wide open-orders, Futures queries, pagination, deduplication, resume, errors, read-only guard, and reporting. Run full suite.
6. Provide clear completion report with tests, remaining blockers, and safe rollback. Keep live disabled and secrets redacted.

IMPORTANT: A prior 2026-11-13 14:08 UTC run verified native Binance spot private_get_openorders succeeds (0 open orders), and Futures 4 symbol queries succeed. The latest full suite had 46 passed, 2 failed (Phantom tests). Review existing code before changes. No claims of success without evidence.
