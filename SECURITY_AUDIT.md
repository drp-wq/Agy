# Security Audit

## Structural Live-Order Prevention

1. `LIVE_EXECUTION_PERMANENTLY_DISABLED = True` in `app/core/security.py`.
2. `EngineConfig` validators reject `mode != "paper"` (`LiveExecutionBlockedError`) and `live_orders_enabled=True` (`ValueError`).
3. `enforce_read_only_exchange()` replaces all CCXT order, withdrawal, transfer, leverage, and margin-mode methods (`BLOCKED_EXCHANGE_METHODS`) as well as dynamic `private_post*`, `privatePost*`, `v5PrivatePost*`, etc. methods with stubs that unconditionally raise `LiveExecutionBlockedError`.
4. No live execution adapter exists in `app/execution/`; only `PaperExecutionEngine` is implemented, and every `paper_orders` / `paper_fills` / `paper_positions` row enforces `is_paper = 1` (`CHECK (is_paper = 1)` in SQLite).

## Credential & Secret Protection

- Credentials are read only from `/root/trading-secrets/exchanges.env` (`0600`, directory `0700`, verified by `validate_secrets_file_permissions()`).
- `ExchangeCredentials` never exposes secrets in `__repr__`, `__str__`, or `metadata()`.
- `SecretRedactionFilter` scrubs all registered secrets and key/secret/passphrase patterns from all log output.
- Dashboard credentials are stored in `/root/trading-secrets/dashboard.env` (`0600`) and verified with `secrets.compare_digest()`.

## Web Dashboard Security

- Bound to `127.0.0.1:8788` (not exposed on `0.0.0.0`).
- HTTP Basic Authentication required on `/`, `/api/state`, `/api/candles/...`, `/api/bots/...`, `/api/risk/...`, and `/api/scan-now`.
- Security headers applied on all responses: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store`, and strict `Content-Security-Policy`.
- Swagger/OpenAPI docs disabled (`docs_url=None`, `redoc_url=None`, `openapi_url=None`).
