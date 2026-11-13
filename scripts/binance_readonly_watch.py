#!/usr/bin/env python3
"""Binance public-data and optional authenticated read-only observer. Never submits orders."""
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from app.core.config import load_environment
from app.exchanges.binance import BinanceAdapter
from app.core.models import MarketType

OUT = Path('/root/multi-exchange-paper/data/binance_readonly_watch.json')

async def main():
    env = load_environment(require_secrets_file=True)
    adapter = BinanceAdapter(credentials=env.credentials.get('binance'), max_retries=1)
    report = {'timestamp_utc': datetime.now(timezone.utc).isoformat(), 'live_execution': 'DISABLED', 'order_submission': 'FORBIDDEN', 'order_history_reconciled': False, 'order_history_status': 'NOT_IMPLEMENTED'}
    try:
        ticker = await adapter.fetch_ticker('BTC/USDT', MarketType.SPOT)
        report['public_market'] = {'status': 'ok', 'symbol': 'BTC/USDT', 'price_available': bool(ticker.get('last'))}
    except Exception as exc:
        report['public_market'] = {'status': 'error', 'error_type': type(exc).__name__}
    try:
        bal = await adapter.fetch_read_only_balance()
        report['account'] = {'status': 'ok' if bal.get('configured') else 'unconfigured', 'asset_count': len(bal.get('balances', {}))}
    except Exception as exc:
        report['account'] = {'status': 'error', 'error_type': type(exc).__name__}
    finally:
        adapter.close()
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(report, indent=2)+'\n')
    os.chmod(tmp, 0o600)
    os.replace(tmp, OUT)
    print(json.dumps(report))

if __name__ == '__main__':
    asyncio.run(main())
