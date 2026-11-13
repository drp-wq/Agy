"""OKX Spot and USDT-margined Perpetual Futures read-only adapter."""

from __future__ import annotations

import socket
from typing import Any

import ccxt
import urllib3.util.connection as urllib3_cn

from app.core.models import ExchangeId
from app.exchanges.base import BaseExchangeAdapter


class OKXAdapter(BaseExchangeAdapter):
    """OKX adapter supporting Spot and USDT-margined Perpetual markets."""

    exchange_id = ExchangeId.OKX

    def _create_raw_client(self, opts: dict[str, Any]) -> Any:
        # Force IPv4 so outbound requests use the server's whitelisted IPv4 address.
        urllib3_cn.allowed_gai_family = lambda: socket.AF_INET
        opts.setdefault("options", {})
        opts["options"].setdefault("defaultType", "spot")
        return ccxt.okx(opts)
