# Test Results

## Automated Unit & Integration Test Suite (`pytest -v`)

Executed after final code edits on 2026-11-13:
- **Total**: 18 passed, 0 failed (1.91s)
- `tests/test_adapters.py`: OKX & Bybit normalization, read-only auth, retry backoff, and outage isolation (2 passed)
- `tests/test_dashboard_and_api.py`: Dashboard auth, security headers, secret non-disclosure, bot toggle, emergency stop (1 passed)
- `tests/test_paper_execution_and_recovery.py`: Partial fills, idempotency, reconciliation, TP exit, backup, crash recovery (2 passed)
- `tests/test_risk_engine.py`: Risk limits, stale data, duplicate suppression, leverage cap, outage protection, emergency stop, daily loss circuit breaker (2 passed)
- `tests/test_scanner_and_indicators.py`: Indicators, regime classification, concurrent 2-exchange scanner (2 passed)
- `tests/test_security_and_live_block.py`: Mandatory live-order block on OKX & Bybit, config rejection, secret redaction, `0600` permission validation, real secrets metadata check (7 passed)
- `tests/test_strategies_and_orchestrator.py`: All 10 strategies, empirical confidence calibration, walk-forward OOS evaluation without look-ahead bias (2 passed)

## Live Read-Only & Paper Verification

- **Public Market Data**: Verified live `BTC/USDT` ticker, order book, OHLCV, and perpetual funding rate on both **OKX** and **Bybit**; full 28-pair concurrent scan (14 OKX + 14 Bybit) completed with paper orders reconciled (`reconciled: true`).
- **Outbound IP & Read-Only Auth**:
  - **Bybit**: Verified (`status: ok`, `asset_count: 18`).
  - **OKX**: Initially failed over IPv6 (`2a02:4780:5e:693c::1`) with `50110` IP whitelist error. After configuring `OKXAdapter` to force IPv4 (`187.52.118.25`), OKX authenticated balance (`8` assets) and positions (`0`) retrieval succeeded (`status: ok`). OKX `/api/v5/account/config` reports key `perm: "read_only,trade"` on the exchange side; locally, `enforce_read_only_exchange()` structurally blocks all trade/write/withdrawal methods (`LiveExecutionBlockedError`).
- **Dashboard & Service**: `multi-exchange-paper.service` active and running; `/api/health` returned `200`, unauthenticated `/api/state` returned `401`, authenticated `/api/state` returned `200` with `PAPER_ONLY`, both exchanges `healthy: true`, and 4 active bots.
