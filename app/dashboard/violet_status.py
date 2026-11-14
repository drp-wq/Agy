"""Authenticated, read-only Violet Lab diagnostics and paper closed-loop router endpoints."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.responses import JSONResponse
from app.dashboard.persistence_health import persistence_health
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.dashboard.auth import make_auth_dependency
from app.dashboard.violet_solana_bridge import solana_summary
from app.intelligence.unified_brain import UnifiedTradingBrain
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


def install_violet_status(
    app,
    runner,
    auth_config,
    closed_loop_router: ClosedLoopAssetRouter | None = None,
    risk_guardian=None,
    state_path=None,
):
    if getattr(app.state, "closed_loop_router", None) is None:
        app.state.closed_loop_router = closed_loop_router or ClosedLoopAssetRouter(
            risk_guardian=risk_guardian,
            state_path=state_path,
        )
    if getattr(app.state, "unified_brain", None) is None:
        app.state.unified_brain = UnifiedTradingBrain(
            router=app.state.closed_loop_router,
            risk_guardian=risk_guardian or app.state.closed_loop_router.risk_guardian,
        )

    auth_dep = Depends(make_auth_dependency(auth_config))

    @app.get('/api/violet/persistence/health', dependencies=[auth_dep])
    async def persistence_health_status():
        result = await run_in_threadpool(persistence_health, app)
        return JSONResponse(result, status_code=200 if result['status'] == 'ok' else 503)

    @app.get('/api/mobile/portfolio', dependencies=[auth_dep])
    async def mobile_portfolio():
        from app.dashboard.mobile_portfolio import build_mobile_portfolio
        return await run_in_threadpool(build_mobile_portfolio, runner)

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
                           'cycles':state.get('cycles'), 'source':'isolated_violet_paper_ledger',
                           'campaign_id': state.get('campaign_id'),
                           'budget': state.get('budget'),
                           'sleeves': state.get('sleeves'),
                           'risk_budget': state.get('risk_budget'),
                           'entry_gate': state.get('entry_gate'),
                           'historical_archive': state.get('historical_archive'),
                           'market_cap_tiers': state.get('market_cap_tiers'),
                           'research_cap_categories': state.get('research_cap_categories'),
                           'heartbeat_age_sec': state.get('heartbeat_age_sec'),
                           'rejections': (state.get('rejections') or [])[:20],
                           'risk': state.get('risk'),
                           'daily_circuit_breakers': state.get('daily_circuit_breakers'),
                           'reconciliation': state.get('reconciliation'),
                           'legacy_preview': state.get('legacy_preview')},
                'solana': await run_in_threadpool(solana_summary),
                'accounting':'SEPARATE_PAPER_LEDGERS_NO_TRANSFERS'}

    @app.get('/api/violet/pnl', dependencies=[auth_dep])
    async def violet_pnl_report(
        asset: str | None = None,
        exchange: str | None = None,
        strategy: str | None = None,
        cap_band: str | None = None,
        status: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ):
        if hasattr(runner, 'pnl_report'):
            return await run_in_threadpool(
                lambda: runner.pnl_report(
                    asset_filter=asset,
                    exchange_filter=exchange,
                    strategy_filter=strategy,
                    cap_band_filter=cap_band,
                    status_filter=status,
                    start_date=start_date,
                    end_date=end_date,
                )
            )
        return {'mode': 'PAPER_ONLY', 'live_accounts_excluded': True, 'overview': {}, 'assets': [], 'charts': {}, 'ledger': []}

    @app.get('/api/violet/ledger', dependencies=[auth_dep])
    async def violet_ledger(limit: int = 200):
        events = await run_in_threadpool(runner.db.list_ledger_events, min(max(1, limit), 1000)) if hasattr(runner, 'db') else []
        rep = runner.pnl_report() if hasattr(runner, 'pnl_report') else {}
        return {
            'mode': 'PAPER_ONLY',
            'live_accounts_excluded': True,
            'events': events,
            'reconciliation': rep.get('reconciliation', {'balanced': True, 'drift_usd': 0.0}),
        }

    @app.get('/api/violet/arbitrage', dependencies=[auth_dep])
    async def violet_arbitrage_research(symbol: str = 'BTC/USDT'):
        from app.dashboard.arbitrage_research import scan_public_books
        if symbol not in ('BTC/USDT', 'ETH/USDT', 'SOL/USDT'):
            raise HTTPException(status_code=422, detail='Unsupported research symbol')
        return await scan_public_books(symbol)

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

    @app.get('/api/violet/brain/status', dependencies=[auth_dep])
    async def unified_brain_status():
        brain: UnifiedTradingBrain = app.state.unified_brain
        summary = brain.router.summary()
        return {
            'mode': 'PAPER_SIMULATION_ONLY',
            'live_execution_enabled': False,
            'agents': ['AGY', 'Codex'],
            'shared_skill': 'unified-treasury-router',
            'risk_gate': summary['risk_gate'],
            'invariant': summary['invariant'],
            'last_report': brain.last_report.model_dump(mode='json') if brain.last_report else None,
        }

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
