"""Tests for Phantom watch-only Solana monitor, unified 18-agent integration, auth, and UI."""

from __future__ import annotations

import base64
from pathlib import Path
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import (
    EngineConfig,
    MidLowCapConfig,
    UnifiedTreasuryConfig,
    WatchOnlyWalletsConfig,
    load_environment,
)
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.server import create_dashboard_app
from app.service import TradingEngineService
from app.wallets.solana_watch import (
    SOLANA_USDT_MINT,
    SolanaWatchOnlyMonitor,
    WatchOnlyWalletEntry,
    load_watch_only_wallets,
)

PHANTOM_ADDR = "FWTfn9ijTN9PE9H4Wcy1LKqFKgoQrxhBQDh13Lf57h5T"


def _basic_auth_header(username: str = "admin", password: str = "secret123456") -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _make_service(tmp_path: Path, treasury_enabled: bool = False) -> TradingEngineService:
    cfg = EngineConfig(
        db_path=tmp_path / "paper.sqlite3",
        backup_dir=tmp_path / "backups",
        log_dir=tmp_path / "logs",
        mid_low_cap=MidLowCapConfig(enabled=False),
        unified_treasury=UnifiedTreasuryConfig(enabled=treasury_enabled),
        watch_only_wallets=WatchOnlyWalletsConfig(
            registry_path=Path("/root/multi-exchange-paper/config/watch_only_wallets.json"),
            rpc_min_interval_sec=0.0,
        ),
    )
    env = load_environment(config=cfg, require_secrets_file=False)
    return TradingEngineService(env=env, enable_telegram=False)


def test_watch_only_registry_and_security_invariants() -> None:
    wallets = load_watch_only_wallets(Path("/root/multi-exchange-paper/config/watch_only_wallets.json"))
    assert len(wallets) == 1
    w = wallets[0]
    assert w.id == "phantom-solana-primary"
    assert w.chain == "solana-mainnet"
    assert w.address == PHANTOM_ADDR
    assert w.mode == "watch_only"
    assert w.signing_enabled is False
    assert w.transfers_enabled is False
    assert w.agent_access == "none"
    assert w.solscan_url == f"https://solscan.io/account/{PHANTOM_ADDR}"

    with pytest.raises(ValidationError):
        WatchOnlyWalletEntry(id="bad", chain="solana-mainnet", address=PHANTOM_ADDR, signing_enabled=True)
    with pytest.raises(ValidationError):
        WatchOnlyWalletEntry(id="bad", chain="solana-mainnet", address=PHANTOM_ADDR, transfers_enabled=True)
    with pytest.raises(ValidationError):
        WatchOnlyWalletEntry(id="bad", chain="solana-mainnet", address=PHANTOM_ADDR, mode="hot")
    with pytest.raises(ValidationError):
        WatchOnlyWalletEntry(id="bad", chain="solana-mainnet", address=PHANTOM_ADDR, agent_access="full")
    with pytest.raises(ValidationError):
        WatchOnlyWalletsConfig(signing_enabled=True)
    with pytest.raises(ValidationError):
        WatchOnlyWalletsConfig(transfers_enabled=True)
    with pytest.raises(ValidationError):
        WatchOnlyWalletsConfig(external_writes=True)


