"""Event adapter translating real scanner, paper ledger, risk, and readiness state into Telegram alerts."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.models import OrderStatus, PaperOrder, PaperPosition
from app.telegram.client import TelegramAPIError, TelegramClient
from app.telegram.config import TelegramConfig
from app.telegram.formatter import format_alert_html
from app.telegram.models import AlertEventType, AlertSeverity, TelegramAlertEvent
from app.telegram.state import TelegramStateStore

if TYPE_CHECKING:
    from app.service import TradingEngineService

logger = logging.getLogger("multi_exchange.telegram.adapter")

DEFAULT_READINESS_PATH = Path("/root/multi-exchange-paper/data/readiness_watch.json")


class TelegramAlertAdapter:
    """Non-invasive, deduplicated, rate-limited alert adapter for the paper engine."""

    def __init__(
        self,
        config: TelegramConfig | None,
        state_store: TelegramStateStore | None = None,
        client: TelegramClient | None = None,
        readiness_path: Path = DEFAULT_READINESS_PATH,
    ) -> None:
        self.config = config
        self.state = state_store or (
            TelegramStateStore(config.state_path) if config else None
        )
        self.client = client or (TelegramClient(config) if config and config.enabled else None)
        self.readiness_path = readiness_path
        self._seen_order_statuses: dict[str, str] = {}
        self._seen_positions: set[str] = set()
        self._last_pnl_bucket: float | None = None

    @property
    def is_enabled(self) -> bool:
        return bool(self.config and self.config.enabled and self.client and self.state)

    async def emit(self, event: TelegramAlertEvent) -> bool:
        """Format, deduplicate, rate-limit, send, and record a TelegramAlertEvent."""
        if not self.is_enabled or self.config is None or self.state is None or self.client is None:
            return False

        window = (
            event.cooldown_sec
            if event.cooldown_sec is not None
            else self.config.dedup_window_sec
        )
        allowed, reason = self.state.check_and_reserve_send(
            dedup_key=event.dedup_key,
            dedup_window_sec=window,
            rate_limit_per_min=self.config.rate_limit_per_min,
        )
        if not allowed:
            logger.debug("Alert suppressed (%s): %s", reason, event.event_type.value)
            return False

        html_text = format_alert_html(event, dashboard_url=self.config.dashboard_url)
        try:
            res = await self.client.send_message(html_text)
            msg_id = res.get("message_id") if isinstance(res, dict) else None
            self.state.record_send_result(event, success=True, message_id=msg_id)
            return True
        except TelegramAPIError as exc:
            self.state.record_send_result(event, success=False, error=str(exc))
            logger.warning("Telegram send failed for %s: %s", event.event_type.value, exc)
            return False
        except Exception as exc:
            self.state.record_send_result(
                event, success=False, error=f"{type(exc).__name__}"
            )
            logger.warning("Unexpected Telegram error: %s", type(exc).__name__)
            return False

    async def emit_startup_recovery(self, recovery: dict[str, Any]) -> None:
        recovered = int(recovery.get("recovered_orders", 0) or 0)
        if recovered <= 0:
            return
        await self.emit(
            TelegramAlertEvent(
                event_type=AlertEventType.EXCHANGE_STATUS,
                severity=AlertSeverity.WARNING,
                title="Paper Engine Startup Recovery",
                details=f"Reconciled {recovered} incomplete paper order(s); open positions: {recovery.get('open_positions', 0)}.",
                dedup_key=f"startup_recovery:{recovered}",
                cooldown_sec=300.0,
            )
        )

    async def on_cycle_complete(
        self,
        service: TradingEngineService,
        orders: list[PaperOrder],
        closed_exits: list[PaperOrder],
    ) -> None:
        """Inspect real cycle outputs and emit alerts only for verified state changes."""
        if not self.is_enabled:
            return

        await self._check_exchange_health(service)
        await self._check_risk_state(service)
        await self._check_orders(orders, closed_exits, service)
        await self._check_opportunities(service)
        await self._check_readiness()

    async def _check_exchange_health(self, service: TradingEngineService) -> None:
        if self.state is None:
            return
        for item in service.registry.health_summary():
            ex = str(item.get("exchange", "unknown"))
            healthy = bool(item.get("healthy", False))
            err_count = int(item.get("consecutive_errors", 0) or 0)
            last_err = item.get("last_error")
            prev = self.state.update_exchange_health(ex, healthy)

            if prev is None and healthy:
                continue
            if not healthy and (prev is True or err_count >= 3):
                await self.emit(
                    TelegramAlertEvent(
                        event_type=AlertEventType.CONNECTIVITY_FAILURE,
                        severity=AlertSeverity.HIGH,
                        title=f"{ex.upper()} Connectivity Degraded",
                        exchange=ex,
                        details=f"Consecutive errors: {err_count}. Last error: {last_err or 'unavailable'}",
                        dedup_key=f"conn_fail:{ex}",
                        cooldown_sec=300.0,
                    )
                )
            elif healthy and prev is False:
                await self.emit(
                    TelegramAlertEvent(
                        event_type=AlertEventType.EXCHANGE_STATUS,
                        severity=AlertSeverity.INFO,
                        title=f"{ex.upper()} Feed Recovered",
                        exchange=ex,
                        details="Read-only market data feed is healthy again.",
                        dedup_key=f"conn_ok:{ex}",
                        cooldown_sec=120.0,
                    )
                )

    async def _check_risk_state(self, service: TradingEngineService) -> None:
        if self.state is None:
            return
        em_stop = bool(service.risk.emergency_stop_active)
        cb = bool(service.risk.circuit_breaker_tripped)
        prev = self.state.update_risk_flags(em_stop, cb)

        if cb and not prev.get("circuit_breaker"):
            await self.emit(
                TelegramAlertEvent(
                    event_type=AlertEventType.CIRCUIT_BREAKER,
                    severity=AlertSeverity.CRITICAL,
                    title="Circuit Breaker Tripped",
                    details=f"Reason: {service.risk.circuit_breaker_reason or 'risk threshold exceeded'}",
                    dedup_key="risk:circuit_breaker:tripped",
                    cooldown_sec=300.0,
                )
            )
        if em_stop and not prev.get("emergency_stop"):
            await self.emit(
                TelegramAlertEvent(
                    event_type=AlertEventType.RISK_ALERT,
                    severity=AlertSeverity.CRITICAL,
                    title="Emergency Stop Active",
                    details=f"Reason: {service.risk.emergency_stop_reason or 'manual or policy stop'}",
                    dedup_key="risk:emergency_stop:active",
                    cooldown_sec=300.0,
                )
            )
        elif (not em_stop and prev.get("emergency_stop")) or (not cb and prev.get("circuit_breaker")):
            await self.emit(
                TelegramAlertEvent(
                    event_type=AlertEventType.RISK_ALERT,
                    severity=AlertSeverity.INFO,
                    title="Risk Halt Cleared",
                    details="Emergency stop and circuit breaker are now clear (PAPER_ONLY).",
                    dedup_key="risk:cleared",
                    cooldown_sec=120.0,
                )
            )

    async def _check_orders(
        self,
        orders: list[PaperOrder],
        closed_exits: list[PaperOrder],
        service: TradingEngineService,
    ) -> None:
        positions: list[PaperPosition] = service.db.list_positions()
        current_pos_ids = {p.position_id for p in positions}
        if not self._seen_positions and current_pos_ids:
            self._seen_positions = set(current_pos_ids)

        for order in orders:
            prev_status = self._seen_order_statuses.get(order.order_id)
            if prev_status == order.status.value:
                continue
            self._seen_order_statuses[order.order_id] = order.status.value

            if order.status == OrderStatus.REJECTED:
                await self.emit(
                    TelegramAlertEvent(
                        event_type=AlertEventType.ORDER_REJECTED,
                        severity=AlertSeverity.WARNING,
                        title="Paper Order Rejected",
                        exchange=order.exchange.value,
                        symbol=order.symbol,
                        market_type=order.market_type.value,
                        side=order.side.value,
                        quantity=order.requested_qty,
                        details=f"Bot: {order.bot_id} · Reason: {order.reject_reason or 'risk_rejected'}",
                        dedup_key=f"order_rej:{order.order_id}",
                        cooldown_sec=300.0,
                    )
                )
            elif order.status == OrderStatus.PARTIALLY_FILLED:
                await self.emit(
                    TelegramAlertEvent(
                        event_type=AlertEventType.ORDER_PARTIAL_FILL,
                        severity=AlertSeverity.INFO,
                        title="Paper Order Partial Fill",
                        exchange=order.exchange.value,
                        symbol=order.symbol,
                        market_type=order.market_type.value,
                        side=order.side.value,
                        price=order.avg_fill_price,
                        quantity=order.filled_qty,
                        details=f"Bot: {order.bot_id} · Filled {order.filled_qty}/{order.requested_qty}",
                        dedup_key=f"order_pfill:{order.order_id}",
                        cooldown_sec=60.0,
                    )
                )
            elif order.status == OrderStatus.FILLED:
                await self.emit(
                    TelegramAlertEvent(
                        event_type=AlertEventType.ORDER_FILL,
                        severity=AlertSeverity.INFO,
                        title="Paper Order Filled",
                        exchange=order.exchange.value,
                        symbol=order.symbol,
                        market_type=order.market_type.value,
                        side=order.side.value,
                        price=order.avg_fill_price,
                        quantity=order.filled_qty,
                        details=f"Bot: {order.bot_id} · Fee: ${order.fee_usd:.4f} · Slip: {order.slippage_bps:.1f} bps",
                        dedup_key=f"order_fill:{order.order_id}",
                        cooldown_sec=300.0,
                    )
                )

        # Detect newly opened positions
        for pos in positions:
            if pos.position_id not in self._seen_positions:
                self._seen_positions.add(pos.position_id)
                await self.emit(
                    TelegramAlertEvent(
                        event_type=AlertEventType.POSITION_OPENED,
                        severity=AlertSeverity.INFO,
                        title="Paper Position Opened",
                        exchange=pos.exchange.value,
                        symbol=pos.symbol,
                        market_type=pos.market_type.value,
                        side=pos.direction.value,
                        price=pos.entry_price,
                        quantity=pos.qty,
                        details=f"Bot: {pos.bot_id} · SL: {pos.stop_loss or '—'} · TP: {pos.take_profit or '—'}",
                        dedup_key=f"pos_open:{pos.position_id}",
                        cooldown_sec=300.0,
                    )
                )

        # TP / SL exits
        for exit_order in closed_exits:
            await self.emit(
                TelegramAlertEvent(
                    event_type=AlertEventType.TP_SL_TRIGGERED,
                    severity=AlertSeverity.INFO,
                    title="Paper TP/SL Exit Triggered",
                    exchange=exit_order.exchange.value,
                    symbol=exit_order.symbol,
                    market_type=exit_order.market_type.value,
                    side=exit_order.side.value,
                    price=exit_order.avg_fill_price,
                    quantity=exit_order.filled_qty,
                    details=f"Bot: {exit_order.bot_id} · Fee: ${exit_order.fee_usd:.4f}",
                    dedup_key=f"tp_sl:{exit_order.order_id}",
                    cooldown_sec=300.0,
                )
            )

        # Realized + unrealized PnL milestone update only when orders/exits happened
        if orders or closed_exits:
            accounts = service.db.list_accounts()
            total_eq = service.paper_engine.total_portfolio_equity(service.scanner.latest_snapshots)
            initial_eq = sum(a["initial_equity_usd"] for a in accounts) or service.config.initial_paper_equity_usd
            total_pnl = round(total_eq - initial_eq, 2)
            bucket = round(total_pnl / 25.0) * 25.0
            if self._last_pnl_bucket is None or bucket != self._last_pnl_bucket:
                self._last_pnl_bucket = bucket
                await self.emit(
                    TelegramAlertEvent(
                        event_type=AlertEventType.PNL_UPDATE,
                        severity=AlertSeverity.INFO,
                        title="Paper Portfolio PnL Update",
                        pnl_usd=total_pnl,
                        details=f"Equity: ${total_eq:,.2f} (Initial: ${initial_eq:,.2f}) · Open positions: {len(positions)}",
                        dedup_key=f"pnl_bucket:{bucket}",
                        cooldown_sec=300.0,
                    )
                )

    async def _check_opportunities(self, service: TradingEngineService) -> None:
        if self.config is None:
            return
        top = [
            s
            for s in service.scanner.ranked_opportunities[:3]
            if s.opportunity_score >= 72.0
        ]
        for snap in top:
            await self.emit(
                TelegramAlertEvent(
                    event_type=AlertEventType.OPPORTUNITY,
                    severity=AlertSeverity.INFO,
                    title="High-Score Scanner Opportunity",
                    exchange=snap.exchange.value,
                    symbol=snap.symbol,
                    market_type=snap.market_type.value,
                    price=snap.last_price,
                    details=(
                        f"Score: {snap.opportunity_score:.1f}/100 · "
                        f"Trend: {snap.trend_strength:+.2f} · "
                        f"Spread: {snap.spread_bps:.1f} bps"
                    ),
                    dedup_key=f"opp:{snap.exchange.value}:{snap.market_type.value}:{snap.symbol}",
                    cooldown_sec=self.config.opportunity_cooldown_sec,
                )
            )

    async def _check_readiness(self) -> None:
        if self.state is None or not self.readiness_path.exists():
            return
        try:
            report = json.loads(self.readiness_path.read_text(encoding="utf-8"))
        except Exception:
            return
        checks = report.get("checks", {})
        failed = sorted(k for k, v in checks.items() if v is not True)
        summary = {
            "failed": failed,
            "live_execution": report.get("live_execution"),
            "live_readiness": report.get("live_readiness"),
        }
        prev = self.state.get_readiness_summary()
        if prev == summary:
            return

        sev = AlertSeverity.HIGH if failed else AlertSeverity.INFO
        detail = (
            f"Failed checks: {', '.join(failed)}"
            if failed
            else "All operational readiness checks passing."
        )
        sent = await self.emit(
            TelegramAlertEvent(
                event_type=AlertEventType.READINESS_TRANSITION,
                severity=sev,
                title="Operational Readiness Transition",
                details=f"{detail} · Live execution: {summary['live_execution']} · Readiness: {summary['live_readiness']}",
                dedup_key=f"readiness:{','.join(failed) or 'ok'}:{summary['live_execution']}:{summary['live_readiness']}",
                cooldown_sec=60.0,
            )
        )
        if sent:
            self.state.set_readiness_summary(summary)

    async def close(self) -> None:
        if self.client is not None:
            await self.client.close()
