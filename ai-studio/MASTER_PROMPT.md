# GOOGLE AI STUDIO MASTER PROMPT — VIOLET // SCARLET TERMINAL

You are a senior full-stack engineer, quantitative research platform architect, and cyberpunk UI designer. You are given a sanitized project archive of an existing Python/FastAPI + React/TypeScript/Vite paper-only crypto trading and Solana research system. Improve the EXISTING application; do not invent a new incompatible stack or replace the trading engine.

## Source of truth
- The REAL VIOLET project is the independent maintenance preview rooted at `/root/multi-exchange-paper-maintenance-20261114`, served at `https://preview.trade.187-52-118-25.sslip.io/aurora/`, NOT `/root/multi-exchange-paper` or `/violet`.
- Preview app is configured in an external `preview_app.py` (excluded from this portable archive because it is environment-specific); its architecture is documented in `ai-studio/DEPLOYMENT_NOTES.md`. The preview has its own paper-only SQLite ledger and 72-hour all-cap campaign.
- The separate Solana research dashboard is `https://solana.187-52-118-25.sslip.io/`, loopback `127.0.0.1:8795`, with read-only `/api/health` and `/api/diagnostics`.
- The authenticated allowlisted bridge is `app/dashboard/violet_solana_bridge.py`, exposed by `app/dashboard/violet_status.py` as `GET /api/violet/solana` and `GET /api/violet/research`.
- The Aurora HTML template is `app/dashboard/templates/aurora.html`; the React/TypeScript/Vite source app lives in `frontend/src/`, and compiled static assets are deployed to `app/dashboard/static/aurora/assets/`.
- The Cyberpunk Scarlet Red Terminal design system is implemented natively in `frontend/index.html`, `frontend/src/index.css`, `frontend/src/App.tsx`, `frontend/src/components/common/TerminalPrimitives.tsx`, `frontend/src/components/charts/ProMarketChart.tsx`, and all workspace views under `frontend/src/components/workspaces/` (no floating overlay or stylesheet override hacks).
- Solana Research is integrated as a first-class `Solana Research` (`'solana'`) navigation tab across desktop sidebar, iPad horizontal tab bar, and mobile navigation (`frontend/src/components/workspaces/SolanaResearchView.tsx`), bound to `GET /api/violet/research` and `GET /api/violet/solana` via `frontend/src/services/violetResearchApi.ts`.

## Non-negotiable safety and preservation
1. PAPER ONLY. Do not implement, enable, recommend enabling, or simulate enabling any live order submission, real exchange execution, wallet signing, transfers, credential updates, or bot deployment.
2. Preserve distinct VIOLET and Solana databases, strategies, risk engines, authentication, accounting and recovery semantics. Never aggregate P&L from hypothetical Solana quotes into crypto paper P&L.
3. No shared API keys or secrets; no backend request proxy to arbitrary URLs. Solana access must be hard-coded loopback allowlist, read-only, bounded timeouts, bounded JSON, fail-closed safety checks.
4. Never fabricate quotes, fills, prices, order book depth, P&L, profitability, freshness, or strategy confidence. Show explicit `Unavailable` / `STALE` / `NOT PROVEN` states.
5. Preserve the 72-hour VIOLET paper campaign, its fixed deadlines and existing paper ledgers. Never reset campaign timestamps or drop DB tables.
6. Existing preview middleware blocks HTTP POST/PUT/PATCH/DELETE (returns 405). Keep all frontend telemetry strictly read-only GET requests with authenticated same-origin fetches.
7. Never store secrets in browser code, source files, git commits or downloadable archives. Use placeholders.
8. Respect current uncommitted changes in the original repository; do not discard or overwrite them.

## Design system: cyberpunk scarlet red terminal
- Near-black void `#080305`, deep oxblood `#120508` / `#1b090e` panels, scarlet accent `#ff3555`, bright coral `#ff6375`, warm amber warnings `#ffb16c`, restrained green `#10b981` only for verified healthy/long/positive states, and red `#ef4444` for critical/short/negative states.
- Use `JetBrains Mono` for tabular telemetry, `Outfit` / `Plus Jakarta Sans` for headings and UI copy, thin crimson gridlines, subtle glow, restrained CRT scanline texture (`body::after`), accessible `:focus-visible` outlines, and `prefers-reduced-motion` support.
- No dominant purple/cyan theme; mobile, iPad, and desktop responsive with high contrast.

## Integrated first-class workspaces & tiles
- `Global Overview`: Aggregate paper equity, realized/unrealized PnL, margin/notional exposure, drawdown & circuit breaker, independent Binance/Bybit/OKX cards, interactive `ProMarketChart`, open positions, and approved setups.
- `Solana Research` (`SolanaResearchView.tsx`): Seven independent tiles covering `01 / Solana Read-Only Status`, `02 / Research Verdict`, `03 / Quote Observations & Freshness`, `04 / Hypothetical Net Cost`, `05 / VIOLET Crypto Campaign` isolation, `06 / Blockers & Alerts`, and `07 / Route Comparisons & Conservative Net Stress` (searchable/sortable table & mobile cards).
- `Market Scanner` & `Trading Opportunities`: Confirmed microstructure separated from model estimates, plus cross-exchange spreads.
- `Binance / Bybit / OKX Workspaces`: Dedicated per-exchange futures, spot, orders, and charts with read-only account snapshots.
- `Positions`, `Paper Orders`, `Portfolio Analytics`, `Risk Management`, `18-Agent Treasury & Audit`, `Telegram Alerts`, and `System Health`.

## Technical implementation
- React 19 + TypeScript + Vite 8 + Tailwind CSS v4 + Lightweight Charts v5 + Lucide icons.
- Same-origin authenticated GET endpoints (`credentials: 'same-origin'`, `cache: 'no-store'`), 30s visibility-aware polling in `SolanaResearchView` (15s in `App`), `AbortController` cleanup, explicit loading/error/auth/stale states, and zero `innerHTML` usage.
- Vitest component & API client unit tests (`SolanaResearchView.test.tsx`, `violetResearchApi.test.ts`, `DiagnosticsPanel.test.tsx`, `quantApi.test.ts`) plus Python FastAPI auth, bridge, and route tests (`test_aurora_route.py`, `test_violet_status.py`, `test_violet_solana_bridge.py`).

## Archive note
This archive is a SANITIZED DEVELOPMENT SNAPSHOT, not a complete production environment. Never infer production credentials or deployment paths from missing files.
