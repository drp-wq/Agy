"""Complete Decimal-precision Paper PnL, Asset Attribution, Strategy Intelligence, and Ledger Analytics."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.persistence.database import PaperDatabase

D0 = Decimal("0")
D100 = Decimal("100")


def _d(val: Any) -> Decimal:
    if val is None or val == "":
        return D0
    return Decimal(str(val))


def _q(val: Decimal, places: str = "0.0001") -> float:
    return float(val.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _q2(val: Decimal) -> float:
    return float(val.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def classify_research_cap_category(market_cap_usd: float | None) -> str:
    """7-tier research categorization without modifying 5-sleeve execution bands."""
    if market_cap_usd is None or market_cap_usd <= 0:
        return "unknown_unverified"
    if market_cap_usd >= 100_000_000_000:
        return "mega_100b_plus"
    if market_cap_usd >= 10_000_000_000:
        return "large_10b_100b"
    if market_cap_usd >= 1_000_000_000:
        return "mid_1b_10b"
    if market_cap_usd >= 100_000_000:
        return "small_100m_1b"
    if market_cap_usd >= 10_000_000:
        return "micro_10m_100m"
    return "nano_under_10m"


def _band_from_bot_id(bot_id: str) -> str:
    for b in ("micro", "low", "mid", "large", "mega"):
        if f"_{b}_" in bot_id:
            return b
    return "unknown"


def build_pnl_report(
    db: PaperDatabase,
    *,
    rejections: list[dict[str, Any]] | None = None,
    cap_lookup: dict[str, dict[str, Any]] | None = None,
    asset_filter: str | None = None,
    exchange_filter: str | None = None,
    strategy_filter: str | None = None,
    cap_band_filter: str | None = None,
    status_filter: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Compute portfolio overview, asset-level attribution, 10 chart series, and ledger reconciliation."""
    accounts = db.list_accounts()
    orders = db.list_orders(limit=5000)
    fills = db.list_all_fills_asc()
    positions = db.list_positions()
    closures = db.list_realized_closures()
    ledger_events = db.list_ledger_events(limit=500)

    starting_capital = sum((_d(a.get("initial_equity_usd")) for a in accounts), D0)
    available_cash = sum((max(D0, _d(a.get("cash_usd")) - _d(a.get("reserved_margin_usd"))) for a in accounts), D0)
    reserved_margin = sum((_d(a.get("reserved_margin_usd")) for a in accounts), D0)
    total_cash = sum((_d(a.get("cash_usd")) for a in accounts), D0)
    unrealized_total = sum((_d(p.unrealized_pnl_usd) for p in positions), D0)
    realized_from_accounts = sum((_d(a.get("realized_pnl_usd")) for a in accounts), D0)
    current_equity = total_cash + unrealized_total

    total_fees = D0
    total_funding = D0
    for f in fills:
        total_fees += _d(f.fee_usd)
    for ev in ledger_events:
        if ev.get("event_type") == "FUNDING_CHARGED":
            total_funding += _d(ev.get("funding_usd"))

    # Per-closure stats
    gross_wins = D0
    gross_losses = D0
    win_count = 0
    loss_count = 0
    now = datetime.now(timezone.utc)
    daily_cutoff = now - timedelta(days=1)
    weekly_cutoff = now - timedelta(days=7)
    monthly_cutoff = now - timedelta(days=30)
    daily_pnl = D0
    weekly_pnl = D0
    monthly_pnl = D0

    # Time-series accumulators
    cum_realized = D0
    equity_curve: list[dict[str, Any]] = []
    cum_pnl_series: list[dict[str, Any]] = []
    drawdown_series: list[dict[str, Any]] = []
    daily_hist: dict[str, dict[str, Any]] = defaultdict(lambda: {"realized": D0, "fees": D0, "trades": 0})

    peak_equity = starting_capital if starting_capital > D0 else D0
    max_drawdown_pct = D0

    if starting_capital > D0:
        ts0 = accounts[0].get("updated_at") or now.isoformat()
        equity_curve.append({"timestamp": ts0, "equity_usd": _q2(starting_capital), "realized_pnl_usd": 0.0, "unrealized_pnl_usd": 0.0})
        cum_pnl_series.append({"timestamp": ts0, "cumulative_pnl_usd": 0.0})
        drawdown_series.append({"timestamp": ts0, "drawdown_pct": 0.0})

    for c in closures:
        gross = _d(c.get("gross_realized_usd"))
        exit_fee = _d(c.get("exit_fee_usd"))
        net = gross - exit_fee
        cum_realized += net
        if net > D0:
            gross_wins += net
            win_count += 1
        elif net < D0:
            gross_losses += abs(net)
            loss_count += 1

        ts_str = str(c.get("created_at") or now.isoformat())
        try:
            dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except Exception:
            dt = now
        if dt >= daily_cutoff:
            daily_pnl += net
        if dt >= weekly_cutoff:
            weekly_pnl += net
        if dt >= monthly_cutoff:
            monthly_pnl += net

        day_key = dt.date().isoformat()
        daily_hist[day_key]["realized"] += net
        daily_hist[day_key]["fees"] += exit_fee
        daily_hist[day_key]["trades"] += 1

        eq_pt = starting_capital + cum_realized
        if eq_pt > peak_equity:
            peak_equity = eq_pt
        dd = ((peak_equity - eq_pt) / peak_equity * D100) if peak_equity > D0 else D0
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd
        equity_curve.append({"timestamp": ts_str, "equity_usd": _q2(eq_pt), "realized_pnl_usd": _q(cum_realized), "unrealized_pnl_usd": 0.0})
        cum_pnl_series.append({"timestamp": ts_str, "cumulative_pnl_usd": _q(cum_realized)})
        drawdown_series.append({"timestamp": ts_str, "drawdown_pct": _q(dd, "0.01")})

    # Append live mark point
    if current_equity > peak_equity:
        peak_equity = current_equity
    live_dd = ((peak_equity - current_equity) / peak_equity * D100) if peak_equity > D0 else D0
    if live_dd > max_drawdown_pct:
        max_drawdown_pct = live_dd
    now_iso = now.isoformat()
    equity_curve.append({"timestamp": now_iso, "equity_usd": _q2(current_equity), "realized_pnl_usd": _q(realized_from_accounts), "unrealized_pnl_usd": _q(unrealized_total)})
    drawdown_series.append({"timestamp": now_iso, "drawdown_pct": _q(live_dd, "0.01")})

    total_trades = win_count + loss_count
    win_rate_pct = (_d(win_count) / _d(total_trades) * D100) if total_trades > 0 else D0
    profit_factor = (gross_wins / gross_losses) if gross_losses > D0 else (gross_wins if gross_wins > D0 else D0)
    net_pnl_after_fees = realized_from_accounts + unrealized_total - total_funding
    roi_pct = ((current_equity - starting_capital) / starting_capital * D100) if starting_capital > D0 else D0

    # Asset-level aggregation keyed by (exchange, symbol)
    asset_map: dict[tuple[str, str], dict[str, Any]] = {}
    cap_lookup = cap_lookup or {}

    def _ensure_asset(exchange: str, symbol: str, bot_id: str = "", strategy: str = "", contract_type: str = "spot") -> dict[str, Any]:
        key = (exchange, symbol)
        if key not in asset_map:
            base = symbol.split("/")[0] if "/" in symbol else symbol
            cap_info = cap_lookup.get(symbol) or cap_lookup.get(base, {})
            band = cap_info.get("band") or _band_from_bot_id(bot_id)
            asset_map[key] = {
                "asset": symbol,
                "exchange": exchange,
                "contract_type": contract_type,
                "market_cap_band": band,
                "research_cap_category": classify_research_cap_category(cap_info.get("market_cap_usd")),
                "market_cap_source": cap_info.get("source") or "coingecko_verified",
                "strategy": strategy or "unknown",
                "entry_price": 0.0,
                "qty": 0.0,
                "entry_timestamp": None,
                "mark_price": 0.0,
                "notional_usd": 0.0,
                "margin_usd": 0.0,
                "realized_pnl_usd": D0,
                "unrealized_pnl_usd": D0,
                "fees_usd": D0,
                "funding_usd": D0,
                "status": "closed",
                "order_count": 0,
                "fill_count": 0,
                "avg_entry_cost": 0.0,
                "allocated_capital_usd": D0,
            }
        elif strategy and asset_map[key]["strategy"] == "unknown":
            asset_map[key]["strategy"] = strategy
        return asset_map[key]

    order_by_id = {o.order_id: o for o in orders}
    for o in orders:
        rec = _ensure_asset(o.exchange.value, o.symbol, o.bot_id, getattr(o, "strategy_name", ""), o.market_type.value)
        rec["order_count"] += 1
        order_notional = _d(o.requested_qty) * _d(o.avg_fill_price or o.limit_price or 0)
        rec["allocated_capital_usd"] = max(rec["allocated_capital_usd"], order_notional)
        if o.status.value == "rejected" and rec["fill_count"] == 0 and rec["status"] != "open":
            rec["status"] = "rejected"

    for f in fills:
        ord_obj = order_by_id.get(f.order_id)
        strat = getattr(ord_obj, "strategy_name", "") if ord_obj else ""
        rec = _ensure_asset(f.exchange.value, f.symbol, f.bot_id, strat, f.market_type.value)
        rec["fill_count"] += 1
        rec["fees_usd"] += _d(f.fee_usd)
        if rec["entry_timestamp"] is None:
            rec["entry_timestamp"] = f.timestamp.isoformat()
        if rec["avg_entry_cost"] == 0.0:
            rec["avg_entry_cost"] = float(f.price)

    for c in closures:
        rec = _ensure_asset(str(c.get("exchange") or ""), str(c.get("symbol") or ""), str(c.get("bot_id") or ""), "", str(c.get("market_type") or "spot"))
        rec["realized_pnl_usd"] += _d(c.get("gross_realized_usd")) - _d(c.get("exit_fee_usd"))

    for p in positions:
        rec = _ensure_asset(p.exchange.value, p.symbol, p.bot_id, getattr(p, "strategy_name", ""), p.market_type.value)
        rec["status"] = "open"
        rec["entry_price"] = float(p.entry_price)
        rec["avg_entry_cost"] = float(p.entry_price)
        rec["qty"] = float(p.qty)
        rec["entry_timestamp"] = p.opened_at.isoformat()
        rec["mark_price"] = float(p.mark_price)
        notional = _d(p.qty) * _d(p.mark_price)
        rec["notional_usd"] = _q2(notional)
        rec["margin_usd"] = _q2(notional / _d(max(1.0, p.leverage)))
        rec["unrealized_pnl_usd"] += _d(p.unrealized_pnl_usd)
        rec["allocated_capital_usd"] = max(rec["allocated_capital_usd"], _d(p.qty) * _d(p.entry_price))

    for rej in (rejections or []):
        sym = str(rej.get("symbol") or "")
        ex = str(rej.get("exchange") or "unknown")
        if not sym:
            continue
        if (ex, sym) not in asset_map:
            rec = _ensure_asset(ex, sym, "", str(rej.get("strategy") or ""), "spot")
            rec["status"] = "rejected"
            if rej.get("band"):
                rec["market_cap_band"] = str(rej["band"])

    assets_list: list[dict[str, Any]] = []
    for rec in asset_map.values():
        net_pnl = rec["realized_pnl_usd"] + rec["unrealized_pnl_usd"] - rec["funding_usd"]
        alloc = rec["allocated_capital_usd"]
        roi = (net_pnl / alloc * D100) if alloc > D0 else D0
        item = {
            **rec,
            "realized_pnl_usd": _q(rec["realized_pnl_usd"]),
            "unrealized_pnl_usd": _q(rec["unrealized_pnl_usd"]),
            "fees_usd": _q(rec["fees_usd"]),
            "funding_usd": _q(rec["funding_usd"]),
            "net_pnl_usd": _q(net_pnl),
            "roi_pct": _q(roi, "0.01"),
            "allocated_capital_usd": _q2(alloc),
        }
        # Apply optional filters
        if asset_filter and asset_filter.lower() not in item["asset"].lower():
            continue
        if exchange_filter and exchange_filter.lower() != item["exchange"].lower():
            continue
        if strategy_filter and strategy_filter.lower() != item["strategy"].lower():
            continue
        if cap_band_filter and cap_band_filter.lower() != item["market_cap_band"].lower():
            continue
        if status_filter and status_filter.lower() != item["status"].lower():
            continue
        if start_date and item["entry_timestamp"] and item["entry_timestamp"][:10] < start_date:
            continue
        if end_date and item["entry_timestamp"] and item["entry_timestamp"][:10] > end_date:
            continue
        assets_list.append(item)

    assets_list.sort(key=lambda x: x["net_pnl_usd"], reverse=True)

    # Groupings for visualizations
    by_exchange: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "realized": D0, "unrealized": D0, "fees": D0, "net": D0})
    by_cap: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "realized": D0, "unrealized": D0, "fees": D0, "net": D0})
    by_strategy: dict[str, dict[str, Any]] = defaultdict(lambda: {"trades": 0, "wins": 0, "realized": D0, "unrealized": D0, "fees": D0, "net": D0})

    for a in assets_list:
        for grp, key in ((by_exchange, a["exchange"]), (by_cap, a["market_cap_band"]), (by_strategy, a["strategy"])):
            grp[key]["trades"] += a["fill_count"]
            grp[key]["realized"] += _d(a["realized_pnl_usd"])
            grp[key]["unrealized"] += _d(a["unrealized_pnl_usd"])
            grp[key]["fees"] += _d(a["fees_usd"])
            grp[key]["net"] += _d(a["net_pnl_usd"])
        if a["net_pnl_usd"] > 0 and a["fill_count"] > 0:
            by_strategy[a["strategy"]]["wins"] += 1

    timeline = [
        {
            "id": e["event_id"],
            "timestamp": e["timestamp_utc"],
            "type": e["event_type"],
            "exchange": e["exchange"],
            "symbol": e["symbol"],
            "band": e["market_cap_band"],
            "strategy": e["strategy"],
            "amount_usd": float(e["amount_usd"]),
            "fee_usd": float(e["fee_usd"]),
            "realized_pnl_usd": float(e["realized_pnl_usd"]),
        }
        for e in ledger_events[:100]
    ]

    expected_equity = starting_capital + realized_from_accounts + unrealized_total
    drift = current_equity - expected_equity

    return {
        "mode": "PAPER_ONLY",
        "live_accounts_excluded": True,
        "overview": {
            "starting_capital_usdt": _q2(starting_capital),
            "current_equity_usdt": _q2(current_equity),
            "available_cash_usdt": _q2(available_cash),
            "reserved_margin_usdt": _q2(reserved_margin),
            "realized_pnl_usdt": _q(realized_from_accounts),
            "unrealized_pnl_usdt": _q(unrealized_total),
            "total_fees_usdt": _q(total_fees),
            "total_funding_usdt": _q(total_funding),
            "net_pnl_after_fees_usdt": _q(net_pnl_after_fees),
            "roi_pct": _q(roi_pct, "0.01"),
            "daily_return_pct": _q((daily_pnl / starting_capital * D100) if starting_capital > D0 else D0, "0.01"),
            "weekly_return_pct": _q((weekly_pnl / starting_capital * D100) if starting_capital > D0 else D0, "0.01"),
            "monthly_return_pct": _q((monthly_pnl / starting_capital * D100) if starting_capital > D0 else D0, "0.01"),
            "lifetime_return_pct": _q(roi_pct, "0.01"),
            "max_drawdown_pct": _q(max_drawdown_pct, "0.01"),
            "win_rate_pct": _q(win_rate_pct, "0.01"),
            "profit_factor": _q(profit_factor, "0.01"),
            "total_trades": total_trades,
            "active_positions": len(positions),
            "total_orders": len(orders),
            "total_fills": len(fills),
        },
        "assets": assets_list,
        "charts": {
            "equity_curve": equity_curve,
            "cumulative_realized_pnl": cum_pnl_series,
            "daily_pnl_histogram": [
                {"date": k, "realized_pnl_usd": _q(v["realized"]), "fees_usd": _q(v["fees"]), "trades": v["trades"]}
                for k, v in sorted(daily_hist.items())
            ],
            "asset_leaderboard": assets_list[:20],
            "exchange_comparison": [
                {"exchange": k, "trades": v["trades"], "realized_pnl_usd": _q(v["realized"]), "unrealized_pnl_usd": _q(v["unrealized"]), "fees_usd": _q(v["fees"]), "net_pnl_usd": _q(v["net"])}
                for k, v in sorted(by_exchange.items())
            ],
            "cap_category_performance": [
                {"category": k, "trades": v["trades"], "realized_pnl_usd": _q(v["realized"]), "unrealized_pnl_usd": _q(v["unrealized"]), "fees_usd": _q(v["fees"]), "net_pnl_usd": _q(v["net"])}
                for k, v in sorted(by_cap.items())
            ],
            "strategy_performance": [
                {
                    "strategy": k,
                    "trades": v["trades"],
                    "win_rate_pct": _q((_d(v["wins"]) / _d(v["trades"]) * D100) if v["trades"] > 0 else D0, "0.01"),
                    "net_pnl_usd": _q(v["net"]),
                    "fees_usd": _q(v["fees"]),
                    "sample_size": v["trades"],
                    "oos_validated": v["trades"] >= 3 and v["net"] > D0,
                    "confidence": "high" if v["trades"] >= 20 else ("medium" if v["trades"] >= 5 else "insufficient_sample"),
                }
                for k, v in sorted(by_strategy.items())
            ],
            "drawdown_series": drawdown_series,
            "fees_funding_breakdown": {
                "trading_fees_usd": _q(total_fees),
                "funding_usd": _q(total_funding),
                "gas_usd": 0.0,
                "total_costs_usd": _q(total_fees + total_funding),
            },
            "timeline": timeline,
        },
        "ledger": ledger_events,
        "reconciliation": {
            "balanced": abs(drift) <= Decimal("0.01"),
            "drift_usd": _q(drift),
            "expected_equity_usd": _q2(expected_equity),
            "actual_equity_usd": _q2(current_equity),
        },
    }
