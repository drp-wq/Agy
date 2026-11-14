"""Fail-closed metrics from this preview's service and paper treasury only."""
from datetime import datetime, timezone
from decimal import Decimal
from statistics import mean, stdev

from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED
from app.intelligence.unified_brain import VenueBotMetrics
from app.treasury.closed_loop_router import ClosedLoopVenue


def collect_service_metrics(service, router):
    if not LIVE_EXECUTION_PERMANENTLY_DISABLED:
        raise ValueError("paper_execution_gate_unavailable")
    now = datetime.now(timezone.utc)
    if service.last_cycle_at is None or not 0 <= (now - service.last_cycle_at).total_seconds() <= 120:
        raise ValueError("service_metrics_missing_or_stale")
    if service.treasury is None or not service.treasury.reconcile()["balanced"]:
        raise ValueError("treasury_metrics_unavailable_or_unreconciled")
    accounts = service.db.list_accounts()
    slots = service.treasury.list_agent_slots()
    if not accounts or not slots:
        raise ValueError("paper_accounts_or_targets_unavailable")
    conn = router._connection
    if conn is None:
        raise ValueError("metrics_require_router_transaction")
    conn.execute("CREATE TABLE IF NOT EXISTS unified_brain_observations "
                 "(venue TEXT, observed_at TEXT, equity TEXT, PRIMARY KEY (venue, observed_at))")
    metrics = []
    for exchange in ("binance", "okx", "bybit"):
        selected = [a for a in accounts if a["exchange"] == exchange]
        target = sum((s.capital_budget for s in slots if s.exchange.value == exchange), Decimal(0))
        if not selected or target <= 0:
            continue
        # Open positions need a fresh market valuation; cash-only evidence is insufficient.
        if any(p.exchange.value == exchange for p in service.db.list_positions()):
            continue
        equity = sum((Decimal(str(a["cash_usd"])) for a in selected), Decimal(0))
        if not equity.is_finite() or equity <= 0:
            continue
        conn.execute("INSERT OR IGNORE INTO unified_brain_observations VALUES (?, ?, ?)",
                     (exchange, service.last_cycle_at.isoformat(), str(equity)))
        rows = conn.execute("SELECT equity FROM unified_brain_observations WHERE venue = ? "
                            "ORDER BY observed_at DESC LIMIT 30", (exchange,)).fetchall()
        values = [float(r[0]) for r in reversed(rows)]
        if len(values) < 4:
            continue
        returns = [b / a - 1 for a, b in zip(values, values[1:])]
        volatility = stdev(returns)
        if volatility == 0:
            continue
        pnl = sum((Decimal(str(a["realized_pnl_usd"])) for a in selected), Decimal(0))
        metrics.append(VenueBotMetrics(
            venue=ClosedLoopVenue(f"cex_{exchange}"), net_pnl_usd=pnl,
            sharpe_estimate=Decimal(str(mean(returns) / volatility)),
            drawdown_pct=Decimal(str((max(values) - values[-1]) / max(values))),
            target_capital_usd=target,
        ))
    return metrics
