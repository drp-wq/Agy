"""Tests for OKX and Bybit adapters, normalization, retry backoff, and outage isolation."""

from __future__ import annotations

import ccxt
import pytest

from app.core.config import ExchangeCredentials
from app.core.models import ExchangeId, MarketType
from app.exchanges.base import ExchangeOutageError
from app.exchanges.bybit import BybitAdapter
from app.exchanges.okx import OKXAdapter
from app.exchanges.registry import ExchangeRegistry


class _MockCCXT:
    def __init__(self, fail_times: int = 0, hard_fail: bool = False) -> None:
        self.fail_times = fail_times
        self.hard_fail = hard_fail
        self.calls = 0
        self.markets = {
            "BTC/USDT": {
                "base": "BTC",
                "quote": "USDT",
                "precision": {"price": 0.1, "amount": 0.0001},
                "limits": {"amount": {"min": 0.0001}, "cost": {"min": 5.0}, "leverage": {"max": 1.0}},
                "maker": 0.0008,
                "taker": 0.0010,
            },
            "BTC/USDT:USDT": {
                "base": "BTC",
                "quote": "USDT",
                "contractSize": 1.0,
                "precision": {"price": 0.1, "amount": 0.001},
                "limits": {"amount": {"min": 0.001}, "cost": {"min": 5.0}, "leverage": {"max": 50.0}},
                "maker": 0.0002,
                "taker": 0.0005,
            },
        }

    def load_markets(self, _reload=False):
        return self.markets

    def fetch_ticker(self, symbol):
        self.calls += 1
        if self.hard_fail or self.calls <= self.fail_times:
            raise ccxt.NetworkError("simulated network blip")
        return {"symbol": symbol, "last": 65000.0, "quoteVolume": 150_000_000.0}

    def fetch_order_book(self, _symbol, limit=20):
        return {
            "bids": [[64990.0, 2.0], [64980.0, 3.0]],
            "asks": [[65010.0, 2.0], [65020.0, 3.0]],
        }

    def fetch_ohlcv(self, _symbol, timeframe="15m", limit=150):
        return [[1700000000000 + i * 60000, 64900 + i, 65000 + i, 64850 + i, 64950 + i, 120.0] for i in range(60)]

    def fetch_funding_rate(self, _symbol):
        return {"fundingRate": 0.00015}

    def fetch_open_interest(self, _symbol):
        return {"openInterestValue": 500_000_000.0}

    def fetch_balance(self):
        return {"total": {"USDT": 1250.0, "BTC": 0.0}}

    def fetch_positions(self):
        return [{"symbol": "BTC/USDT:USDT", "side": "long", "contracts": 0.0, "entryPrice": 0.0}]


@pytest.mark.asyncio
async def test_okx_and_bybit_normalization_and_read_only_auth():
    okx = OKXAdapter(
        credentials=ExchangeCredentials("okx", "k", "s", "p"),
        client_factory=lambda _o: _MockCCXT(),
    )
    bybit = BybitAdapter(
        credentials=ExchangeCredentials("bybit", "k", "s"),
        client_factory=lambda _o: _MockCCXT(),
    )

    spec_spot = await okx.get_instrument_spec("BTC/USDT", MarketType.SPOT)
    spec_perp = await bybit.get_instrument_spec("BTC/USDT", MarketType.PERP)
    assert spec_spot.exchange == ExchangeId.OKX
    assert spec_perp.symbol == "BTC/USDT:USDT"
    assert spec_perp.max_leverage == 50.0

    ob = await okx.fetch_order_book("BTC/USDT", MarketType.SPOT)
    assert ob.best_bid == 64990.0
    assert ob.best_ask == 65010.0
    assert ob.spread_bps > 0

    bal = await okx.fetch_read_only_balance()
    assert bal["configured"] is True
    assert bal["balances"] == {"USDT": 1250.0}

    fr = await bybit.fetch_funding_rate("BTC/USDT")
    oi = await bybit.fetch_open_interest("BTC/USDT")
    assert fr == 0.00015
    assert oi == 500_000_000.0


@pytest.mark.asyncio
async def test_retry_backoff_and_outage_isolation():
    flaky_okx = OKXAdapter(max_retries=2, client_factory=lambda _o: _MockCCXT(fail_times=1))
    ticker = await flaky_okx.fetch_ticker("BTC/USDT", MarketType.SPOT)
    assert ticker["last"] == 65000.0

    down_okx = OKXAdapter(
        max_retries=0,
        outage_threshold=2,
        cooldown_sec=30.0,
        client_factory=lambda _o: _MockCCXT(hard_fail=True),
    )
    healthy_bybit = BybitAdapter(client_factory=lambda _o: _MockCCXT())
    reg = ExchangeRegistry({ExchangeId.OKX: down_okx, ExchangeId.BYBIT: healthy_bybit})

    for _ in range(2):
        with pytest.raises(ccxt.NetworkError):
            await reg.get(ExchangeId.OKX).fetch_ticker("BTC/USDT", MarketType.SPOT)

    with pytest.raises(ExchangeOutageError):
        await reg.get(ExchangeId.OKX).fetch_ticker("BTC/USDT", MarketType.SPOT)

    assert reg.get(ExchangeId.OKX).health_status()["circuit_open"] is True
    # Bybit remains completely unaffected
    bybit_ticker = await reg.get(ExchangeId.BYBIT).fetch_ticker("BTC/USDT", MarketType.SPOT)
    assert bybit_ticker["last"] == 65000.0
    assert reg.get(ExchangeId.BYBIT).health_status()["healthy"] is True
