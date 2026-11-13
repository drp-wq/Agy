# NEON QUANT V3 — VPS Deployment, API Contract & Regression Test Plan

## 1. Security & Paper-Only Guarantees

- `LIVE_EXECUTION_PERMANENTLY_DISABLED = true` is enforced at both TypeScript compile time and runtime (`src/types/quant.ts`, `src/services/quantApi.ts`).
- `LiveExecutionBlockedError` is thrown by `quantApi.submitLiveOrder()` if any UI or programmatic path attempts live order execution.
- Zero exchange API secrets, private keys, or wallet credentials exist in frontend source or `.env.example`.
- All FastAPI requests attach `X-Telegram-Init-Data` from `window.Telegram.WebApp.initData` for server-side HMAC-SHA256 verification and owner enforcement.

## 2. Existing & Proposed Authenticated Read-Only FastAPI Endpoints

### Existing Endpoints (Used by `src/services/quantApi.ts`)
- `GET /api/miniapp/state` — Full portfolio, exchange account health, open paper positions, scanner opportunities, risk state, and alerts.
- `GET /api/miniapp/health` — Backend service heartbeat, `live_execution_permanently_disabled`, Telegram auth verification, and public/private exchange adapter distinction.
- `GET /api/miniapp/candles/{exchange}/{market_type}/{base}/{quote}/{timeframe}` — Timestamped OHLCV candle array used for chart rendering and client-side calculation of EMA(9/21), VWAP, Bollinger Bands(20), RSI(14), MACD(12,26,9), ATR(14), and S/R levels.

### Proposed Additive Read-Only Endpoints
All proposed routes must require `Depends(verify_telegram_init_data)` and owner whitelist checks:
1. `GET /api/miniapp/orders?exchange=all&status=all&limit=100` — Paginated paper order & fill ledger with slippage/fee breakdown.
2. `GET /api/miniapp/positions/history?exchange=all&days=30` — Closed & historical paper futures positions with intraday PnL snapshots.
3. `GET /api/miniapp/scanner/arbitrage` — Live cross-exchange mark/orderbook spread dispersion across Binance, Bybit, and OKX.
4. `GET /api/miniapp/alerts?category=all&limit=50` — Filtered Telegram alert history and delivery confirmation log.

## 3. Ubuntu 24.04 VPS Deployment & Rollback Plan

> **Safety Rule**: Never overwrite `/root/multi-exchange-paper` without backing up the existing bundle first.

```bash
# 1. Create timestamped backup on VPS
cd /root/multi-exchange-paper
cp -a miniapp_dist miniapp_dist.bak.$(date +%Y%m%d_%H%M%S)

# 2. Build NEON QUANT V3 frontend
npm ci
VITE_API_BASE_URL="" npm run build

# 3. Verify CSP & Caddy routing (preserve existing Caddyfile headers)
# Ensure Caddy serves /miniapp/* from dist/ and proxies /api/miniapp/* to 127.0.0.1:<fastapi_port>
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy

# 4. Rollback Command (if any regression is observed)
rm -rf /root/multi-exchange-paper/miniapp_dist
mv /root/multi-exchange-paper/miniapp_dist.bak.* /root/multi-exchange-paper/miniapp_dist
```

## 4. Regression & Security Test Matrix

| Check | Verification Method | Expected Outcome |
|---|---|---|
| Type Safety & Build | `npm run lint` (`tsc --noEmit`) & `npm run build` | Zero TypeScript or bundler errors |
| Paper-Only Guard | Trigger `Verify Guard` in Paper Orders view (`quantApi.submitLiveOrder()`) | Throws `LiveExecutionBlockedError` (`LIVE_EXECUTION_BLOCKED`) |
| Telegram Auth Boundary | Return HTTP 401/403 from `/api/miniapp/state` | Renders `AuthErrorBanner`; zero fallback to unverified live data |
| Public vs Private Separation | Inspect Binance/Bybit/OKX headers when `private_auth_ok=false` | Public feed shows Connected; Private Auth explicitly displays Unauthenticated |
| Spot vs Futures Separation | Inspect Positions -> Spot Inventory vs Futures | Spot assets show qty/valuation only; never shown with leverage or liquidation price |
| Real Indicator Math | Inspect `src/utils/indicators.ts` with timestamped OHLCV | EMA, RSI, MACD, VWAP, BB, ATR derived solely from real candle closes/HLCV |
| Responsive Viewports | Test 375px (Telegram Mobile), 768px/1024px (iPad), 1440px (Desktop) | Bottom nav on mobile (`pb-safe`), horizontal tabs on iPad, sidebar on desktop |
