from pathlib import Path
from dataclasses import asdict
from decimal import Decimal
import sqlite3,json,hashlib,datetime
from app.treasury.legacy_pnl_backfill import replay_paper_orders
out=Path('/root/violet-release-audit/evidence')
paths={'pre_repair':Path('/root/p0p1-staging-20261113/paper_engine_pre_repair_20261113T160555Z.sqlite3'),'current':Path('/root/violet-release-audit/checkpoints/legacy.sqlite3')}
result={'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'files':{}}
connections={}
for label,path in paths.items():
 c=sqlite3.connect(f'file:{path}?mode=ro',uri=True);c.row_factory=sqlite3.Row;connections[label]=c
 replay=replay_paper_orders(c)
 result['files'][label]={'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'replay':asdict(replay),'integrity':c.execute('pragma integrity_check').fetchone()[0]}
pre=connections['pre_repair'];now=connections['current']
changed=[];absent=[]
for row in pre.execute('SELECT * FROM paper_orders'):
 other=now.execute('SELECT * FROM paper_orders WHERE order_id=?',(row['order_id'],)).fetchone()
 if other is None:absent.append(row['order_id'])
 elif dict(row)!=dict(other):changed.append(row['order_id'])
result['historical_source_orders_comparison']={'changed_ids':changed,'missing_ids':absent,'count':pre.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0]}
rows=result['files']['pre_repair']['replay']['missing_closures']
result['events']=[r|{'net_unattributed_usd':str(Decimal(r['gross_realized_usd'])-Decimal(r['exit_fee_usd']))} for r in rows]
(out/'pnl-event-reconstruction.json').write_text(json.dumps(result,indent=2))
print(json.dumps({'events':len(rows),'missing_source_orders':absent,'changed_source_orders':changed,'replayed_current_orders':result['files']['current']['replay']['replayed_orders']},indent=2))
