from app.core.models import ExchangeId
from app.core.config import ExchangeCredentials, EngineConfig
from app.exchanges.binance import BinanceAdapter
from app.exchanges.registry import ExchangeRegistry
import pytest

def test_binance_registered_and_no_live_order_submission():
    registry = ExchangeRegistry.from_credentials({})
    assert ExchangeId.BINANCE in registry.all()
    adapter = registry.get(ExchangeId.BINANCE)
    assert adapter.health_status()["read_only_enforced"] is True
    assert "binance" in EngineConfig().exchanges
    registry.close_all()
