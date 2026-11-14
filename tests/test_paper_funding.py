from decimal import Decimal
import pytest
from app.persistence.database import PaperDatabase
from app.core.config import UnifiedTreasuryConfig
from app.treasury.ledger import UnifiedTreasuryLedger
from app.treasury.paper_funding import settle_paper_funding

def test_evidenced_funding_idempotent_and_reconciled(tmp_path):
    db=PaperDatabase(tmp_path/'funding.sqlite3')
    db.ensure_account('bot','bybit',1000)
    treasury=UnifiedTreasuryLedger(db,UnifiedTreasuryConfig(enabled=True))
    args=dict(settlement_id='funding_001',bot_id='bot',exchange='bybit',
              symbol='BTC/USDT',signed_usd='-1.25',evidence='paper_fixture_001')
    assert settle_paper_funding(db,treasury,**args)['duplicate'] is False
    assert settle_paper_funding(db,treasury,**args)['duplicate'] is True
    assert db.get_account('bot')['cash_usd']==998.75
    assert db.get_account('bot')['realized_pnl_usd']==-1.25
    with pytest.raises(ValueError):
        settle_paper_funding(db,treasury,**(args|{'signed_usd':'9'}))
    assert Decimal(treasury.get_attribution_summary('exchange')['bybit']['funding_usd'])==Decimal('-1.25')
    db.close()

from datetime import datetime, timezone
from app.treasury.paper_funding import settle_position_funding
from app.treasury.reconciliation_audit import audit_paper_pnl_coverage


@pytest.mark.parametrize('qty', ['2', '-2'])
@pytest.mark.parametrize('rate', ['0.001', '-0.001', '0'])
def test_linear_funding_economics_and_restart(tmp_path, qty, rate):
    path = tmp_path / 'linear.sqlite3'
    db = PaperDatabase(path)
    db.ensure_account('bot', 'bybit', 500)
    treasury = UnifiedTreasuryLedger(db, UnifiedTreasuryConfig(enabled=True))
    args = dict(bot_id='bot', exchange='bybit', symbol='BTC/USDT',
                settlement_at=datetime(2026, 11, 14, 8, tzinfo=timezone.utc),
                interval_seconds=28800, rate=rate, mark_price='100',
                exposure_history=[{'timestamp': '2026-11-14T07:59:59+00:00', 'signed_qty': qty},
                                  {'timestamp': '2026-11-14T08:00:00+00:00', 'signed_qty': '99'}],
                evidence='synthetic audited interval')
    expected = -Decimal(qty) * Decimal('100') * Decimal(rate)
    result = settle_position_funding(db, treasury, **args)
    assert Decimal(result['amount_usd']) == expected
    account = db.get_account('bot')
    assert account['cash_usd'] == pytest.approx(500 + float(expected))
    assert account['realized_pnl_usd'] == pytest.approx(float(expected))
    assert account['reserved_margin_usd'] == 0
    assert db.list_positions('bot') == []
    assert audit_paper_pnl_coverage(db)['recorded_evidence_coverage_ok']
    db.close()
    db = PaperDatabase(path)
    treasury = UnifiedTreasuryLedger(db, UnifiedTreasuryConfig(enabled=True))
    assert settle_position_funding(db, treasury, **args)['duplicate']
    assert db.get_account('bot')['cash_usd'] == pytest.approx(500 + float(expected))
    args['settlement_at'] = datetime(2026, 11, 14, 16, tzinfo=timezone.utc)
    # A missed interval is independently posted with the position effective then.
    settle_position_funding(db, treasury, **args)
    second = -Decimal('99') * Decimal('100') * Decimal(rate)
    assert db.get_account('bot')['cash_usd'] == pytest.approx(500 + float(expected + second))
    assert audit_paper_pnl_coverage(db)['recorded_evidence_coverage_ok']
    db.close()


def test_funding_attribution_failure_rolls_back_cash(tmp_path, monkeypatch):
    db = PaperDatabase(tmp_path / 'atomic.sqlite3')
    db.ensure_account('bot', 'bybit', 500)
    treasury = UnifiedTreasuryLedger(db, UnifiedTreasuryConfig(enabled=True))
    # Table creation is included in the transaction as well.
    def fail(**kwargs):
        raise RuntimeError('simulated attribution crash')
    monkeypatch.setattr(treasury, 'record_attribution', fail)
    with pytest.raises(RuntimeError):
        settle_paper_funding(db, treasury, settlement_id='crash', bot_id='bot',
                             exchange='bybit', symbol='BTC/USDT', signed_usd='-1', evidence='fixture')
    assert db.get_account('bot')['cash_usd'] == 500
    assert db.get_account('bot')['realized_pnl_usd'] == 0
    assert db._conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='paper_funding_settlements'").fetchone()[0] == 0
    db.close()


def test_funding_rejects_preexisting_conflicting_attribution(tmp_path):
    from app.treasury.models import AttributionCategory
    db = PaperDatabase(tmp_path / 'conflict.sqlite3')
    db.ensure_account('bot', 'bybit', 500)
    treasury = UnifiedTreasuryLedger(db, UnifiedTreasuryConfig(enabled=True))
    treasury.record_attribution(idempotency_key='paper_funding:collision', agent_id='bot',
        exchange='bybit', account_id='bybit:paper', strategy='fixture', symbol='BTC/USDT',
        asset='USD', category=AttributionCategory.FUNDING, amount='9')
    with pytest.raises(ValueError, match='Conflicting existing'):
        settle_paper_funding(db, treasury, settlement_id='collision', bot_id='bot',
            exchange='bybit', symbol='BTC/USDT', signed_usd='-1', evidence='fixture')
    assert db.get_account('bot')['cash_usd'] == 500
    db.close()
