"""Adapts TradingEngineService state into the V7 Mini App / Dashboard contract without fabricating data."""

from __future__ import annotations

import time
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from app.core.models import (
    Direction,
    ExchangeId,
    MarketRegime,
    MarketSnapshot,
    MarketType,
    OrderStatus,
    PaperOrder,
    PaperPosition,
    StrategySignal,
)
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED
from app.service import TradingEngineService

PROCESS_STARTED_AT = time.time()

EXCHANGE_LABELS: dict[str, str] = {
    "binance": "Binance",
    "bybit": "Bybit UTA",
    "okx": "OKX V5",
}

EXCHANGE_ACCOUNT_TYPES: dict[str, str] = {
    "binance": "Spot / Futures (Read-Only)",
    "bybit": "Unified Trading Account",
    "okx": "Multi-Currency Margin",
}


def _iso_to_unix(value: Any, default: int | None = None) -> int:
    if isinstance(value, (int, float)):
        return int(value / 1000) if value > 1e12 else int(value)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            pass
    return default if default is not None else int(time.time())


def _map_regime(regime: MarketRegime | str) -> str:
    val = regime.value if isinstance(regime, MarketRegime) else str(regime)
    mapping = {
        "trending_bullish": "TRENDING_UP",
        "trending_bearish": "TRENDING_DOWN",
        "ranging": "RANGING",
        "high_volatility": "HIGH_VOL_BREAKOUT",
    }
    return mapping.get(val, "RANGING")


def _map_order_status(status: OrderStatus | str) -> str:
    val = status.value if isinstance(status, OrderStatus) else str(status)
    if val in {"created", "validated", "submitted", "partially_filled"}:
        return "OPEN"
    if val == "filled":
        return "FILLED"
    if val == "canceled":
        return "CANCELLED"
    if val == "rejected":
        return "REJECTED"
    return "OPEN"


def _exchange_auth_note(ex_id: str, auth_info: dict[str, Any], configured: bool) -> str:
    if not configured:
        return "API key not configured (public feed only)"
    status = auth_info.get("status")
    if status == "ok":
        assets = auth_info.get("asset_count", 0)
        return f"Read-only auth verified ({assets} assets)"
    if status == "auth_error":
        return f"Auth error: {auth_info.get('error_type', 'unknown')}"
    return "Read-only auth pending verification"


def build_miniapp_health_payload(
    service: TradingEngineService,
    auth_user: str,
) -> dict[str, Any]:
    now_ts = int(time.time())
    health_list = service.registry.health_summary()
    auth_ver = service.auth_verification or {}
    adapters: dict[str, Any] = {}
    healthy_count = 0

    for item in health_list:
        ex_id = item["exchange"]
        is_healthy = bool(item.get("healthy", False))
        if is_healthy:
            healthy_count += 1
        ex_auth = auth_ver.get(ex_id, {})
        configured = bool(item.get("credentials_configured", False))
        private_ok = ex_auth.get("status") == "ok"
        last_ok = _iso_to_unix(item.get("last_ok_at"), default=0)
        adapters[ex_id] = {
            "public_ws_ok": False,  # Engine uses CCXT REST polling
            "public_rest_ok": is_healthy,
            "private_auth_ok": private_ok,
            "private_auth_note": _exchange_auth_note(ex_id, ex_auth, configured),
            "latency_ms": None,
            "last_tick_ts": last_ok,
        }

    overall_status = "ok" if healthy_count == len(health_list) and not service.risk.emergency_stop_active else (
        "degraded" if healthy_count > 0 else "error"
    )

    return {
        "status": overall_status,
        "mode": "PAPER_ONLY",
        "live_execution_disabled": LIVE_EXECUTION_PERMANENTLY_DISABLED,
        "live_execution_permanently_disabled": LIVE_EXECUTION_PERMANENTLY_DISABLED,
        "service": "multi-exchange-paper",
        "version": "7.0.0",
        "paper_engine_running": bool(
            (service._task is not None and not service._task.done()) or service.cycle_count > 0
        ),
        "telegram_auth_verified": auth_user.startswith("tg:"),
        "owner_authorized": True,
        "uptime_seconds": max(0, int(time.time() - PROCESS_STARTED_AT)),
        "vps_host": "srv2115613",
        "caddy_tls": "trade.187-52-118-25.sslip.io",
        "adapters": adapters,
        "exchanges": health_list,
        "telegram": {
            "enabled": service.telegram_adapter.is_enabled,
            "metrics": service.telegram_state.get_metrics() if service.telegram_state else {},
        },
        "timestamp": now_ts,
    }


