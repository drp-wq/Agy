"""Server-side Telegram Mini App initData HMAC-SHA256 verification and owner enforcement."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl

from fastapi import HTTPException, status


@dataclass(frozen=True)
class TelegramWebAppUser:
    user_id: str
    username: str | None
    first_name: str | None
    auth_date: int


def verify_telegram_init_data(
    init_data: str | None,
    bot_token: str,
    owner_chat_id: str,
    max_age_sec: int = 900,
    now_epoch: int | None = None,
) -> TelegramWebAppUser:
    """Verify Telegram WebApp initData signature, auth_date freshness, and owner ID."""
    if not init_data or not init_data.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram WebApp initData",
        )
    if not bot_token or not owner_chat_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram Mini App authentication is not configured",
        )

    pairs = parse_qsl(init_data.strip(), keep_blank_values=True)
    data_map: dict[str, str] = {}
    received_hash = ""
    for k, v in pairs:
        if k == "hash":
            received_hash = v
        else:
            data_map[k] = v

    if not received_hash or len(received_hash) != 64:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initData signature format",
        )

    data_check_string = "\n".join(f"{k}={data_map[k]}" for k in sorted(data_map.keys()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    expected_hash = hmac.new(
        secret_key, data_check_string.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected_hash.lower(), received_hash.lower()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram initData signature",
        )

    raw_auth_date = data_map.get("auth_date")
    try:
        auth_date = int(raw_auth_date or "")
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Telegram auth_date",
        ) from None

    now = int(time.time()) if now_epoch is None else int(now_epoch)
    if auth_date > now + 60:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram auth_date is in the future",
        )
    if (now - auth_date) > max_age_sec:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram initData expired",
        )

    raw_user = data_map.get("user")
    if not raw_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram user payload",
        )
    try:
        user_obj: dict[str, Any] = json.loads(raw_user)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed Telegram user payload",
        ) from None

    user_id = str(user_obj.get("id", "")).strip()
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Telegram user id",
        )

    if not hmac.compare_digest(user_id, str(owner_chat_id).strip()):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Telegram user not authorized",
        )

    return TelegramWebAppUser(
        user_id=user_id,
        username=user_obj.get("username"),
        first_name=user_obj.get("first_name"),
        auth_date=auth_date,
    )
