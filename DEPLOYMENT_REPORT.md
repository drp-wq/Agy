# Deployment Report

- **Target Server**: `srv2115613` (Ubuntu 24.04 LTS, Python 3.12.3)
- **Workspace**: `/root/multi-exchange-paper/`
- **Virtual Environment**: `/root/multi-exchange-paper/.venv`
- **Systemd Service**: `/etc/systemd/system/multi-exchange-paper.service` (`active (running)`)
- **Database**: `/root/multi-exchange-paper/data/paper_engine.sqlite3` (SQLite WAL mode)
- **Logs**: `/root/multi-exchange-paper/logs/engine.log` (rotating JSON, secret-redacted)
- **Dashboard Bind**: `127.0.0.1:8788` (HTTP Basic Auth via `/root/trading-secrets/dashboard.env`)
- **Isolation**: Existing `/root/binance-live/` files and `binance-*.service` units were not modified or restarted.

## OKX & Bybit Authentication Status

- **Bybit**: Authenticated read-only balance & positions verified (`status: ok`, `asset_count: 18`).
- **OKX**: Outbound IPv4 (`187.52.118.25`) forced in `OKXAdapter` to match the OKX API key IP whitelist (avoiding IPv6 `2a02:4780:5e:693c::1`). Authenticated balance (`8` assets) and positions (`0`) verified (`status: ok`). Note: OKX account config reports key `perm: "read_only,trade"` on OKX's side; local `enforce_read_only_exchange()` structurally blocks all order/write endpoints (`LiveExecutionBlockedError`).
