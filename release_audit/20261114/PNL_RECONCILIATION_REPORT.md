# PNL RECONCILIATION REPORT

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: VERIFIED for the specified historical discrepancy and the 14:06 snapshot; PARTIALLY VERIFIED for complete platform economics.

The pre-repair database independently reproduces 18 missing fills and exits. Gross missing P&L = -76.729666 USDT; missing exit fees = 5.484766 USDT; net gap = -82.214432 USDT. This is missing attribution/evidence of losses already present in account balances, not a new economic loss to debit again.

Current snapshot: 158 filled orders, 161 fill records, 58 closures. Realized account P&L and attributed P&L both -98.198322 USDT; unexplained difference = 0 at the audit’s eight-decimal comparison precision. Replay agrees with recorded cash, realized P&L, reserved margin, and remaining position keys/direction/quantity/entry price. All 3,516 pre-repair order source records remain present and byte-for-field equal in the current snapshot.

An audit defect incorrectly treated multiple fills for one order as duplicates. Order ord_6d4d120d754d has four valid execution slices. Candidate validation now checks aggregate quantity, weighted price, fees and identity at 1e-6 tolerance, while still rejecting duplicated economics.

| Exit event ID | Bot | Quantity | Entry | Exit | Gross P&L | Exit fee | Net missing attribution |
|---|---|---:|---:|---:|---:|---:|---:|
| ord_f2e525825c91 | bybit_perp_funding | 0.062003 | 1935.065506 | 1955.31 | -1.255219 | 0.066679 | -1.321898 |
| ord_708c31e32e98 | bybit_spot_swing | 0.096814 | 1936.204547 | 1956.43 | -1.958107 | 0.18941 | -2.147517 |
| ord_a4a6c7555a94 | okx_perp_momentum | 0.07752 | 1934.676513 | 1954.82 | -1.561523 | 0.075769 | -1.637292 |
| ord_2ad74c66060d | okx_spot_alpha | 0.116188 | 1936.034634 | 1956.0 | -2.319736 | 0.227264 | -2.547000 |
| ord_ef0354de6895 | bybit_spot_swing | 1638.028457 | 1.028914 | 1.0197 | -15.092794 | 1.670298 | -16.763092 |
| ord_7b30f1b03762 | bybit_perp_funding | 199.761114 | 8.40105 | 8.512 | -22.163496 | 0.935202 | -23.098698 |
| ord_ef23352487be | bybit_spot_swing | 0.00546 | 68667.760833 | 69572.3 | -4.938784 | 0.379865 | -5.318649 |
| ord_a59b6d1d8170 | okx_perp_momentum | 1.863818 | 80.411958 | 81.33 | -1.711063 | 0.075792 | -1.786855 |
| ord_3a8374e40ac0 | okx_spot_alpha | 0.006551 | 68681.175398 | 69634.7 | -6.24654 | 0.456177 | -6.702717 |
| ord_e179a080ab6f | okx_perp_momentum | 0.004371 | 68641.281285 | 69608.8 | -4.229024 | 0.15213 | -4.381154 |
| ord_8419ac577f70 | bybit_perp_funding | 0.003496 | 68626.269527 | 69644.4 | -3.559384 | 0.133912 | -3.693296 |
| ord_ff12dff30ad1 | okx_spot_alpha | 3.468137 | 80.47555 | 81.36 | -3.067394 | 0.282168 | -3.349562 |
| ord_f06706adc127 | bybit_spot_swing | 182.32139 | 1.019597 | 1.0324 | -2.334261 | 0.188229 | -2.522490 |
| ord_1cec3606d4fa | binance_spot_paper | 1.538953 | 81.21187 | 81.9 | -1.059 | 0.12604 | -1.18504 |
| ord_404b07d04096 | okx_perp_momentum | 1.829585 | 81.711828 | 82.32 | -1.112702 | 0.075306 | -1.188008 |
| ord_53529debd43e | okx_spot_alpha | 2.738519 | 81.781772 | 82.36 | -1.583488 | 0.225544 | -1.809032 |
| ord_eea8c5619401 | binance_perp_paper | 0.919095 | 81.59184 | 82.34 | -0.68763 | 0.037839 | -0.725469 |
| ord_4ea4384c6ef6 | bybit_spot_swing | 179.495442 | 1.032296 | 1.0426 | -1.849521 | 0.187142 | -2.036663 |

Reproduce from the maintenance repository: `PYTHONPATH=. /root/multi-exchange-paper/.venv/bin/python /root/violet-release-audit/reconcile_evidence.py`. Full calculations, file checksums, per-bot replay and source comparisons: evidence/pnl-event-reconstruction.json, historical-pre-repair.json and historical-current.json. No production records were edited. Replay uses the original engine’s historical rounding/accounting model; agreement does not independently certify exchange-level spot custody semantics, unrecorded external events, or lifetime funding completeness.
