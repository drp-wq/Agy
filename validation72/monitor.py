#!/usr/bin/env python3
"""72-hour observational paper validation; no trade or exchange writes."""
import datetime as dt,json,os,sqlite3,subprocess,time,urllib.request
from pathlib import Path
root=Path('/root/multi-exchange-paper/validation72'); meta=root/'window.json'
window=json.loads(meta.read_text()); now=dt.datetime.now(dt.timezone.utc); end=dt.datetime.fromisoformat(window['end_utc'])
if now>end:
 (root/'complete.flag').touch(exist_ok=True)
 raise SystemExit(0)
record={'timestamp':now.isoformat(),'seconds_remaining':max(0,int((end-now).total_seconds()))}
def cmd(*args):
 try:
  p=subprocess.run(args,capture_output=True,text=True,timeout=7)
  return p.returncode==0 and p.stdout.strip()=='active'
 except Exception:return False
record['engine_active']=cmd('systemctl','is-active','multi-exchange-paper.service')
record['telegram_timer_active']=cmd('systemctl','is-active','multi-exchange-telegram.timer')
record['octobot_observer_active']=cmd('systemctl','is-active','octobot-paper-observer.timer')
try:
 p=subprocess.run(['docker','inspect','--format','{{.State.Health.Status}}','octobot-paper'],capture_output=True,text=True,timeout=7)
 record['octobot_health']=p.stdout.strip() if p.returncode==0 else 'unavailable'
except Exception:record['octobot_health']='unavailable'
for name,url in [('public_health','https://trade.187-52-118-25.sslip.io/api/health'),('private_auth_guard','https://trade.187-52-118-25.sslip.io/api/miniapp/health')]:
 try:
  with urllib.request.urlopen(url,timeout=7) as r:record[name]=r.status
 except urllib.error.HTTPError as e:record[name]=e.code
 except Exception:record[name]=None
try:
 c=sqlite3.connect('file:/root/multi-exchange-paper/data/paper_engine.sqlite3?mode=ro',uri=True,timeout=5)
 for table in ['paper_accounts','paper_orders','paper_fills','paper_positions']:
  record[table+'_count']=c.execute('select count(*) from '+table).fetchone()[0]
 record['exchange_orders']={k:v for k,v in c.execute('select exchange,count(*) from paper_orders group by exchange')}
 record['order_status']={k:v for k,v in c.execute('select status,count(*) from paper_orders group by status')}
 record['paper_fees_usd']=round(c.execute('select coalesce(sum(fee_usd),0) from paper_fills').fetchone()[0],6)
 record['paper_realized_pnl_usd']=round(c.execute('select coalesce(sum(realized_pnl_usd),0) from paper_accounts').fetchone()[0],4)
 record['paper_cash_usd']=round(c.execute('select coalesce(sum(cash_usd),0) from paper_accounts').fetchone()[0],4)
 record['all_orders_paper']=c.execute('select count(*) from paper_orders where is_paper != 1').fetchone()[0]==0
 record['all_fills_paper']=c.execute('select count(*) from paper_fills where is_paper != 1').fetchone()[0]==0
 c.close()
except Exception as exc:record['ledger_error_type']=type(exc).__name__
with (root/'observations.jsonl').open('a') as f:f.write(json.dumps(record,separators=(',',':'))+'\n')
tmp=root/'latest.tmp';tmp.write_text(json.dumps(record,indent=2));os.chmod(tmp,0o600);tmp.replace(root/'latest.json')
