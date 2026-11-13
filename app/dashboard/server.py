"""FastAPI dashboard server with security headers, authenticated endpoints, and cyberpunk UI."""

from __future__ import annotations

from pathlib import Path
import secrets
from typing import Any
from urllib.parse import urlparse
import json

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.core.config import BASE_DIR
from app.core.models import ExchangeId, MarketType
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED, assert_paper_only
from app.dashboard.auth import DashboardAuthConfig, make_auth_dependency, security
from app.dashboard.candles import CandleFeedService
from app.dashboard.google_auth import install as install_google, CALLBACK
from app.dashboard.miniapp_presenter import (
    build_miniapp_health_payload,
    build_miniapp_state_payload,
)
from app.service import TradingEngineService
from app.telegram.webapp_auth import verify_telegram_init_data
from app.treasury.models import TreasuryError

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_MINIAPP_ASSETS_DIR = Path(__file__).parent / "static" / "miniapp" / "assets"
STATIC_AURORA_ASSETS_DIR = Path(__file__).parent / "static" / "aurora" / "assets"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


class BotToggleRequest(BaseModel):
    enabled: bool


class EmergencyStopRequest(BaseModel):
    reason: str = "Manual operator emergency stop"


class PaperTransferSimRequest(BaseModel):
    agent_id: str
    amount: str
    idempotency_key: str
    readonly_evidence_ref: str = "paper_sim_evidence_ok"


class PaperRebalanceRequest(BaseModel):
    apply: bool = False


