"""Isolated, strictly read-only exchange account telemetry adapter for Binance, Bybit, and OKX."""
from __future__ import annotations

import asyncio
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import ccxt.async_support as ccxt_async

from app.core.logging_setup import register_sensitive_value
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED

DEFAULT_EXCHANGES_ENV = Path("/root/trading-secrets/exchanges.env")
ALLOWED_EXCHANGES = ("binance", "bybit", "okx")
FORBIDDEN_WRITE_METHODS = frozenset(
    {
        "create_order",
        "cancel_order",
        "cancel_all_orders",
        "edit_order",
        "withdraw",
        "transfer",
        "set_leverage",
        "set_margin_mode",
    }
)


def _sanitize_error(exc: Exception, secrets_to_mask: list[str]) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    for sec in secrets_to_mask:
        if sec and len(sec) >= 4 and sec in msg:
            msg = msg.replace(sec, "***")
    return msg[:240]


def _classify_error(exc: Exception) -> str:
    text = f"{type(exc).__name__} {exc}".lower()
    if any(
        tok in text
        for tok in (
            "authenticationerror",
            "permissiondenied",
            "invalid api-key",
            "invalid key",
            "api-key format invalid",
            "signature",
            "unauthorized",
            "401",
            "403",
            "10003",
            "10004",
            "50110",
            "50111",
            "50113",
            "50119",
            "-2015",
            "-2014",
        )
    ):
        return "invalid_permissions"
    return "exchange_error"


