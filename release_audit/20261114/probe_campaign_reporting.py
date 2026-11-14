from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
import json,sqlite3,base64
from urllib.request import Request,build_opener,ProxyHandler
from urllib.error import HTTPError
pointer=json.loads(Path('/root/paper-maintenance-preview-data/active-violet-campaign.json').read_text())
p=Path(pointer['campaign_dir'])/'violet-all-cap.sqlite3'
c=sqlite3.connect(f'file:{p}?mode=ro',uri=True);c.row_factory=sqlite3.Row;c.execute('BEGIN')
def total(sql):return sum((Decimal(str(r[0])) for r in c.execute(sql)),Decimal(0))
initial=total('SELECT initial_equity_usd FROM paper_accounts');cash=total('SELECT cash_usd FROM paper_accounts')
fees=total('SELECT fee_usd FROM paper_fills');gross=total('SELECT gross_realized_usd FROM paper_realized_closures')
funding=total('SELECT signed_usd FROM paper_funding_settlements') if c.execute("SELECT 1 FROM sqlite_master WHERE name='paper_funding_settlements'").fetchone() else Decimal(0)
result={'observed_at':datetime.now(timezone.utc).isoformat(),'mode':'ACTUAL_PAPER_CAMPAIGN_READ_ONLY','actual_exchange_profit':False,'campaign_id':pointer['campaign_id'],'cash_equation':{'initial':str(initial),'cash':str(cash),'fees':str(fees),'gross_realized':str(gross),'funding':str(funding),'economic_difference':str(cash-initial-gross+fees-funding)},'source_counts':{'orders':c.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0],'fills':c.execute('SELECT COUNT(*) FROM paper_fills').fetchone()[0],'closures':c.execute('SELECT COUNT(*) FROM paper_realized_closures').fetchone()[0]}}
c.rollback();c.close()
creds=dict(s.split('=',1) for s in Path('/root/paper-maintenance-preview-data/preview-auth.env').read_text().splitlines() if '=' in s and not s.startswith('#'))
header='Basic '+base64.b64encode((creds['DASHBOARD_USER']+':'+creds['DASHBOARD_PASSWORD']).encode()).decode()
try:
 with build_opener(ProxyHandler({})).open(Request('http://127.0.0.3:8790/api/violet/pnl',headers={'Authorization':header}),timeout=15) as response:
  data=json.load(response)
 result['published']={'overview':data.get('overview'),'asset_realized_sum':sum(a['realized_pnl_usd'] for a in data.get('assets',[])),'asset_net_sum':sum(a['net_pnl_usd'] for a in data.get('assets',[])),'reconciliation':data.get('reconciliation')}
except HTTPError as exc:result['published']={'http_status':exc.code}
except Exception as exc:result['published']={'error_type':type(exc).__name__}
Path('/root/violet-release-audit/evidence/campaign-reporting-gap.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'source_economic_difference':result['cash_equation']['economic_difference'],'published_asset_realized_sum':result['published'].get('asset_realized_sum'),'published_overview_realized':(result['published'].get('overview') or {}).get('realized_pnl_usdt')}))
