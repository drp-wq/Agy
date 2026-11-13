# LIVE READINESS AUDIT — Multi-Exchange Platform (Binance / Bybit / OKX)

> **GATE STATUS: BLOCKED (`NOT_APPROVED` FOR LIVE TRADING)**
> - `LIVE_EXECUTION_PERMANENTLY_DISABLED = True` (`app/core/security.py`)
> - `mode = "PAPER_ONLY"`, `live_execution_disabled = True`, `live_readiness = "NOT_APPROVED"` (`data/readiness_watch.json`)
> - **No real orders, transfers, margin changes, wallet permission edits, or testnet/live order submissions were performed during this audit.**
> - Paper profitability or future live profitability is **NOT** promised or implied (current paper telemetry at audit time shows `-87.45 USD` realized PnL, `25.7%` win rate over `35` closed paper trades across `< 1` day of history).

---

## 1. Platform-Wide Structural Safety & Shared Blockers

| Control Area | Current State in Repo | Live-Trading Blocker Status |
| :--- | :--- | :--- |
| **Read-Only API Guard** | `enforce_read_only_exchange()` + `_wrap_transport_get_only()` + `ALLOWED_READ_ONLY_EXCHANGE_METHODS` (`GET`-only transport wrapper) and `BLOCKED_EXCHANGE_METHODS` block all `create_order`, `cancel_order`, `set_leverage`, `set_margin_mode`, `transfer`, `withdraw` calls with `LiveExecutionBlockedError` (`app/core/security.py`). | **INTACT GUARD (BLOCKER BY DESIGN)**. Live execution cannot run until an explicit multi-stage architecture review replaces read-only wrappers for an isolated order-execution module, with paper/sandbox mode separation preserved. |
| **Live Order Executor** | Only `PaperExecutionEngine` exists (`app/execution/paper_engine.py`). No live REST/WebSocket order router, exchange client-order-ID state reconciliation, partial-fill handler, cancel/replace loop, or post-trade fee/slippage verifier exists. | **MISSING (CRITICAL BLOCKER)**. Cannot place, amend, track, or cancel real exchange orders. |
| **Testnet / Sandbox Dry-Run** | Zero exchange testnet (`binance` testnet, `bybit` testnet, `okx` demo trading `x-simulated-trading: 1`) end-to-end order lifecycle runs exist. Current simulation sweeps cached REST orderbook/snapshot data locally in SQLite. | **MISSING (CRITICAL BLOCKER)**. Full sandbox order placement, rejection handling, partial fills, and cancel/SL triggers must be verified before any mainnet deployment. |
| **Exchange Filters & Min Notional** | `BaseExchangeAdapter.get_instrument_spec()` (`app/exchanges/base.py:150-175`) reads CCXT `precision`/`limits` with permissive defaults (`price_precision=0.01`, `amount_precision=0.0001`, `min_amount=0.0001`, `min_cost=5.0`). Nothing quantizes prices/quantities to exchange tick/step size or checks market-specific exchange filter rules before order assembly. | **INCOMPLETE (HIGH BLOCKER)**. Will trigger exchange `-1013` / invalid lot / min-notional / contract-size rejections in live markets. |
| **Protective Stops (SL / TP)** | `PaperExecutionEngine.mark_to_market_and_check_exits()` simulates SL/TP exits strictly in local memory during periodic scanner cycles (~15s+). No exchange-side conditional / `reduceOnly` stop-market or take-profit orders are placed. | **MISSING (CRITICAL BLOCKER)**. If the VPS restarts, hangs, or loses connectivity during volatility, open live positions would have **no resting exchange-side stop-loss**. |
| **Position & Balance Reconciliation** | Paper invariant checker `reconcile_positions()` only checks `is_paper=True` in SQLite. `sync_readonly_accounts()` caches `fetch_balance()` + `fetch_positions()` only. Aggregate live read-only USD valuation is `None` (`usd_valuation_available=False`) because spot/margin holdings lack verified FX conversion quotes. | **INCOMPLETE (CRITICAL BLOCKER)**. Live local state vs exchange state drift detector, open-order sync, fill ingestion, and real funding-fee accounting do not exist across venues. |
| **Leverage & Margin Mode Config** | `set_leverage`, `set_margin_mode`, `set_position_mode` are blocked by `BLOCKED_EXCHANGE_METHODS`. Paper bots use fixed notional `leverage=1.0` (spot) / `2.0` (perp) (`RiskPolicyConfig.max_leverage = 3.0`), without querying or enforcing isolated/cross margin settings or position mode (One-Way vs Hedge) on the real venue. | **MISSING (HIGH BLOCKER)**. Exchange account leverage/margin mode mismatch can cause unexpected liquidation risk or API order errors. |
| **Emergency Kill Switch** | `GlobalRiskGuardian.trigger_emergency_stop()` halts *new* paper signals locally (`app/risk/guardian.py`). It does **not** cancel resting live orders or submit `reduceOnly` close/flatten orders because write endpoints are blocked. | **INCOMPLETE FOR LIVE (CRITICAL BLOCKER)**. Live kill switch must actively cancel all open orders and optionally flatten/hedge exposure on exchange APIs. |
| **Loss Caps & Risk Limits** | `RiskPolicyConfig` (`app/core/config.py`) sets `daily_loss_limit_pct=0.03` (3%), `max_drawdown_pct=0.10` (10%), `max_portfolio_exposure_pct=0.60`, `max_per_exchange_exposure_pct=0.40`, `max_per_symbol_exposure_pct=0.20`, `max_correlated_exposure_pct=0.35`, `max_concurrent_positions=8`, `min_liquidation_buffer_pct=0.25`, `require_stop_loss=True`. Now updated every cycle in `TradingEngineService.run_cycle()`, but only gates simulated entries. | **PAPER-ONLY ENFORCED**. Requires exchange-realized equity feed + mark-to-market liquidation feed + server-side daily loss cutoff tied to verified account equity. |
| **Explicit Live Capital Allocation** | Current allocations in `bot_manager.py` are paper budgets totaling `$14,000` (`$4,000` Binance, `$4,500` Bybit, `$5,500` OKX). No operator-signed live USD/USDT capital caps per sub-account/exchange exist in config or enforcement. | **UNCONFIGURED (BLOCKER)**. Operator must define hard per-venue and per-market-type live collateral ceilings before any live funding. |
| **Soak Test & User Final Approval** | Multi-day paper soak has not completed (`< 1` UTC day of closed trades recorded; 30-min stability watcher running). `live_readiness` remains `NOT_APPROVED`. | **NOT APPROVED (FINAL GATE BLOCKED)**. |