@pytest.mark.asyncio
async def test_solana_rpc_sol_and_mint_verified_usdt_decimals() -> None:
    wallet = WatchOnlyWalletEntry(id="phantom", chain="solana-mainnet", address=PHANTOM_ADDR)

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content.decode())
        method = body["method"]
        if method == "getBalance":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": 1_500_000_000}})
        if method == "getTokenAccountsByOwner":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "result": {
                        "value": [
                            {
                                "account": {
                                    "data": {
                                        "parsed": {
                                            "info": {
                                                "mint": SOLANA_USDT_MINT,
                                                "tokenAmount": {"amount": "125500000", "decimals": 6},
                                            }
                                        }
                                    }
                                }
                            },
                            {
                                "account": {
                                    "data": {
                                        "parsed": {
                                            "info": {
                                                "mint": SOLANA_USDT_MINT,
                                                "tokenAmount": {"amount": "4500000", "decimals": 6},
                                            }
                                        }
                                    }
                                }
                            },
                            {
                                # Spoofed non-USDT mint must be ignored even if returned
                                "account": {
                                    "data": {
                                        "parsed": {
                                            "info": {
                                                "mint": "FakeMint111111111111111111111111111111111111",
                                                "tokenAmount": {"amount": "999999999999", "decimals": 6},
                                            }
                                        }
                                    }
                                }
                            },
                        ]
                    },
                },
            )
        raise AssertionError(f"Unexpected method: {method}")

    monitor = SolanaWatchOnlyMonitor(
        wallets=[wallet],
        min_request_interval_sec=0.0,
        backoff_base_sec=0.0,
        transport=httpx.MockTransport(handler),
    )
    snap = await monitor.refresh_wallet(wallet)
    assert snap.sol_lamports == 1_500_000_000
    assert snap.sol_balance == "1.5"
    assert snap.usdt_balance == "130"
    assert snap.usdt_token_accounts_count == 2
    assert snap.usdt_mint == SOLANA_USDT_MINT
    assert snap.rpc_health == "ok"
    assert snap.stale is False
    assert snap.counted_as_trading_cash is False
    assert snap.signing_enabled is False
    assert snap.transfers_enabled is False


@pytest.mark.asyncio
async def test_solana_rpc_no_usdt_accounts_and_malformed_timeout_rate_limit() -> None:
    wallet = WatchOnlyWalletEntry(id="phantom", chain="solana-mainnet", address=PHANTOM_ADDR)

    # Case 1: 0 USDT token accounts
    def no_usdt_handler(request: httpx.Request) -> httpx.Response:
        import json

        method = json.loads(request.content.decode())["method"]
        if method == "getBalance":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": 0}})
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": []}})

    monitor = SolanaWatchOnlyMonitor(
        wallets=[wallet],
        min_request_interval_sec=0.0,
        backoff_base_sec=0.0,
        cache_ttl_sec=1.0,
        transport=httpx.MockTransport(no_usdt_handler),
    )
    snap = await monitor.refresh_wallet(wallet)
    assert snap.sol_balance == "0"
    assert snap.usdt_balance == "0"
    assert snap.usdt_token_accounts_count == 0
    assert snap.rpc_health == "ok"

    # Case 2: Subsequent HTTP 429 rate-limit falls back to stale cache with degraded health
    def rate_limit_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "Too many requests"})

    monitor._transport = httpx.MockTransport(rate_limit_handler)
    degraded = await monitor.refresh_wallet(wallet, force=True)
    assert degraded.stale is True
    assert degraded.rpc_health == "degraded"
    assert degraded.sol_balance == "0"
    assert degraded.error is not None

    # Case 3: Timeout with no cache yields unknown balances & unavailable RPC
    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("RPC timed out")

    cold_monitor = SolanaWatchOnlyMonitor(
        wallets=[wallet],
        min_request_interval_sec=0.0,
        max_retries=1,
        backoff_base_sec=0.0,
        transport=httpx.MockTransport(timeout_handler),
    )
    unavail = await cold_monitor.refresh_wallet(wallet)
    assert unavail.sol_balance is None
    assert unavail.usdt_balance is None
    assert unavail.rpc_health == "unavailable"
    assert unavail.stale is True
    assert "RPC timed out" in (unavail.error or "")

    # Case 4: Malformed RPC JSON
    def malformed_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"value": "not-an-int"}})

    bad_monitor = SolanaWatchOnlyMonitor(
        wallets=[wallet],
        min_request_interval_sec=0.0,
        max_retries=0,
        transport=httpx.MockTransport(malformed_handler),
    )
    bad_snap = await bad_monitor.refresh_wallet(wallet)
    assert bad_snap.sol_balance is None
    assert bad_snap.rpc_health == "unavailable"


