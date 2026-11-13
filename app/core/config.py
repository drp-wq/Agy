"""Configuration and secret-safe credential loading for OKX and Bybit (paper-only)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import dotenv_values
from pydantic import BaseModel, Field, field_validator

from app.core.logging_setup import register_sensitive_value
from app.core.security import (
    LIVE_EXECUTION_PERMANENTLY_DISABLED,
    assert_paper_only,
    validate_secrets_file_permissions,
)

BASE_DIR = Path("/root/multi-exchange-paper")
DEFAULT_SECRETS_PATH = Path("/root/trading-secrets/exchanges.env")
SUPPORTED_EXCHANGES: tuple[str, ...] = ("okx", "bybit", "binance")


class RiskPolicyConfig(BaseModel):
    """Conservative shared risk limits enforced across all bots and strategies."""

    max_portfolio_exposure_pct: float = Field(default=0.60, ge=0.05, le=1.0)
    max_per_exchange_exposure_pct: float = Field(default=0.40, ge=0.05, le=1.0)
    max_per_symbol_exposure_pct: float = Field(default=0.20, ge=0.02, le=0.50)
    max_concurrent_positions: int = Field(default=8, ge=1, le=50)
    daily_loss_limit_pct: float = Field(default=0.03, ge=0.005, le=0.20)
    max_drawdown_pct: float = Field(default=0.10, ge=0.01, le=0.50)
    max_leverage: float = Field(default=3.0, ge=1.0, le=10.0)
    min_liquidation_buffer_pct: float = Field(default=0.25, ge=0.05, le=0.90)
    max_correlated_exposure_pct: float = Field(default=0.35, ge=0.05, le=1.0)
    require_stop_loss: bool = True
    max_market_data_age_sec: float = Field(default=30.0, ge=2.0, le=300.0)
    duplicate_signal_window_sec: float = Field(default=120.0, ge=5.0, le=3600.0)
    api_outage_error_threshold: int = Field(default=5, ge=1, le=50)


class ExchangeCredentials:
    """In-memory credential container that never reveals secrets in repr/str/dict."""

    def __init__(
        self,
        exchange: str,
        api_key: str | None = None,
        api_secret: str | None = None,
        passphrase: str | None = None,
    ) -> None:
        self.exchange = exchange
        self._api_key = (api_key or "").strip()
        self._api_secret = (api_secret or "").strip()
        self._passphrase = (passphrase or "").strip()
        register_sensitive_value(self._api_key)
        register_sensitive_value(self._api_secret)
        register_sensitive_value(self._passphrase)

    @property
    def is_configured(self) -> bool:
        if self.exchange == "okx":
            return bool(self._api_key and self._api_secret and self._passphrase)
        return bool(self._api_key and self._api_secret)

    def as_ccxt_auth(self) -> dict[str, str]:
        """Return auth dict for CCXT read-only calls without logging."""
        if not self.is_configured:
            return {}
        cfg: dict[str, str] = {
            "apiKey": self._api_key,
            "secret": self._api_secret,
        }
        if self.exchange == "okx":
            cfg["password"] = self._passphrase
        return cfg

    def metadata(self) -> dict[str, Any]:
        """Return safe non-secret metadata only."""
        return {
            "exchange": self.exchange,
            "configured": self.is_configured,
            "has_api_key": bool(self._api_key),
            "has_api_secret": bool(self._api_secret),
            "has_passphrase": bool(self._passphrase) if self.exchange == "okx" else None,
        }

    def __repr__(self) -> str:
        return f"ExchangeCredentials(exchange={self.exchange!r}, configured={self.is_configured})"

    __str__ = __repr__


class EngineConfig(BaseModel):
    """Top-level validated engine configuration."""

    base_dir: Path = BASE_DIR
    secrets_path: Path = DEFAULT_SECRETS_PATH
    db_path: Path = BASE_DIR / "data" / "paper_engine.sqlite3"
    backup_dir: Path = BASE_DIR / "data" / "backups"
    log_dir: Path = BASE_DIR / "logs"
    exchanges: tuple[str, ...] = SUPPORTED_EXCHANGES
    mode: str = "paper"
    live_orders_enabled: bool = False
    initial_paper_equity_usd: float = Field(default=10000.0, ge=100.0)
    scanner_interval_sec: float = Field(default=15.0, ge=2.0)
    scanner_top_symbols: int = Field(default=12, ge=1, le=100)
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8788
    risk: RiskPolicyConfig = Field(default_factory=RiskPolicyConfig)

    @field_validator("exchanges")
    @classmethod
    def _validate_exchanges(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(x.lower().strip() for x in value)
        for ex in cleaned:
            if ex not in SUPPORTED_EXCHANGES:
                raise ValueError(f"Unsupported exchange '{ex}'. Allowed: {SUPPORTED_EXCHANGES}")
        return cleaned

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, value: str) -> str:
        assert_paper_only(value)
        return "paper"

    @field_validator("live_orders_enabled")
    @classmethod
    def _validate_live_disabled(cls, value: bool) -> bool:
        if value or not LIVE_EXECUTION_PERMANENTLY_DISABLED:
            raise ValueError("Live orders are structurally disabled and cannot be enabled.")
        return False


@dataclass
class LoadedEnvironment:
    config: EngineConfig
    credentials: dict[str, ExchangeCredentials] = field(default_factory=dict)
    secrets_audit: dict[str, Any] = field(default_factory=dict)


def load_environment(
    secrets_path: Path = DEFAULT_SECRETS_PATH,
    config: EngineConfig | None = None,
    require_secrets_file: bool = True,
) -> LoadedEnvironment:
    """Load validated config and read-only credentials without ever exposing secrets."""
    cfg = config or EngineConfig(secrets_path=secrets_path)
    assert_paper_only(cfg.mode)

    raw_env: dict[str, str | None] = {}
    audit: dict[str, Any] = {"permissions_ok": False, "path": str(secrets_path)}

    if secrets_path.exists():
        audit = validate_secrets_file_permissions(secrets_path)
        raw_env = dict(dotenv_values(secrets_path))
    elif require_secrets_file:
        raise FileNotFoundError(f"Required secrets file missing: {secrets_path}")

    creds = {
        "okx": ExchangeCredentials(
            exchange="okx",
            api_key=raw_env.get("OKX_API_KEY") or os.getenv("OKX_API_KEY"),
            api_secret=raw_env.get("OKX_API_SECRET") or os.getenv("OKX_API_SECRET"),
            passphrase=raw_env.get("OKX_API_PASSPHRASE") or os.getenv("OKX_API_PASSPHRASE"),
        ),
        "binance": ExchangeCredentials(
            exchange="binance",
            api_key=raw_env.get("BINANCE_API_KEY") or os.getenv("BINANCE_API_KEY"),
            api_secret=raw_env.get("BINANCE_API_SECRET") or os.getenv("BINANCE_API_SECRET"),
        ),
        "bybit": ExchangeCredentials(
            exchange="bybit",
            api_key=raw_env.get("BYBIT_API_KEY") or os.getenv("BYBIT_API_KEY"),
            api_secret=raw_env.get("BYBIT_API_SECRET") or os.getenv("BYBIT_API_SECRET"),
        ),
    }

    return LoadedEnvironment(config=cfg, credentials=creds, secrets_audit=audit)
