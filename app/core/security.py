"""Strict security boundaries enforcing structural paper-only execution and secret protection."""

from __future__ import annotations

import logging
import os
import re
import stat
from pathlib import Path
from typing import Any, Iterable

# Structural compile-time invariant: live execution cannot be enabled by config.
LIVE_EXECUTION_PERMANENTLY_DISABLED: bool = True

BLOCKED_EXCHANGE_METHODS: tuple[str, ...] = (
    "create_order",
    "create_orders",
    "create_limit_order",
    "create_limit_buy_order",
    "create_limit_sell_order",
    "create_market_order",
    "create_market_buy_order",
    "create_market_sell_order",
    "create_stop_order",
    "create_stop_limit_order",
    "create_stop_market_order",
    "create_trigger_order",
    "create_reduce_only_order",
    "create_post_only_order",
    "edit_order",
    "edit_limit_order",
    "cancel_order",
    "cancel_orders",
    "cancel_all_orders",
    "cancel_all_orders_after",
    "withdraw",
    "transfer",
    "set_leverage",
    "set_margin_mode",
    "set_position_mode",
    "add_margin",
    "reduce_margin",
    "borrow_cross_margin",
    "repay_cross_margin",
    "borrow_isolated_margin",
    "repay_isolated_margin",
)

_WRITE_METHOD_PATTERNS = (
    re.compile(r"^private_(post|put|delete|patch)", re.IGNORECASE),
    re.compile(r"^private(Post|Put|Delete|Patch)"),
    re.compile(r"^v\d+Private(Post|Put|Delete|Patch)"),
    re.compile(r"^(spot|linear|inverse|option|account|trade)Private(Post|Put|Delete|Patch)"),
)


class LiveExecutionBlockedError(RuntimeError):
    """Raised whenever any live order, withdrawal, or account write is attempted."""


class SecretPermissionError(PermissionError):
    """Raised when the secrets file or directory has unsafe filesystem permissions."""


def assert_paper_only(mode: str = "paper") -> None:
    """Verify structural paper-only invariant. Never allows live mode."""
    if not LIVE_EXECUTION_PERMANENTLY_DISABLED:
        raise LiveExecutionBlockedError("Structural live execution lock compromised.")
    if mode.lower() != "paper":
        raise LiveExecutionBlockedError(
            f"Requested mode '{mode}' rejected: live execution is structurally disabled."
        )


def _blocked_stub(method_name: str):
    def _raiser(*_args: Any, **_kwargs: Any) -> Any:
        raise LiveExecutionBlockedError(
            f"Blocked write/order method '{method_name}': live execution is structurally disabled."
        )

    return _raiser


def enforce_read_only_exchange(exchange_obj: Any) -> Any:
    """Structurally disable all order-submission and account-write methods on an exchange client."""
    for method_name in BLOCKED_EXCHANGE_METHODS:
        if hasattr(exchange_obj, method_name):
            setattr(exchange_obj, method_name, _blocked_stub(method_name))

    for attr_name in dir(exchange_obj):
        if any(pat.match(attr_name) for pat in _WRITE_METHOD_PATTERNS):
            setattr(exchange_obj, attr_name, _blocked_stub(attr_name))

    return exchange_obj


def validate_secrets_file_permissions(secrets_path: Path) -> dict[str, Any]:
    """Validate that secrets file and parent directory have strict permissions (0600/0700)."""
    if not secrets_path.exists():
        raise FileNotFoundError(f"Secrets file not found: {secrets_path}")

    parent = secrets_path.parent
    parent_mode = stat.S_IMODE(parent.stat().st_mode)
    file_stat = secrets_path.stat()
    file_mode = stat.S_IMODE(file_stat.st_mode)

    if parent_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SecretPermissionError(
            f"Unsafe permissions {oct(parent_mode)} on secrets dir {parent}; expected 0700."
        )
    if file_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SecretPermissionError(
            f"Unsafe permissions {oct(file_mode)} on secrets file {secrets_path}; expected 0600."
        )
    if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
        raise SecretPermissionError(
            f"Secrets file {secrets_path} owner uid {file_stat.st_uid} != current uid {os.geteuid()}."
        )

    return {
        "path": str(secrets_path),
        "dir_mode": oct(parent_mode),
        "file_mode": oct(file_mode),
        "permissions_ok": True,
    }


class SecretRedactionFilter(logging.Filter):
    """Logging filter that scrubs registered secrets and credential-like patterns."""

    _KEY_VALUE_PATTERN = re.compile(
        r"(?i)(api[_-]?key|api[_-]?secret|passphrase|secret|password|token|authorization)\s*[:=]\s*['\"]?([^\s,'\"&]+)['\"]?"
    )

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets: set[str] = {s for s in secrets if s and len(s) >= 4}

    def register_secret(self, value: str | None) -> None:
        if value and len(value) >= 4:
            self._secrets.add(value)

    def redact(self, text: str) -> str:
        result = text
        for secret in self._secrets:
            if secret in result:
                result = result.replace(secret, "[REDACTED]")
        result = self._KEY_VALUE_PATTERN.sub(r"\1=[REDACTED]", result)
        return result

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: self.redact(str(v)) if isinstance(v, str) else v
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, tuple):
                record.args = tuple(
                    self.redact(a) if isinstance(a, str) else a for a in record.args
                )
        return True
