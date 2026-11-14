---
name: gate-public-market-data
description: Official Gate public spot market-data research only; runtime integration blocked pending validation.
---
Source inspected: https://www.gate.com/docs/developers/apiv4/en/
REST base: https://api.gateio.ws/api/v4
Public GET endpoint inventory: /spot/currency_pairs (metadata), /spot/tickers
(quotes), /spot/order_book (currency_pair required), /spot/candlesticks (history).
Validate current schemas, timestamps, units, pagination and rate limits against
that official documentation before implementing an adapter. No SDK installation.
Only HTTPS public GET requests to these paths are permitted by this local skill.
No credentials, auth headers, signed requests, account endpoints, orders,
transfers, margin, derivatives, external writes or remote code execution.
Bound request counts, concurrency and timeouts. Reject missing/stale/nonfinite
prices, source times, ambiguous asset identity and malformed responses. Book
receipt time alone is not proof of exchange source freshness. Redact error
payloads. Market cap must come from a separate verified circulating-cap source.
Runtime status: BLOCKED, no Gate adapter or Gate coverage claimed or fabricated.
