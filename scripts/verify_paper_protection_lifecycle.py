"""Credential-free synthetic entry -> staged exit -> accounting evidence.

Run from the repository root with PYTHONPATH=.; never targets an operating DB.
All results are paper fixture outcomes, not observed exchange profit.
"""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json

from app.core.config import RiskPolicyConfig
from app.core.models import Direction, ExchangeId, MarketType, MarketSnapshot, StrategySignal, OrderStatus
from app.execution.paper_engine import PaperExecutionEngine
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian


def run():
    report = {'mode': 'SYNTHETIC_PAPER_FIXTURE', 'actual_exchange_profit': False,
              'observed_at': datetime.now(timezone.utc).isoformat(), 'cases': []}
    for market, direction in [(MarketType.SPOT, Direction.LONG), (MarketType.PERP, Direction.LONG), (MarketType.PERP, Direction.SHORT)]:
        with TemporaryDirectory(prefix='violet-protection-proof-') as tmp:
            db = PaperDatabase(Path(tmp)/'fixture.sqlite3')
            try:
                engine = PaperExecutionEngine(db, GlobalRiskGuardian(RiskPolicyConfig()))
                engine.ensure_bot_account('fixture', ExchangeId.OKX, 500)
                def quote(price):
                    return MarketSnapshot(exchange=ExchangeId.OKX,symbol='FIXTURE/USDT',market_type=market,
                        timestamp=datetime.now(timezone.utc),last_price=price,bid=price,ask=price,
                        spread_bps=2,volume_24h_usd=1e8,order_book_depth_usd=1e5,
                        liquidity_score=1,bids=[(price,100)],asks=[(price,100)])
                targets = [110,120] if direction==Direction.LONG else [90,80]
                signal = StrategySignal(strategy_name='synthetic_protection_evidence',exchange=ExchangeId.OKX,
                    symbol='FIXTURE/USDT',market_type=market,direction=direction,entry_low=99,entry_high=101,
                    stop_loss=90 if direction==Direction.LONG else 110,take_profit_targets=targets,
                    invalidation_condition='fixture only',estimated_fee_pct=.001,estimated_slippage_pct=0,
                    risk_reward_ratio=2,confidence_score=.8,rationale='not an observed trading result')
                entry = engine.submit_signal_order('fixture',signal,100,2 if market==MarketType.PERP else 1,quote(100),'fixture-entry')
                assert entry.status==OrderStatus.FILLED
                stages = []
                for price in targets:
                    tick = quote(price)
                    orders = engine.mark_to_market_and_check_exits({f'okx:{market.value}:FIXTURE/USDT':tick})
                    assert len(orders)==1 and orders[0].filled_qty==.5
                    assert not engine.mark_to_market_and_check_exits({f'okx:{market.value}:FIXTURE/USDT':tick})
                    stages.append({'exit_order':orders[0].model_dump(mode='json'),
                                   'account':db.get_account('fixture'),
                                   'remaining_positions':[p.model_dump(mode='json') for p in db.list_positions()]})
                fees=sum((Decimal(str(f.fee_usd)) for f in db.list_fills(limit=100)),Decimal(0))
                gross=sum((Decimal(str(c['gross_realized_usd'])) for c in db.list_realized_closures()),Decimal(0))
                account=db.get_account('fixture')
                assert abs(Decimal(str(account['cash_usd']))-(Decimal(500)+gross-fees))<Decimal('0.000001')
                assert account['reserved_margin_usd']==0 and not db.list_positions()
                assert engine.reconcile_positions()['reconciled']
                report['cases'].append({'market':market.value,'direction':direction.value,'initial_usdt':500,
                    'entry_order':entry.model_dump(mode='json'),'stages':stages,
                    'fills':[f.model_dump(mode='json') for f in db.list_fills(limit=100)],
                    'closures':db.list_realized_closures(),'synthetic_total_fees_usd':str(fees),
                    'synthetic_gross_realized_usd':str(gross),'final_account':account,
                    'reconciliation':engine.reconcile_positions()})
            finally:
                db.close()
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=run()
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'mode':result['mode'],'cases_verified':len(result['cases']),
                      'actual_exchange_profit':False,'output':str(args.out)}))
