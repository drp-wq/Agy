# Mid/Low-Cap Adaptive Paper Autotrading Implementation Report

## 1. Summary & Safety Invariants
Implemented feature-flagged mid-cap and low-cap adaptive **paper-only** spot scanning and autotrading in `/root/multi-exchange-paper`:
- **Live execution remains permanently disabled**: `LIVE_EXECUTION_PERMANENTLY_DISABLED = True`, `EngineConfig(live_orders_enabled=True)` raises `ValueError`, no credential changes were made, and no real orders are submitted.
- **Production service protection**: The existing running `scripts/run_platform.py` process was **not** restarted. All new functionality is gated behind `MidLowCapConfig.enabled` (default `False`; toggleable via `MID_LOW_CAP_AUTOTRADE_ENABLED=true` or explicit config).
- **Existing paper ledger preserved**: Mid/low-cap paper orders, fills, positions, and audit logs flow through `PaperExecutionEngine` into the existing SQLite ledger (`paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `audit_logs`) under dedicated bot ID `mid_low_cap_spot_paper` with `is_paper=True`.

## 2. Implemented Modules
1. **Configuration & Feature Flags** (`app/core/config.py`)
   - Added `MidLowCapConfig` to `EngineConfig` (`enabled=False`, `allow_short=False`, `max_leverage=1.0`).
   - Configurable bands: `low` `$50M–$300M`, `mid` `$300M–$10B`.
   - Risk & sizing bounds: `max_risk_per_trade_pct=0.0025` (0.25%), `daily_loss_limit_pct=0.01` (1%), `max_simultaneous_positions=3`, `min_risk_reward_ratio=2.0`.
2. **Market-Cap Classification & Provenance** (`app/scanner/market_cap.py`)
   - `MarketCapProvider` queries CoinGecko's documented public `/api/v3/coins/markets` endpoint without any embedded API secrets.
   - Bounded (`market_cap_max_pages=4`), rate-limited (`market_cap_min_request_interval_sec=2.0`), cached (`market_cap_cache_ttl_sec=1800.0`), with freshness validation (`market_cap_max_age_sec=3600.0`).
   - Classifies `MICRO`, `LOW`, `MID`, `LARGE`, or `UNKNOWN`. Never infers market cap from 24h volume or price. Ambiguous ticker collisions, missing circulating supply/market cap, stale timestamps, or API failures classify as `UNKNOWN` and are excluded.
3. **Dynamic USDT Spot Discovery & Safety Filters** (`app/scanner/mid_low_cap_discovery.py`)
   - `DynamicSpotMarketDiscoverer` discovers active `USDT` spot markets from read-only exchange metadata (`adapter.load_markets()`), excluding inactive markets, futures/swaps/options, non-USDT quotes, leveraged tokens (`*UP`, `*DOWN`, `*BULL`, `*BEAR`, `*[2345][LS]`), stablecoins/fiat-pegged assets, and non-target/unknown cap bands.
   - Uses rotating bounded scan batches (`scan_batch_size=12`, `discovery_cache_ttl_sec=900.0`).
   - `evaluate_safety_filters` rejects on any missing safety data, non-spot market, stale data (`>30s`), wide spread (`>35 bps`), low order book depth (`<$25,000`), low 24h volume (`<$1,000,000`), insufficient candle history (`<60` candles), missing ATR, suspicious volume turnover (`volume_24h / market_cap > 3.0`), suspicious single-candle volume spikes (`>12x` median with `<0.2%` range), or same-base open position (concentration / no averaging down).
4. **Adaptive Strategy Selection & OOS/Cost Gating** (`app/strategies/mid_low_cap_selector.py`)
   - `MidLowCapStrategySelector` restricts selection to `trend_following`, `breakout_retest`, and `mean_reversion` from `AIStrategyOrchestrator` when market regime permits, spot-long only by default.
   - Enforces ATR-anchored stops and `risk_reward_ratio >= 2.0`.
   - Incorporates exchange taker fees and spread-derived slippage into `compute_net_reward_risk` and `walk_forward_net_oos`, requiring `trades >= min_oos_trades` and `net_expectancy_r > 0` before permitting a signal.
5. **Runner & Service Integration** (`app/execution/mid_low_cap_runner.py`, `app/service.py`)
   - `MidLowCapAutoTradeRunner` sizes positions strictly by `0.25%` paper equity risk divided by ATR stop distance (capped by depth, cash, and single-position allocation), enforces max 3 simultaneous positions, 1% daily loss circuit breaker, and no martingale/averaging down.
   - Integrated into `TradingEngineService.run_cycle()` (when `mid_low_cap.enabled` is `True`) and exposed via `TradingEngineService.state_snapshot()["mid_low_cap"]`.

## 3. External Market-Cap API Verification & Blockers
- **CoinGecko Public API (`/api/v3/coins/markets`)**: Tested directly from this host with `market_cap_max_pages=2`. Returned HTTP `200` with `494` cached symbols across 2 requests (`band_counts`: `micro=70, low=278, mid=117, large=13, unknown=16`).
- **Operational Note / Potential Blocker**: CoinGecko's unauthenticated public endpoint enforces rate limits (HTTP 429 on bursts). Our implementation mitigates this with `market_cap_max_pages=4`, `market_cap_min_request_interval_sec=2.0`, and `market_cap_cache_ttl_sec=1800.0`. If CoinGecko rate-limits or is unreachable, `MarketCapProvider` logs a warning and classifies symbols as `UNKNOWN`, safely halting cap-targeted entries rather than guessing from volume or price.

## 4. Verification & Test Evidence
- Added `tests/test_mid_low_cap_autotrade.py` covering:
  1. Circulating market-cap classification, provenance, caching, ambiguity guard, and no-volume-inference.
  2. Dynamic USDT spot discovery and exclusion of inactive, perp/swap, leveraged-token, stablecoin, and non-target-cap markets.
  3. Hard safety filter rejections (missing/stale data, wide spread, low depth/volume, short history, suspicious turnover, same-base duplicate).
  4. Adaptive strategy selection with regime gating, `R:R >= 2.0`, fee/slippage net expectancy, and walk-forward OOS gating.
  5. End-to-end paper execution through `TradingEngineService` -> `MidLowCapAutoTradeRunner` -> `PaperExecutionEngine` -> SQLite ledger, verifying `is_paper=True`, `OrderStatus.FILLED`, `leverage=1.0`, 3-position cap, 0.25% ATR-risk sizing, 1% daily loss circuit breaker, and `live_execution_disabled=True`.
- Full test suite executed via `.venv/bin/pytest -v` after final edits: **35 passed** (see final run output).
