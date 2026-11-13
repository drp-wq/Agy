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

ALLOWED_HTTP_METHODS: frozenset[str] = frozenset({"GET"})

# Explicit allowlist of read-only CCXT exchange methods permitted by the platform.
ALLOWED_READ_ONLY_EXCHANGE_METHODS: frozenset[str] = frozenset(
    {
        "load_markets",
        "loadMarkets",
        "fetch_markets",
        "fetchMarkets",
        "fetch_currencies",
        "fetchCurrencies",
        "fetch_ticker",
        "fetchTicker",
        "fetch_tickers",
        "fetchTickers",
        "fetch_order_book",
        "fetchOrderBook",
        "fetch_ohlcv",
        "fetchOHLCV",
        "fetch_trades",
        "fetchTrades",
        "fetch_funding_rate",
        "fetchFundingRate",
        "fetch_open_interest",
        "fetchOpenInterest",
        "fetch_balance",
        "fetchBalance",
        "fetch_positions",
        "fetchPositions",
        "fetch_open_orders",
        "fetchOpenOrders",
        "fetch_orders",
        "fetchOrders",
        "fetch_my_trades",
        "fetchMyTrades",
        "fetch_closed_orders",
        "fetchClosedOrders",
        "fetch_canceled_orders",
        "fetchCanceledOrders",
        "fetch_status",
        "fetchStatus",
        "fetch_time",
        "fetchTime",
        "private_get_openorders",
        "fapiprivate_get_openorders",
        "close",
    }
)

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

_SAFE_GET_ENDPOINT_PATTERNS = (
    re.compile(r"^[a-z0-9_]+_get_[a-z0-9_]+$"),
    re.compile(r"^[a-z0-9]+Get(?:[A-Z0-9][A-Za-z0-9]*)?$"),
)

_WRITE_ACTION_PATTERN = re.compile(
    r"^(?:"
    r"create(?:_|[A-Z])|"
    r"cancel(?:_|[A-Z])|"
    r"edit(?:_|[A-Z])|"
    r"amend(?:_|[A-Z])|"
    r"place(?:_|[A-Z])|"
    r"submit(?:_|[A-Z])|"
    r"withdraw(?:$|_|[A-Z])|"
    r"transfer(?:$|_|[A-Z])|"
    r"borrow(?:_|[A-Z])|"
    r"repay(?:_|[A-Z])|"
    r"close(?:_position|_all|Position|All)|"
    r"add(?:_margin|Margin)|"
    r"reduce(?:_margin|Margin)|"
    r"set(?:_leverage|_margin|_position|Leverage|Margin|Position)"
    r")"
)

_IMPLICIT_WRITE_ENDPOINT_PATTERN = re.compile(
    r"(?:^|_)(?:private|sapi|fapi|dapi|eapi|papi|wapi|v\d+private|spot|linear|inverse|option|account|trade|asset|margin|wallet)"
    r".*(?:_?(?:post|put|delete|patch)(?:_|$|[A-Z0-9])|(?:Post|Put|Delete|Patch)(?:[A-Z0-9]|$))",
    re.IGNORECASE,
)

# Non-network CCXT local data constructors that legitimately start with create_
_SAFE_LOCAL_CONSTRUCTORS: frozenset[str] = frozenset(
    {
        "create_ohlcv_object",
        "createOhlcvObject",
        "create_safe_dictionary",
        "createSafeDictionary",
        "create_ccxt_trade_id",
        "createCcxtTradeId",
        "create_networks_by_id_object",
        "createNetworksByIdObject",
    }
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


def _is_safe_get_endpoint(attr_name: str) -> bool:
    if _IMPLICIT_WRITE_ENDPOINT_PATTERN.search(attr_name):
        return False
    return any(pat.match(attr_name) for pat in _SAFE_GET_ENDPOINT_PATTERNS)


def _should_block_exchange_method(attr_name: str) -> bool:
    if attr_name in ALLOWED_READ_ONLY_EXCHANGE_METHODS or attr_name in _SAFE_LOCAL_CONSTRUCTORS:
        return False
    if attr_name in BLOCKED_EXCHANGE_METHODS:
        return True
    if _WRITE_ACTION_PATTERN.match(attr_name):
        return True
    if _IMPLICIT_WRITE_ENDPOINT_PATTERN.search(attr_name):
        return True
    if "private" in attr_name.lower() and not _is_safe_get_endpoint(attr_name):
        return True
    return False


def _wrap_transport_get_only(exchange_obj: Any) -> None:
    """Wrap CCXT transport methods so only HTTP GET requests can ever leave the process."""

    def _guard_request_like(orig_fn: Any, method_arg_idx: int):
        def _guarded(*args: Any, **kwargs: Any) -> Any:
            http_method = kwargs.get("method")
            if http_method is None and len(args) > method_arg_idx:
                http_method = args[method_arg_idx]
            resolved = str(http_method or "GET").strip().upper()
            if resolved not in ALLOWED_HTTP_METHODS:
                raise LiveExecutionBlockedError(
                    f"Blocked non-GET transport method '{resolved}': live execution is structurally disabled."
                )
            return orig_fn(*args, **kwargs)

        _guarded.__qualname__ = "_read_only_transport_guard"
        return _guarded

    for fn_name, idx in (("request", 2), ("fetch2", 2), ("fetch", 1)):
        orig = getattr(exchange_obj, fn_name, None)
        if callable(orig) and getattr(orig, "__qualname__", "") != "_read_only_transport_guard":
            setattr(exchange_obj, fn_name, _guard_request_like(orig, idx))


def enforce_read_only_exchange(exchange_obj: Any) -> Any:
    """Structurally disable all order-submission, account-write, and non-GET transport paths."""
    for method_name in BLOCKED_EXCHANGE_METHODS:
        if hasattr(exchange_obj, method_name):
            setattr(exchange_obj, method_name, _blocked_stub(method_name))

    for attr_name in dir(exchange_obj):
        if attr_name.startswith("__"):
            continue
        if not _should_block_exchange_method(attr_name):
            continue
        try:
            attr_val = getattr(exchange_obj, attr_name)
        except Exception:
            continue
        if callable(attr_val):
            setattr(exchange_obj, attr_name, _blocked_stub(attr_name))

    _wrap_transport_get_only(exchange_obj)
    try:
        setattr(exchange_obj, "_read_only_enforced", True)
    except Exception:
        pass
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
