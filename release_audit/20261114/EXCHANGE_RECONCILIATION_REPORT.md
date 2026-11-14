# EXCHANGE RECONCILIATION REPORT

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: PARTIALLY VERIFIED; complete account-history reconciliation is BLOCKED.

VERIFIED via authenticated, read-only requests: Binance, Bybit and OKX balances; Bybit/OKX positions. Native Binance futures V3 GET position-risk and balance calls succeed (zero open positions and 11 asset rows at probe time), despite the unified fetch_positions discovery path encountering the structural write guard. Candidate telemetry now uses native GET calls and exposes endpoint failure status rather than silently certifying empty positions.

VERIFIED permissions: Binance restrictions report reading enabled, trading/withdrawal/futures flags disabled. OKX account config reports `read_only,trade`. Bybit key query reports readOnly=0 and trading permissions. Permissions/credentials were not altered. Exchange-side write capability is independent of the application’s paper-only structural lock. Candidate async telemetry clients also receive the GET-only/write-method guard.

Transfer probe results and query scope: evidence/private-endpoints.json. Limited query success is not complete lifetime reconciliation. Native funding data, fee tiers, transfer pagination, all symbols, retained history limits, retries and network fault recovery require additional coverage. Gate.io is configured as a public research adapter and has no verified private account integration here.

Endpoint and permission evidence: evidence/exchange-private.json and private-endpoints.json; no credentials appear in these artifacts. NTP synchronization was true at discovery.

Binance primary API reference for transfer-history query semantics: https://binance.github.io/binance-connector-js/classes/_binance_wallet.WalletRestAPI.AssetApi.html#queryuseruniversaltransferhistory . Defaults cover seven days and retention is limited; this must not be reported as all-time completeness.

VERIFIED bounded Binance transfer-history reads: MAIN_UMFUTURE returned 2/2 records and UMFUTURE_MAIN returned 6/6 records in the endpoint default seven-day window. No transfers were executed. Full retained-history pagination and deposit/withdrawal reconciliation remain incomplete.

Public funding probe 2026-11-14T15:31:08Z: Binance and Bybit GET responses captured; OKX HTTP request failed. Binance native fundingTime includes millisecond offsets, and Bybit history lacks markPrice. These observations do not verify full settlement history or any additional private permissions. evidence/funding-public-sources.json.

Funding-worker public validation: three actual BTC/USDT native funding events with charge-associated mark prices were retrieved credential-free through the candidate Binance adapter (evidence/funding-worker-public-history.json). No campaign DB was opened. Full account funding/history reconciliation and Bybit/OKX settlement-mark integration remain BLOCKED; these are not exchange balance/trade proofs.
