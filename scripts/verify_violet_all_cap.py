"""One bounded public-data-only Violet Lab cycle; writes only a separate local verification ledger."""
import asyncio
import json
from pathlib import Path

from app.core.config import AllCapConfig
from app.exchanges.registry import ExchangeRegistry
from app.execution.all_cap_runner import AllCapPaperRunner


async def main():
    registry = ExchangeRegistry.violet_public()
    runner = AllCapPaperRunner(Path('verification/72h/public-cycle'), registry,
                               AllCapConfig(enabled=True,cycle_timeout_sec=90))
    try:
        state = await runner.run_cycle()
        result = {'state': state, 'ledger': str(runner.db.path) if hasattr(runner.db,'path') else str(runner.root/'violet-all-cap.sqlite3'),
                  'accounts': runner.db.list_accounts(),
                  'orders': [o.model_dump(mode='json') for o in runner.db.list_orders(limit=100)],
                  'fills': [f.model_dump(mode='json') for f in runner.db.list_fills(limit=100)]}
        Path('verification/72h/public-cycle.json').write_text(json.dumps(result,indent=2,default=str)+'\n')
        print(json.dumps(state,default=str))
    finally:
        runner.close()
        registry.close_all()


if __name__ == '__main__':
    # This sandbox can lose cross-thread selector wakeups during executor shutdown.
    # A timer bounds selector sleeps without altering data, strategies, or risk gates.
    with asyncio.Runner() as runtime:
        loop = runtime.get_loop()
        def pulse():
            loop.call_later(.05, pulse)
        pulse()
        runtime.run(main())