def test_wallet_freshness_ttl_staleness() -> None:
    wallet = WatchOnlyWalletEntry(id="phantom", chain="solana-mainnet", address=PHANTOM_ADDR)
    monitor = SolanaWatchOnlyMonitor(wallets=[wallet], cache_ttl_sec=1.0)
    cached = monitor.get_cached_snapshot(wallet)
    assert cached.rpc_health == "uninitialized"
    assert cached.stale is True

    # Simulate cached entry older than TTL
    snap = cached.model_copy(update={"sol_balance": "2.0", "usdt_balance": "50", "rpc_health": "ok", "stale": False})
    monitor._cache[PHANTOM_ADDR] = (time.monotonic() - 5.0, snap)
    stale_snap = monitor.get_cached_snapshot(wallet)
    assert stale_snap.stale is True
    assert stale_snap.rpc_health == "degraded"
    assert (stale_snap.cache_age_sec or 0) >= 4.0


def test_dashboard_auth_api_contracts_treasury_conservation_and_ui_smoke(tmp_path: Path) -> None:
    svc = _make_service(tmp_path, treasury_enabled=True)
    assert svc.treasury is not None
    slots = svc.treasury.initialize_default_slots(18)
    svc.treasury.simulate_paper_transfer_e2e(
        agent_id=slots[0].agent_id,
        amount="500",
        idempotency_key="smoke-1",
        approved_by="admin",
        readonly_evidence_ref="paper_ok",
    )

    auth_cfg = DashboardAuthConfig(username="admin", password="secret123456")
    app = create_dashboard_app(svc, auth_config=auth_cfg)
    client = TestClient(app)

    # Unauthenticated requests must be rejected
    assert client.get("/api/wallets/watch-only").status_code == 401
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/treasury/overview").status_code == 401

    headers = _basic_auth_header()

    # Authenticated watch-only wallet endpoint
    w_res = client.get("/api/wallets/watch-only", headers=headers)
    assert w_res.status_code == 200
    w_body = w_res.json()
    assert w_body["mode"] == "PAPER_ONLY"
    assert w_body["wallet_mode"] == "watch_only"
    assert w_body["read_only"] is True
    assert w_body["live_order_submission"] is False
    assert w_body["external_writes"] is False
    assert w_body["signing_enabled"] is False
    assert w_body["transfers_enabled"] is False
    assert w_body["counted_as_trading_cash"] is False
    assert w_body["wallets"][0]["address"] == PHANTOM_ADDR

    # Authenticated state snapshot includes both unified_treasury & watch_only_wallets
    s_res = client.get("/api/state", headers=headers)
    assert s_res.status_code == 200
    s_body = s_res.json()
    assert s_body["mode"] == "PAPER_ONLY"
    assert s_body["live_execution_disabled"] is True
    assert s_body["watch_only_wallets"]["counted_as_trading_cash"] is False
    ut = s_body["unified_treasury"]
    assert ut["enabled"] is True
    assert ut["configured_slots"] == 18
    assert len(ut["slots"]) == 18
    assert ut["reconciliation"]["balanced"] is True
    assert ut["reconciliation"]["pool_conservation_ok"] is True
    assert ut["reconciliation"]["paper_separated_from_real"] is True
    assert ut["live_order_submission"] is False
    assert ut["external_writes"] is False
    # Watch-only wallet funds never inflated the 10000 USDT paper master pool
    assert ut["reconciliation"]["master_pool"]["total_capital"] == "10000.00000000"

    # UI render smoke & live-disabled invariant
    ui_res = client.get("/", headers=headers)
    assert ui_res.status_code == 200
    html = ui_res.text
    assert "Phantom Watch-Only Wallet (Solana Mainnet)" in html
    assert "Unified 18-Agent Paper Treasury" in html
    assert "SIGN (DISABLED)" in html
    assert "TRANSFER (DISABLED)" in html
    assert "LIVE ORDER (DISABLED)" in html
    assert "WITHDRAW (DISABLED)" in html
    assert "renderWatchOnlyWallets" in html
    assert "renderUnifiedTreasury" in html

    svc.db.close()
