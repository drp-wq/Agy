# PAPER PROTECTION IMPLEMENTATION REPORT

Observed: 2026-11-14T15:16:18.118900+00:00

Overall: PARTIALLY VERIFIED. Candidate execution and accounting changes are VERIFIED by the evidence below. Production deployment and complete platform readiness remain BLOCKED; external/device scopes listed below remain NOT TESTED.

Candidate behavior:

- New multi-target, trailing-stop, and futures positions get a versioned durable protection plan in the existing risk_state table, committed with the entry. No campaign schema migration, account seed, balance reset or historical-source rewrite is required. Existing operating positions were not retrofitted or changed.
- Multi-target quantities conserve executed size. Partial exits retain the next target; external reductions rescale only remaining levels. Completed targets are not resurrected when a position is resized. Trailing anchors/stops cannot implicitly recede on scale-in.
- A gap crossing several levels produces one combined request against one observed book. Partial liquidity is consumed once; duplicate delivery, earlier quotes and pre-entry quotes cannot create extra executions. A fresh quote can complete a surviving exit after restart. Fixed stops and trailing stops have distinct auditable reasons.
- Simplified paper cross-margin liquidation uses the bot account's cash plus marked position P&L against the sum of each futures position's configured maintenance notional. Default rate is 0.5%, explicitly a simulation assumption. All account positions require usable marks and all futures positions require known protection policies; missing evidence does not trigger an inferred liquidation. Stop/target barriers are not fictitious execution prices: fills use observed book/depth prices. Gap insolvency remains explicit; no insurance credit or cash reset is fabricated.
- Initial, resized and partially closed positions now carry unrealized P&L for their actual remaining size. The previous entry/partial-close materialization defect is fixed.
- Actual execution cost, fees and reversal margin are rechecked under the account write transaction. Missing accounts, unfunded new spot shorts/oversells, invalid inputs, unsupported zero-priced fills and implicit leverage changes are rejected before financial posting.
- Marketable limit orders use taker fees. A limit flag alone no longer earns a maker discount. A genuinely resting maker-fill lifecycle is still BLOCKED, not certified.
- Explicit paper cancellation changes only an unfilled remainder. Recovery reconstructs order VWAP, fees and slippage from persisted fills. Executed partial/canceled orders are included in historical replay and missing-evidence checks; evidenced funding rows participate in replayed cash/realized P&L.
- Materialized reconciliation now checks cash/realized, reserved margin, stored unrealized and order/fill quantity/fee/VWAP equations across all orders. It still explicitly declines to certify complete historical economics. The mid/low-cap selector no longer appends nearer, inadequate-reward targets after its approved primary target.

Evidence:

- Full backend suite: 250 passed, two warnings, 68.51s. evidence/backend-protection-release-validated.xml and .log.
- Dedicated protection lifecycle tests cover long spot, long/short futures, staged exits, gap liquidity, replay, independent engine instances, restart, trailing behavior, cancellation, resizing, actual-cost rejection, invalid inputs, economic drift and atomic crash rollback. tests/test_paper_protection_lifecycle.py; the full suite includes all 25 cases.
- Reproducible credential-free entry-to-exit proof: `PYTHONPATH=. /root/multi-exchange-paper/.venv/bin/python scripts/verify_paper_protection_lifecycle.py --out /root/violet-release-audit/evidence/protection-trade-events.json`. Three cases record order/fill/closure IDs, fees, cash, margin and independently reconstructed equations. These are SYNTHETIC PAPER FIXTURES, never observed exchange profits.
- Historical revalidation still reproduces the original 18 exits, with all 3,516 source orders unchanged and 158 current-snapshot executed orders replayed. evidence/pnl-protection-revalidation.log.
- Native client now checks position-mark P&L against the portfolio total, and its shared synthetic fixture includes entry slippage. Debug APK build: 21 tests, zero failures/errors/skips; evidence/android-protection-contract.log. APK SHA-256: 99b9bbe54365a0994b70115bbd8cd0ef00e6e78e624a1acd51a1e18f7a8c393c.
- Frontend typecheck, 42 tests and build passed. Mandatory closed-loop/status/Solana backend gates: 11 passed.

Remaining limits:

BLOCKED: genuine resting-maker execution; automated funding interval capture/settlement/recovery integration; all-venue protective-order precision/minimum/dust policies; complete signal/worker/journal/UI recovery and source-provenance release validation; multi-day candidate stability. New plans do not automatically apply to legacy positions. The standard legacy exit path retains its prior execution model.

PARTIALLY VERIFIED: liquidation is a documented paper cross-margin model, not an exchange tier/insurance/funding/ADL replica. Do not represent it as verified live liquidation behavior. Historical attribution still needs its separate audit; equation agreement alone is insufficient. The current dashboard exposes the next active take-profit field; complete multi-level overlay rendering is not yet verified. Derived paper_ledger_events views can still be appended outside the financial transaction by existing workers; atomic derived-view recovery needs further work.

Rollback limitation: an older coordinator does not understand staged plans. Do not silently downgrade an operating account containing these new plans. Before deployment, prepare/test a compatible recovery coordinator or quiesce entries and retain the candidate protection executor until affected positions are flat. No destructive database restore is authorized or required.

NOT TESTED: actual provider login, Pixel hardware/instrumentation (adb reported no devices), exchange-specific liquidation tiers/penalties and complete production fault-injection recovery. APK signatures were verified against the actual debug certificate. Uploaded client JSON contained no Android OAuth registration entries, so provider-console/release-certificate registration is unverified, not inferred absent. No signing keys or credentials were generated or changed.

Runtime: no deployment, service restart, remote push or operating-ledger mutation. Existing services retain PIDs 673/670/5133 and Caddy 662, NRestarts=0 at this checkpoint. Existing campaign samples span 5.3200/72 hours, with one degraded sample and maximum gap 931.293478s. This old running deployment's observation does not establish a 72-hour pass for the new candidate commit.

Additional verified transitions: concurrent entry intents return the same committed order; explicit full closes retire the durable plan; pure reductions remain possible at exposure caps/entry circuit breakers, while reversals require net new-exposure risk approval and actual execution capacity. The native source commit for this checkpoint is c4e3725b8e868b232e60886b939ac2816583ee24.

Validated protection backend source commit: 1d29f16a7ee44e5cf5e7d7cc011f844b4a51c3ad. Native contract source commit: c4e3725b8e868b232e60886b939ac2816583ee24. Both are local candidates; neither has been pushed or deployed. Existing production base remains 0f2a0d2dd752eafdd59bbeb8d93f0c440bf9245a with local modifications, so no exact deployed-commit certification is claimed.