---

## 2. Per-Exchange & Per-Market Audit Matrix

### A. Binance Spot (`BinanceAdapter`, `MarketType.SPOT`)
* **Paper Bot & Allocation (`bot_manager.py`)**: `binance_spot_paper` — Symbols: `BTC/USDT, ETH/USDT, SOL/USDT`; Paper budget: `$2,500.00` (1.0x, 1.0% risk/trade).
* **Actual Trade Permissions vs Read-Only Guard**:
  * Credentials configured and verified in read-only mode (`private_auth_ok: True`, `3` non-zero assets observed).
  * Local engine enforces HTTP `GET`-only & blocks all writes via `LiveExecutionBlockedError`.
  * **Exchange Portal Check Required**: Confirm Spot & Margin Trade is **disabled** (or restricted to whitelisted IPv4 with explicit trade scoping when approved later) and Withdrawals remain **permanently disabled**.
* **Order Executor & Testnet Dry-Run**: None for live Spot (`create_order`, Spot OCO/limit/market routing absent). No Binance Spot Testnet (`testnet.binance.vision`) dry-run executed.
* **Exchange Filters / Min Notional**: Missing strict enforcement of Binance Spot filters (`LOT_SIZE` stepSize, `PRICE_FILTER` tickSize, `NOTIONAL` / `MIN_NOTIONAL`, `PERCENT_PRICE_BY_SIDE`, base asset dust handling after fees if fee is charged in base asset instead of BNB/quote).
* **Protective Stops**: Paper-only polled SL/TP. No live Binance Spot `STOP_LOSS_LIMIT` or `OCO` (`orderList`) placement or open-order tracking.
* **Reconciliation**: `app/intelligence/binance_reconcile.py` runs read-only watchlist inspection (`PARTIAL_SNAPSHOT` status by design — limited to watchlist symbols and retention windows, does not verify full symbol universe or mutate paper state). Missing full balance + `myTrades` + open order reconciliation for live readiness.
* **Leverage / Margin**: Spot `1.0x` (no borrow). Must ensure Cross/Isolated Margin borrow is never triggered (`SPOT` account type enforced).
* **Kill Switch / Loss Caps / Capital**: No live open-order `DELETE /api/v3/openOrders` or spot liquidation path; no live Spot USDT cap configured; user final approval pending.

