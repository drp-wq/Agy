# PAPER TRADE LIFECYCLE REPORT

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: PARTIALLY VERIFIED.

VERIFIED in backend suite: partial-fill aggregation, repeated client intent idempotency, protective exit persistence, partial/full close and reversal quantity/margin/P&L, rejected orders, minimum-notional/precision gates, recovered incomplete orders, persistence and ledger attribution. Relevant suites: test_paper_execution_and_recovery.py, test_paper_reversal_orders.py, test_pnl_tracker_and_ledger.py, test_all_cap_paper.py, test_violet_500_campaign.py, test_paper_campaign_and_persistence.py. Final backend result: 217 passed, two warnings, 84.60 seconds; evidence/backend-final-tests.xml.

VERIFIED snapshot: the active server campaign consists of five 100 USDT sleeves, total initial capital 500 USDT, separate from historical and simulator ledgers. At 14:06 it had no executions; later observation records one paper fill and one open position. These are different timestamps, not a discrepancy. No balance reset, position close or historical deletion was performed.

BLOCKED: full required lifecycle coverage is incomplete. The engine uses only take_profit_targets[0]; complete multi-level take-profit behavior is not implemented. Trailing-stop and liquidation-simulation support is not established in the inspected execution engine. Maker/taker defaults exist but are not proof of verified account-specific fee schedules. Do not equate synthetic P&L with actual exchange performance. One current paper entry is not an observed full entry-to-exit campaign lifecycle. The Android candidate now projects the VPS ledger and removes local simulator trading from primary runtime flows; device and deployed integration validation remain pending.

NOT TESTED: a single integrated test spanning all market-data/signal/risk/execution/settlement/dashboard layers for every requested scenario.