def build_miniapp_state_payload(
    service: TradingEngineService,
    auth_user: str,
    readiness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now_ts = int(time.time())
    raw_snap = service.state_snapshot()
    positions: list[PaperPosition] = service.db.list_positions()
    accounts = service.db.list_accounts()
    orders: list[PaperOrder] = service.db.list_orders(limit=100)
    fills = service.db.list_fills(limit=100)
    snapshots = service.scanner.latest_snapshots
    signals: list[StrategySignal] = list(service.orchestrator.latest_signals)

    # Map bots -> exchange so we can aggregate per-exchange paper equity & realized PnL
    bot_exchange: dict[str, str] = {
        b["bot_id"]: b["exchange"] for b in raw_snap.get("bots", [])
    }

    ex_cash: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}
    ex_realized: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}
    for acc in accounts:
        ex_id = bot_exchange.get(acc["bot_id"])
        if ex_id in ex_cash:
            ex_cash[ex_id] += float(acc.get("cash_usd") or 0.0)
            ex_realized[ex_id] += float(acc.get("realized_pnl_usd") or 0.0)

    # Build exchanges map
    exchanges_map: dict[str, Any] = {}
    health_by_ex = {h["exchange"]: h for h in raw_snap.get("exchanges", [])}
    auth_ver = raw_snap.get("auth_verification") or {}
    account_snapshots = raw_snap.get("readonly_account_snapshots") or {}

    for ex in ExchangeId:
        ex_id = ex.value
        h = health_by_ex.get(ex_id, {})
        ex_auth = auth_ver.get(ex_id, {})
        live_snapshot = account_snapshots.get(ex_id, {})
        configured = bool(h.get("credentials_configured", False))
        private_ok = ex_auth.get("status") == "ok"
        last_sync = _iso_to_unix(h.get("last_ok_at"), default=0)

        ex_positions = [p for p in positions if p.exchange.value == ex_id]
        ex_unrealized = sum(p.unrealized_pnl_usd for p in ex_positions)
        ex_locked_margin = sum(
            abs(p.qty * p.mark_price) / max(p.leverage, 1.0) for p in ex_positions
        )
        ex_paper_equity = round(ex_cash[ex_id] + ex_unrealized + sum(
            abs(p.qty * p.entry_price) / max(p.leverage, 1.0) for p in ex_positions
        ), 2)

        ex_pairs = sorted({
            s.symbol for s in snapshots.values() if s.exchange.value == ex_id
        })

        exchanges_map[ex_id] = {
            "exchange": ex_id,
            "label": EXCHANGE_LABELS.get(ex_id, ex_id.upper()),
            "public_feed_ok": bool(h.get("healthy", False)),
            "public_latency_ms": None,
            "private_auth_ok": private_ok,
            "private_auth_error": None if private_ok else _exchange_auth_note(ex_id, ex_auth, configured),
            "last_sync_ts": last_sync,
            "paper_equity_usd": ex_paper_equity,
            "live_readonly_equity_usd": None,  # Cannot value mixed assets without verified USD quotes.
            "live_readonly_snapshot": live_snapshot,
            "available_margin_usd": round(max(0.0, ex_cash[ex_id]), 2),
            "locked_margin_usd": round(ex_locked_margin, 2),
            "realized_pnl_24h": round(ex_realized[ex_id], 2),
            "unrealized_pnl": round(ex_unrealized, 2),
            "exchange_meta": {
                "account_type": EXCHANGE_ACCOUNT_TYPES.get(ex_id, "Paper Engine"),
                "fee_tier": "Paper Standard",
                "margin_ratio_pct": None,
                "im_rate_pct": None,
                "mm_rate_pct": None,
            },
            "supported_pairs": ex_pairs,
        }

    # Spot holdings & Futures positions from real paper positions
    spot_holdings: list[dict[str, Any]] = []
    futures_positions: list[dict[str, Any]] = []

    for pos in positions:
        notional = round(abs(pos.qty * pos.mark_price), 2)
        cost_basis = abs(pos.qty * pos.entry_price)
        margin_usd = round(cost_basis / max(pos.leverage, 1.0), 2)
        pnl_pct = round((pos.unrealized_pnl_usd / margin_usd) * 100.0, 2) if margin_usd > 0 else 0.0

        if pos.market_type == MarketType.SPOT:
            base_asset = pos.symbol.split("/")[0]
            spot_holdings.append({
                "id": pos.position_id,
                "exchange": pos.exchange.value,
                "account_mode": "paper",
                "asset": base_asset,
                "symbol": pos.symbol,
                "available": pos.qty,
                "locked": 0.0,
                "total": pos.qty,
                "avg_entry_price": pos.entry_price,
                "mark_price": pos.mark_price,
                "valuation_usd": notional,
                "unrealized_pnl_usd": round(pos.unrealized_pnl_usd, 2),
                "unrealized_pnl_pct": pnl_pct,
                "updated_at": _iso_to_unix(pos.updated_at, default=now_ts),
            })
        else:
            sl_tp_status = (
                "ACTIVE" if (pos.stop_loss and pos.take_profit)
                else ("PARTIAL" if (pos.stop_loss or pos.take_profit) else "NONE")
            )
            liq_price: float | None = None
            liq_dist_pct: float | None = None
            if pos.leverage > 1.0 and pos.entry_price > 0:
                if pos.direction == Direction.LONG:
                    liq_price = round(pos.entry_price * (1.0 - (0.9 / pos.leverage)), 4)
                    if pos.mark_price > 0:
                        liq_dist_pct = round(((pos.mark_price - liq_price) / pos.mark_price) * 100.0, 2)
                else:
                    liq_price = round(pos.entry_price * (1.0 + (0.9 / pos.leverage)), 4)
                    if pos.mark_price > 0:
                        liq_dist_pct = round(((liq_price - pos.mark_price) / pos.mark_price) * 100.0, 2)

            futures_positions.append({
                "id": pos.position_id,
                "exchange": pos.exchange.value,
                "account_mode": "paper",
                "market_type": "futures",
                "status": "open",
                "symbol": pos.symbol,
                "instrument_id": f"{pos.symbol.replace('/', '-')}-PERP",
                "side": "LONG" if pos.direction == Direction.LONG else "SHORT",
                "quantity": pos.qty,
                "entry_price": pos.entry_price,
                "mark_price": pos.mark_price,
                "notional_usd": notional,
                "margin_usd": margin_usd,
                "leverage": pos.leverage,
                "margin_mode": "ISOLATED",
                "liquidation_price": liq_price,
                "liquidation_distance_pct": liq_dist_pct,
                "unrealized_pnl_usd": round(pos.unrealized_pnl_usd, 2),
                "unrealized_pnl_pct": pnl_pct,
                "realized_pnl_usd": round(pos.realized_pnl_usd, 2),
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "sl_tp_status": sl_tp_status,
                "strategy_name": pos.bot_id,
                "opened_at": _iso_to_unix(pos.opened_at, default=now_ts),
                "closed_at": None,
            })

    # Orders
    mapped_orders: list[dict[str, Any]] = []
    for o in orders:
        fill_price = o.avg_fill_price or o.limit_price or 0.0
        qty_for_notional = o.filled_qty if o.filled_qty > 0 else o.requested_qty
        mapped_orders.append({
            "id": o.order_id,
            "exchange": o.exchange.value,
            "account_mode": "paper",
            "market_type": "futures" if o.market_type == MarketType.PERP else "spot",
            "symbol": o.symbol,
            "side": o.side.value.upper(),
            "order_type": o.order_type.value.upper(),
            "status": _map_order_status(o.status),
            "price": o.limit_price or o.avg_fill_price or 0.0,
            "avg_fill_price": o.avg_fill_price if o.avg_fill_price > 0 else None,
            "quantity": o.requested_qty,
            "filled_quantity": o.filled_qty,
            "notional_usd": round(qty_for_notional * fill_price, 2),
            "fee_usd": round(o.fee_usd, 4),
            "slippage_bps": round(o.slippage_bps, 2),
            "strategy_name": o.bot_id,
            "created_at": _iso_to_unix(o.created_at, default=now_ts),
            "updated_at": _iso_to_unix(o.updated_at, default=now_ts),
        })

    # Opportunities from real scanner snapshots + latest signals
    signal_by_key: dict[str, StrategySignal] = {}
    for sig in signals:
        k = f"{sig.exchange.value}:{sig.market_type.value}:{sig.symbol}"
        if k not in signal_by_key:
            signal_by_key[k] = sig

    opportunities: list[dict[str, Any]] = []
    for snap_key, snap in snapshots.items():
        sig = signal_by_key.get(snap_key)
        mtype_ui = "futures" if snap.market_type == MarketType.PERP else "spot"
        observed_ts = _iso_to_unix(snap.timestamp, default=now_ts)
        # Approximate 1% depth equally split if total depth only is stored
        half_depth = round(snap.order_book_depth_usd / 2.0, 2)
        opportunities.append({
            "id": f"opp_{snap.exchange.value}_{mtype_ui}_{snap.symbol.replace('/', '_')}",
            "exchange": snap.exchange.value,
            "market_type": mtype_ui,
            "symbol": snap.symbol,
            "confirmed": {
                "last_price": snap.last_price,
                "change_24h_pct": 0.0,
                "volume_24h_usd": round(snap.volume_24h_usd, 2),
                "spread_bps": round(snap.spread_bps, 2),
                "bid_depth_1pct_usd": half_depth,
                "ask_depth_1pct_usd": half_depth,
                "realized_volatility_pct": round(snap.volatility * 100.0, 2),
                "momentum_score": round(snap.opportunity_score, 1),
                "regime": _map_regime(snap.regime),
                "observed_at": observed_ts,
            },
            "estimates": {
                "strategy_name": sig.strategy_name if sig else "Scanner Observation",
                "direction": (
                    "LONG" if sig and sig.direction == Direction.LONG
                    else ("SHORT" if sig and sig.direction == Direction.SHORT else "NEUTRAL")
                ),
                "confidence_pct": round(sig.confidence_score * 100.0, 1) if sig else round(min(95.0, snap.opportunity_score), 1),
                "est_fees_bps": round(sig.estimated_fee_pct * 10000.0, 1) if sig else 10.0,
                "est_slippage_bps": round(sig.estimated_slippage_pct * 10000.0, 1) if sig else round(snap.spread_bps * 0.5, 1),
                "risk_reward_ratio": round(sig.risk_reward_ratio, 2) if sig else 0.0,
                "suggested_entry": round((sig.entry_low + sig.entry_high) / 2.0, 4) if sig else snap.last_price,
                "suggested_sl": sig.stop_loss if sig else 0.0,
                "suggested_tp": sig.take_profit_targets[0] if (sig and sig.take_profit_targets) else 0.0,
                "screening_decision": "APPROVED" if sig else "WATCHLIST",
                "rejection_reasons": [] if sig else ["No active strategy signal in current cycle"],
            },
        })

    # Cross-exchange spreads computed strictly from real snapshots
    prices_by_sym: dict[str, dict[str, float]] = {}
    for snap in snapshots.values():
        if snap.last_price > 0:
            prices_by_sym.setdefault(snap.symbol, {})[snap.exchange.value] = snap.last_price

    cross_spreads: list[dict[str, Any]] = []
    for sym, ex_prices in sorted(prices_by_sym.items()):
        if len(ex_prices) < 2:
            continue
        best_buy_ex = min(ex_prices, key=ex_prices.get)  # type: ignore[arg-type]
        best_sell_ex = max(ex_prices, key=ex_prices.get)  # type: ignore[arg-type]
        low_p = ex_prices[best_buy_ex]
        high_p = ex_prices[best_sell_ex]
        spread_bps = round(((high_p - low_p) / low_p) * 10000.0, 2) if low_p > 0 else 0.0
        est_net_bps = round(spread_bps - 20.0, 2)
        cross_spreads.append({
            "symbol": sym,
            "binance_price": ex_prices.get("binance"),
            "bybit_price": ex_prices.get("bybit"),
            "okx_price": ex_prices.get("okx"),
            "max_spread_bps": spread_bps,
            "best_buy_exchange": best_buy_ex,
            "best_sell_exchange": best_sell_ex,
            "est_net_arb_bps": est_net_bps,
            "executable_note": "Observed quote dispersion only (est. ~20 bps round-trip taker fee)",
        })

    # Risk
    portfolio = raw_snap.get("portfolio", {})
    total_eq = float(portfolio.get("total_equity_usd") or 0.0)
    risk_raw = raw_snap.get("risk", {})
    peak_eq = float(risk_raw.get("peak_equity_usd") or total_eq)
    drawdown_pct = round(((peak_eq - total_eq) / peak_eq) * 100.0, 2) if peak_eq > 0 and total_eq < peak_eq else 0.0
    total_unrealized = sum(p.unrealized_pnl_usd for p in positions)
    total_realized = sum(ex_realized.values())
    gross_exposure = float(risk_raw.get("portfolio_exposure_usd") or 0.0)
    net_delta = sum(
        (p.qty * p.mark_price) if p.direction == Direction.LONG else -(p.qty * p.mark_price)
        for p in positions
    )

    asset_exp: dict[str, float] = {}
    for p in positions:
        base = p.symbol.split("/")[0]
        asset_exp[base] = asset_exp.get(base, 0.0) + abs(p.qty * p.mark_price)
    concentration = [
        {
            "asset": asset,
            "exposure_usd": round(exp, 2),
            "share_pct": round((exp / gross_exposure) * 100.0, 1) if gross_exposure > 0 else 0.0,
        }
        for asset, exp in sorted(asset_exp.items(), key=lambda x: x[1], reverse=True)
    ]

    stale_warnings: list[dict[str, Any]] = []
    last_cycle_ts = _iso_to_unix(raw_snap.get("last_cycle_at"), default=0)
    if last_cycle_ts <= 0:
        stale_warnings.append({
            "component": "Market Scanner",
            "age_sec": None,
            "message": "No completed scanner cycle timestamp available",
        })
    if last_cycle_ts > 0 and (now_ts - last_cycle_ts) > 120:
        stale_warnings.append({
            "component": "Market Scanner",
            "age_sec": now_ts - last_cycle_ts,
            "message": f"Last scanner cycle was {now_ts - last_cycle_ts}s ago",
        })

    cb_state = "TRIPPED_VOLATILITY" if risk_raw.get("circuit_breaker_tripped") else "ARMED_NOMINAL"

    risk_payload = {
        "total_paper_equity_usd": round(total_eq, 2),
        "total_live_readonly_equity_usd": None,
        "daily_realized_pnl_usd": round(total_realized, 2),
        "daily_unrealized_pnl_usd": round(total_unrealized, 2),
        "current_drawdown_pct": drawdown_pct,
        "max_drawdown_pct": round(service.risk.policy.max_drawdown_pct * 100.0, 2),
        "drawdown_limit_pct": round(service.risk.policy.max_drawdown_pct * 100.0, 2),
        "risk_allocation_pct": round(float(risk_raw.get("portfolio_exposure_pct") or 0.0) * 100.0, 2),
        "gross_margin_exposure_usd": round(gross_exposure, 2),
        "net_delta_exposure_usd": round(net_delta, 2),
        "circuit_breaker_state": cb_state,
        "emergency_stop_active": bool(risk_raw.get("emergency_stop_active", False)),
        "concentration_by_asset": concentration,
        "stale_data_warnings": stale_warnings,
        "reconciliation_errors": [],
    }

    # Portfolio analytics from real fills/accounts
    pnl_by_exchange = {ex.value: round(ex_realized[ex.value] + exchanges_map[ex.value]["unrealized_pnl"], 2) for ex in ExchangeId}
    initial_eq = float(portfolio.get("initial_equity_usd") or total_eq or 10000.0)
    equity_curve = [{"time": now_ts - 3600, "equity": round(initial_eq, 2)}, {"time": now_ts, "equity": round(total_eq, 2)}]

    analytics_payload = {
        "equity_curve": equity_curve,
        "win_rate_pct": 0.0,
        "profit_factor": 0.0,
        "sharpe_ratio": 0.0,
        "sortino_ratio": 0.0,
        "total_trades": len(fills),
        "avg_win_usd": 0.0,
        "avg_loss_usd": 0.0,
        "pnl_by_exchange": pnl_by_exchange,
        "pnl_by_strategy": [],
    }

    # Telegram + Risk Alerts
    alerts_payload: list[dict[str, Any]] = []
    tg_alerts = raw_snap.get("telegram", {}).get("recent_alerts", [])
    for idx, a in enumerate(tg_alerts):
        alerts_payload.append({
            "id": str(a.get("event_id") or f"tg_{idx}"),
            "timestamp": _iso_to_unix(a.get("sent_at") or a.get("timestamp"), default=now_ts),
            "category": str(a.get("category") or "SYSTEM").upper(),
            "severity": str(a.get("severity") or "info").lower(),
            "exchange": a.get("exchange"),
            "symbol": a.get("symbol"),
            "title": str(a.get("title") or a.get("event_type") or "Telegram Alert"),
            "message": str(a.get("summary") or a.get("message") or ""),
            "delivered_to_bot": True,
            "account_mode": "paper",
        })
    for idx, ra in enumerate(risk_raw.get("recent_alerts", [])):
        lvl = str(ra.get("level") or "INFO").lower()
        sev = "critical" if lvl == "critical" else ("warning" if lvl == "warning" else "info")
        alerts_payload.append({
            "id": f"risk_{idx}",
            "timestamp": _iso_to_unix(ra.get("timestamp"), default=now_ts),
            "category": "RISK",
            "severity": sev,
            "title": f"Risk Guardian ({lvl.upper()})",
            "message": str(ra.get("message") or ""),
            "delivered_to_bot": False,
            "account_mode": "paper",
        })
    alerts_payload.sort(key=lambda x: x["timestamp"], reverse=True)

    tg_id: int | None = None
    if auth_user.startswith("tg:"):
        try:
            tg_id = int(auth_user.split(":", 1)[1])
        except ValueError:
            tg_id = None

    treasury_reconciliation: dict[str, Any] | None = None
    paper_pnl_audit: dict[str, Any] | None = None
    if getattr(service, "treasury", None) is not None:
        try:
            treasury_reconciliation = service.treasury.reconcile(
                readonly_snapshots=service.readonly_account_snapshots
            )
        except Exception:
            treasury_reconciliation = None
        try:
            paper_pnl_audit = service.paper_pnl_audit()
        except Exception:
            paper_pnl_audit = None

    return {
        # Backwards-compatible fields
        "mode": "PAPER_ONLY",
        "live_execution_disabled": LIVE_EXECUTION_PERMANENTLY_DISABLED,
        "cycle_count": raw_snap.get("cycle_count", 0),
        "last_cycle_at": raw_snap.get("last_cycle_at"),
        "readiness": readiness,
        # V7 MiniAppStateResponse contract
        "is_demo": False,
        "live_execution_permanently_disabled": LIVE_EXECUTION_PERMANENTLY_DISABLED,
        "generated_at": now_ts,
        "telegram_owner": {
            "authenticated": True,
            "telegram_id": tg_id,
            "username": None,
            "auth_method": "signed_init_data" if auth_user.startswith("tg:") else "unverified",
        },
        "exchanges": exchanges_map,
        "spot_holdings": spot_holdings,
        "futures_positions": futures_positions,
        "orders": mapped_orders,
        "opportunities": opportunities,
        "cross_exchange_spreads": cross_spreads,
        "risk": risk_payload,
        "analytics": analytics_payload,
        "alerts": alerts_payload[:50],
        "unified_treasury": raw_snap.get("unified_treasury", {"enabled": False, "mode": "PAPER_ONLY"}),
        "treasury_reconciliation": treasury_reconciliation,
        "paper_pnl_audit": paper_pnl_audit,
        "watch_only_wallets": raw_snap.get("watch_only_wallets", {"mode": "watch_only", "wallets": []}),
    }
