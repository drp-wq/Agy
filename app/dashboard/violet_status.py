"""Authenticated, read-only Violet Lab diagnostics using existing dashboard auth."""
from fastapi import Depends
from starlette.concurrency import run_in_threadpool
from app.dashboard.violet_solana_bridge import solana_summary
from app.dashboard.auth import make_auth_dependency


def install_violet_status(app, runner, auth_config):
    @app.get('/api/violet/all-cap', dependencies=[Depends(make_auth_dependency(auth_config))])
    async def all_cap_status():
        return runner.status() if hasattr(runner, 'status') else runner.state

    @app.get('/api/violet/solana', dependencies=[Depends(make_auth_dependency(auth_config))])
    async def solana_research_status():
        return await run_in_threadpool(solana_summary)

    @app.get('/api/violet/research', dependencies=[Depends(make_auth_dependency(auth_config))])
    async def combined_research():
        state = runner.status() if hasattr(runner, 'status') else runner.state
        return {'mode':'READ_ONLY_PAPER','live_execution_enabled':False,
                'violet': {'campaign':state.get('campaign'), 'worker_health':state.get('worker_health'),
                           'cycles':state.get('cycles'), 'source':'isolated_violet_paper_ledger'},
                'solana': await run_in_threadpool(solana_summary),
                'accounting':'SEPARATE_PAPER_LEDGERS_NO_TRANSFERS'}
