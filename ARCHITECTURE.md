# Architecture — Multi-Exchange AI Paper Trading Engine (OKX & Bybit)

## Overview

The platform is an isolated, institutional-style, **PAPER-ONLY** multi-exchange quantitative trading engine deployed in `/root/multi-exchange-paper/` on `srv2115613` (Ubuntu 24.04 LTS, Python 3.12.3).

It supports **OKX** (Spot & USDT-margined Perpetual Futures) and **Bybit** (Spot & Linear Perpetual Futures) concurrently, while keeping `/root/binance-live/` and its running services completely untouched.

## Directory Layout

- `app/core/`: Configuration (`config.py`), structural security boundaries (`security.py`), structured JSON logging with secret redaction (`logging_setup.py`), and Pydantic domain models (`models.py`).
- `app/exchanges/`: Read-only exchange adapters (`base.py`, `okx.py`, `bybit.py`, `registry.py`) with separate public and authenticated clients, rate limiting, retry backoff, and per-exchange circuit-breaker isolation.
- `app/scanner/`: Technical indicators (`indicators.py`) and concurrent multi-exchange, multi-timeframe (`1m`, `5m`, `15m`, `1h`, `4h`, `1d`) market scanner (`market_scanner.py`).
- `app/strategies/`: Modular strategy base & empirical confidence calibrator (`base.py`), 10 quantitative strategies (`catalog.py`), and AI strategy orchestrator with walk-forward out-of-sample evaluation (`orchestrator.py`).
- `app/risk/`: Shared `GlobalRiskGuardian` (`guardian.py`) enforcing portfolio, exchange, symbol, correlation, drawdown, leverage, stale-data, outage, and emergency-stop controls.
- `app/execution/`: Deterministic `PaperExecutionEngine` (`paper_engine.py`) with partial fills, exchange-specific fee/slippage models, idempotency, and crash recovery; concurrent `MultiBotManager` (`bot_manager.py`).
- `app/persistence/`: SQLite WAL transactional ledger (`database.py`) with schema migrations, audit logs, and online backups.
- `app/dashboard/`: Authenticated FastAPI server (`server.py`, `auth.py`) and responsive neon/cyberpunk web UI (`templates/index.html`).
- `app/service.py`: Unified `TradingEngineService` coordinating all subsystems.
- `scripts/`: Operational entrypoint (`run_platform.py`) and SQLite backup script (`backup_db.sh`).