### B. Binance Futures (`BinanceAdapter` USD-M Perp, `MarketType.PERP`)
* **Paper Bot & Allocation (`bot_manager.py`)**: `binance_perp_paper` — Symbols: `BTC/USDT, ETH/USDT, SOL/USDT` (`:USDT`); Paper budget: `$1,500.00` (`2.0x` paper leverage, 1.0% risk/trade).
* **Actual Trade Permissions vs Read-Only Guard**:
  * Adapter creates default `spot` CCXT client (`opts["options"].setdefault("defaultType", "spot")` in `app/exchanges/binance.py`), switching symbols to `:USDT` for market data. Dedicated USD-M futures (`fapi`) authenticated position/margin/commission inspection is restricted under read-only guard and depends on API key "Enable Futures" permission + IP whitelist (`187.52.118.25`).
* **Order Executor & Testnet Dry-Run**: No `fapi` live order router (`POST /fapi/v1/order`), `positionSide` (`BOTH` vs `LONG`/`SHORT` Hedge Mode) detection, or `testnet.binancefuture.com` dry-run.
* **Exchange Filters / Min Notional**: Missing `fapi` `LOT_SIZE`, `MARKET_LOT_SIZE`, `MIN_NOTIONAL` (typically 5–100+ USDT depending on pair), `MAX_NUM_ORDERS`, and reduce-only rounding edge cases.
* **Protective Stops**: Missing native `STOP_MARKET` / `TAKE_PROFIT_MARKET` (`closePosition=true` or `reduceOnly=true`, `workingType=MARK_PRICE`). Currently relies on in-process polling only.
* **Reconciliation**: `binance_reconcile.py` fetches only configured watchlist futures history within `FUTURES_RETENTION_DAYS = 90` (`PARTIAL_SNAPSHOT`). No live Unrealized PnL / Funding / Commission / ADL / Liquidation event sync.
* **Leverage & Margin**: Cannot call `set_leverage` or `set_margin_mode` (`ISOLATED` vs `CROSSED`) or verify multi-asset vs single-asset margin mode because write endpoints are structurally blocked.
* **Kill Switch / Loss Caps / Capital**: No `DELETE /fapi/v1/allOpenOrders` + reduce-only market close routine; no explicit USD-M isolated wallet allocation cap; user final approval pending.

### C. Bybit Spot (`BybitAdapter`, `MarketType.SPOT`)
* **Paper Bot & Allocation (`bot_manager.py`)**: `bybit_spot_swing` — Symbols: `BTC/USDT, ETH/USDT, XRP/USDT`; Paper budget: `$2,500.00` (`1.0x`, 1.5% risk/trade).
* **Actual Trade Permissions vs Read-Only Guard**:
  * Read-only Unified Trading Account (UTA) auth verified (`private_auth_ok: True`, `18` assets observed via `fetch_balance`).
  * Engine structurally blocks writes via `LiveExecutionBlockedError`.
* **Order Executor & Testnet Dry-Run**: No Bybit V5 (`category="spot"`) live order submission, `isLeverage=0` guard (critical on UTA so Spot buys do not borrow margin), or Bybit Testnet (`api-testnet.bybit.com`) dry-run.
* **Exchange Filters / Min Notional**: Missing Bybit V5 `lotSizeFilter` (`basePrecision`, `quotePrecision`, `minOrderQty`, `minOrderAmt`) and `priceFilter` (`tickSize`) enforcement.
* **Protective Stops**: No Bybit V5 Spot TP/SL (`tpTriggerBy`/`slTriggerBy`) order placement or order-link-ID tracking.
* **Reconciliation**: Only snapshots UTA `fetch_balance()` balances (`read-only`). On Bybit UTA, Spot & Derivatives share unified collateral — there is currently **no** Bybit Spot vs Linear fill/order ledger reconciliation separating UTA equity components.
* **Leverage / Margin**: On Bybit UTA, Spot orders can inadvertently use margin borrowing unless `isLeverage=0` and collateral currency switches are verified. Currently unimplemented.
* **Kill Switch / Loss Caps / Capital**: No V5 `cancel-all` (`category="spot"`) or UTA collateral isolation cap; user final approval pending.

