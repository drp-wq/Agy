# FINAL READINESS REPORT

Observed/reported: 2026-11-14T15:39:27.973389+00:00

Verdict: **BLOCKED**. This is an engineering checkpoint. No release, live approval or candidate deployment is claimed.

Status meanings: VERIFIED = evidence supports the stated scope; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

| Required gate | Status | Evidence and limitation |
|---|---|---|
| Historical −82.214432 discrepancy | VERIFIED | 18 exits: −76.729666 gross and −5.484766 exit fees; captured current replay gap zero; all 3516 original orders preserved. Full funding history remains separate. |
| Funding economically correct | PARTIALLY VERIFIED | Signed-rate tests, durable exposure, atomic cash/attribution/journal, recovery/concurrency and 8dp rounding tests. Actual venue delivery/scheduler unresolved. |
| Complete paper lifecycle | BLOCKED | Three entry-to-exit synthetic cases, staged targets, stop/trail and liquidation model tested; resting-maker execution and deployed integration incomplete. |
| Spot/futures accounting | PARTIALLY VERIFIED | Replay and independent cash/fees/margin equations agree within tested scope; full custody/history not certified. |
| Fees/slippage/margin/liquidation | PARTIALLY VERIFIED | Actual-cost checks and taker classification repaired; maker, venue tiers, protective precision and dust gaps remain. |
| Exchange reconciliation | PARTIALLY VERIFIED | Read-only Binance/Bybit/OKX authenticated endpoints sampled; native Binance futures and bounded transfers recovered. Complete histories/permissions matrix unresolved. |
| Authoritative 500 USDT campaign | PARTIALLY VERIFIED | Existing five 100-USDT sleeves preserved; candidate Android projects server account. Deployed mobile verification pending. |
| Required multi-day stability | BLOCKED | Existing deployment only: 210 samples spanning 5.705580h, one degraded sample, max gap 931.293478s. 72h ends Nov17 09:53:05.553807 UTC; candidate needs its own full observation. |
| Android build and available tests | PARTIALLY VERIFIED | Debug APK built; 21 tests, zero failures/errors; no physical Pixel/emulator validation, verified Room-v1 migration or complete provider flow. |
| Dashboard functions/charts | PARTIALLY VERIFIED | Typecheck, 42 frontend tests/build; sampled browser views, controls, pan/zoom/mobile layout. Full indicators/overlays not independently certified. |
| Authentication/security | BLOCKED | Candidate PKCE/read-only session tests pass; production Google-provider/native flow, App Check, authorization and complete session recovery remain pending. |
| Live-order structural block | PARTIALLY VERIFIED | Candidate permanent guard and GET-only adapter tests; production dirty checkout lacks exact loaded-release provenance. Credentials/permissions unchanged. |
| Persistence/recovery | PARTIALLY VERIFIED | Transaction rollback, reopen, duplicate delivery and concurrency fixtures pass; runtime interruption matrix incomplete. |
| Operator checklist | BLOCKED | See unresolved release gates below. |
| Required approvals | NOT TESTED | No destructive operation, live activation, wallet operation or key/permission change was authorized or performed. |

Current services verified active with zero reported restarts: multi-exchange-paper.service PID673; multi-exchange-paper-audit.service PID670; multi-exchange-paper-maintenance-preview.service PID5133; caddy.service PID662. The first serves trade.187-52-118-25.sslip.io; preview also serves prottushjd.tech through the existing Caddy configuration. This is process inventory, not a soak pass.

**Exact deployed commit: UNVERIFIED.** Original production checkout base is 0f2a0d2dd752eafdd59bbeb8d93f0c440bf9245a with tracked local changes; preview includes an external launcher. Loaded code cannot honestly be equated to Git HEAD. Candidate source commit is recorded in DEPLOYMENT_MANIFEST.md after validation. No remote push, production source swap, restart, paper-account reset, historical rewrite, credential change or wallet action occurred.

Latest native source commit: c4e3725b8e868b232e60886b939ac2816583ee24. APK: artifacts/violet-scarlet-pro-protection-debug.apk; SHA-256 99b9bbe54365a0994b70115bbd8cd0ef00e6e78e624a1acd51a1e18f7a8c393c. Actual debug signing identity was inspected; no release key was generated/replaced. Device-specific validation remains pending.

Funding evidence: evidence/funding-captured-event-audit.json contains six isolated long/short positive/negative/zero scenarios with source order and event IDs, quantities, fees, funding/cash equations, restart duplicate checks and zero exposure after closure. Trade lifecycle evidence: evidence/funding-capture-lifecycle-events.json contains three isolated complete spot/futures scenarios. These are synthetic paper outcomes, never actual profits.

Release operator checklist remains incomplete: establish exact deployable provenance and compatible rollback; integrate verified funding venue history/boundaries/scheduler; finish maker and protective venue constraints; complete trade-derived journal atomicity; validate Google/native/provider/device/Room upgrade and dashboard numeric/overlay behavior; finish exchange history reconciliation and runtime recovery matrix; deploy a tested paper candidate with rollback; run its full 72h window and analyze every failure; record only an authorized push destination/branch. An existing campaign scheduled to finish does not pass any gate before observation completes.

All ten required reports are present alongside implementation reports and raw evidence. Earlier checkpoint reports are retained under checkpoints/funding-integration-20261114. Independent work continues; overall readiness remains BLOCKED.

Final funding-capture validation: backend 267 passed, 0 failed, 2 warnings in 63.43s; focused funding/mandatory gates 37 passed in 4.33s; frontend typecheck, 42 tests and build passed. Tested backend candidate source commit: e52906332c15faa098e449f911edc512a95727ec. This is a local candidate, not deployed or pushed. Commands/durations: evidence/validation-funding-commands.json; full test results: evidence/backend-funding-final.xml.

Funding delivery checkpoint: candidate automatic Binance history ingestion and service-cycle settlement are implemented/tested. Funding readiness blocks new futures exposure on unsupported/stale/failed or unresolved legacy evidence, preserving reductions/protective exits. Three actual public rate/mark events were retrieved without credentials or campaign access. Overall verdict remains BLOCKED; Bybit/OKX funding, legacy history, deployment, remaining lifecycle/recovery/mobile gates and full soak are unresolved. See FUNDING_DELIVERY_IMPLEMENTATION_REPORT.md.

Current operating soak sample: 222 records over 5.993648 hours, one degraded sample, maximum gap 931.293478s; last sample 2026-11-14T15:53:12.802465Z. No 72h pass. Newly confirmed dashboard blocker: legacy P&L reporting omits the new funding event type and uses truncated event history; presentation-accounting repair remains pending.

Final funding-delivery validation: full backend 288 passed, zero failed, two warnings in 63.52s (evidence/backend-funding-delivery-release.xml); focused funding plus mandatory gates 58 passed in 4.72s. Frontend typecheck, 42 tests and build passed. Tested local backend candidate commit: 198cc1742d063fd54092edeba87f39dc4295aa68. No remote push or service restart/cutover occurred.

Read-only running-campaign check found a real presentation discrepancy: source cash/fees/closures equation difference is zero, while the published asset-realized sum is −0.2007 and portfolio-realized is −0.2207 USDT; the 0.02 entry fee is omitted from asset attribution. evidence/campaign-reporting-gap.json records this paper-account observation. No source mutation or reset was performed. Dashboard accounting repair is the next independent task.
