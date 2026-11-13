"""Async Telegram Bot API client with retry/backoff, 429 handling, and token redaction."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.core.security import SecretRedactionFilter
from app.telegram.config import TelegramConfig

logger = logging.getLogger("multi_exchange.telegram.client")


class TelegramAPIError(RuntimeError):
    """Raised when a Telegram Bot API call fails after retries."""


class TelegramClient:
    """Minimal, secret-safe async Telegram Bot API client."""

    BASE_URL = "https://api.telegram.org"

    def __init__(
        self,
        config: TelegramConfig,
        transport: httpx.AsyncBaseTransport | None = None,
        max_retries: int = 3,
        base_backoff_sec: float = 0.6,
    ) -> None:
        self.config = config
        self.max_retries = max_retries
        self.base_backoff_sec = base_backoff_sec
        self._redactor = SecretRedactionFilter()
        self._redactor.register_secret(config.bot_token)
        self._client = httpx.AsyncClient(
            base_url=f"{self.BASE_URL}/bot{config.bot_token}",
            timeout=httpx.Timeout(35.0, connect=10.0),
            transport=transport,
        )

    def _safe_err(self, text: str) -> str:
        return self._redactor.redact(text)[:240]

    async def _post(self, method: str, payload: dict[str, Any]) -> Any:
        last_error = "unknown_error"
        for attempt in range(self.max_retries):
            try:
                resp = await self._client.post(f"/{method}", json=payload)
                if resp.status_code == 429:
                    retry_after = 1.5
                    try:
                        body = resp.json()
                        retry_after = float(body.get("parameters", {}).get("retry_after", 1.5))
                    except Exception:
                        pass
                    last_error = f"rate_limited_429(retry_after={retry_after})"
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(min(retry_after, 10.0))
                        continue
                    raise TelegramAPIError(last_error)

                if resp.status_code >= 500:
                    last_error = f"http_{resp.status_code}"
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.base_backoff_sec * (2**attempt))
                        continue
                    raise TelegramAPIError(last_error)

                data = resp.json()
                if not isinstance(data, dict) or not data.get("ok"):
                    desc = self._safe_err(str(data.get("description", f"http_{resp.status_code}")))
                    raise TelegramAPIError(f"telegram_rejected:{desc}")
                return data.get("result")

            except TelegramAPIError:
                raise
            except (httpx.HTTPError, ValueError) as exc:
                last_error = self._safe_err(f"{type(exc).__name__}: {exc}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.base_backoff_sec * (2**attempt))
                    continue
                raise TelegramAPIError(f"Telegram {method} failed: {last_error}") from None

        raise TelegramAPIError(f"Telegram {method} failed: {last_error}")

    async def send_message(
        self,
        text: str,
        chat_id: str | None = None,
        parse_mode: str = "HTML",
        reply_markup: dict[str, Any] | None = None,
        disable_web_page_preview: bool = True,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id or self.config.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": disable_web_page_preview,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = await self._post("sendMessage", payload)
        return result if isinstance(result, dict) else {}

    async def get_updates(self, offset: int = 0, timeout: int = 25) -> list[dict[str, Any]]:
        payload = {
            "offset": offset,
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        result = await self._post("getUpdates", payload)
        return result if isinstance(result, list) else []

    async def set_my_commands(self, commands: list[dict[str, str]]) -> bool:
        result = await self._post("setMyCommands", {"commands": commands})
        return bool(result)

    async def set_chat_menu_button(
        self,
        text: str = "Terminal",
        miniapp_url: str | None = None,
        chat_id: str | None = None,
    ) -> bool:
        url = miniapp_url or self.config.miniapp_url
        payload: dict[str, Any] = {
            "chat_id": chat_id or self.config.chat_id,
            "menu_button": {
                "type": "web_app",
                "text": text,
                "web_app": {"url": url},
            },
        }
        result = await self._post("setChatMenuButton", payload)
        return bool(result)

    async def close(self) -> None:
        await self._client.aclose()
