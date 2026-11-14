import asyncio,json,datetime
from pathlib import Path
from app.exchanges.readonly_telemetry import ReadOnlyExchangeTelemetry
async def main():
 t=ReadOnlyExchangeTelemetry(timeout_sec=12)
 results={}
 for exchange,calls in [('binance',[('futures_positions','fapiprivatev3_get_positionrisk',{}),('futures_balance','fapiprivatev3_get_balance',{}),('transfers_spot_to_futures_7d','sapi_get_asset_transfer',{'type':'MAIN_UMFUTURE','size':100}),('transfers_futures_to_spot_7d','sapi_get_asset_transfer',{'type':'UMFUTURE_MAIN','size':100})]),('okx',[('account_permissions','private_get_account_config',{})]),('bybit',[('key_permissions','private_get_v5_user_query_api',{})])]:
  c,session=t._build_client(exchange)
  try:
   for label,method,args in calls:
    try:
     raw=await asyncio.wait_for(getattr(c,method)(args),15)
     row={'status':'VERIFIED','response_count':len(raw) if isinstance(raw,list) else None}
     if 'transfers_' in label:row['total']=raw.get('total');row['returned_rows']=len(raw.get('rows',[]));row['scope']='default seven days; not complete account history'
     if exchange=='okx':row['permissions']=[{k:v.get(k) for k in ['perm','acctLv','posMode']} for v in raw.get('data',[])]
     if exchange=='bybit':row['permissions']={k:raw.get('result',{}).get(k) for k in ['readOnly','permissions']}
     results[f'{exchange}:{label}']=row
    except Exception as exc:results[f'{exchange}:{label}']={'status':'BLOCKED','error_type':type(exc).__name__}
  finally:
   if c:await c.close()
   if session:await session.close()
 Path('/root/violet-release-audit/evidence/private-endpoints.json').write_text(json.dumps({'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'results':results},indent=2))
 print(json.dumps(results,indent=2))
asyncio.run(main())
