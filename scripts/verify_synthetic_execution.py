"""EXPLICIT SYNTHETIC SIMULATION. Retained fixture ledgers; never public trades."""
import json
from pathlib import Path
from uuid import uuid4
from app.core.config import AllCapConfig
from app.exchanges.registry import ExchangeRegistry
from app.execution.all_cap_runner import AllCapPaperRunner, BANDS, POLICIES
from test_all_cap_paper import snapshot, signal

root = Path('verification/synthetic') / uuid4().hex
root.mkdir(parents=True)
results = []
for band in BANDS:
    for exit_name, price in [('stop_loss',18.9),('take_profit',24.1)]:
        ledger = root / f'{band.value}-{exit_name}'
        runner = AllCapPaperRunner(ledger,ExchangeRegistry(),AllCapConfig(enabled=True))
        snap = snapshot()
        sig = signal(snap)
        bot = runner.bot_id(band)
        size = runner.size(sig,snap,band)
        account = runner.db.get_account(bot)
        before = float(account['cash_usd'])
        order = runner.engine.submit_signal_order(bot,sig,size,1,snap,'synthetic-fixture',True)
        assert order.is_paper and order.filled_qty > 0
        # Worst-fill stop risk including conservative round-trip fee/slippage.
        risk = order.filled_qty * ((sig.entry_high-sig.stop_loss) + 2*sig.entry_high*(sig.estimated_fee_pct+sig.estimated_slippage_pct))
        assert risk <= before * POLICIES[band][3]
        duplicate = runner.engine.submit_signal_order(bot,sig,size,1,snap,'synthetic-fixture',True)
        assert duplicate.order_id == order.order_id
        runner.close()
        runner = AllCapPaperRunner(ledger,ExchangeRegistry(),AllCapConfig(enabled=True))
        assert len(runner.db.list_positions(bot)) == 1
        exits = runner.engine.mark_to_market_and_check_exits({'binance:spot:MIDCAP/USDT':snap.model_copy(update={'last_price':price})})
        assert len(exits)==1 and not runner.db.list_positions(bot)
        fills=runner.db.list_fills(limit=100)
        assert len(fills)==2 and all(f.is_paper for f in fills)
        assert len(runner.db.list_orders(limit=100))==2
        after=float(runner.db.get_account(bot)['cash_usd'])
        expected=sum((f.price*f.qty if f.side.value=='sell' else -f.price*f.qty)-f.fee_usd for f in fills)
        assert abs(after-before-expected)<.001
        assert runner.engine.reconcile_positions()['reconciled']
        results.append({'band':band.value,'exit':exit_name,'ledger':str(ledger/'violet-all-cap.sqlite3'),
                        'fills':len(fills),'risk_usd':risk,'risk_limit_usd':before*POLICIES[band][3],
                        'cash_before':before,'cash_after':after,'cash_reconciliation_error':after-before-expected,
                        'restart_recovery':True,'idempotent_submission':True,'paper_only':True})
        runner.close()
Path('verification/synthetic-execution.json').write_text(json.dumps({'classification':'SYNTHETIC FIXTURE ONLY','scenarios':results},indent=2)+'\n')
print(f'SYNTHETIC ONLY: {len(results)} scenarios; {sum(r["fills"] for r in results)} retained paper fills; all reconciled')