def create_dashboard_app(
    service: TradingEngineService,
    auth_config: DashboardAuthConfig | None = None,
) -> FastAPI:
    app = FastAPI(
        title="Multi-Exchange AI Paper Trading Engine (OKX & Bybit)",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    auth_cfg = auth_config or DashboardAuthConfig.load_or_create()
    basic_auth = make_auth_dependency(auth_cfg)
    google_cfg, oauth, google_enabled = install_google(app)
    candle_feed = CandleFeedService()

    if STATIC_MINIAPP_ASSETS_DIR.exists():
        app.mount(
            "/miniapp/assets",
            StaticFiles(directory=str(STATIC_MINIAPP_ASSETS_DIR)),
            name="miniapp_assets",
        )

    if STATIC_AURORA_ASSETS_DIR.exists():
        app.mount(
            "/aurora/assets",
            StaticFiles(directory=str(STATIC_AURORA_ASSETS_DIR)),
            name="aurora_assets",
        )

    def _verify_session_csrf(request: Request) -> None:
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return
        csrf_hdr = request.headers.get("X-CSRF-Token", "")
        session_csrf = request.session.get("csrf_token", "")
        if csrf_hdr and session_csrf and secrets.compare_digest(csrf_hdr, session_csrf):
            return
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            parsed = urlparse(origin)
            expected_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
            if parsed.netloc and parsed.netloc == expected_host:
                return
        raise HTTPException(status_code=403, detail="CSRF validation failed")

    def require_auth(request: Request, credentials=Depends(security)) -> str:
        if google_enabled and request.session.get("google_email") == google_cfg["GOOGLE_ALLOWED_EMAIL"].lower():
            _verify_session_csrf(request)
            return request.session["google_email"]
        return basic_auth(credentials)

    def require_miniapp_auth(request: Request, credentials=Depends(security)) -> str:
        init_data = request.headers.get("X-Telegram-Init-Data")
        if init_data:
            tg_cfg = getattr(service, "telegram_config", None)
            if tg_cfg is None:
                raise HTTPException(status_code=503, detail="Telegram Mini App auth not configured")
            tg_user = verify_telegram_init_data(
                init_data=init_data,
                bot_token=tg_cfg.bot_token,
                owner_chat_id=tg_cfg.chat_id,
                max_age_sec=tg_cfg.init_data_max_age_sec,
            )
            return f"tg:{tg_user.user_id}"
        return require_auth(request, credentials)

    @app.get("/auth/google")
    async def google_start(request: Request):
        if not google_enabled:
            raise HTTPException(status_code=503, detail="Google login not configured")
        return await oauth.google.authorize_redirect(request, google_cfg.get("GOOGLE_OAUTH_CALLBACK_URL") or CALLBACK)

    @app.get("/auth/google/callback")
    async def google_callback(request: Request):
        if not google_enabled:
            raise HTTPException(status_code=503, detail="Google login not configured")
        try:
            token = await oauth.google.authorize_access_token(request)
            info = token.get("userinfo") or {}
        except Exception:
            raise HTTPException(status_code=401, detail="Google authentication failed")
        email = str(info.get("email", "")).lower()
        if not info.get("email_verified") or not email or email != google_cfg["GOOGLE_ALLOWED_EMAIL"].lower():
            raise HTTPException(status_code=403, detail="Account not authorized")
        request.session.clear()
        request.session["google_email"] = email
        request.session["csrf_token"] = secrets.token_urlsafe(32)
        return RedirectResponse("/", status_code=303)

    @app.post("/auth/logout")
    async def google_logout(request: Request):
        if google_enabled and request.session.get("google_email"):
            _verify_session_csrf(request)
        request.session.clear()
        return {"status": "ok"}


    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store, private"
        if request.url.path.startswith("/miniapp"):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' https://telegram.org; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' data: https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org;"
            )
        elif request.url.path.startswith("/aurora"):
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' https://telegram.org; "
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                "font-src 'self' data: https://fonts.gstatic.com; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none';"
            )
        else:
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self' 'unsafe-inline'; "
                "img-src 'self' data:; "
                "connect-src 'self'; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none';"
            )
        return response

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "PAPER_ONLY",
            "live_execution_disabled": LIVE_EXECUTION_PERMANENTLY_DISABLED,
            "exchanges": ["okx", "bybit", "binance"],
        }

    def _signin_html(basic_href: str) -> HTMLResponse:
        return HTMLResponse(f"""<!doctype html><html lang="en"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>NEON QUANT · Sign in</title><body style="background:#070913;color:#e2e8f0;font-family:system-ui;display:grid;place-items:center;min-height:90vh">
<main style="background:#0e1326;padding:36px;border:1px solid #1f2b4d;border-radius:16px;text-align:center">
<h1 style="color:#00f0ff">NEON QUANT</h1><p>Protected multi-exchange paper dashboard</p>
<a style="display:block;padding:14px;background:#00f0ff;color:#070913;border-radius:8px;margin:20px 0" href="/auth/google">Sign in with Google</a>
<a style="color:#e2e8f0" href="{basic_href}">Use existing dashboard password</a>
<p style="color:#8492a6">Live trading disabled</p></main></body></html>""")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, credentials=Depends(security)):
        try:
            user = require_auth(request, credentials)
        except HTTPException:
            if google_enabled and request.query_params.get("basic") != "1":
                return _signin_html("/?basic=1")
            raise
        return templates.TemplateResponse(request, "index.html", {"user": user, "google_enabled": google_enabled})

    @app.get("/aurora", response_class=HTMLResponse)
    @app.get("/aurora/", response_class=HTMLResponse)
    async def aurora_dashboard(request: Request, credentials=Depends(security)):
        try:
            user = require_auth(request, credentials)
        except HTTPException:
            if google_enabled and request.query_params.get("basic") != "1":
                return _signin_html("/aurora?basic=1")
            raise
        return templates.TemplateResponse(request, "aurora.html", {"user": user, "google_enabled": google_enabled})

    @app.get("/api/state")
    async def get_state(_user: str = Depends(require_auth)) -> dict[str, Any]:
        return service.state_snapshot()

    @app.get("/api/wallets/watch-only")
    async def get_watch_only_wallets(
        refresh: bool = False,
        _user: str = Depends(require_auth),
    ) -> dict[str, Any]:
        if refresh:
            wallets = await service.sync_watch_only_wallets(force=False)
        else:
            wallets = service.watch_only_monitor.all_cached_snapshots()
        return {
            "mode": "PAPER_ONLY",
            "wallet_mode": "watch_only",
            "read_only": True,
            "live_order_submission": False,
            "external_writes": False,
            "signing_enabled": False,
            "transfers_enabled": False,
            "counted_as_trading_cash": False,
            "wallets": wallets,
        }

    @app.get("/api/candles/{exchange}/{market_type}/{base}/{quote}/{timeframe}")
    async def get_candles(
        exchange: str,
        market_type: str,
        base: str,
        quote: str,
        timeframe: str,
        since: int | None = None,
        limit: int = 120,
        allow_stale: bool = False,
        _user: str = Depends(require_auth),
    ) -> dict[str, Any]:
        return await candle_feed.get_candles(
            service=service,
            exchange=exchange,
            market_type=market_type,
            base=base,
            quote=quote,
            timeframe=timeframe,
            since=since,
            limit=limit,
            allow_stale=allow_stale,
        )

    @app.post("/api/bots/{bot_id}/toggle")
    async def toggle_bot(
        bot_id: str,
        payload: BotToggleRequest,
        _user: str = Depends(require_auth),
    ) -> dict[str, Any]:
        try:
            bot = service.bot_manager.toggle_bot(bot_id, payload.enabled)
            return {"status": "ok", "bot": bot.model_dump()}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc))

    @app.post("/api/risk/emergency-stop")
    async def trigger_emergency_stop(
        payload: EmergencyStopRequest,
        _user: str = Depends(require_auth),
    ) -> dict[str, Any]:
        service.risk.trigger_emergency_stop(payload.reason)
        service.db.log_audit("EMERGENCY_STOP", _user, {"reason": payload.reason})
        return {"status": "ok", "emergency_stop_active": True}

    @app.post("/api/risk/reset-stop")
    async def reset_emergency_stop(_user: str = Depends(require_auth)) -> dict[str, Any]:
        service.risk.reset_emergency_stop()
        service.db.log_audit("EMERGENCY_STOP_RESET", _user, {})
        return {"status": "ok", "emergency_stop_active": False}

    @app.post("/api/scan-now")
    async def scan_now(_user: str = Depends(require_auth)) -> dict[str, Any]:
        result = await service.run_cycle(fetch_all_tfs=False)
        return {"status": "ok", "cycle_result": result}

    @app.get("/miniapp", response_class=HTMLResponse)
    async def miniapp_shell(request: Request):
        return templates.TemplateResponse(request, "miniapp.html", {})

    @app.get("/api/miniapp/state")
    async def miniapp_state(_user: str = Depends(require_miniapp_auth)) -> dict[str, Any]:
        readiness_path = BASE_DIR / "data" / "readiness_watch.json"
        readiness: dict[str, Any] | None = None
        if readiness_path.exists():
            try:
                readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
            except Exception:
                readiness = None
        return build_miniapp_state_payload(service, _user, readiness=readiness)

    @app.get("/api/miniapp/candles/{exchange}/{market_type}/{base}/{quote}/{timeframe}")
    async def miniapp_candles(
        exchange: str,
        market_type: str,
        base: str,
        quote: str,
        timeframe: str,
        since: int | None = None,
        limit: int = 120,
        allow_stale: bool = False,
        _user: str = Depends(require_miniapp_auth),
    ) -> dict[str, Any]:
        normalized_mtype = "perp" if market_type.lower() == "futures" else market_type.lower()
        raw = await candle_feed.get_candles(
            service=service,
            exchange=exchange,
            market_type=normalized_mtype,
            base=base,
            quote=quote,
            timeframe=timeframe,
            since=since,
            limit=limit,
            allow_stale=allow_stale,
        )
        normalized_candles = []
        for c in raw.get("candles", []):
            ts_ms = c.get("timestamp")
            time_sec = c.get("time") or (int(ts_ms // 1000) if isinstance(ts_ms, (int, float)) else 0)
            normalized_candles.append({**c, "time": time_sec})
        return {
            **raw,
            "market_type": market_type.lower(),
            "candles": normalized_candles,
        }

    @app.get("/api/miniapp/health")
    async def miniapp_health(_user: str = Depends(require_miniapp_auth)) -> dict[str, Any]:
        return build_miniapp_health_payload(service, _user)

    def _require_treasury():
        if service.treasury is None:
            raise HTTPException(status_code=503, detail="Unified treasury feature flag is disabled")
        return service.treasury

    @app.get("/api/treasury/overview")
    async def treasury_overview(_user: str = Depends(require_auth)) -> dict[str, Any]:
        treasury = _require_treasury()
        return treasury.overview_snapshot(readonly_snapshots=service.readonly_account_snapshots)

    @app.get("/api/treasury/agents")
    async def treasury_agents(_user: str = Depends(require_auth)) -> dict[str, Any]:
        treasury = _require_treasury()
        return {
            "mode": "PAPER_ONLY",
            "slots": [s.model_dump(mode="json") for s in treasury.list_agent_slots()],
        }

    @app.get("/api/treasury/transfers")
    async def treasury_transfers(_user: str = Depends(require_auth)) -> dict[str, Any]:
        treasury = _require_treasury()
        return {
            "mode": "PAPER_ONLY",
            "transfers": [t.model_dump(mode="json") for t in treasury.list_transfers(limit=100)],
        }

    @app.get("/api/treasury/journal")
    async def treasury_journal(_user: str = Depends(require_auth)) -> dict[str, Any]:
        treasury = _require_treasury()
        return {
            "mode": "PAPER_ONLY",
            "entries": [e.model_dump(mode="json") for e in treasury.list_journal_entries(limit=100)],
        }

    @app.get("/api/treasury/attribution")
    async def treasury_attribution(
        group_by: str = "agent_id",
        _user: str = Depends(require_auth),
    ) -> dict[str, Any]:
        treasury = _require_treasury()
        try:
            summary = treasury.get_attribution_summary(group_by=group_by)
        except TreasuryError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"mode": "PAPER_ONLY", "group_by": group_by, "summary": summary}

    @app.get("/api/treasury/reconciliation")
    async def treasury_reconciliation(_user: str = Depends(require_auth)) -> dict[str, Any]:
        treasury = _require_treasury()
        result = treasury.reconcile(readonly_snapshots=service.readonly_account_snapshots)
        result["paper_pnl_audit"] = service.paper_pnl_audit()
        return result

    @app.get("/api/treasury/paper/pnl-audit")
    async def treasury_pnl_audit(_user: str = Depends(require_auth)) -> dict[str, Any]:
        _require_treasury()
        return service.paper_pnl_audit()

    @app.post("/api/treasury/paper/init-slots")
    async def treasury_init_slots(_user: str = Depends(require_auth)) -> dict[str, Any]:
        assert_paper_only("paper")
        treasury = _require_treasury()
        slots = treasury.initialize_default_slots(18)
        return {"status": "ok", "mode": "PAPER_ONLY", "configured_slots": len(slots)}

    @app.post("/api/treasury/paper/simulate-transfer")
    async def treasury_simulate_transfer(
        payload: PaperTransferSimRequest,
        _user: str = Depends(require_auth),
    ) -> dict[str, Any]:
        assert_paper_only("paper")
        treasury = _require_treasury()
        try:
            trf = treasury.simulate_paper_transfer_e2e(
                agent_id=payload.agent_id,
                amount=payload.amount,
                idempotency_key=payload.idempotency_key,
                approved_by=_user,
                readonly_evidence_ref=payload.readonly_evidence_ref,
            )
        except TreasuryError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return {"status": "ok", "mode": "PAPER_ONLY", "transfer": trf.model_dump(mode="json")}

    @app.post("/api/treasury/paper/rebalance")
    async def treasury_rebalance(
        payload: PaperRebalanceRequest,
        _user: str = Depends(require_auth),
    ) -> dict[str, Any]:
        assert_paper_only("paper")
        _require_treasury()
        if service.treasury_allocator is None:
            raise HTTPException(status_code=503, detail="Treasury allocator not initialized")
        proposals = service.treasury_allocator.propose_rebalance(apply=payload.apply)
        return {
            "status": "ok",
            "mode": "PAPER_ONLY",
            "proposals": [p.model_dump(mode="json") for p in proposals],
        }

    return app
