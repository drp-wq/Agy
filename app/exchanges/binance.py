"""Binance spot and USDT-margined perpetual read-only market adapter."""
from typing import Any
import ccxt
from app.core.models import ExchangeId
from app.exchanges.base import BaseExchangeAdapter
from app.exchanges.public_streams import binance_timestamped_book

class BinanceAdapter(BaseExchangeAdapter):
    exchange_id = ExchangeId.BINANCE

    def __init__(self, *args, timestamped_spot_books=False, **kwargs):
        self.timestamped_spot_books = timestamped_spot_books
        super().__init__(*args, **kwargs)

    async def _fetch_raw_order_book(self, norm, limit):
        if not self.timestamped_spot_books or ':' in norm:
            return await super()._fetch_raw_order_book(norm, limit)
        await self.load_markets()
        return await self._run(lambda: binance_timestamped_book(self._public_client, norm, limit),
                               f'public_depth_sync:{norm}')

    def _create_raw_client(self, opts: dict[str, Any]) -> Any:
        opts.setdefault("options", {})
        opts["options"].setdefault("defaultType", "spot")
        return ccxt.binance(opts)
