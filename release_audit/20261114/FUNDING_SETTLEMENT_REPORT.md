# FUNDING SETTLEMENT REPORT

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: PARTIALLY VERIFIED.

VERIFIED in candidate automated fixtures: linear USDT funding payment = -(signed base quantity × settlement mark × rate). Positive rates debit longs and credit shorts; negative rates reverse the sign; zero events are recorded. Explicit UTC boundaries and per-interval exposure history select the latest exposure strictly before settlement. Changes exactly at the boundary apply next interval. Each missed interval requires its own evidenced rate/mark and durable event identity. Existing settlement identity rejects changed source evidence. Repeat delivery and reopen/restart do not repeat cash impact.

Cash and realized P&L change once; no trade-position quantity, reserved margin or unrealized P&L is manufactured. Funding attribution and account mutation now share one SQLite transaction; forced attribution failure rolls back settlement and cash. Conflicting preexisting attribution also rolls back. This fixes the previous non-atomic split between cash and attribution commits.

Evidence: tests/test_paper_funding.py and evidence/backend-final-tests.xml. Values are synthetic paper test fixtures, never actual investment returns. Raw signed-amount posting remains available for explicitly evidenced legacy events.

BLOCKED: automated funding-history capture and scheduler integration into the active campaign are not implemented/validated by this candidate. The active campaign is spot-only. The new calculator supports linear USDT contracts only, uses a documented simulation boundary convention, and requires trustworthy historical exposure input; it does not independently prove supplied snapshots. Inverse contracts and exchange-specific boundary sequencing are NOT TESTED. No production funding schema was changed or settlement run against the operating account.

Protection checkpoint extends historical replay to include persisted funding settlements in cash/realized equations. This does not establish a funding scheduler or verified exposure-history capture; those integration gates remain BLOCKED.
