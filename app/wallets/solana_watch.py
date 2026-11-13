"""Secure read-only Solana mainnet balance monitor for registered watch-only wallets."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
from pathlib import Path
import re
import time
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

SOLANA_MAINNET_RPC_URL = "https://api.mainnet-beta.solana.com"
SOLANA_USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
LAMPORTS_PER_SOL = Decimal("1000000000")
_BASE58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")


class WatchOnlyWalletEntry(BaseModel):
    """Sanitized registry entry for an external watch-only wallet."""

    id: str
    chain: str
    address: str
    mode: str = "watch_only"
    signing_enabled: bool = False
    transfers_enabled: bool = False
    agent_access: str = "none"

    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        if v != "watch_only":
            raise ValueError("Watch-only wallet mode must be 'watch_only'.")
        return v

    @field_validator("signing_enabled")
    @classmethod
    def _validate_signing_disabled(cls, v: bool) -> bool:
        if v:
            raise ValueError("signing_enabled must be false for watch-only wallets.")
        return False

    @field_validator("transfers_enabled")
    @classmethod
    def _validate_transfers_disabled(cls, v: bool) -> bool:
        if v:
            raise ValueError("transfers_enabled must be false for watch-only wallets.")
        return False

    @field_validator("agent_access")
    @classmethod
    def _validate_agent_access(cls, v: str) -> str:
        if v != "none":
            raise ValueError("agent_access must be 'none' for watch-only wallets.")
        return "none"

    @field_validator("address")
    @classmethod
    def _validate_address(cls, v: str) -> str:
        cleaned = v.strip()
        if not _BASE58_RE.match(cleaned):
            raise ValueError("Invalid Solana base58 public address.")
        return cleaned

    @property
    def solscan_url(self) -> str:
        return f"https://solscan.io/account/{self.address}"


class WalletBalanceSnapshot(BaseModel):
    """Read-only balance snapshot for a watch-only wallet; never counted as trading cash."""

    wallet_id: str
    chain: str
    address: str
    mode: str = "watch_only"
    read_only: bool = True
    signing_enabled: bool = False
    transfers_enabled: bool = False
    agent_access: str = "none"
    counted_as_trading_cash: bool = False
    sol_balance: str | None = None
    sol_lamports: int | None = None
    usdt_balance: str | None = None
    usdt_mint: str = SOLANA_USDT_MINT
    usdt_token_accounts_count: int | None = None
    rpc_health: str = "uninitialized"
    stale: bool = True
    error: str | None = None
    last_success_at: str | None = None
    last_attempt_at: str | None = None
    cache_age_sec: float | None = None
    solscan_url: str


def load_watch_only_wallets(path: Path) -> list[WatchOnlyWalletEntry]:
    """Load and strictly validate watch-only wallets from JSON config."""
    if not path.exists():
        logger.warning("Watch-only wallet registry not found at %s", path)
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get("wallets", []) if isinstance(raw, dict) else []
        entries: list[WatchOnlyWalletEntry] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            entries.append(WatchOnlyWalletEntry(**item))
        return entries
    except Exception as exc:
        logger.error("Failed to load watch-only wallets from %s: %s", path, type(exc).__name__)
        return []


def _format_decimal(value: Decimal) -> str:
    normalized = value.normalize()
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class SolanaWatchOnlyMonitor:
    """Bounded, rate-limited, cached read-only Solana JSON-RPC balance monitor."""

    def __init__(
        self,
        wallets: list[WatchOnlyWalletEntry],
        rpc_url: str = SOLANA_MAINNET_RPC_URL,
        timeout_sec: float = 8.0,
        max_retries: int = 2,
        backoff_base_sec: float = 0.5,
        min_request_interval_sec: float = 1.5,
        cache_ttl_sec: float = 60.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.wallets = list(wallets)
        self.rpc_url = rpc_url
        self.timeout_sec = max(1.0, float(timeout_sec))
        self.max_retries = max(0, int(max_retries))
        self.backoff_base_sec = max(0.0, float(backoff_base_sec))
        self.min_request_interval_sec = max(0.0, float(min_request_interval_sec))
        self.cache_ttl_sec = max(1.0, float(cache_ttl_sec))
        self._transport = transport

        self._lock = asyncio.Lock()
        self._last_rpc_monotonic: float = 0.0
        self._cache: dict[str, tuple[float, WalletBalanceSnapshot]] = {}
        self._last_error: dict[str, tuple[str, str]] = {}

    def _base_snapshot(self, wallet: WatchOnlyWalletEntry) -> WalletBalanceSnapshot:
        return WalletBalanceSnapshot(
            wallet_id=wallet.id,
            chain=wallet.chain,
            address=wallet.address,
            mode="watch_only",
            read_only=True,
            signing_enabled=False,
            transfers_enabled=False,
            agent_access="none",
            counted_as_trading_cash=False,
            usdt_mint=SOLANA_USDT_MINT,
            rpc_health="uninitialized",
            stale=True,
            solscan_url=wallet.solscan_url,
        )

    def get_cached_snapshot(self, wallet: WatchOnlyWalletEntry) -> WalletBalanceSnapshot:
        """Non-blocking read of cached wallet state with dynamic staleness calculation."""
        now_mono = time.monotonic()
        cached = self._cache.get(wallet.address)
        if cached is None:
            snap = self._base_snapshot(wallet)
            err_info = self._last_error.get(wallet.address)
            if err_info:
                snap.rpc_health = "unavailable"
                snap.error = err_info[0]
                snap.last_attempt_at = err_info[1]
            return snap

        cached_at, snap = cached
        age = max(0.0, round(now_mono - cached_at, 2))
        is_stale = age > self.cache_ttl_sec
        err_info = self._last_error.get(wallet.address)
        if err_info:
            return snap.model_copy(
                update={
                    "stale": True,
                    "rpc_health": "degraded",
                    "error": err_info[0],
                    "last_attempt_at": err_info[1],
                    "cache_age_sec": age,
                }
            )
        return snap.model_copy(
            update={
                "stale": is_stale,
                "rpc_health": "degraded" if is_stale else "ok",
                "cache_age_sec": age,
            }
        )

    def all_cached_snapshots(self) -> list[dict[str, Any]]:
        return [self.get_cached_snapshot(w).model_dump(mode="json") for w in self.wallets]

    async def _rate_limit_wait(self) -> None:
        if self.min_request_interval_sec <= 0:
            return
        elapsed = time.monotonic() - self._last_rpc_monotonic
        if elapsed < self.min_request_interval_sec:
            await asyncio.sleep(self.min_request_interval_sec - elapsed)
        self._last_rpc_monotonic = time.monotonic()

    async def _rpc_call(
        self,
        client: httpx.AsyncClient,
        method: str,
        params: list[Any],
    ) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            await self._rate_limit_wait()
            try:
                resp = await client.post(
                    self.rpc_url,
                    json=payload,
                    timeout=self.timeout_sec,
                    headers={"Content-Type": "application/json"},
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise RuntimeError(f"RPC HTTP {resp.status_code}")
                if resp.status_code != 200:
                    raise ValueError(f"RPC HTTP {resp.status_code}")

                data = resp.json()
                if not isinstance(data, dict):
                    raise ValueError("Malformed RPC JSON response")
                if "error" in data and data["error"] is not None:
                    err = data["error"]
                    msg = err.get("message") if isinstance(err, dict) else str(err)
                    raise ValueError(f"RPC error: {str(msg)[:120]}")
                result = data.get("result")
                if not isinstance(result, dict):
                    raise ValueError("Malformed RPC result object")
                return result
            except (httpx.TimeoutException, httpx.TransportError, RuntimeError) as exc:
                last_exc = exc
                if attempt < self.max_retries and self.backoff_base_sec > 0:
                    await asyncio.sleep(self.backoff_base_sec * (2**attempt))
            except Exception as exc:
                last_exc = exc
                break

        raise RuntimeError(str(last_exc) if last_exc else "Solana RPC request failed")

    @staticmethod
    def _parse_sol_balance(result: dict[str, Any]) -> tuple[int, str]:
        lamports = result.get("value")
        if isinstance(lamports, bool) or not isinstance(lamports, int) or lamports < 0:
            raise ValueError("Invalid lamports balance in RPC response")
        sol = Decimal(lamports) / LAMPORTS_PER_SOL
        return lamports, _format_decimal(sol)

    @staticmethod
    def _parse_usdt_accounts(result: dict[str, Any]) -> tuple[str, int]:
        accounts = result.get("value")
        if not isinstance(accounts, list):
            raise ValueError("Invalid token accounts list in RPC response")

        total_usdt = Decimal("0")
        verified_count = 0

        for acct in accounts:
            if not isinstance(acct, dict):
                raise ValueError("Malformed token account entry")
            account_obj = acct.get("account")
            if not isinstance(account_obj, dict):
                raise ValueError("Missing account object in token account")
            data_obj = account_obj.get("data")
            if not isinstance(data_obj, dict):
                raise ValueError("Missing parsed data object in token account")
            parsed = data_obj.get("parsed")
            if not isinstance(parsed, dict):
                raise ValueError("Missing parsed field in token account")
            info = parsed.get("info")
            if not isinstance(info, dict):
                raise ValueError("Missing info field in parsed token account")

            # Strict mint verification: never trust arbitrary token symbols.
            mint = info.get("mint")
            if mint != SOLANA_USDT_MINT:
                continue

            token_amount = info.get("tokenAmount")
            if not isinstance(token_amount, dict):
                raise ValueError("Missing tokenAmount for verified USDT account")

            raw_amount = token_amount.get("amount")
            decimals = token_amount.get("decimals")
            if (
                not isinstance(raw_amount, str)
                or not raw_amount.isdigit()
                or isinstance(decimals, bool)
                or not isinstance(decimals, int)
                or decimals < 0
                or decimals > 18
            ):
                raise ValueError("Invalid USDT tokenAmount precision fields")

            try:
                amount_dec = Decimal(raw_amount) / (Decimal(10) ** decimals)
            except InvalidOperation as exc:
                raise ValueError("Failed to decode USDT decimal amount") from exc

            total_usdt += amount_dec
            verified_count += 1

        return _format_decimal(total_usdt), verified_count

    async def refresh_wallet(
        self,
        wallet: WatchOnlyWalletEntry,
        force: bool = False,
    ) -> WalletBalanceSnapshot:
        """Fetch SOL and mint-verified SPL USDT balances for a watch-only wallet."""
        async with self._lock:
            now_mono = time.monotonic()
            cached = self._cache.get(wallet.address)
            if not force and cached is not None:
                cached_at, snap = cached
                age = max(0.0, round(now_mono - cached_at, 2))
                if age <= self.cache_ttl_sec:
                    return snap.model_copy(
                        update={"stale": False, "rpc_health": "ok", "cache_age_sec": age}
                    )

            attempt_iso = datetime.now(timezone.utc).isoformat()
            client_kwargs: dict[str, Any] = {}
            if self._transport is not None:
                client_kwargs["transport"] = self._transport

            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    bal_res = await self._rpc_call(
                        client,
                        "getBalance",
                        [wallet.address, {"commitment": "confirmed"}],
                    )
                    lamports, sol_str = self._parse_sol_balance(bal_res)

                    tok_res = await self._rpc_call(
                        client,
                        "getTokenAccountsByOwner",
                        [
                            wallet.address,
                            {"mint": SOLANA_USDT_MINT},
                            {"encoding": "jsonParsed", "commitment": "confirmed"},
                        ],
                    )
                    usdt_str, usdt_count = self._parse_usdt_accounts(tok_res)

                now_iso = datetime.now(timezone.utc).isoformat()
                snap = WalletBalanceSnapshot(
                    wallet_id=wallet.id,
                    chain=wallet.chain,
                    address=wallet.address,
                    mode="watch_only",
                    read_only=True,
                    signing_enabled=False,
                    transfers_enabled=False,
                    agent_access="none",
                    counted_as_trading_cash=False,
                    sol_balance=sol_str,
                    sol_lamports=lamports,
                    usdt_balance=usdt_str,
                    usdt_mint=SOLANA_USDT_MINT,
                    usdt_token_accounts_count=usdt_count,
                    rpc_health="ok",
                    stale=False,
                    error=None,
                    last_success_at=now_iso,
                    last_attempt_at=attempt_iso,
                    cache_age_sec=0.0,
                    solscan_url=wallet.solscan_url,
                )
                self._cache[wallet.address] = (time.monotonic(), snap)
                self._last_error.pop(wallet.address, None)
                return snap
            except Exception as exc:
                err_msg = f"{type(exc).__name__}: {str(exc)[:140]}"
                self._last_error[wallet.address] = (err_msg, attempt_iso)
                logger.warning("Solana watch-only RPC error for %s: %s", wallet.id, err_msg)
                return self.get_cached_snapshot(wallet)

    async def refresh_all(self, force: bool = False) -> list[WalletBalanceSnapshot]:
        """Safely refresh all registered watch-only wallets without raising."""
        results: list[WalletBalanceSnapshot] = []
        for wallet in self.wallets:
            try:
                results.append(await self.refresh_wallet(wallet, force=force))
            except Exception as exc:
                logger.warning("Unexpected watch-only monitor error for %s: %s", wallet.id, type(exc).__name__)
                results.append(self.get_cached_snapshot(wallet))
        return results
