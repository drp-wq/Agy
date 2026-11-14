"""Credential-free Gate.io spot public-data adapter for Violet Lab only."""
import ccxt
from app.core.models import ExchangeId, MarketType
from app.exchanges.base import BaseExchangeAdapter
from app.exchanges.public_streams import gate_timestamped_ticker
from app.exchanges.timestamps import source_timestamp_ms


class GateIOAdapter(BaseExchangeAdapter):
    exchange_id = ExchangeId.GATEIO

    def __init__(self, **kwargs):
        if kwargs.get('credentials') is not None:
            raise ValueError('gate_public_adapter_credentials_forbidden')
        super().__init__(**kwargs)

    def _create_raw_client(self, opts):
        opts.setdefault('options', {}).update(defaultType='spot', fetchMarkets={'types':['spot']})
        return ccxt.gate(opts)

    async def fetch_ticker(self, symbol, market_type):
        if market_type != MarketType.SPOT:
            raise ValueError('gate_public_adapter_spot_only')
        norm = self.normalize_symbol(symbol, market_type)
        await self.load_markets()
        return await self._run(lambda: gate_timestamped_ticker(self._public_client, norm), f'public_ticker_stream:{norm}')

    async def _fetch_raw_order_book(self, norm, limit):
        await self.load_markets()
        def fetch():
            client = self._public_client
            response = client.publicSpotGetOrderBook({'currency_pair':client.market(norm)['id'], 'limit':limit, 'with_id':True})
            return {'bids':response.get('bids',[]), 'asks':response.get('asks',[]),
                    'timestamp':source_timestamp_ms(response.get('update')), 'nonce':response.get('id')}
        return await self._run(fetch, f'fetch_order_book:{norm}')
