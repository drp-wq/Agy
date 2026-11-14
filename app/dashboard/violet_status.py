"""Authenticated, read-only Violet Lab diagnostics and paper closed-loop router endpoints."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.dashboard.auth import make_auth_dependency
from app.dashboard.violet_solana_bridge import solana_summary
from app.treasury.closed_loop_router import (
    ClosedLoopAssetRouter,
    ClosedLoopRiskBlockedError,
)
from app.treasury.models import LiveTransferBlockedError, TreasuryError


class SimulateTransferRequest(BaseModel):
    source_venue: str = Field(default="hub_binance_master", max_length=64)
    destination_venue: str = Field(default="cex_binance", max_length=64)
    amount_usd: str = Field(default="250.00", max_length=32)
    idempotency_key: str = Field(..., min_length=1, max_length=128)
    reason: str = Field(default="ai_brain_paper_rebalance", max_length=120)
    requested_by: str = Field(default="unified_ai_brain", max_length=64)


def install_violet_status(app, runner, auth_config, closed_loop_router: ClosedLoopAssetRouter | None = None):
    if getattr(app.state, "closed_loop_router", None) is None:
        app.state.closed_loop_router = closed_loop_router or ClosedLoopAssetRouter()

    auth_dep = Depends(make_auth_dependency(auth_config))

    @app.get('/api/violet/all-cap', dependencies=[auth_dep])
    async def all_cap_status():
        return runner.status() if hasattr(runner, 'status') else runner.state

    @app.get('/api/violet/solana', dependencies=[auth_dep])
    async def solana_research_status():
        return await run_in_threadpool(solana_summary)

    @app.get('/api/violet/research', dependencies=[auth_dep])
    async def combined_research():
        state = runner.status() if hasattr(runner, 'status') else runner.state
        return {'mode':'READ_ONLY_PAPER','live_execution_enabled':False,
                'violet': {'campaign':state.get('campaign'), 'worker_health':state.get('worker_health'),
                           'cycles':state.get('cycles'), 'source':'isolated_violet_paper_ledger'},
                'solana': await run_in_threadpool(solana_summary),
                'accounting':'SEPARATE_PAPER_LEDGERS_NO_TRANSFERS'}

    @app.get('/api/violet/unified-account', dependencies=[auth_dep])
    async def unified_account_status():
        router: ClosedLoopAssetRouter = app.state.closed_loop_router
        summary = router.summary()
        summary['brain_sync'] = {
            'agents': ['AGY', 'Codex'],
            'shared_skills': ['unified-treasury-router', 'binance', 'onchain-pay-open-api', 'binance-paper-audit'],
            'governance_contract': 'AGENTS.md',
            'paper_only': True,
        }
        return summary

    @app.get('/api/violet/transfers', dependencies=[auth_dep])
    async def closed_loop_transfers():
        router: ClosedLoopAssetRouter = app.state.closed_loop_router
        summary = router.summary()
        return {
            'mode': summary['mode'],
            'live_execution_enabled': False,
            'invariant': summary['invariant'],
            'allowlist': summary['allowlist'],
            'transfer_count': summary['transfer_count'],
            'transfers': summary['transfers'],
        }

    @app.post('/api/violet/transfers/simulate', dependencies=[auth_dep])
    async def simulate_closed_loop_transfer(payload: SimulateTransferRequest):
        router: ClosedLoopAssetRouter = app.state.closed_loop_router
        try:
            record = router.simulate_transfer(
                source_venue=payload.source_venue,
                destination_venue=payload.destination_venue,
                amount_usd=payload.amount_usd,
                idempotency_key=payload.idempotency_key,
                reason=payload.reason,
                requested_by=payload.requested_by,
                live_mode=False,
            )
        except (ClosedLoopRiskBlockedError, LiveTransferBlockedError) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except TreasuryError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            'mode': 'PAPER_SIMULATION_ONLY',
            'live_execution_enabled': False,
            'transfer': record.model_dump(mode='json'),
            'summary': router.summary(),
        }
