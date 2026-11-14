"""Exchange registry managing OKX and Bybit adapters concurrently with failure isolation."""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import ExchangeCredentials
from app.core.models import ExchangeId
from app.exchanges.base import BaseExchangeAdapter
from app.exchanges.bybit import BybitAdapter
from app.exchanges.binance import BinanceAdapter
from app.exchanges.okx import OKXAdapter


class ExchangeRegistry:
    """Registry of active read-only exchange adapters (OKX and Bybit)."""

    def __init__(self, adapters: dict[ExchangeId, BaseExchangeAdapter] | None = None) -> None:
        self._adapters: dict[ExchangeId, BaseExchangeAdapter] = adapters or {}

    @classmethod
    def from_credentials(cls, credentials: dict[str, ExchangeCredentials]) -> ExchangeRegistry:
        adapters: dict[ExchangeId, BaseExchangeAdapter] = {
            ExchangeId.OKX: OKXAdapter(credentials=credentials.get("okx")),
            ExchangeId.BYBIT: BybitAdapter(credentials=credentials.get("bybit")),
            ExchangeId.BINANCE: BinanceAdapter(credentials=credentials.get("binance")),
        }
        return cls(adapters=adapters)

    @classmethod
    def violet_public(cls):
        """Independent Lab-only registry; never inherits private credentials."""
        from app.exchanges.gateio import GateIOAdapter
        return cls({ExchangeId.OKX: OKXAdapter(), ExchangeId.BYBIT: BybitAdapter(),
                    ExchangeId.BINANCE: BinanceAdapter(timestamped_spot_books=True),
                    ExchangeId.GATEIO: GateIOAdapter()})

    def get(self, exchange_id: ExchangeId | str) -> BaseExchangeAdapter:
        key = ExchangeId(exchange_id) if isinstance(exchange_id, str) else exchange_id
        if key not in self._adapters:
            raise KeyError(f"Exchange '{key}' is not registered.")
        return self._adapters[key]

    def all(self) -> dict[ExchangeId, BaseExchangeAdapter]:
        return dict(self._adapters)

    async def verify_all_read_only_auth(self) -> dict[str, Any]:
        """Concurrently verify read-only authenticated access where credentials exist."""
        results: dict[str, Any] = {}

        async def _check(ex_id: ExchangeId, adapter: BaseExchangeAdapter) -> None:
            try:
                bal = await adapter.fetch_read_only_balance()
                results[ex_id.value] = {
                    "status": "ok" if bal.get("configured") else "unconfigured",
                    "configured": bal.get("configured", False),
                    "asset_count": len(bal.get("balances", {})),
                }
            except Exception as exc:
                results[ex_id.value] = {
                    "status": "auth_error",
                    "configured": bool(adapter.credentials and adapter.credentials.is_configured),
                    "error_type": type(exc).__name__,
                }

        await asyncio.gather(*(_check(k, v) for k, v in self._adapters.items()))
        return results

    def health_summary(self) -> list[dict[str, Any]]:
        return [adapter.health_status() for adapter in self._adapters.values()]

    def close_all(self) -> None:
        for adapter in self._adapters.values():
            adapter.close()
