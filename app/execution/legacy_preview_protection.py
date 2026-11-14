"""Protect existing legacy preview positions without starting new legacy entries."""
import asyncio
from datetime import datetime, timezone


async def protect_legacy_preview(service, registry, validator):
    positions = service.db.list_positions()
    targets = {(p.exchange,p.symbol,p.market_type) for p in positions}
    fresh = {}
    failures = []
    # Sequential venue requests stay bounded; only already open instruments are read.
    for exchange, symbol, market_type in sorted(targets,key=lambda row:tuple(str(x) for x in row)):
        try:
            snap = await service.scanner.scan_pair(registry.get(exchange),symbol,market_type,False)
            if snap is not None and validator(snap):
                fresh[f'{exchange.value}:{market_type.value}:{symbol}'] = snap
                # Exit promptly rather than waiting for every venue in this pass.
                service.paper_engine.mark_to_market_and_check_exits(fresh)
            else:
                failures.append(f'{exchange.value}:{symbol}')
        except Exception as exc:
            failures.append(f'{exchange.value}:{symbol}:{type(exc).__name__}')
    return {'mode':'PROTECTIVE_EXITS_ONLY', 'observed_at':datetime.now(timezone.utc).isoformat(),
            'open_positions':len(service.db.list_positions()), 'instruments_checked':len(targets),
            'fresh_instruments':len(fresh), 'failures':failures}