def _extract_nonzero_balances(bal: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(bal, dict):
        return {}
    totals = bal.get("total")
    if not isinstance(totals, dict):
        return {}
    out: dict[str, float] = {}
    for asset, val in totals.items():
        try:
            num = float(val or 0.0)
        except (TypeError, ValueError):
            continue
        if num > 1e-12:
            out[str(asset)] = round(num, 8)
    return out


def _extract_open_positions(positions: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not isinstance(positions, list):
        return []
    out: list[dict[str, Any]] = []
    for p in positions:
        if not isinstance(p, dict):
            continue
        try:
            contracts = float(p.get("contracts") or p.get("contractSize") or 0.0)
        except (TypeError, ValueError):
            contracts = 0.0
        if abs(contracts) <= 1e-12:
            continue
        out.append(
            {
                "symbol": str(p.get("symbol") or ""),
                "side": str(p.get("side") or ""),
                "contracts": contracts,
                "entry_price": float(p.get("entryPrice") or 0.0),
                "unrealized_pnl": round(float(p.get("unrealizedPnl") or 0.0), 4),
            }
        )
    return out


class ReadOnlyExchangeTelemetry:
    """Fetches read-only balances, positions, and auth status without exposing keys or allowing writes."""

    def __init__(
        self,
        env_path: Path = DEFAULT_EXCHANGES_ENV,
        timeout_sec: float = 10.0,
        min_refresh_interval_sec: float = 5.0,
        stale_after_sec: float = 300.0,
    ) -> None:
        assert LIVE_EXECUTION_PERMANENTLY_DISABLED is True
        self.env_path = Path(env_path)
        self.timeout_sec = timeout_sec
        self.min_refresh_interval_sec = min_refresh_interval_sec
        self.stale_after_sec = stale_after_sec
        self._creds = self._load_credentials(self.env_path)
        self._cache: dict[str, dict[str, Any]] = {}
        self._last_refresh_mono: float = 0.0
        self._lock = asyncio.Lock()

    @staticmethod
    def _load_credentials(env_path: Path) -> dict[str, dict[str, str]]:
        raw: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                k, v = stripped.split("=", 1)
                val = v.strip().strip('"').strip("'")
                raw[k.strip()] = val
                if any(s in k for s in ("KEY", "SECRET", "PASSPHRASE")) and val:
                    register_sensitive_value(val)

        creds: dict[str, dict[str, str]] = {}
        if raw.get("BINANCE_API_KEY") and raw.get("BINANCE_API_SECRET"):
            creds["binance"] = {
                "apiKey": raw["BINANCE_API_KEY"],
                "secret": raw["BINANCE_API_SECRET"],
            }
        if raw.get("BYBIT_API_KEY") and raw.get("BYBIT_API_SECRET"):
            creds["bybit"] = {
                "apiKey": raw["BYBIT_API_KEY"],
                "secret": raw["BYBIT_API_SECRET"],
            }
        if raw.get("OKX_API_KEY") and raw.get("OKX_API_SECRET") and raw.get("OKX_API_PASSPHRASE"):
            creds["okx"] = {
                "apiKey": raw["OKX_API_KEY"],
                "secret": raw["OKX_API_SECRET"],
                "password": raw["OKX_API_PASSPHRASE"],
            }
        return creds

    def _build_client(self, exchange_id: str) -> tuple[Any, aiohttp.ClientSession | None]:
        assert LIVE_EXECUTION_PERMANENTLY_DISABLED is True
        cred = self._creds.get(exchange_id)
        if not cred:
            return None, None
        cls = getattr(ccxt_async, exchange_id)
        session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(family=socket.AF_INET))
        opts: dict[str, Any] = {
            "apiKey": cred["apiKey"],
            "secret": cred["secret"],
            "enableRateLimit": True,
            "timeout": int(self.timeout_sec * 1000),
            "session": session,
        }
        if "password" in cred:
            opts["password"] = cred["password"]
        if exchange_id == "binance":
            opts["options"] = {"defaultType": "spot", "adjustForTimeDifference": True}
        elif exchange_id == "bybit":
            opts["options"] = {"defaultType": "unified"}
        elif exchange_id == "okx":
            opts["options"] = {"defaultType": "swap"}
        return cls(opts), session

    async def _fetch_one(self, exchange_id: str) -> dict[str, Any]:
        now_ts = int(time.time())
        cred = self._creds.get(exchange_id)
        if not cred:
            return {
                "exchange": exchange_id,
                "public_feed_ok": True,
                "private_auth_ok": False,
                "auth_state": "missing_credentials",
                "private_auth_error": "missing_credentials",
                "last_sync_ts": None,
                "latency_ms": None,
                "freshness": "unavailable",
                "account_status": "unconfigured",
                "api_permissions": None,
                "live_readonly_snapshot": {
                    "status": "error",
                    "timestamp": now_ts,
                    "balances": {},
                    "positions": [],
                    "error": "missing_credentials",
                },
            }

        secrets_list = list(cred.values())
        last_exc: Exception | None = None
        for attempt in range(2):
            client, session = self._build_client(exchange_id)
            t0 = time.monotonic()
            try:
                async def _query() -> dict[str, Any]:
                    public_ok = True
                    try:
                        await client.fetch_ticker("BTC/USDT")
                    except Exception:
                        public_ok = False

                    balances: dict[str, float] = {}
                    positions: list[dict[str, Any]] = []
                    account_status = "read_only_verified"
                    api_permissions: dict[str, Any] | None = None

                    if exchange_id == "binance":
                        spot_bal = await client.fetch_balance({"type": "spot"})
                        balances = _extract_nonzero_balances(spot_bal)
                        try:
                            pos_raw = await client.fetch_positions()
                            positions = _extract_open_positions(pos_raw)
                        except Exception:
                            positions = []
                        try:
                            restrictions = await client.sapi_get_account_apirestrictions()
                            api_permissions = {
                                "read_only": not bool(restrictions.get("enableSpotAndMarginTrading"))
                                and not bool(restrictions.get("enableWithdrawals")),
                                "enableReading": bool(restrictions.get("enableReading", True)),
                                "enableWithdrawals": bool(restrictions.get("enableWithdrawals", False)),
                            }
                        except Exception:
                            api_permissions = {"enableReading": True}

                    elif exchange_id == "bybit":
                        uta_bal = await client.fetch_balance({"type": "unified"})
                        balances = _extract_nonzero_balances(uta_bal)
                        try:
                            pos_raw = await client.fetch_positions()
                            positions = _extract_open_positions(pos_raw)
                        except Exception:
                            positions = []
                        try:
                            info = await client.private_get_v5_account_info()
                            res = info.get("result") if isinstance(info, dict) else {}
                            if isinstance(res, dict) and res.get("unifiedMarginStatus"):
                                account_status = f"UTA_status_{res.get('unifiedMarginStatus')}"
                        except Exception:
                            pass

                    elif exchange_id == "okx":
                        okx_bal = await client.fetch_balance()
                        balances = _extract_nonzero_balances(okx_bal)
                        try:
                            pos_raw = await client.fetch_positions()
                            positions = _extract_open_positions(pos_raw)
                        except Exception:
                            positions = []
                        try:
                            cfg = await client.private_get_account_config()
                            data = cfg.get("data") if isinstance(cfg, dict) else []
                            if isinstance(data, list) and data and isinstance(data[0], dict):
                                account_status = f"acctLv_{data[0].get('acctLv', 'ok')}"
                        except Exception:
                            pass

                    return {
                        "public_ok": public_ok,
                        "balances": balances,
                        "positions": positions,
                        "account_status": account_status,
                        "api_permissions": api_permissions,
                    }

                res = await asyncio.wait_for(_query(), timeout=self.timeout_sec)
                latency_ms = round((time.monotonic() - t0) * 1000.0, 1)
                sync_ts = int(time.time())
                return {
                    "exchange": exchange_id,
                    "public_feed_ok": res["public_ok"],
                    "private_auth_ok": True,
                    "auth_state": "authenticated",
                    "private_auth_error": None,
                    "last_sync_ts": sync_ts,
                    "latency_ms": latency_ms,
                    "freshness": "fresh",
                    "account_status": res["account_status"],
                    "api_permissions": res["api_permissions"],
                    "live_readonly_snapshot": {
                        "status": "ok",
                        "timestamp": sync_ts,
                        "balances": res["balances"],
                        "positions": res["positions"],
                    },
                }
            except Exception as exc:
                last_exc = exc
                if attempt == 0 and _classify_error(exc) == "exchange_error":
                    await asyncio.sleep(0.5)
            finally:
                try:
                    await client.close()
                except Exception:
                    pass
                if session is not None:
                    try:
                        await session.close()
                    except Exception:
                        pass

        err_state = _classify_error(last_exc) if last_exc else "exchange_error"
        err_msg = _sanitize_error(last_exc, secrets_list) if last_exc else "unknown_error"
        prev = self._cache.get(exchange_id)
        prev_snap = prev.get("live_readonly_snapshot") if isinstance(prev, dict) else None
        return {
            "exchange": exchange_id,
            "public_feed_ok": True,
            "private_auth_ok": False,
            "auth_state": err_state,
            "private_auth_error": err_msg,
            "last_sync_ts": prev.get("last_sync_ts") if isinstance(prev, dict) else None,
            "latency_ms": None,
            "freshness": "stale" if (isinstance(prev_snap, dict) and prev_snap.get("status") == "ok") else "unavailable",
            "account_status": err_state,
            "api_permissions": None,
            "live_readonly_snapshot": {
                "status": "error",
                "timestamp": now_ts,
                "balances": {},
                "positions": [],
                "error": err_msg,
            },
        }

    async def refresh(self, force: bool = False) -> dict[str, dict[str, Any]]:
        async with self._lock:
            now_mono = time.monotonic()
            if (
                self._cache
                and not force
                and (now_mono - self._last_refresh_mono) < self.stale_after_sec
            ):
                return self.snapshot()
            if (
                self._cache
                and force
                and (now_mono - self._last_refresh_mono) < self.min_refresh_interval_sec
            ):
                return self.snapshot()

            results = await asyncio.gather(*(self._fetch_one(ex) for ex in ALLOWED_EXCHANGES))
            for item in results:
                self._cache[item["exchange"]] = item
            self._last_refresh_mono = time.monotonic()
            return self.snapshot()

    def snapshot(self) -> dict[str, dict[str, Any]]:
        now_ts = int(time.time())
        out: dict[str, dict[str, Any]] = {}
        for ex in ALLOWED_EXCHANGES:
            entry = dict(self._cache.get(ex) or {})
            if not entry:
                out[ex] = {
                    "exchange": ex,
                    "public_feed_ok": True,
                    "private_auth_ok": False,
                    "auth_state": "not_synced_yet",
                    "private_auth_error": "Telemetry refresh pending",
                    "last_sync_ts": None,
                    "latency_ms": None,
                    "freshness": "unavailable",
                    "account_status": "pending",
                    "api_permissions": None,
                    "live_readonly_snapshot": None,
                }
                continue
            ts = entry.get("last_sync_ts")
            if isinstance(ts, (int, float)) and ts > 0:
                age = max(0, now_ts - int(ts))
                entry["age_seconds"] = age
                if entry.get("private_auth_ok") and age > self.stale_after_sec:
                    entry["freshness"] = "stale"
                    entry["auth_state"] = "stale_data"
            else:
                entry["age_seconds"] = None
            out[ex] = entry
        return out
