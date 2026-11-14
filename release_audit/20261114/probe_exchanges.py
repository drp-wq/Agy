import asyncio,json,datetime
from pathlib import Path
from app.exchanges.readonly_telemetry import ReadOnlyExchangeTelemetry
async def main():
 t=ReadOnlyExchangeTelemetry(timeout_sec=20)
 result=await t.refresh(force=True)
 Path('/root/violet-release-audit/evidence/exchange-private.json').write_text(json.dumps({'observed_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mode':'READ_ONLY','exchanges':result},indent=2))
 print(json.dumps({k:{f:v.get(f) for f in ['auth_state','api_permissions','endpoint_status','public_feed_ok']} for k,v in result.items()},indent=2))
asyncio.run(main())
