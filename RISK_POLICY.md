# Risk Policy (`app/risk/guardian.py`)

All paper bots and strategies are gated by `GlobalRiskGuardian`. No strategy can bypass it.

## Conservative Default Limits (`RiskPolicyConfig`)

- **Maximum Portfolio Exposure**: `60%` of total paper equity (`max_portfolio_exposure_pct = 0.60`)
- **Maximum Per-Exchange Exposure**: `40%` of total paper equity (`max_per_exchange_exposure_pct = 0.40`)
- **Maximum Per-Symbol Exposure**: `20%` of total paper equity (`max_per_symbol_exposure_pct = 0.20`)
- **Maximum Correlated Bucket Exposure**: `35%` (`max_correlated_exposure_pct = 0.35`, grouping `BTC`, `ETH`, `SOL`, `AVAX`, `LINK` as `major_crypto_beta`)
- **Maximum Concurrent Positions**: `8` (`max_concurrent_positions = 8`)
- **Daily Loss Limit**: `3%` (`daily_loss_limit_pct = 0.03`) — trips circuit breaker
- **Maximum Drawdown**: `10%` (`max_drawdown_pct = 0.10`) — trips circuit breaker
- **Leverage Cap**: `3.0x` (`max_leverage = 3.0`)
- **Minimum Liquidation Buffer**: `25%` (`min_liquidation_buffer_pct = 0.25`)
- **Stop-Loss Required**: `True` (`require_stop_loss = True`)
- **Stale Market-Data Rejection**: `> 30.0s` (`max_market_data_age_sec = 30.0`)
- **Duplicate Signal Suppression**: `120.0s` window per `(exchange, market_type, symbol, direction, strategy)`
- **API Outage Protection**: Blocks orders when exchange adapter health is degraded/circuit-open
- **Emergency Stop**: Immediate global halt via `trigger_emergency_stop()` or `POST /api/risk/emergency-stop`