### D. Bybit Futures (`BybitAdapter` Linear Perp, `MarketType.PERP`)
* **Paper Bot & Allocation (`bot_manager.py`)**: `bybit_perp_funding` — Symbols: `BTC/USDT, ETH/USDT, AVAX/USDT` (`:USDT`); Paper budget: `$2,000.00` (`2.0x`, 1.2% risk/trade).
* **Actual Trade Permissions vs Read-Only Guard**:
  * Read-only UTA auth verified; engine blocks all writes (`create_order`, `set_leverage`, etc.).
* **Order Executor & Testnet Dry-Run**: No Bybit V5 (`category="linear"`) live executor or Bybit Testnet linear perpetual dry-run.
* **Exchange Filters / Min Notional**: Missing Linear `qtyStep`, `minOrderQty`, `minNotionalValue`, and `tickSize` validation before order formatting.
* **Protective Stops**: No exchange-side V5 attached `takeProfit`/`stopLoss` (`tpslMode=Full` or `Partial`, `MarkPrice` trigger) or standalone conditional `reduceOnly` stop order manager.
* **Reconciliation**: `fetch_read_only_positions()` reads active derivatives, but lacks `order/realtime`, `execution/list` (fills & funding fee ledger), and UTA margin ratio (`IM` / `MM` rate) polling (`im_rate_pct` and `mm_rate_pct` are `None` in presenter because adapter only calls `fetch_balance`/`fetch_positions`).
* **Leverage & Margin**: Missing UTA `Isolated` vs `Cross` / `Portfolio` mode enforcement, `positionIdx` (`0` one-way vs `1/2` hedge mode), and `set_leverage` synchronization.
* **Kill Switch / Loss Caps / Capital**: No V5 `category="linear"` batch cancel + reduce-only flatten handler; shares UTA collateral pool with Spot (requires strict live sub-account separation or allocation guard); user final approval pending.

### E. OKX Spot (`OKXAdapter`, `MarketType.SPOT`)
* **Paper Bot & Allocation (`bot_manager.py`)**: `okx_spot_alpha` — Symbols: `BTC/USDT, ETH/USDT, SOL/USDT`; Paper budget: `$3,000.00` (`1.0x`, 1.5% risk/trade).
* **Actual Trade Permissions vs Read-Only Guard**:
  * Read-only auth verified over IPv4 (`187.52.118.25`, `private_auth_ok: True`, `8` assets observed).
  * **EXISTING EXCHANGE-SIDE PERMISSION FINDING** (documented in `LIVE_READINESS_CHECKLIST.md:9,24`): The OKX API key configured in secrets retains `"read_only,trade"` permission on the OKX portal. Although the Python process blocks all writes (`enforce_read_only_exchange`, `GET`-only HTTP wrapper, `LIVE_EXECUTION_PERMANENTLY_DISABLED = True`), the exchange-side key should be restricted to pure `Read` permission until live execution is explicitly engineered and approved.
* **Order Executor & Testnet Dry-Run**: No OKX V5 Spot (`tdMode="cash"`) executor and no OKX Demo Trading (`x-simulated-trading: 1`) dry-run.
* **Exchange Filters / Min Notional**: Missing OKX `instruments` validation for `lotSz`, `minSz`, `tickSz`, and `tdMode="cash"` enforcement (so Spot orders never borrow in Multi-Currency Margin mode).
* **Protective Stops**: No OKX V5 Spot algo/OCO SL/TP order placement (`POST /api/v5/trade/order-algo`).
* **Reconciliation**: Only `fetch_balance()` balance snapshot exists; no OKX Spot order history (`orders-history`), fill (`fills`), or fee/rebate reconciliation.
* **Leverage / Margin**: OKX account operates in Multi-Currency Margin / Simple mode (`EXCHANGE_ACCOUNT_TYPES["okx"] = "Multi-Currency Margin"`); live Spot must explicitly enforce `tdMode="cash"` or isolated sub-account margin verification.
* **Kill Switch / Loss Caps / Capital**: No OKX V5 cancel-all / algo-cancel mechanism; no dedicated live Spot allocation cap; user final approval pending.

