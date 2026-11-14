"""Reproducible isolated funding audit; never opens an operating campaign DB."""
from __future__ import annotations

import argparse
import json
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.core.config import RiskPolicyConfig, UnifiedTreasuryConfig
from app.core.models import ExchangeId, MarketType, OrderStatus, PaperOrder, Side
from app.execution.paper_engine import PaperExecutionEngine
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.treasury.funding_coordinator import settle_captured_funding
from app.treasury.ledger import UnifiedTreasuryLedger


def run():
    boundary=datetime(2026,11,14,8,tzinfo=timezone.utc)
    cases=[]
    for sign in (1,-1):
        for rate in ('0.001','-0.001','0'):
            with tempfile.TemporaryDirectory(prefix='violet-funding-audit-') as directory:
                path=Path(directory)/'synthetic.sqlite3'
                db=PaperDatabase(path)
                db.ensure_account('fixture','okx',500)
                engine=PaperExecutionEngine(db,GlobalRiskGuardian(RiskPolicyConfig()))
                treasury=UnifiedTreasuryLedger(db,UnifiedTreasuryConfig(enabled=True))
                orders=[]
                for name,side,at in (
                    ('open',Side.BUY if sign==1 else Side.SELL,boundary-timedelta(seconds=1)),
                    ('close',Side.SELL if sign==1 else Side.BUY,boundary)):
                    order=PaperOrder(order_id=name,client_order_id=name,bot_id='fixture',signal_id=name,
                        exchange=ExchangeId.OKX,symbol='BTC/USDT',market_type=MarketType.PERP,
                        side=side,requested_qty=2,filled_qty=2,avg_fill_price=100,
                        fee_usd=.2,leverage=2,status=OrderStatus.FILLED,
                        created_at=at,updated_at=at,is_paper=True)
                    with db.transaction() as conn:
                        db.save_order(order,conn=conn)
                        engine._apply_fill_to_position_and_account(order,100,conn=conn)
                    orders.append(order.model_dump(mode='json'))
                args=dict(bot_id='fixture',exchange='okx',symbol='BTC/USDT',
                    settlement_at=boundary,interval_seconds=28800,rate=rate,mark_price='100',
                    source={'endpoint':'fixture://public-funding','venue_event_id':'synthetic-0800'},
                    now=boundary+timedelta(days=1))
                posted=settle_captured_funding(db,treasury,**args)
                expected=-Decimal(sign)*2*100*Decimal(rate)
                assert Decimal(posted['amount_usd'])==expected
                expected_cash=Decimal('500')-Decimal('.4')+expected
                account=dict(db.get_account('fixture'))
                assert abs(Decimal(str(account['cash_usd']))-expected_cash)<Decimal('.00000001')
                assert account['reserved_margin_usd']==0 and db.list_positions('fixture')==[]
                db.close()
                db=PaperDatabase(path)
                treasury=UnifiedTreasuryLedger(db,UnifiedTreasuryConfig(enabled=True))
                assert settle_captured_funding(db,treasury,**args)['duplicate']
                later=settle_captured_funding(db,treasury,**(args | {
                    'settlement_at':boundary+timedelta(hours=8),
                    'source':{'endpoint':'fixture://public-funding','venue_event_id':'synthetic-1600'}}))
                assert Decimal(later['amount_usd'])==0
                assert db.get_account('fixture')['cash_usd']==account['cash_usd']
                cases.append({'direction':'long' if sign==1 else 'short','rate':rate,
                    'expected_funding_usdt':str(expected),'expected_cash_usdt':str(expected_cash),
                    'orders':orders,'funding':posted,'closed_interval':later,'account':account,
                    'exposure_events':[dict(r) for r in db._conn.execute('SELECT * FROM paper_exposure_events ORDER BY seq')],
                    'settlements':[dict(r) for r in db._conn.execute('SELECT * FROM paper_funding_settlements ORDER BY settlement_id')],
                    'interval_observations':[dict(r) for r in db._conn.execute('SELECT * FROM paper_funding_interval_observations ORDER BY settlement_id,evidence_hash')],
                    'journal_events':db.list_ledger_events(),
                    'cash_equation':'500 - 0.4 trading fees + signed funding; no trade gain/loss',
                    'restart_duplicate_verified':True})
                db.close()
    return {'observed_at':datetime.now(timezone.utc).isoformat(),
            'mode':'SYNTHETIC_PAPER_FIXTURE','actual_exchange_profit':False,
            'venue_scheduler_verified':False,'cases':cases}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=run()
    args.out.write_text(json.dumps(result,indent=2))
    print(json.dumps({'mode':result['mode'],'cases_verified':len(result['cases']),
                      'actual_exchange_profit':False,'output':str(args.out)}))
