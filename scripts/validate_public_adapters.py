"""Bounded credential-free adapter probe. No orders, no private endpoints."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from app.exchanges.registry import ExchangeRegistry
from app.core.models import MarketType
from app.execution.all_cap_runner import source_age

async def main():
    registry = ExchangeRegistry.violet_public()
    results = {'observed_at':datetime.now(timezone.utc).isoformat(), 'mode':'PUBLIC_DATA_ONLY', 'gate_io':'candidate_public_adapter'}
    try:
        for name, adapter in registry.all().items():
            row = results[name.value] = {}
            for label, call in [
                ('markets', lambda: adapter.load_markets()),
                ('spec', lambda: adapter.get_validated_spot_spec('BTC/USDT')),
                ('ticker', lambda: adapter.fetch_ticker('BTC/USDT',MarketType.SPOT)),
                ('book', lambda: adapter.fetch_order_book('BTC/USDT',MarketType.SPOT)),
                ('candles', lambda: adapter.fetch_ohlcv('BTC/USDT',MarketType.SPOT,limit=120))]:
                try:
                    value = await call()
                    now = datetime.now(timezone.utc)
                    if label == 'markets':
                        row[label] = {'count':len(value),'btc_spot': bool(value.get('BTC/USDT',{}).get('spot'))}
                    elif label == 'ticker':
                        row[label] = {k:value.get(k) for k in ['symbol','timestamp','last','bid','ask','quoteVolume','source_timestamp_kind']}
                        row[label]['fresh'] = source_age(value.get('timestamp'),now,30)
                        row[label]['age_seconds'] = now.timestamp()-value['timestamp']/1000 if value.get('timestamp') else None
                    elif label == 'book':
                        row[label] = {'source_timestamp_ms':value.source_timestamp_ms,'fresh':source_age(value.source_timestamp_ms,now,30),'bid':value.best_bid,'ask':value.best_ask,'bids':len(value.bids),'asks':len(value.asks)}
                    elif label == 'candles':
                        row[label] = {'count':len(value),'fresh':bool(value) and source_age(value[-1].timestamp,now,930), 'continuous':all(b.timestamp-a.timestamp==900000 for a,b in zip(value,value[1:]))}
                    else:
                        row[label] = value.model_dump(mode='json')
                except Exception as exc:
                    row[label] = {'error_type':type(exc).__name__}
            row['health'] = adapter.health_status()
    finally:
        registry.close_all()
    Path('verification/72h/public-adapters.json').write_text(json.dumps(results,indent=2)+'\n')
    print(json.dumps(results,indent=2))

if __name__ == '__main__':
    with asyncio.Runner() as runtime:
        loop=runtime.get_loop()
        def pulse(): loop.call_later(.05,pulse)
        pulse()
        runtime.run(main())
