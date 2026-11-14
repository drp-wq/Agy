"""Bounded public WebSocket snapshots. Never sends account/order messages."""
import json
import math
import time
import ccxt
from websockets.sync.client import connect
from websockets.exceptions import WebSocketException
from app.exchanges.timestamps import source_timestamp_ms


def positive_levels(rows, allow_zero=False):
    levels = {}
    for row in rows:
        p, q = float(row[0]), float(row[1])
        if not math.isfinite(p) or not math.isfinite(q) or p <= 0 or q < 0 or (q == 0 and not allow_zero):
            raise ValueError('invalid_public_book_level')
        levels[p] = q
    return levels


class BinanceDepth:
    """Sync a REST snapshot to a contiguous exchange-timestamped diff event."""
    def __init__(self, snapshot, symbol):
        self.symbol = symbol
        self.nonce = int(snapshot['nonce'])
        self.bids = positive_levels(snapshot['bids'])
        self.asks = positive_levels(snapshot['asks'])
        if not self.bids or not self.asks or max(self.bids) >= min(self.asks):
            raise ValueError('invalid_binance_snapshot')
        self.bid_boundary, self.ask_boundary = min(self.bids), max(self.asks)

    def apply(self, event, limit):
        if event.get('e') != 'depthUpdate' or event.get('s') != self.symbol:
            raise ValueError('binance_depth_symbol_or_event_mismatch')
        first, last = int(event['U']), int(event['u'])
        if first <= 0 or last < first:
            raise ValueError('invalid_binance_depth_sequence')
        if last <= self.nonce:
            return None
        if not first <= self.nonce + 1 <= last:
            raise ValueError('binance_depth_sequence_gap')
        timestamp = source_timestamp_ms(event.get('E'))
        if timestamp is None:
            raise ValueError('missing_binance_depth_event_timestamp')
        for side, key in [(self.bids, 'b'), (self.asks, 'a')]:
            for price, qty in positive_levels(event.get(key, []), allow_zero=True).items():
                if qty == 0:
                    side.pop(price, None)
                else:
                    side[price] = qty
        self.nonce = last
        bids = sorted(((p,q) for p,q in self.bids.items() if p >= self.bid_boundary), reverse=True)[:limit]
        asks = sorted((p,q) for p,q in self.asks.items() if p <= self.ask_boundary)[:limit]
        if len(bids) < limit or len(asks) < limit or bids[0][0] >= asks[0][0]:
            raise ValueError('insufficient_or_crossed_synced_binance_depth')
        return {'bids':bids,'asks':asks,'timestamp':timestamp,'nonce':last}


def binance_timestamped_book(client, symbol, limit, connector=connect):
    market = client.market(symbol)
    try:
        # Subscription in the URL: the connection sends no trading messages.
        with connector(f"wss://stream.binance.com:443/ws/{market['id'].lower()}@depth@100ms",
                       open_timeout=5, close_timeout=1, max_queue=32, proxy=None) as ws:
            snapshot = client.fetch_order_book(symbol, limit=1000)
            book = BinanceDepth(snapshot, market['id'])
            deadline = time.monotonic() + 5
            for _ in range(100):
                event = json.loads(ws.recv(timeout=max(.01, deadline-time.monotonic())))
                result = book.apply(event, limit)
                if result is not None:
                    return result
                if time.monotonic() >= deadline:
                    break
    except (OSError, TimeoutError, WebSocketException) as exc:
        raise ccxt.NetworkError('binance_public_depth_stream_unavailable') from exc
    raise ccxt.RequestTimeout('binance_public_depth_sync_timeout')


def gate_timestamped_ticker(client, symbol, connector=connect):
    market = client.market(symbol)
    try:
        with connector('wss://api.gateio.ws/ws/v4/', open_timeout=5, close_timeout=1, max_queue=8, proxy=None) as ws:
            # time is a subscription-protocol field only, never a data timestamp.
            ws.send(json.dumps({'time':int(time.time()), 'channel':'spot.tickers',
                                'event':'subscribe', 'payload':[market['id']]}))
            for _ in range(4):
                event = json.loads(ws.recv(timeout=5))
                if event.get('error'):
                    raise ValueError('gate_public_ticker_subscription_failed')
                if event.get('event') != 'update':
                    continue
                row = event.get('result') or {}
                if event.get('channel') != 'spot.tickers' or row.get('currency_pair') != market['id']:
                    raise ValueError('gate_ticker_symbol_or_channel_mismatch')
                timestamp = source_timestamp_ms(event.get('time_ms'))
                if timestamp is None:
                    raise ValueError('missing_gate_ticker_source_timestamp')
                ticker = client.parse_ticker(row, market)
                ticker.update(timestamp=timestamp, datetime=client.iso8601(timestamp),
                              source_timestamp_kind='gate_spot_tickers_time_ms')
                return ticker
    except (OSError, TimeoutError, WebSocketException) as exc:
        raise ccxt.NetworkError('gate_public_ticker_stream_unavailable') from exc
    raise ccxt.RequestTimeout('gate_public_ticker_update_timeout')
