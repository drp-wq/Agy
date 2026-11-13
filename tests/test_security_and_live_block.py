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
