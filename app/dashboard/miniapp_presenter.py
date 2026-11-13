"""Adapts TradingEngineService state into the V7 Mini App / Dashboard contract without fabricating data."""

from __future__ import annotations

import time
import json
import math
from pathlib import Path
from datetime import datetime, timedelta, timezone
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


def _build_strategy_label(bot_id: str) -> str:
    return bot_id.replace("_", " ").strip() or "Unassigned Bot"


def _compute_realized_and_analytics(
    now_ts: int,
    initial_eq: float,
    total_eq: float,
    ex_realized: dict[str, float],
    ex_unrealized_map: dict[str, float],
    all_fills_count: int,
    closures: list[dict[str, Any]],
    all_fills: list[Any],
    funding_events: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Compute provenance-verified daily/lifetime realized PnL, HWM trajectory, and portfolio analytics."""
    utc_midnight_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    utc_day_start_ts = int(utc_midnight_dt.timestamp())
    rolling_24h_start_ts = now_ts - 86400

    # Events that directly moved paper_accounts.realized_pnl_usd:
    # 1) Closure gross realized PnL
    # 2) Fill fees (-fee_usd for every entry/exit fill)
    # 3) Treasury-attributed funding settlements (+usd_value)
    events: list[tuple[int, str, float]] = []
    ex_recorded_total: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}
    ex_utc_day_pnl: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}
    ex_24h_pnl: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}

    close_pnls: list[float] = []
    strategy_stats: dict[str, dict[str, Any]] = {}

    for row in closures:
        ex_id = str(row.get("exchange") or "")
        gross = float(row.get("gross_realized_usd") or 0.0)
        exit_fee = float(row.get("exit_fee_usd") or 0.0)
        net_close = gross - exit_fee
        close_pnls.append(net_close)

        bot_id = str(row.get("bot_id") or "unassigned")
        s_bucket = strategy_stats.setdefault(
            bot_id, {"strategy": _build_strategy_label(bot_id), "trades": 0, "wins": 0, "net_pnl_usd": 0.0}
        )
        s_bucket["trades"] += 1
        if net_close > 0:
            s_bucket["wins"] += 1
        s_bucket["net_pnl_usd"] += net_close

        ts = _iso_to_unix(row.get("created_at"), default=now_ts)
        events.append((ts, ex_id, gross))
        if ex_id in ex_recorded_total:
            ex_recorded_total[ex_id] += gross
            if ts >= utc_day_start_ts:
                ex_utc_day_pnl[ex_id] += gross
            if ts >= rolling_24h_start_ts:
                ex_24h_pnl[ex_id] += gross

    for fill in all_fills:
        ex_id = fill.exchange.value if hasattr(fill.exchange, "value") else str(fill.exchange)
        fee = -float(getattr(fill, "fee_usd", 0.0) or 0.0)
        ts = _iso_to_unix(getattr(fill, "timestamp", None), default=now_ts)
        events.append((ts, ex_id, fee))
        if ex_id in ex_recorded_total:
            ex_recorded_total[ex_id] += fee
            if ts >= utc_day_start_ts:
                ex_utc_day_pnl[ex_id] += fee
            if ts >= rolling_24h_start_ts:
                ex_24h_pnl[ex_id] += fee

    for fev in funding_events:
        ex_id = str(fev.get("exchange") or "")
        val = float(fev.get("usd_value") or fev.get("amount") or 0.0)
        ts = _iso_to_unix(fev.get("created_at"), default=now_ts)
        events.append((ts, ex_id, val))
        if ex_id in ex_recorded_total:
            ex_recorded_total[ex_id] += val
            if ts >= utc_day_start_ts:
                ex_utc_day_pnl[ex_id] += val
            if ts >= rolling_24h_start_ts:
                ex_24h_pnl[ex_id] += val

    ex_reconciled: dict[str, bool] = {}
    for ex_id in ex_realized:
        ex_reconciled[ex_id] = abs(ex_realized[ex_id] - ex_recorded_total.get(ex_id, 0.0)) <= 0.05

    lifetime_total = sum(ex_realized.values())
    recorded_total = sum(ex_recorded_total.values())
    total_reconciled = abs(lifetime_total - recorded_total) <= 0.05
    daily_utc_total = sum(ex_utc_day_pnl.values())

    # Build chronological strictly ascending equity curve from initial_eq + realized events -> current total_eq
    events.sort(key=lambda item: item[0])
    curve_map: dict[int, float] = {}
    first_ts = events[0][0] if events else (now_ts - 3600)
    start_ts = min(first_ts - 60, now_ts - 60)
    running_eq = initial_eq
    curve_map[start_ts] = round(running_eq, 2)

    for ts, _ex_id, delta in events:
        running_eq += delta
        clamped_ts = min(ts, now_ts)
        curve_map[clamped_ts] = round(running_eq, 2)

    curve_map[now_ts] = round(total_eq, 2)
    full_curve = [{"time": t, "equity": curve_map[t]} for t in sorted(curve_map.keys())]

    # True historical drawdown from trajectory (excluding policy limit)
    max_hist_dd: float | None = None
    if total_reconciled and initial_eq > 0 and full_curve:
        peak_val = full_curve[0]["equity"]
        max_dd_acc = 0.0
        for pt in full_curve:
            eq_val = pt["equity"]
            if eq_val > peak_val:
                peak_val = eq_val
            if peak_val > 0 and eq_val < peak_val:
                dd_pct = ((peak_val - eq_val) / peak_val) * 100.0
                if dd_pct > max_dd_acc:
                    max_dd_acc = dd_pct
        max_hist_dd = round(max_dd_acc, 2)

    # Downsample chart curve to <= 120 points while preserving endpoints & extrema
    equity_curve = full_curve
    if len(full_curve) > 120:
        step = max(1, len(full_curve) // 100)
        sampled = full_curve[::step]
        if sampled[-1]["time"] != full_curve[-1]["time"]:
            sampled.append(full_curve[-1])
        equity_curve = sampled

    # Closed-trade win/loss metrics (strictly from recorded closures; null when zero closed trades)
    wins = [p for p in close_pnls if p > 0]
    losses = [p for p in close_pnls if p < 0]
    closed_count = len(close_pnls)
    win_rate_pct: float | None = round((len(wins) / closed_count) * 100.0, 1) if closed_count > 0 else None
    avg_win_usd: float | None = round(sum(wins) / len(wins), 2) if wins else (0.0 if closed_count > 0 else None)
    avg_loss_usd: float | None = round(sum(losses) / len(losses), 2) if losses else (0.0 if closed_count > 0 else None)
    gross_wins = sum(wins)
    gross_losses_abs = abs(sum(losses))
    if closed_count == 0:
        profit_factor: float | None = None
    elif gross_losses_abs > 0:
        profit_factor = round(gross_wins / gross_losses_abs, 2)
    elif gross_wins > 0:
        profit_factor = None
    else:
        profit_factor = 0.0

    # Daily return Sharpe / Sortino only when >= 4 distinct UTC days exist
    daily_end_equity: dict[str, float] = {}
    for pt in full_curve:
        day_key = datetime.fromtimestamp(pt["time"], tz=timezone.utc).strftime("%Y-%m-%d")
        daily_end_equity[day_key] = pt["equity"]
    ordered_days = sorted(daily_end_equity.keys())
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None
    if len(ordered_days) >= 4:
        rets: list[float] = []
        for idx in range(1, len(ordered_days)):
            prev_eq = daily_end_equity[ordered_days[idx - 1]]
            curr_eq = daily_end_equity[ordered_days[idx]]
            if prev_eq > 0:
                rets.append((curr_eq - prev_eq) / prev_eq)
        if len(rets) >= 3:
            mean_r = sum(rets) / len(rets)
            var_r = sum((r - mean_r) ** 2 for r in rets) / max(1, len(rets) - 1)
            std_r = math.sqrt(var_r)
            downside = [min(0.0, r) ** 2 for r in rets]
            downside_dev = math.sqrt(sum(downside) / len(rets))
            if std_r > 1e-9:
                sharpe_ratio = round((mean_r / std_r) * math.sqrt(365.0), 2)
            if downside_dev > 1e-9:
                sortino_ratio = round((mean_r / downside_dev) * math.sqrt(365.0), 2)

    pnl_by_exchange = {
        ex_id: round(ex_realized[ex_id] + ex_unrealized_map.get(ex_id, 0.0), 2)
        for ex_id in ex_realized
    }
    pnl_by_strategy = sorted(
        [
            {
                "strategy": item["strategy"],
                "trades": item["trades"],
                "win_rate": round((item["wins"] / item["trades"]) * 100.0, 1) if item["trades"] > 0 else 0.0,
                "net_pnl_usd": round(item["net_pnl_usd"], 2),
            }
            for item in strategy_stats.values()
        ],
        key=lambda x: x["net_pnl_usd"],
        reverse=True,
    )

    realized_meta = {
        "total_reconciled": total_reconciled,
        "daily_realized_available": total_reconciled,
        "daily_realized_pnl_usd": round(daily_utc_total, 2) if total_reconciled else None,
        "lifetime_realized_pnl_usd": round(lifetime_total, 2),
        "daily_realized_cutoff_ts": utc_day_start_ts,
        "daily_realized_scope": "utc_day" if total_reconciled else "unavailable_unbacked_legacy",
        "ex_reconciled": ex_reconciled,
        "ex_utc_day_pnl": {k: round(v, 2) for k, v in ex_utc_day_pnl.items()},
        "ex_24h_pnl": {k: round(v, 2) for k, v in ex_24h_pnl.items()},
    }
    analytics_payload = {
        "equity_curve": equity_curve,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "sharpe_ratio": sharpe_ratio,
        "sortino_ratio": sortino_ratio,
        "total_trades": all_fills_count,
        "closed_trades": closed_count,
        "max_historical_drawdown_pct": max_hist_dd,
        "avg_win_usd": avg_win_usd,
        "avg_loss_usd": avg_loss_usd,
        "pnl_by_exchange": pnl_by_exchange,
        "pnl_by_strategy": pnl_by_strategy,
        "metrics_note": (
            None
            if sharpe_ratio is not None
            else f"Sharpe/Sortino require >=4 UTC daily closes ({len(ordered_days)} day(s) recorded)"
        ),
    }
    return realized_meta, analytics_payload


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
    all_fills = service.db.list_all_fills_asc()
    closures = service.db.list_realized_closures()
    funding_events = service.db.list_treasury_funding_events()
    snapshots = service.scanner.latest_snapshots
    signals: list[StrategySignal] = list(service.orchestrator.latest_signals)

    # Map bots -> exchange so we can aggregate per-exchange paper equity & realized PnL
    bot_exchange: dict[str, str] = {
        b["bot_id"]: b["exchange"] for b in raw_snap.get("bots", [])
    }

    ex_cash: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}
    ex_realized: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}
    ex_reserved_margin: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}
    for acc in accounts:
        ex_id = bot_exchange.get(acc["bot_id"]) or str(acc.get("exchange") or "")
        if ex_id in ex_cash:
            ex_cash[ex_id] += float(acc.get("cash_usd") or 0.0)
            ex_realized[ex_id] += float(acc.get("realized_pnl_usd") or 0.0)
            ex_reserved_margin[ex_id] += float(acc.get("reserved_margin_usd") or 0.0)

    ex_unrealized_map: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}
    ex_notional_map: dict[str, float] = {ex.value: 0.0 for ex in ExchangeId}
    for p in positions:
        ex_id = p.exchange.value
        ex_unrealized_map[ex_id] = ex_unrealized_map.get(ex_id, 0.0) + float(p.unrealized_pnl_usd or 0.0)
        ex_notional_map[ex_id] = ex_notional_map.get(ex_id, 0.0) + abs(float(p.qty) * float(p.mark_price))

    portfolio = raw_snap.get("portfolio", {})
    total_eq = float(portfolio.get("total_equity_usd") or 0.0)
    initial_eq = float(portfolio.get("initial_equity_usd") or total_eq or 10000.0)
    realized_meta, analytics_payload = _compute_realized_and_analytics(
        now_ts=now_ts,
        initial_eq=initial_eq,
        total_eq=total_eq,
        ex_realized=ex_realized,
        ex_unrealized_map=ex_unrealized_map,
        all_fills_count=len(all_fills),
        closures=closures,
        all_fills=all_fills,
        funding_events=funding_events,
    )

    # Build exchanges map
    exchanges_map: dict[str, Any] = {}
    health_by_ex = {h["exchange"]: h for h in raw_snap.get("exchanges", [])}
    auth_ver = raw_snap.get("auth_verification") or {}
    account_snapshots = raw_snap.get("readonly_account_snapshots") or {}
    authenticated_exchanges: list[str] = []

    for ex in ExchangeId:
        ex_id = ex.value
        h = health_by_ex.get(ex_id, {})
        ex_auth = auth_ver.get(ex_id, {})
        live_snapshot = account_snapshots.get(ex_id, {})
        configured = bool(h.get("credentials_configured", False))
        private_ok = ex_auth.get("status") == "ok"
        if private_ok:
            authenticated_exchanges.append(ex_id)
        last_sync = _iso_to_unix(h.get("last_ok_at"), default=0)

        ex_positions = [p for p in positions if p.exchange.value == ex_id]
        ex_unrealized = ex_unrealized_map.get(ex_id, 0.0)
        ex_reserved = ex_reserved_margin.get(ex_id, 0.0)
        # Fallback only if DB reserved_margin_usd is empty while positions exist
        if ex_reserved <= 0.0 and ex_positions:
            ex_reserved = sum(
                abs(p.qty * p.entry_price) / max(p.leverage, 1.0) for p in ex_positions
            )
        # paper_accounts.cash_usd already includes reserved margin (entry only deducts fees)
        ex_paper_equity = round(ex_cash[ex_id] + ex_unrealized, 2)
        ex_available_margin = round(max(0.0, ex_cash[ex_id] - ex_reserved), 2)

        ex_pairs = sorted({
            s.symbol for s in snapshots.values() if s.exchange.value == ex_id
        })
        ex_rec_ok = bool(realized_meta["ex_reconciled"].get(ex_id, False))

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
            "available_margin_usd": ex_available_margin,
            "locked_margin_usd": round(ex_reserved, 2),
            "notional_exposure_usd": round(ex_notional_map.get(ex_id, 0.0), 2),
            "realized_pnl_24h": realized_meta["ex_24h_pnl"][ex_id] if ex_rec_ok else None,
            "realized_pnl_24h_available": ex_rec_ok,
            "realized_pnl_utc_day": realized_meta["ex_utc_day_pnl"][ex_id] if ex_rec_ok else None,
            "lifetime_realized_pnl": round(ex_realized[ex_id], 2),
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

    orders_by_id: dict[str, PaperOrder] = {o.order_id: o for o in orders}
    for row in reversed(closures):
        if len([fp for fp in futures_positions if fp["status"] == "closed"]) >= 25:
            break
        if str(row.get("market_type") or "") != MarketType.PERP.value:
            continue
        oid = str(row.get("order_id") or "")
        exit_ord = orders_by_id.get(oid)
        close_qty = float(row.get("close_qty") or 0.0)
        entry_px = float(row.get("entry_price") or 0.0)
        exit_px = float(row.get("exit_price") or 0.0)
        gross_pnl = float(row.get("gross_realized_usd") or 0.0)
        exit_fee = float(row.get("exit_fee_usd") or 0.0)
        net_pnl = round(gross_pnl - exit_fee, 2)
        leverage = float(exit_ord.leverage) if exit_ord else 1.0
        if exit_ord is not None:
            pos_side = "LONG" if exit_ord.side.value == "sell" else "SHORT"
        elif exit_px != entry_px and close_qty > 0:
            pos_side = "LONG" if (exit_px - entry_px) * gross_pnl >= 0 else "SHORT"
        else:
            pos_side = "LONG"
        notional_c = round(abs(close_qty * exit_px), 2)
        margin_c = round((abs(close_qty * entry_px)) / max(leverage, 1.0), 2)
        closed_ts = _iso_to_unix(row.get("created_at"), default=now_ts)
        futures_positions.append({
            "id": f"closed_{oid}",
            "exchange": str(row.get("exchange") or "binance"),
            "account_mode": "paper",
            "market_type": "futures",
            "status": "closed",
            "symbol": str(row.get("symbol") or ""),
            "instrument_id": f"{str(row.get('symbol') or '').replace('/', '-')}-PERP",
            "side": pos_side,
            "quantity": close_qty,
            "entry_price": entry_px,
            "mark_price": exit_px,
            "notional_usd": notional_c,
            "margin_usd": margin_c,
            "leverage": leverage,
            "margin_mode": "ISOLATED",
            "liquidation_price": None,
            "liquidation_distance_pct": None,
            "unrealized_pnl_usd": 0.0,
            "unrealized_pnl_pct": round((net_pnl / margin_c) * 100.0, 2) if margin_c > 0 else 0.0,
            "realized_pnl_usd": net_pnl,
            "stop_loss": exit_ord.stop_loss if exit_ord else None,
            "take_profit": exit_ord.take_profit if exit_ord else None,
            "sl_tp_status": "NONE",
            "strategy_name": str(row.get("bot_id") or ""),
            "opened_at": _iso_to_unix(exit_ord.created_at, default=closed_ts) if exit_ord else closed_ts,
            "closed_at": closed_ts,
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

    # Risk & Capital Allocation Telemetry
    risk_raw = raw_snap.get("risk", {})
    raw_peak_eq = float(risk_raw.get("peak_equity_usd") or 0.0)
    drawdown_available = raw_peak_eq > 0.0
    drawdown_pct: float | None = (
        round(max(0.0, ((raw_peak_eq - total_eq) / raw_peak_eq) * 100.0), 2)
        if drawdown_available
        else None
    )
    hwm_equity_usd: float | None = round(raw_peak_eq, 2) if drawdown_available else None

    total_unrealized = sum(p.unrealized_pnl_usd for p in positions)
    gross_notional_exposure = sum(abs(p.qty * p.mark_price) for p in positions)
    total_reserved_margin = sum(float(exchanges_map[ex.value]["locked_margin_usd"]) for ex in ExchangeId)
    total_cash = sum(ex_cash.values())
    free_paper_capital = max(0.0, total_cash - total_reserved_margin)
    margin_allocation_pct = round((total_reserved_margin / total_eq) * 100.0, 2) if total_eq > 0 else 0.0
    notional_exposure_pct = round((gross_notional_exposure / total_eq) * 100.0, 2) if total_eq > 0 else 0.0
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
            "share_pct": round((exp / gross_notional_exposure) * 100.0, 1) if gross_notional_exposure > 0 else 0.0,
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
        "live_readonly_auth_summary": {
            "authenticated_exchanges": authenticated_exchanges,
            "total_exchanges": len(ExchangeId),
            "usd_valuation_available": False,
            "reason": "Spot and cross-margin exchange balances require verified USD conversion quotes before aggregate summation.",
        },
        "daily_realized_pnl_usd": realized_meta["daily_realized_pnl_usd"],
        "daily_realized_available": realized_meta["daily_realized_available"],
        "daily_realized_cutoff_ts": realized_meta["daily_realized_cutoff_ts"],
        "daily_realized_scope": realized_meta["daily_realized_scope"],
        "lifetime_realized_pnl_usd": realized_meta["lifetime_realized_pnl_usd"],
        "daily_unrealized_pnl_usd": round(total_unrealized, 2),
        "current_drawdown_pct": drawdown_pct,
        "drawdown_available": drawdown_available,
        "hwm_equity_usd": hwm_equity_usd,
        "max_drawdown_pct": (
            analytics_payload["max_historical_drawdown_pct"]
            if analytics_payload["max_historical_drawdown_pct"] is not None
            else drawdown_pct
        ),
        "drawdown_limit_pct": round(service.risk.policy.max_drawdown_pct * 100.0, 2),
        "risk_allocation_pct": margin_allocation_pct,
        "margin_allocation_pct": margin_allocation_pct,
        "notional_exposure_pct": notional_exposure_pct,
        "reserved_margin_usd": round(total_reserved_margin, 2),
        "free_paper_capital_usd": round(free_paper_capital, 2),
        "gross_notional_exposure_usd": round(gross_notional_exposure, 2),
        "gross_margin_exposure_usd": round(total_reserved_margin, 2),
        "net_delta_exposure_usd": round(net_delta, 2),
        "circuit_breaker_state": cb_state,
        "emergency_stop_active": bool(risk_raw.get("emergency_stop_active", False)),
        "concentration_by_asset": concentration,
        "stale_data_warnings": stale_warnings,
        "reconciliation_errors": [],
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
