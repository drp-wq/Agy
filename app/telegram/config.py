"""Secret-safe Telegram configuration loader."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.config import BASE_DIR
from app.core.logging_setup import register_sensitive_value
from app.core.security import validate_secrets_file_permissions

DEFAULT_TELEGRAM_ENV = Path(
    os.getenv("TELEGRAM_ENV_PATH", str(BASE_DIR / "config" / "telegram.env"))
)
DEFAULT_STATE_PATH = BASE_DIR / "data" / "telegram_state.json"


@dataclass
class TelegramConfig:
    """Validated Telegram bot configuration with automatic secret redaction."""

    bot_token: str
    chat_id: str
    dashboard_url: str = "https://trade.187-52-118-25.sslip.io"
    miniapp_url: str = "https://trade.187-52-118-25.sslip.io/miniapp"
    state_path: Path = DEFAULT_STATE_PATH
    rate_limit_per_min: int = 20
    dedup_window_sec: float = 120.0
    opportunity_cooldown_sec: float = 300.0
    init_data_max_age_sec: int = 900
    enabled: bool = True

    def __post_init__(self) -> None:
        self.bot_token = self.bot_token.strip()
        self.chat_id = str(self.chat_id).strip()
        register_sensitive_value(self.bot_token)

    def __repr__(self) -> str:
        return (
            f"TelegramConfig(chat_id={self.chat_id!r}, "
            f"configured={bool(self.bot_token and self.chat_id)}, enabled={self.enabled})"
        )

    __str__ = __repr__


def load_telegram_config(
    env_path: Path = DEFAULT_TELEGRAM_ENV,
    state_path: Path = DEFAULT_STATE_PATH,
    require_file: bool = False,
) -> TelegramConfig | None:
    """Load Telegram credentials from env vars or 0600 env file without exposing secrets."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()

    if not (token and chat_id):
        if not env_path.exists():
            if require_file:
                raise FileNotFoundError(f"Telegram config not found: {env_path}")
            return None
        validate_secrets_file_permissions(env_path)
        parsed: dict[str, str] = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            k, v = stripped.split("=", 1)
            parsed[k.strip()] = v.strip().strip("'\"")
        token = parsed.get("TELEGRAM_BOT_TOKEN", "").strip()
        chat_id = parsed.get("TELEGRAM_CHAT_ID", "").strip()

    if not token or not chat_id:
        if require_file:
            raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
        return None

    return TelegramConfig(
        bot_token=token,
        chat_id=chat_id,
        dashboard_url=os.getenv("TELEGRAM_DASHBOARD_URL", "https://trade.187-52-118-25.sslip.io"),
        miniapp_url=os.getenv("TELEGRAM_MINIAPP_URL", "https://trade.187-52-118-25.sslip.io/miniapp"),
        state_path=state_path,
    )
