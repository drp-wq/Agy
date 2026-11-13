from app.persistence.database import PaperDatabase
from app.treasury.reconciliation_audit import audit_paper_pnl_coverage

def test_audit_never_claims_historical_completeness(tmp_path):
    db=PaperDatabase(tmp_path/'audit.sqlite3')
    db.ensure_account('bot','binance',1000)
    db.update_account_cash('bot',-2.5,-2.5)
    result=audit_paper_pnl_coverage(db)
    assert result['historical_completeness_certified'] is False
    assert result['recorded_evidence_coverage_ok'] is True
    assert result['unexplained_usd']=='-2.5'
    db.close()
