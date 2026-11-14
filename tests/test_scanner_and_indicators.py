"""Tests for indicators, market regime classification, and concurrent market scanner."""

from __future__ import annotations

import pytest

from app.core.models import Candle, ExchangeId, MarketRegime, MarketType
from app.exchanges.bybit import BybitAdapter
from app.exchanges.okx import OKXAdapter
from app.exchanges.registry import ExchangeRegistry
from app.scanner.indicators import compute_indicators
from app.scanner.market_scanner import GlobalMarketScanner


def _make_trending_candles(n: int = 80, step: float = 15.0) -> list[Candle]:
    candles = []
    px = 50000.0
    for i in range(n):
        px += step
        candles.append(
            Candle(
                timestamp=1700000000000 + i * 900_000,
                open=px - step * 0.5,
                high=px + 25.0,
                low=px - 25.0,
                close=px,
                volume=100.0 + (i % 5) * 20.0,
            )
        )
    return candles


def test_compute_indicators_and_regime():
    candles = _make_trending_candles(80, step=20.0)
    ind = compute_indicators(candles, spread_bps=3.0, order_book_depth_usd=120_000.0)
    assert ind["rsi"] > 60.0
    assert ind["atr"] > 0.0
    assert ind["ema_alignment"] == "bullish"
    assert ind["regime"] == MarketRegime.TRENDING_BULLISH

    low_liq = compute_indicators(candles, spread_bps=80.0, order_book_depth_usd=500.0)
    assert low_liq["regime"] == MarketRegime.LOW_LIQUIDITY


class _ScannerMockClient:
    def load_markets(self, reload=False):
        return {}

    def market(self, symbol):
        return {'symbol': symbol, 'id': symbol.split(':')[0].replace('/', '')}

    def publicGetV5MarketTickers(self, request):
        return {'retCode': 0, 'time': 1700000000000, 'result': {'category': request['category'], 'list': [{'symbol': request['symbol']}]}}

    def parse_ticker(self, row, market):
        return self.fetch_ticker(market['symbol'])

    def iso8601(self, timestamp):
        return None

    def fetch_ticker(self, _sym):
        return {"last": 51600.0, "quoteVolume": 200_000_000.0}

    def fetch_order_book(self, _sym, limit=20):
        return {"bids": [[51595.0, 3.0]], "asks": [[51605.0, 3.0]]}

    def fetch_ohlcv(self, _sym, timeframe="15m", limit=120):
        return [
            [c.timestamp, c.open, c.high, c.low, c.close, c.volume]
            for c in _make_trending_candles(80, step=15.0)
        ]

    def fetch_funding_rate(self, _sym):
        return {"fundingRate": 0.0001}

    def fetch_open_interest(self, _sym):
        return {"openInterestValue": 300_000_000.0}


@pytest.mark.asyncio
async def test_global_market_scanner_concurrent():
    okx = OKXAdapter(client_factory=lambda _o: _ScannerMockClient())
    bybit = BybitAdapter(client_factory=lambda _o: _ScannerMockClient())
    reg = ExchangeRegistry({ExchangeId.OKX: okx, ExchangeId.BYBIT: bybit})
    scanner = GlobalMarketScanner(reg, symbols=("BTC/USDT", "ETH/USDT"))

    snaps = await scanner.scan_all(fetch_all_tfs=False)
    assert len(snaps) == 8  # 2 exchanges * 2 symbols * 2 market types (spot, perp)
    assert all(s.opportunity_score > 0 for s in snaps)
    assert snaps[0].opportunity_score >= snaps[-1].opportunity_score
