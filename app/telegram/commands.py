"""Authorized read-only Telegram command router (/start, /help, /status, /exchanges, /alerts, /risk, /pnl, /positions)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED
from app.telegram.config import TelegramConfig
from app.telegram.formatter import _fmt_num, _is_safe_https_url, esc, truncate_lines_safely
from app.telegram.state import TelegramStateStore

if TYPE_CHECKING:
    from app.service import TradingEngineService

BOT_COMMANDS: list[dict[str, str]] = [
    {"command": "status", "description": "Paper engine & Telegram health"},
    {"command": "exchanges", "description": "Binance, Bybit & OKX feed status"},
    {"command": "pnl", "description": "Paper portfolio & realized/unrealized PnL"},
    {"command": "positions", "description": "Open paper positions"},
    {"command": "risk", "description": "Risk guardian & circuit breaker state"},
    {"command": "alerts", "description": "Recent alerts"},
    {"command": "help", "description": "List read-only commands & Mini App"},
]


class TelegramCommandHandler:
    """Strictly read-only command handler restricted to the authorized owner CHAT_ID."""

    def __init__(
        self,
        service: TradingEngineService,
        config: TelegramConfig,
        state_store: TelegramStateStore,
        readiness_path: Path = Path("/root/multi-exchange-paper/data/readiness_watch.json"),
    ) -> None:
        self.service = service
        self.config = config
        self.state = state_store
        self.readiness_path = readiness_path

    def is_authorized(self, message: dict[str, Any]) -> bool:
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", "")).strip()
        if not chat_id or chat_id != self.config.chat_id:
            return False
        sender = message.get("from") or {}
        sender_id = str(sender.get("id", "")).strip()
        if sender_id and chat.get("type") == "private" and sender_id != self.config.chat_id:
            return False
        return True

    def handle_update(self, update: dict[str, Any]) -> tuple[str, dict[str, Any] | None] | None:
        """Process a Telegram update. Returns (html_reply, reply_markup) or None if ignored/unauthorized."""
        message = update.get("message")
        if not isinstance(message, dict):
            return None

        if not self.is_authorized(message):
            self.state.record_command_metric(authorized=False)
            return None

        text = str(message.get("text") or "").strip()
        if not text.startswith("/"):
            return None

        cmd = text.split()[0].split("@")[0].lower()
        self.state.record_command_metric(authorized=True)

        if cmd in ("/start", "/help"):
            return self._cmd_help()
        if cmd == "/status":
            return self._cmd_status(), None
        if cmd == "/exchanges":
            return self._cmd_exchanges(), None
        if cmd == "/alerts":
            return self._cmd_alerts(), None
        if cmd == "/risk":
            return self._cmd_risk(), None
        if cmd == "/pnl":
            return self._cmd_pnl(), None
        if cmd == "/positions":
            return self._cmd_positions(), None

        return (
            "ℹ️ <b>Read-only bot</b>\n"
            f"Unknown or unsupported command <code>{esc(cmd)}</code>.\n"
            "Use /help to view available read-only commands.",
            None,
        )

    def _miniapp_markup(self) -> dict[str, Any] | None:
        if not _is_safe_https_url(self.config.miniapp_url):
            return None
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "⚡ Open NEON QUANT Mini App",
                        "web_app": {"url": self.config.miniapp_url},
                    }
                ]
            ]
        }

    def _cmd_help(self) -> tuple[str, dict[str, Any] | None]:
        lines = [
            "⚡ <b>NEON QUANT // Read-Only Terminal</b>",
            f"<b>Mode:</b> <code>PAPER_ONLY</code> · <b>Live Execution:</b> <code>{'DISABLED' if LIVE_EXECUTION_PERMANENTLY_DISABLED else 'UNKNOWN'}</code>",
            "",
            "<b>Available Read-Only Commands:</b>",
            "• /status — Engine, cycle, portfolio & Telegram health",
            "• /exchanges — Binance, Bybit & OKX feed & auth status",
            "• /pnl — Paper equity, realized & unrealized PnL",
            "• /positions — Open paper positions with SL/TP",
            "• /risk — Risk guardian, drawdown & circuit breakers",
            "• /alerts — Recent alerts & risk events",
            "• /help — Show this help & Mini App launcher",
            "",
            "<i>Read-only mode: order placement, withdrawals, and credential edits are disabled.</i>",
        ]
        return truncate_lines_safely(lines), self._miniapp_markup()

    def _cmd_status(self) -> str:
        snap = self.service.state_snapshot()
        port = snap.get("portfolio", {})
        tg_metrics = self.state.get_metrics()
        last_cycle = snap.get("last_cycle_at") or "unavailable (no cycle completed yet)"
        readiness_str = "unavailable"
        if self.readiness_path.exists():
            try:
                rep = json.loads(self.readiness_path.read_text(encoding="utf-8"))
                readiness_str = (
                    f"{'PASS' if rep.get('operational_checks_passed') else 'FAIL'} "
                    f"(live={rep.get('live_readiness', 'NOT_APPROVED')})"
                )
            except Exception:
                readiness_str = "unavailable"

        lines = [
            "📡 <b>Engine Status</b>",
            f"<b>Mode:</b> <code>{esc(snap.get('mode', 'PAPER_ONLY'))}</code> · <b>Live Locked:</b> <code>{esc(snap.get('live_execution_disabled'))}</code>",
            f"<b>Cycles:</b> <code>{esc(snap.get('cycle_count', 0))}</code> · <b>Last Cycle:</b> <code>{esc(last_cycle)}</code>",
            f"<b>Paper Equity:</b> <code>${port.get('total_equity_usd', 0.0):,.2f}</code> · <b>Open Positions:</b> <code>{len(port.get('open_positions', []))}</code>",
            f"<b>Readiness:</b> <code>{esc(readiness_str)}</code>",
            f"<b>Telegram Alerts:</b> sent=<code>{tg_metrics.get('sent_count', 0)}</code> failed=<code>{tg_metrics.get('failed_count', 0)}</code> deduped=<code>{tg_metrics.get('deduped_count', 0)}</code>",
            f"<b>Checked:</b> <code>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</code>",
        ]
        return truncate_lines_safely(lines)

    def _cmd_exchanges(self) -> str:
        health_list = self.service.registry.health_summary()
        auth_ver = self.service.auth_verification or {}
        if not health_list:
            return "📡 <b>Exchanges</b>\nUnavailable: no exchange adapters registered."

        lines = ["📡 <b>Exchange Feed &amp; Auth Status</b>"]
        for item in health_list:
            ex = str(item.get("exchange", "unknown")).lower()
            healthy = bool(item.get("healthy", False))
            icon = "🟢" if healthy else "🔴"
            auth_info = auth_ver.get(ex)
            auth_status = auth_info.get("status", "unverified") if isinstance(auth_info, dict) else "unverified"
            latency = item.get("last_latency_ms")
            lat_str = f"{latency:.0f}ms" if isinstance(latency, (int, float)) else "—"
            errs = item.get("consecutive_errors", 0)
            lines.append(
                f"{icon} <b>{esc(ex.upper())}</b> · health=<code>{'OK' if healthy else 'DEGRADED'}</code> · "
                f"auth=<code>{esc(auth_status)}</code> · lat=<code>{esc(lat_str)}</code> · errs=<code>{esc(errs)}</code>"
            )
            if item.get("last_error"):
                lines.append(f"   <i>last_error: {esc(str(item['last_error'])[:120])}</i>")
        return truncate_lines_safely(lines)

    def _cmd_pnl(self) -> str:
        snap = self.service.state_snapshot()
        port = snap.get("portfolio", {})
        accounts = self.service.db.list_accounts()
        positions = self.service.db.list_positions()

        realized_total = sum(float(a.get("realized_pnl_usd", 0.0)) for a in accounts)
        unrealized_total = sum(float(p.unrealized_pnl_usd) for p in positions)
        total_eq = float(port.get("total_equity_usd", 0.0))
        init_eq = float(port.get("initial_equity_usd", 0.0))
        net_pnl = float(port.get("total_pnl_usd", 0.0))
        net_pct = float(port.get("total_pnl_pct", 0.0))

        lines = [
            "💹 <b>Paper Portfolio PnL</b>",
            f"<b>Total Equity:</b> <code>${total_eq:,.2f}</code> (Initial: <code>${init_eq:,.2f}</code>)",
            f"<b>Net PnL:</b> <code>{net_pnl:+,.2f} USD</code> (<code>{net_pct:+.2f}%</code>)",
            f"<b>Realized PnL:</b> <code>{realized_total:+,.2f} USD</code> · <b>Unrealized:</b> <code>{unrealized_total:+,.2f} USD</code>",
        ]
        if accounts:
            lines.append("")
            lines.append("<b>Per-Bot Paper Accounts:</b>")
            for a in accounts:
                lines.append(
                    f"• <code>{esc(a['bot_id'])}</code> ({esc(str(a['exchange']).upper())}): "
                    f"cash=<code>${float(a['cash_usd']):,.2f}</code> · realized=<code>{float(a['realized_pnl_usd']):+,.2f}</code>"
                )
        else:
            lines.append("<i>Per-bot account breakdown unavailable.</i>")
        return truncate_lines_safely(lines)

    def _cmd_positions(self) -> str:
        positions = self.service.db.list_positions()
        if not positions:
            return "📈 <b>Open Paper Positions</b>\nNo open paper positions."

        lines = [f"📈 <b>Open Paper Positions ({len(positions)})</b>"]
        for p in positions[:15]:
            lines.append(
                f"• <b>{esc(p.exchange.value.upper())}</b> <code>{esc(p.symbol)}</code> "
                f"({esc(p.market_type.value.upper())} {esc(p.direction.value.upper())}) "
                f"qty=<code>{esc(_fmt_num(p.qty, 6))}</code> "
                f"entry=<code>{esc(_fmt_num(p.entry_price, 4))}</code> "
                f"mark=<code>{esc(_fmt_num(p.mark_price, 4))}</code> "
                f"uPnL=<code>{p.unrealized_pnl_usd:+,.2f}</code>"
            )
            if p.stop_loss is not None or p.take_profit is not None:
                lines.append(
                    f"   SL=<code>{esc(_fmt_num(p.stop_loss, 4))}</code> · TP=<code>{esc(_fmt_num(p.take_profit, 4))}</code>"
                )
        return truncate_lines_safely(lines)

    def _cmd_risk(self) -> str:
        positions = self.service.db.list_positions()
        total_eq = self.service.paper_engine.total_portfolio_equity(self.service.scanner.latest_snapshots)
        summary = self.service.risk.status_summary(total_eq, positions)

        em_stop = summary.get("emergency_stop_active", False)
        cb = summary.get("circuit_breaker_tripped", False)
        icon = "🔴" if (em_stop or cb) else "🟢"
        lines = [
            f"🛡️ <b>Global Risk Guardian</b> {icon}",
            f"<b>Emergency Stop:</b> <code>{'ACTIVE' if em_stop else 'CLEAR'}</code>",
            f"<b>Circuit Breaker:</b> <code>{'TRIPPED' if cb else 'CLEAR'}</code>",
            f"<b>Peak Equity:</b> <code>${float(summary.get('peak_equity_usd', 0.0)):,.2f}</code>",
            f"<b>Drawdown:</b> <code>{float(summary.get('drawdown_pct', 0.0)):.2f}%</code> · <b>Daily Loss:</b> <code>{float(summary.get('daily_loss_pct', 0.0)):.2f}%</code>",
            f"<b>Open Positions:</b> <code>{summary.get('open_positions_count', len(positions))}</code>",
        ]
        if summary.get("emergency_stop_reason"):
            lines.append(f"<b>Stop Reason:</b> <code>{esc(summary['emergency_stop_reason'])}</code>")
        if summary.get("circuit_breaker_reason"):
            lines.append(f"<b>CB Reason:</b> <code>{esc(summary['circuit_breaker_reason'])}</code>")
        return truncate_lines_safely(lines)

    def _cmd_alerts(self) -> str:
        tg_alerts = self.state.list_recent_alerts(limit=8)
        risk_alerts = self.service.risk.alerts[:5]
        if not tg_alerts and not risk_alerts:
            return "🔔 <b>Recent Alerts</b>\nNo alerts recorded yet."

        lines = ["🔔 <b>Recent Alerts</b>"]
        for a in tg_alerts:
            status_tag = "sent" if a.get("delivered") else "failed"
            lines.append(
                f"• [{esc(str(a.get('severity', 'info')).upper())}] <b>{esc(a.get('title', 'Alert'))}</b> "
                f"({esc(status_tag)}) — <code>{esc(str(a.get('timestamp', ''))[:19])}</code>"
            )
        if risk_alerts:
            lines.append("")
            lines.append("<b>Risk Engine Alerts:</b>")
            for r in risk_alerts:
                lines.append(
                    f"• [{esc(r.get('level', 'INFO'))}] {esc(r.get('message', ''))}"
                )
        return truncate_lines_safely(lines)
