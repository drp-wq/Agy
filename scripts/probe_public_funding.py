"""Fetch public Binance funding evidence without credentials or a campaign DB."""
from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.exchanges.binance import BinanceAdapter
from app.exchanges.funding_history import fetch_funding_evidence


async def run(symbol,hours):
    observed=datetime.now(timezone.utc)
    until=observed-timedelta(minutes=1)
    adapter=BinanceAdapter(credentials=None,max_retries=1)
    try:
        result=await fetch_funding_evidence(adapter,symbol=symbol,
            coverage_from=until-timedelta(hours=hours),until=until)
        return {'status':'VERIFIED_PUBLIC_HISTORY_RANGE','observed_at':observed.isoformat(),
                'execution':'PUBLIC_GET_ONLY_NO_DATABASE_NO_CREDENTIALS',
                'actual_exchange_profit':False,'symbol':symbol,'batch':asdict(result)}
    except Exception as exc:
        return {'status':'BLOCKED','observed_at':observed.isoformat(),
                'error_type':type(exc).__name__,'reason':'public_history_probe_failed'}
    finally:
        adapter.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--symbol',default='BTC/USDT')
    parser.add_argument('--hours',type=int,default=24,choices=range(1,169))
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    result=asyncio.run(run(args.symbol,args.hours))
    args.out.write_text(json.dumps(result,indent=2,default=lambda value:value.isoformat()))
    print(json.dumps({'status':result['status'],'events':len(result.get('batch',{}).get('events',[])),
                      'output':str(args.out)}))
    raise SystemExit(0 if result['status']=='VERIFIED_PUBLIC_HISTORY_RANGE' else 1)
