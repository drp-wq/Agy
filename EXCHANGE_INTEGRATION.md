# Exchange Integration — OKX & Bybit

## Supported Exchanges & Markets

1. **OKX (`app/exchanges/okx.py`)**
   - Spot markets (`BTC/USDT`, `ETH/USDT`, etc.)
   - USDT-margined Perpetual Futures (`BTC/USDT:USDT`, etc.)
   - API key + secret + passphrase authentication for read-only balances/positions
   - Funding rates, open interest, order book depth, and instrument specifications

2. **Bybit (`app/exchanges/bybit.py`)**
   - Spot markets (`BTC/USDT`, `ETH/USDT`, etc.)
   - Linear Perpetual Futures (`BTC/USDT:USDT`, etc.)
   - API key + secret authentication for read-only balances/positions
   - Funding rates, open interest, order book depth, and instrument specifications

## Isolation & Resilience (`app/exchanges/base.py`)

- **Public vs. Authenticated Client Split**: Public market data (`load_markets`, `fetch_ticker`, `fetch_order_book`, `fetch_ohlcv`, `fetch_funding_rate`, `fetch_open_interest`) uses an unauthenticated `_public_client`, while read-only account queries (`fetch_balance`, `fetch_positions`) use `_auth_client`. An API key IP-whitelist restriction or auth error never interrupts public market scanning or paper execution.
- **Rate Limits & Retries**: CCXT `enableRateLimit: True`, 15,000 ms timeout, and exponential backoff (`0.35s * 2^attempt`) on `NetworkError`, `RequestTimeout`, and `RateLimitExceeded`.
- **Circuit-Breaker Outage Isolation**: After 5 consecutive errors, an exchange enters a 30s cooldown (`ExchangeOutageError`) while the other exchange continues operating normally.
- **Structural Read-Only Enforcement**: Both `_public_client` and `_auth_client` are passed through `enforce_read_only_exchange()` on creation.
