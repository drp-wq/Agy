from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.dashboard.mobile_portfolio import build_mobile_portfolio
from app.persistence.database import PaperDatabase
from app.core.models import ExchangeId, OrderStatus
from app.exchanges.registry import ExchangeRegistry
from app.execution.all_cap_runner import AllCapPaperRunner
from app.core.config import AllCapConfig
from app.scanner.market_cap import MarketCapBand
from test_all_cap_paper import snapshot, signal


def test_native_projection_matches_authoritative_fill_and_equity_without_mutation(tmp_path):
    runner = AllCapPaperRunner(tmp_path, ExchangeRegistry(), AllCapConfig(enabled=True, sleeve_capital_usd=100), campaign_id='fixture-500')
    try:
        bot = runner.bot_id(MarketCapBand.MID)
        snap = snapshot()
        order = runner.engine.submit_signal_order(bot_id=bot, signal=signal(snap),
            requested_notional_usd=20, snapshot=snap, client_order_id='native-fixture', leverage=1)
        assert order.status == OrderStatus.FILLED
        before = runner.db._conn.total_changes
        result = build_mobile_portfolio(runner)
        assert runner.db._conn.total_changes == before
        accounts = runner.db.list_accounts()
        cash = sum(Decimal(str(a['cash_usd'])) for a in accounts)
        reserved = sum(Decimal(str(a['reserved_margin_usd'])) for a in accounts)
        assert Decimal(result['cash_usdt']) == cash
        assert Decimal(result['available_cash_usdt']) == cash - reserved
        assert Decimal(result['reserved_margin_usdt']) == reserved
        assert Decimal(result['equity_usdt']) == cash
        assert Decimal(result['fees_usdt']) == Decimal(str(order.fee_usd))
        assert result['campaign_id'] == 'fixture-500' and result['source'] == 'authoritative_vps_campaign'
        assert len(result['positions']) == 1 and len(result['orders']) == 1
        assert sum(Decimal(s['available_usdt']) + Decimal(s['reserved_usdt']) + Decimal(s['unrealized_usdt']) for s in result['sleeves']) == Decimal(result['equity_usdt'])
    finally:
        runner.close()


def test_native_projection_rejects_demo_capital_or_drift_without_reset(tmp_path):
    db = PaperDatabase(tmp_path/'campaign.sqlite3')
    runner = SimpleNamespace(db=db, campaign_id='fixture')
    for i in range(5):db.ensure_account(f'bot-{i}', 'binance', 100)
    assert build_mobile_portfolio(runner)['initial_capital_usdt'] == '500'
    with db.transaction() as conn:conn.execute("UPDATE paper_accounts SET cash_usd=99 WHERE bot_id='bot-1'")
    with pytest.raises(HTTPException):build_mobile_portfolio(runner)
    assert db.get_account('bot-1')['cash_usd'] == 99
    with db.transaction() as conn:conn.execute('UPDATE paper_accounts SET initial_equity_usd=3200')
    with pytest.raises(HTTPException):build_mobile_portfolio(runner)
    assert sum(a['initial_equity_usd'] for a in db.list_accounts()) == 16000
    db.close()


def test_native_projection_cannot_advertise_paper_if_structural_lock_is_compromised(monkeypatch):
    from app.core import security
    monkeypatch.setattr(security, 'LIVE_EXECUTION_PERMANENTLY_DISABLED', False)
    with pytest.raises(security.LiveExecutionBlockedError):
        build_mobile_portfolio(SimpleNamespace())
