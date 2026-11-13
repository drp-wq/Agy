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