### F. OKX Futures (`OKXAdapter` SWAP Perp, `MarketType.PERP`)
* **Paper Bot & Allocation (`bot_manager.py`)**: `okx_perp_momentum` — Symbols: `BTC/USDT, ETH/USDT, SOL/USDT` (`:USDT`); Paper budget: `$2,500.00` (`2.0x`, 1.2% risk/trade).
* **Actual Trade Permissions vs Read-Only Guard**:
  * Read-only auth verified over IPv4 (`187.52.118.25`, `private_auth_ok: True`); same portal finding as OKX Spot (`perm` includes `trade` on OKX portal; local engine blocks all writes).
* **Order Executor & Testnet Dry-Run**: No OKX V5 `SWAP` (`instId` e.g. `BTC-USDT-SWAP`, `tdMode="isolated"`, `posSide`) live order router or OKX Demo Trading dry-run.
* **Exchange Filters / Min Notional & Contract Multiplier**: **CRITICAL OKX-SPECIFIC RISK** — OKX `SWAP` quantities are denominated in **contracts**, not base coins (e.g., 1 `BTC-USDT-SWAP` contract = `0.01 BTC` `ctVal`). Converting USD notional directly to base coin quantity without `ctVal * lotSz` conversion would cause severe mis-sizing (e.g. up to 100x wrong size if misapplied). `BaseExchangeAdapter.get_instrument_spec` fetches `contractSize`, but no live contract sizing module exists.
* **Protective Stops**: No OKX V5 SWAP `attachAlgoOrds` or standalone conditional `order-algo` SL/TP placement with `reduceOnly=True`.
* **Reconciliation**: `fetch_read_only_positions()` only reads active positions; lacks OKX SWAP `positions-history`, `bills` (funding fees), open `orders-algo-pending`, and live `mgnRatio` margin ratio polling.
* **Leverage & Margin**: Missing OKX `POST /api/v5/account/set-leverage` (`mgnMode="isolated"`), `set-position-mode` (`net_mode` vs `long_short_mode`), and cross-collateral check under Multi-Currency Margin.
* **Kill Switch / Loss Caps / Capital**: No `close-position` / `cancel-batch-orders` handler; no OKX sub-account/SWAP capital ceiling; user final approval pending.

---

## 3. Mandatory Gates Before Any Future Live Activation

All of the following must pass and receive **explicit written approval from the user** before `LIVE_EXECUTION_PERMANENTLY_DISABLED` or `mode="paper"` is ever changed:

1. **Multi-Day Paper Soak Completion**: Extended uninterrupted paper soak (verified stability, reconciled SQLite & treasury ledger, and strategy validation across varying market regimes — current sample is `< 1` day and `-87.45 USD` realized).
2. **Exchange-Side API Hygiene**:
   - Restrict OKX key to pure Read-Only during ongoing paper soak.
   - Verify IP whitelisting (`187.52.118.25`), sub-account isolation, and strict **NO WITHDRAWAL** permissions across Binance, Bybit, and OKX.
3. **Separate Live Execution Architecture & Filter Precision**:
   - Implement explicit exchange order router with CCXT market filter quantization (`tickSize`, `stepSize`/`lotSz`, `minNotional`, and OKX `ctVal` contract conversion).
   - Require server-side/exchange-side `reduceOnly` SL/TP placement on every entry (`attachAlgoOrds` / `STOP_MARKET` / UTA TP-SL).
4. **Testnet / Demo Dry-Run Sign-Off**: Pass full end-to-end order placement, partial fill, SL/TP trigger, kill-switch cancel+flatten, and crash-recovery reconciliation on Binance Testnet, Bybit Testnet, and OKX Demo Trading (`x-simulated-trading: 1`).
5. **Explicit Per-Exchange Capital Allocation & Hard Kill Switch**:
   - Operator sets explicit live USD allocation ceilings per exchange & market type (Binance Spot, Binance Futures, Bybit Spot, Bybit Futures, OKX Spot, OKX Futures).
   - Live Emergency Kill Switch implemented to issue `cancel_all` + `reduceOnly` flatten across all venues.
6. **Final Operator Authorization**: Explicit go/no-go approval by the account owner before switching any single exchange/pair out of `PAPER_ONLY`.
