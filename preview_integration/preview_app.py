"""Independent PAPER_ONLY simulator with isolated ledger and no exchange credentials."""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi.responses import JSONResponse
from app.core.config import EngineConfig, LoadedEnvironment, WatchOnlyWalletsConfig, MidLowCapConfig
from app.dashboard.auth import DashboardAuthConfig
from app.dashboard.violet_status import install_violet_status
from app.dashboard.server import create_dashboard_app
from app.service import TradingEngineService

from app.core.config import AllCapConfig
from app.execution.all_cap_runner import AllCapPaperRunner
from app.execution.violet_campaign_config import resolve_violet_campaign_settings
from app.exchanges.readonly_telemetry import ReadOnlyExchangeTelemetry
from app.exchanges.registry import ExchangeRegistry
from app.execution.legacy_preview_protection import protect_legacy_preview

root = Path('/root/paper-maintenance-preview-data')
config = EngineConfig(mid_low_cap=MidLowCapConfig(enabled=False), base_dir=root, db_path=root/'preview.sqlite3', backup_dir=root/'backups', log_dir=root/'logs', dashboard_host='127.0.0.3', dashboard_port=8790, watch_only_wallets=WatchOnlyWalletsConfig(registry_path=root/'empty-wallets.json'))
service = TradingEngineService(env=LoadedEnvironment(config=config, credentials={}), enable_telegram=False)
# Router custody is isolated from both preview execution and the Violet campaign.
from app.treasury.closed_loop_router import ClosedLoopAssetRouter
from app.intelligence.unified_brain import UnifiedTradingBrain
from app.intelligence.service_metrics import collect_service_metrics
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED

assert LIVE_EXECUTION_PERMANENTLY_DISABLED is True
router = ClosedLoopAssetRouter(state_path=root / 'closed-loop-paper' / 'router.sqlite3',
                               risk_guardian=service.risk)
brain = UnifiedTradingBrain(router, risk_guardian=service.risk)

async def brain_loop():
    from datetime import datetime, timezone
    while True:
        try:
            # Minute IDs survive restarts; report replay prevents repeated movement.
            cycle_id = datetime.now(timezone.utc).strftime('preview:%Y%m%d%H%M')
            with router.transaction():
                try:
                    metrics = collect_service_metrics(service, router)
                    reason = None if metrics else 'service_metrics_insufficient_history_or_valuation'
                except Exception as exc:
                    metrics, reason = [], f'service_metrics_unavailable:{type(exc).__name__}:{exc}'
                brain.step(metrics, cycle_id, unavailable_reason=reason)
            app.state.persistence_scheduler_heartbeat = datetime.now(timezone.utc).isoformat()
        except Exception:
            logging.getLogger(__name__).exception('Paper brain cycle failed closed')
        await asyncio.sleep(60)

lab_registry = ExchangeRegistry.violet_public()
campaign_settings = resolve_violet_campaign_settings(root)
all_cap_cfg = AllCapConfig(
    enabled=True,
    sleeve_capital_usd=campaign_settings.sleeve_capital_usdt,
    max_risk_per_trade_pct=campaign_settings.max_risk_per_trade_pct,
    min_order_notional_usd=campaign_settings.min_order_notional_usd,
    min_24h_volume_usd=150_000.0,
    min_order_book_depth_usd=5_000.0,
    max_spread_bps=35.0,
)
all_cap = AllCapPaperRunner(
    campaign_settings.campaign_dir,
    lab_registry,
    all_cap_cfg,
    campaign_hours=campaign_settings.campaign_hours,
    campaign_id=campaign_settings.campaign_id,
    historical_archive_dir=campaign_settings.historical_archive_dir,
)
auth_config = DashboardAuthConfig.load_or_create(root/'preview-auth.env')
app = create_dashboard_app(service, auth_config=auth_config)
app.state.unified_brain = brain
app.state.all_cap_runner = all_cap
readonly_telemetry = ReadOnlyExchangeTelemetry()
app.state.readonly_telemetry = readonly_telemetry
install_violet_status(app, all_cap, auth_config, closed_loop_router=router, risk_guardian=service.risk)

async def all_cap_loop():
    while True:
        try:
            await all_cap.run_cycle()
        except Exception:
            logging.getLogger(__name__).exception('Violet Lab cycle failed; retrying next interval')
            all_cap.state['worker_health'] = 'cycle_error'
        await asyncio.sleep(60)


async def legacy_protection_loop():
    while True:
        try:
            all_cap.legacy_protection = await protect_legacy_preview(service,lab_registry,all_cap.safe_for_protective_exit)
        except Exception:
            logging.getLogger(__name__).exception('Legacy preview protective pass failed')
        await asyncio.sleep(60)


async def readonly_telemetry_loop():
    while True:
        try:
            await readonly_telemetry.refresh(force=False)
        except Exception:
            logging.getLogger(__name__).exception('Read-only exchange telemetry cycle failed')
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(_app):
    # The 72-hour campaign owns paper execution; the legacy preview dashboard stays read-only.
    brain_task = asyncio.create_task(brain_loop())
    all_cap_task = asyncio.create_task(all_cap_loop())
    legacy_task = asyncio.create_task(legacy_protection_loop())
    ro_task = asyncio.create_task(readonly_telemetry_loop())
    try:
        yield
    finally:
        brain_task.cancel()
        try:
            await brain_task
        except asyncio.CancelledError:
            pass
        all_cap_task.cancel()
        legacy_task.cancel()
        ro_task.cancel()
        try:
            await all_cap_task
        except asyncio.CancelledError:
            pass
        try:
            await legacy_task
        except asyncio.CancelledError:
            pass
        try:
            await ro_task
        except asyncio.CancelledError:
            pass
        all_cap.close()
        lab_registry.close_all()
        await service.shutdown()
app.router.lifespan_context = lifespan

@app.middleware('http')
async def preview_readonly_guard(request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return JSONResponse({'detail':'Independent paper preview: HTTP mutations disabled'}, status_code=405)
    return await call_next(request)
