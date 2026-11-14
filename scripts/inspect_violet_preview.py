"""Read-only host verification; credentials are used in memory and never printed."""
import base64
import json
import sqlite3
from pathlib import Path
from urllib.request import build_opener, ProxyHandler, Request
from urllib.error import HTTPError

root=Path('/root/paper-maintenance-preview-data')
credentials=dict(line.split('=',1) for line in (root/'preview-auth.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
header='Basic '+base64.b64encode((credentials['DASHBOARD_USER']+':'+credentials['DASHBOARD_PASSWORD']).encode()).decode()
opener=build_opener(ProxyHandler({}))
result={}
for label,url,method,auth in [
    ('anonymous_api','http://127.0.0.3:8790/api/violet/all-cap','GET',False),
    ('authenticated_api','http://127.0.0.3:8790/api/violet/all-cap','GET',True),
    ('write_rejected','http://127.0.0.3:8790/api/violet/all-cap','POST',True),
    ('dashboard','https://preview.trade.187-52-118-25.sslip.io/aurora/','GET',True)]:
    request=Request(url,method=method,headers={'Authorization':header} if auth else {})
    try:
        with opener.open(request,timeout=15) as response:
            row={'status':response.status}
            if label=='authenticated_api': row['body']=json.load(response)
            result[label]=row
    except HTTPError as exc: result[label]={'status':exc.code}
    except Exception as exc: result[label]={'error_type':type(exc).__name__}
pointer_path = root / 'active-violet-campaign.json'
if pointer_path.exists():
    campaign_dir = Path(json.loads(pointer_path.read_text())['campaign_dir'])
else:
    campaign_dir = root / 'violet-all-cap'
path = campaign_dir / 'violet-all-cap.sqlite3'
with sqlite3.connect(f'file:{path}?mode=ro',uri=True) as db:
    tables={row[0] for row in db.execute("select name from sqlite_master where type='table'")}
    result['ledger_counts']={table:db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in ['paper_orders','paper_fills','paper_positions','paper_accounts'] if table in tables}
    result['integrity_check']=db.execute('pragma integrity_check').fetchone()[0]
Path('verification/72h/deployed-preview.json').parent.mkdir(parents=True, exist_ok=True)
Path('verification/72h/deployed-preview.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
