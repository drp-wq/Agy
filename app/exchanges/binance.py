"""Binance spot and USDT-margined perpetual read-only market adapter."""
from typing import Any
import ccxt
from app.core.models import ExchangeId
from app.exchanges.base import BaseExchangeAdapter

class BinanceAdapter(BaseExchangeAdapter):
    exchange_id = ExchangeId.BINANCE

    def _create_raw_client(self, opts: dict[str, Any]) -> Any:
        opts.setdefault("options", {})
        opts["options"].setdefault("defaultType", "spot")
        return ccxt.binance(opts)
