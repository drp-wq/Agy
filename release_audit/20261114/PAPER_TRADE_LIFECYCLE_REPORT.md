# PAPER TRADE LIFECYCLE REPORT

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: PARTIALLY VERIFIED.

VERIFIED in backend suite: partial-fill aggregation, repeated client intent idempotency, protective exit persistence, partial/full close and reversal quantity/margin/P&L, rejected orders, minimum-notional/precision gates, recovered incomplete orders, persistence and ledger attribution. Relevant suites: test_paper_execution_and_recovery.py, test_paper_reversal_orders.py, test_pnl_tracker_and_ledger.py, test_all_cap_paper.py, test_violet_500_campaign.py, test_paper_campaign_and_persistence.py. Final backend result: 217 passed, two warnings, 84.60 seconds; evidence/backend-final-tests.xml.

VERIFIED snapshot: the active server campaign consists of five 100 USDT sleeves, total initial capital 500 USDT, separate from historical and simulator ledgers. At 14:06 it had no executions; later observation records one paper fill and one open position. These are different timestamps, not a discrepancy. No balance reset, position close or historical deletion was performed.

PARTIALLY VERIFIED: the new candidate implements and tests multi-level take-profit, trailing stops and a documented cross-margin liquidation simulation. See PAPER_PROTECTION_IMPLEMENTATION_REPORT.md. These are synthetic economic/recovery fixtures, not an observed full campaign or certified exchange liquidation behavior. Marketable limit fills now use taker fees. Genuine resting-maker execution, all-venue protective constraint handling, atomic derived-journal recovery and funding scheduling remain BLOCKED. Candidate native portfolio integration remains undeployed/unvalidated on a physical device.

NOT TESTED: a single integrated test spanning all market-data/signal/risk/execution/settlement/dashboard layers for every requested scenario.

Latest checkpoint: 250 backend tests, 21 Android tests and 42 frontend tests passed. Three reproducible synthetic entry-to-staged-exit cases are recorded in evidence/protection-trade-events.json. The candidate does not establish full release readiness.
