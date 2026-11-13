"""Bybit Spot and Linear Perpetual Futures read-only adapter."""

from __future__ import annotations

from typing import Any

import ccxt

from app.core.models import ExchangeId
from app.exchanges.base import BaseExchangeAdapter


class BybitAdapter(BaseExchangeAdapter):
    """Bybit adapter supporting Spot and Linear Perpetual markets."""

    exchange_id = ExchangeId.BYBIT

    def _create_raw_client(self, opts: dict[str, Any]) -> Any:
        opts.setdefault("options", {})
        opts["options"].setdefault("defaultType", "spot")
        return ccxt.bybit(opts)
