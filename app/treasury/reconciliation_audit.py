"""Read-only evidence coverage report. Balanced journals != complete PnL."""
from decimal import Decimal

def audit_paper_pnl_coverage(db):
    with db._lock:
        c=db._conn
        scalar=lambda sql: c.execute(sql).fetchone()[0]
        fills=scalar('SELECT COUNT(*) FROM paper_fills WHERE is_paper=1')
        fees=scalar("SELECT COUNT(*) FROM treasury_attribution_events WHERE idempotency_key LIKE 'paper_fill_fee:%'")
        closures=scalar('SELECT COUNT(*) FROM paper_realized_closures WHERE is_paper=1')
        closure_attr=scalar("SELECT COUNT(*) FROM treasury_attribution_events WHERE idempotency_key LIKE 'paper_close_pnl:%'")
        funding_table=scalar("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='paper_funding_settlements'")
        funding=scalar('SELECT COUNT(*) FROM paper_funding_settlements WHERE is_paper=1') if funding_table else 0
        funding_attr=scalar("SELECT COUNT(*) FROM treasury_attribution_events WHERE idempotency_key LIKE 'paper_funding:%'")
        account_pnl=sum((Decimal(str(r[0])) for r in c.execute('SELECT realized_pnl_usd FROM paper_accounts')),Decimal('0'))
        attributed_pnl=sum((Decimal(r[0]) for r in c.execute("SELECT amount FROM treasury_attribution_events WHERE event_category IN ('trade_realized_pnl','trading_fee','funding')")),Decimal('0'))
    gap=account_pnl-attributed_pnl
    coverage=(fees==fills and closure_attr==closures and funding_attr==funding)
    return {'paper_fills':fills,'fee_attributions':fees,'recorded_closures':closures,
            'closure_attributions':closure_attr,'funding_settlements':funding,
            'funding_attributions':funding_attr,'account_realized_usd':str(account_pnl),
            'attributed_realized_usd':str(attributed_pnl),'unexplained_usd':str(gap),
            'recorded_evidence_coverage_ok':coverage,
            'historical_completeness_certified':False,
            'reason':'Legacy closes and funding may lack durable source events; no fabricated backfill'}
