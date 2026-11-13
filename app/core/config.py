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

BASE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SECRETS_PATH = Path("/root/trading-secrets/exchanges.env")
SUPPORTED_EXCHANGES: tuple[str, ...] = ("okx", "bybit", "binance")


class RiskPolicyConfig(BaseModel):
    """Conservative shared risk limits enforced across all bots and strategies."""

    max_portfolio_exposure_pct: float = Field(default=0.60, ge=0.05, le=1.0)
    max_per_exchange_exposure_pct: float = Field(default=0.40, ge=0.05, le=1.0)
    max_per_symbol_exposure_pct: float = Field(default=0.20, ge=0.02, le=0.50)
    max_concurrent_positions: int = Field(default=18, ge=1, le=50)
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


class MidLowCapConfig(BaseModel):
    """Feature-flagged configuration for adaptive mid/low-cap spot paper trading."""

    enabled: bool = False
    allow_short: bool = False
    max_leverage: float = Field(default=1.0, ge=1.0, le=1.0)
    low_cap_min_usd: float = Field(default=50_000_000.0, ge=1_000_000.0)
    low_cap_max_usd: float = Field(default=300_000_000.0, ge=10_000_000.0)
    mid_cap_min_usd: float = Field(default=300_000_000.0, ge=10_000_000.0)
    mid_cap_max_usd: float = Field(default=10_000_000_000.0, ge=100_000_000.0)
    market_cap_provider: str = "coingecko"
    market_cap_cache_ttl_sec: float = Field(default=1800.0, ge=30.0)
    market_cap_max_age_sec: float = Field(default=3600.0, ge=60.0)
    market_cap_max_pages: int = Field(default=4, ge=1, le=10)
    market_cap_min_request_interval_sec: float = Field(default=2.0, ge=0.0)
    discovery_cache_ttl_sec: float = Field(default=900.0, ge=30.0)
    max_discovered_symbols_per_exchange: int = Field(default=40, ge=1, le=200)
    scan_batch_size: int = Field(default=12, ge=1, le=50)
    min_24h_volume_usd: float = Field(default=1_000_000.0, ge=10_000.0)
    max_spread_bps: float = Field(default=35.0, ge=1.0, le=500.0)
    min_order_book_depth_usd: float = Field(default=25_000.0, ge=1_000.0)
    min_history_candles: int = Field(default=60, ge=20, le=500)
    max_market_data_age_sec: float = Field(default=30.0, ge=2.0, le=300.0)
    max_turnover_ratio: float = Field(default=3.0, ge=0.1, le=20.0)
    max_single_candle_volume_spike: float = Field(default=12.0, ge=2.0, le=100.0)
    max_notional_to_depth_ratio: float = Field(default=0.05, ge=0.005, le=0.5)
    max_positions_per_base: int = Field(default=1, ge=1, le=3)
    allowed_strategies: tuple[str, ...] = (
        "trend_following",
        "breakout_retest",
        "mean_reversion",
    )
    min_risk_reward_ratio: float = Field(default=2.0, ge=1.5, le=10.0)
    max_risk_per_trade_pct: float = Field(default=0.0025, ge=0.0005, le=0.0025)
    daily_loss_limit_pct: float = Field(default=0.01, ge=0.001, le=0.05)
    max_simultaneous_positions: int = Field(default=3, ge=1, le=10)
    min_oos_trades: int = Field(default=3, ge=1, le=50)
    min_oos_net_expectancy_r: float = Field(default=0.0, ge=-1.0, le=5.0)
    allocated_capital_usd: float = Field(default=2500.0, ge=100.0)


class UnifiedTreasuryConfig(BaseModel):
    """Feature-flagged configuration for unified 18-agent paper treasury and attribution."""

    enabled: bool = False
    max_agent_slots: int = Field(default=18, ge=1, le=18)
    master_pool_currency: str = "USDT"
    initial_master_capital: float = Field(default=10000.0, ge=0.0)
    default_usdt_usd_rate: float = Field(default=0.9995, gt=0.5, lt=1.5)
    max_single_agent_concentration_pct: float = Field(default=0.25, ge=0.01, le=1.0)
    max_aggregate_allocated_pct: float = Field(default=0.85, ge=0.05, le=1.0)
    rebalance_cooldown_sec: float = Field(default=300.0, ge=0.0)
    agent_daily_loss_cap_pct: float = Field(default=0.02, ge=0.001, le=0.50)
    require_ownership_verification: bool = True
    paper_only: bool = True

    @field_validator("paper_only")
    @classmethod
    def _validate_paper_only(cls, value: bool) -> bool:
        if not value or not LIVE_EXECUTION_PERMANENTLY_DISABLED:
            raise ValueError("Unified treasury is structurally paper-only.")
        return True


class WatchOnlyWalletsConfig(BaseModel):
    """Read-only external watch-only wallet monitoring configuration."""

    registry_path: Path = BASE_DIR / "config" / "watch_only_wallets.json"
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    rpc_timeout_sec: float = Field(default=8.0, ge=1.0, le=30.0)
    rpc_max_retries: int = Field(default=2, ge=0, le=5)
    rpc_min_interval_sec: float = Field(default=1.5, ge=0.0, le=60.0)
    cache_ttl_sec: float = Field(default=60.0, ge=5.0, le=3600.0)
    signing_enabled: bool = False
    transfers_enabled: bool = False
    external_writes: bool = False

    @field_validator("signing_enabled", "transfers_enabled", "external_writes")
    @classmethod
    def _validate_readonly_flags(cls, value: bool) -> bool:
        if value:
            raise ValueError("Watch-only wallet signing, transfers, and external writes are permanently disabled.")
        return False


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
    mid_low_cap: MidLowCapConfig = Field(default_factory=MidLowCapConfig)
    unified_treasury: UnifiedTreasuryConfig = Field(default_factory=UnifiedTreasuryConfig)
    watch_only_wallets: WatchOnlyWalletsConfig = Field(default_factory=WatchOnlyWalletsConfig)

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
    if config is not None:
        cfg = config
    else:
        mlc_enabled = os.getenv("MID_LOW_CAP_AUTOTRADE_ENABLED", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        treasury_enabled = os.getenv("UNIFIED_TREASURY_ENABLED", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        dash_host = os.getenv("DASHBOARD_HOST", "127.0.0.1").strip() or "127.0.0.1"
        dash_port = int(os.getenv("DASHBOARD_PORT", "8788").strip() or "8788")
        cfg = EngineConfig(
            secrets_path=secrets_path,
            dashboard_host=dash_host,
            dashboard_port=dash_port,
            mid_low_cap=MidLowCapConfig(enabled=mlc_enabled),
            unified_treasury=UnifiedTreasuryConfig(enabled=treasury_enabled),
        )
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
