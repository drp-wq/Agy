"""Bybit Spot and Linear Perpetual Futures read-only adapter."""

from __future__ import annotations

from typing import Any

import ccxt

from app.core.models import ExchangeId, MarketType
from app.exchanges.base import BaseExchangeAdapter
from app.exchanges.timestamps import source_timestamp_ms


class BybitAdapter(BaseExchangeAdapter):
    """Bybit adapter supporting Spot and Linear Perpetual markets."""

    exchange_id = ExchangeId.BYBIT

    async def fetch_ticker(self, symbol: str, market_type: MarketType) -> dict[str, Any]:
        """Retain V5 envelope time, which CCXT's fetch_ticker discards.

        This is the exchange snapshot response time, not last-trade time.
        Never use fetch_time or a previous response to stamp this snapshot.
        """
        norm = self.normalize_symbol(symbol, market_type)
        await self.load_markets()

        def fetch():
            client = self._public_client
            market = client.market(norm)
            category = 'spot' if market_type == MarketType.SPOT else 'linear'
            response = client.publicGetV5MarketTickers({'category': category, 'symbol': market['id']})
            if response.get('retCode') != 0:
                raise ValueError('bybit_ticker_response_error')
            result = response.get('result') or {}
            rows = result.get('list') or []
            if result.get('category') != category or len(rows) != 1 or rows[0].get('symbol') != market['id']:
                raise ValueError('bybit_ticker_symbol_or_category_mismatch')
            ticker = client.parse_ticker(rows[0], market)
            ticker['timestamp'] = source_timestamp_ms(response.get('time'))
            ticker['datetime'] = client.iso8601(ticker['timestamp']) if ticker['timestamp'] else None
            ticker['source_timestamp_kind'] = 'bybit_v5_response_time'
            return ticker

        return await self._run(fetch, f'fetch_ticker:{norm}')

    def _create_raw_client(self, opts: dict[str, Any]) -> Any:
        opts.setdefault("options", {})
        opts["options"].setdefault("defaultType", "spot")
        return ccxt.bybit(opts)
