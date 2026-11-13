"""Security, secret-redaction, permission, and mandatory live-order block tests."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from app.core.config import EngineConfig, ExchangeCredentials, load_environment
from app.core.security import (
    BLOCKED_EXCHANGE_METHODS,
    LIVE_EXECUTION_PERMANENTLY_DISABLED,
    LiveExecutionBlockedError,
    SecretPermissionError,
    SecretRedactionFilter,
    assert_paper_only,
    validate_secrets_file_permissions,
)
from app.exchanges.bybit import BybitAdapter
from app.exchanges.okx import OKXAdapter


class _DummyCCXTClient:
    def create_order(self, *a, **kw):
        return {"id": "SHOULD_NEVER_EXECUTE"}

    def create_limit_order(self, *a, **kw):
        return {"id": "SHOULD_NEVER_EXECUTE"}

    def create_market_order(self, *a, **kw):
        return {"id": "SHOULD_NEVER_EXECUTE"}

    def cancel_order(self, *a, **kw):
        return {"id": "SHOULD_NEVER_EXECUTE"}

    def withdraw(self, *a, **kw):
        return {"id": "SHOULD_NEVER_EXECUTE"}

    def transfer(self, *a, **kw):
        return {"id": "SHOULD_NEVER_EXECUTE"}

    def private_post_trade_order(self, *a, **kw):
        return {"id": "SHOULD_NEVER_EXECUTE"}

    def v5PrivatePostOrderCreate(self, *a, **kw):
        return {"id": "SHOULD_NEVER_EXECUTE"}


def test_live_execution_permanently_disabled():
    assert LIVE_EXECUTION_PERMANENTLY_DISABLED is True
    with pytest.raises(LiveExecutionBlockedError):
        assert_paper_only("live")
    with pytest.raises(LiveExecutionBlockedError):
        EngineConfig(mode="live")
    with pytest.raises(ValueError):
        EngineConfig(live_orders_enabled=True)


def test_gate_is_explicitly_rejected():
    with pytest.raises(ValueError):
        EngineConfig(exchanges=("gate",))


@pytest.mark.parametrize("adapter_cls", [OKXAdapter, BybitAdapter])
def test_all_live_order_paths_unreachable(adapter_cls):
    """Mandatory test: every live-order and write path must raise LiveExecutionBlockedError."""
    adapter = adapter_cls(client_factory=lambda _opts: _DummyCCXTClient())
    client = adapter._client

    for method in BLOCKED_EXCHANGE_METHODS:
        if hasattr(client, method):
            with pytest.raises(LiveExecutionBlockedError):
                getattr(client, method)("BTC/USDT", "limit", "buy", 0.01, 50000.0)

    for dynamic_write in ("private_post_trade_order", "v5PrivatePostOrderCreate"):
        with pytest.raises(LiveExecutionBlockedError):
            getattr(client, dynamic_write)({})


def test_secret_redaction_and_never_printed():
    secret = "super_secret_token_987654"
    creds = ExchangeCredentials("okx", api_key="key_abc123", api_secret=secret, passphrase="pass_xyz")
    assert secret not in repr(creds)
    assert secret not in str(creds)
    assert secret not in str(creds.metadata())

    filt = SecretRedactionFilter([secret, "key_abc123"])
    rec = logging.LogRecord("test", logging.INFO, "", 0, f"auth failed for {secret} apiKey=key_abc123", None, None)
    filt.filter(rec)
    assert secret not in rec.msg
    assert "key_abc123" not in rec.msg
    assert "[REDACTED]" in rec.msg


def test_secrets_file_permissions_validation(tmp_path: Path):
    sec_dir = tmp_path / "secrets"
    sec_dir.mkdir()
    os.chmod(sec_dir, 0o700)
    env_file = sec_dir / "exchanges.env"
    env_file.write_text("OKX_API_KEY=k\nOKX_API_SECRET=s\nOKX_API_PASSPHRASE=p\n", encoding="utf-8")
    os.chmod(env_file, 0o644)

    with pytest.raises(SecretPermissionError):
        validate_secrets_file_permissions(env_file)

    os.chmod(env_file, 0o600)
    res = validate_secrets_file_permissions(env_file)
    assert res["permissions_ok"] is True


def test_real_secrets_file_metadata_without_disclosure():
    secrets_path = Path("/root/trading-secrets/exchanges.env")
    if not secrets_path.exists():
        pytest.skip("VPS secrets file not present")
    env = load_environment(secrets_path=secrets_path, require_secrets_file=True)
    assert env.secrets_audit["permissions_ok"] is True
    assert set(env.credentials.keys()) == {"okx", "bybit", "binance"}
    for ex, cred in env.credentials.items():
        meta = cred.metadata()
        assert meta["exchange"] == ex
        assert "api_secret" not in str(meta).lower() or "has_api_secret" in str(meta).lower()


def test_real_ccxt_clients_block_all_write_and_non_get_paths():
    """Verify real CCXT Binance/OKX/Bybit clients block camelCase, WS, implicit POST/PUT/DELETE/PATCH, and transport writes."""
    import re
    import ccxt
    from app.exchanges.binance import BinanceAdapter

    write_prefixes = (
        "create_", "cancel_", "edit_", "withdraw", "transfer",
        "set_leverage", "set_margin", "set_position", "add_margin", "reduce_margin",
        "borrow_", "repay_", "close_position", "close_all", "place_", "submit_",
    )
    camel_prefixes = (
        "createOrder", "createMarket", "createLimit", "createStop", "createTrigger",
        "cancelOrder", "cancelAll", "editOrder", "withdraw", "transfer",
        "setLeverage", "setMargin", "setPosition", "addMargin", "reduceMargin",
        "borrow", "repay", "closePosition", "closeAll",
    )
    raw_write_re = re.compile(r"(post|put|delete|patch)", re.I)
    safe_local = {"create_ohlcv_object", "create_safe_dictionary", "create_ccxt_trade_id"}

    for adapter_cls in (BinanceAdapter, OKXAdapter, BybitAdapter):
        adapter = adapter_cls()
        try:
            for client in (adapter._public_client, adapter._auth_client):
                for attr in dir(client):
                    fn = getattr(client, attr, None)
                    if not callable(fn):
                        continue
                    qual = getattr(fn, "__qualname__", "")
                    if "_blocked_stub" in qual:
                        continue
                    if attr.startswith(write_prefixes) and not attr.endswith("_id") and attr not in safe_local:
                        raise AssertionError(f"{adapter_cls.__name__} left snake write method unblocked: {attr}")
                    if attr.startswith(camel_prefixes):
                        raise AssertionError(f"{adapter_cls.__name__} left camelCase write method unblocked: {attr}")
                    if (
                        any(ns in attr.lower() for ns in ("private", "sapi", "fapi", "dapi", "eapi", "papi", "v5"))
                        and raw_write_re.search(attr)
                        and not attr.startswith(("fetch", "load", "parse", "sign", "describe"))
                    ):
                        raise AssertionError(f"{adapter_cls.__name__} left implicit write endpoint unblocked: {attr}")

                # Spot-check high-risk methods raise LiveExecutionBlockedError
                for sample in ("createOrder", "cancelOrder", "create_order_ws", "close_position"):
                    if hasattr(client, sample):
                        with pytest.raises(LiveExecutionBlockedError):
                            getattr(client, sample)("BTC/USDT", "limit", "buy", 1, 1)

                # Transport-level GET-only allowlist must reject POST/PUT/DELETE/PATCH
                for verb in ("POST", "PUT", "DELETE", "PATCH"):
                    with pytest.raises(LiveExecutionBlockedError):
                        client.request("order", "private", verb, {})
                    with pytest.raises(LiveExecutionBlockedError):
                        client.fetch("https://example.invalid/order", method=verb)

                # Read-only allowlisted methods remain callable (not replaced by _blocked_stub)
                for ro_method in ("load_markets", "fetch_ticker", "fetch_balance", "fetch_positions", "fetch_orders"):
                    assert "_blocked_stub" not in getattr(getattr(client, ro_method), "__qualname__", "")

                # fetch2 transport-level GET-only allowlist must reject POST/PUT/DELETE/PATCH
                for verb in ("POST", "PUT", "DELETE", "PATCH"):
                    with pytest.raises(LiveExecutionBlockedError):
                        client.fetch2("order", "private", verb, {})
        finally:
            adapter.close()


def test_secret_redaction_filter_args_and_patterns():
    filt = SecretRedactionFilter(["my_secret_1234"])
    rec_tuple = logging.LogRecord(
        "test", logging.INFO, "", 0, "payload=%s token=%s", ("my_secret_1234", "tok_xyz"), None
    )
    filt.filter(rec_tuple)
    assert "my_secret_1234" not in str(rec_tuple.args)
    assert "[REDACTED]" in str(rec_tuple.args)

    rec_kv = logging.LogRecord(
        "test",
        logging.INFO,
        "",
        0,
        "headers: authorization=Bearer123 passphrase=p123 api_secret=s123",
        None,
        None,
    )
    filt.filter(rec_kv)
    assert "Bearer123" not in rec_kv.msg
    assert "p123" not in rec_kv.msg
    assert "s123" not in rec_kv.msg
