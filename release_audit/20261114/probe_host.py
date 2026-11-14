import json,sqlite3,base64,datetime
from pathlib import Path
from urllib.request import build_opener,ProxyHandler,Request
from urllib.error import HTTPError
out=Path('/root/violet-release-audit/evidence')
creds=dict(line.split('=',1) for line in Path('/root/paper-maintenance-preview-data/preview-auth.env').read_text().splitlines() if '=' in line and not line.startswith('#'))
header='Basic '+base64.b64encode((creds['DASHBOARD_USER']+':'+creds['DASHBOARD_PASSWORD']).encode()).decode()
opener=build_opener(ProxyHandler({})); result={'timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat()}
for name,url,method,auth in [('production','https://trade.187-52-118-25.sslip.io/','GET',False),('anonymous_campaign','http://127.0.0.3:8790/api/violet/all-cap','GET',False),('campaign','http://127.0.0.3:8790/api/violet/all-cap','GET',True),('write_campaign','http://127.0.0.3:8790/api/violet/all-cap','POST',True),('android_exchange','https://prottushjd.tech/auth/mobile/exchange','POST',False),('android_login','https://prottushjd.tech/auth/login','POST',False),('invalid_bearer','https://prottushjd.tech/api/violet/all-cap','GET','invalid'),('preview','https://preview.trade.187-52-118-25.sslip.io/aurora/','GET',True)]:
 headers={'Authorization':header} if auth is True else ({'Authorization':'Bearer invalid-audit-token'} if auth else {})
 try:
  with opener.open(Request(url,method=method,headers=headers),timeout=15) as response:
   row={'status':response.status,'final_url':response.url}
   if name=='campaign':row['body']=json.load(response)
   result[name]=row
 except HTTPError as exc:result[name]={'status':exc.code}
 except Exception as exc:result[name]={'error_type':type(exc).__name__}
(out/'endpoint-probes.json').write_text(json.dumps(result,indent=2))
print(json.dumps({k:({a:b for a,b in v.items() if a!='body'} if isinstance(v,dict) else v) for k,v in result.items()},indent=2))
