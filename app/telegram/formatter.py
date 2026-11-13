"""HTML-safe Telegram message formatter with concise emojis and safe truncation."""

from __future__ import annotations

import html
from typing import Iterable

from app.telegram.models import AlertEventType, AlertSeverity, TelegramAlertEvent

MAX_TELEGRAM_HTML_LEN = 3900

_SEVERITY_EMOJI = {
    AlertSeverity.INFO: "🟢",
    AlertSeverity.WARNING: "🟡",
    AlertSeverity.HIGH: "🟠",
    AlertSeverity.CRITICAL: "🔴",
}

_EVENT_EMOJI = {
    AlertEventType.EXCHANGE_STATUS: "📡",
    AlertEventType.OPPORTUNITY: "🎯",
    AlertEventType.ORDER_INTENT: "📝",
    AlertEventType.ORDER_FILL: "✅",
    AlertEventType.ORDER_PARTIAL_FILL: "🌗",
    AlertEventType.ORDER_REJECTED: "⛔",
    AlertEventType.POSITION_OPENED: "📈",
    AlertEventType.POSITION_CLOSED: "📉",
    AlertEventType.PNL_UPDATE: "💹",
    AlertEventType.TP_SL_TRIGGERED: "🏁",
    AlertEventType.RISK_ALERT: "🛡️",
    AlertEventType.CIRCUIT_BREAKER: "🚨",
    AlertEventType.CONNECTIVITY_FAILURE: "🔌",
    AlertEventType.READINESS_TRANSITION: "🧭",
}


def esc(value: object) -> str:
    """Escape text for Telegram HTML parse_mode."""
    return html.escape(str(value), quote=True)


def _fmt_num(val: float | None, decimals: int = 4) -> str:
    if val is None:
        return "—"
    if abs(val) >= 1000:
        return f"{val:,.2f}"
    if abs(val) >= 1:
        return f"{val:,.4f}".rstrip("0").rstrip(".")
    return f"{val:.{decimals}f}".rstrip("0").rstrip(".")


def _is_safe_https_url(url: str | None) -> bool:
    return bool(url and url.startswith("https://") and "@" not in url)


def truncate_lines_safely(lines: Iterable[str], max_len: int = MAX_TELEGRAM_HTML_LEN) -> str:
    """Join pre-escaped HTML lines without exceeding Telegram length limits or breaking tags."""
    out: list[str] = []
    total = 0
    for line in lines:
        add_len = len(line) + (1 if out else 0)
        if total + add_len > max_len - 24:
            out.append("<i>…truncated</i>")
            break
        out.append(line)
        total += add_len
    return "\n".join(out)


def format_alert_html(
    event: TelegramAlertEvent,
    dashboard_url: str | None = None,
) -> str:
    """Render a TelegramAlertEvent as concise, escaped HTML."""
    sev_icon = _SEVERITY_EMOJI.get(event.severity, "ℹ️")
    ev_icon = _EVENT_EMOJI.get(event.event_type, "•")
    sev_label = event.severity.value.upper()

    lines: list[str] = [
        f"{sev_icon} {ev_icon} <b>{esc(event.title)}</b>",
        f"<b>Mode:</b> <code>PAPER_ONLY</code>  <b>Severity:</b> <code>{esc(sev_label)}</code>",
    ]

    meta_parts: list[str] = []
    if event.exchange:
        ex_str = event.exchange.upper()
        if event.market_type:
            ex_str += f" ({event.market_type.upper()})"
        meta_parts.append(f"<b>Exchange:</b> <code>{esc(ex_str)}</code>")
    if event.symbol:
        meta_parts.append(f"<b>Symbol:</b> <code>{esc(event.symbol)}</code>")
    if meta_parts:
        lines.append("  ".join(meta_parts))

    trade_parts: list[str] = []
    if event.side:
        trade_parts.append(f"<b>Side:</b> <code>{esc(event.side.upper())}</code>")
    if event.price is not None:
        trade_parts.append(f"<b>Price:</b> <code>{esc(_fmt_num(event.price, 6))}</code>")
    if event.quantity is not None:
        trade_parts.append(f"<b>Qty:</b> <code>{esc(_fmt_num(event.quantity, 6))}</code>")
    if trade_parts:
        lines.append("  ".join(trade_parts))

    if event.pnl_usd is not None:
        sign = "+" if event.pnl_usd >= 0 else ""
        lines.append(f"<b>PnL:</b> <code>{sign}{event.pnl_usd:,.2f} USD</code>")

    if event.details:
        lines.append(f"{esc(event.details)}")

    ts_str = event.timestamp.strftime("%Y-%m-%d %H:%M:%S UTC")
    footer = f"<b>Time:</b> <code>{esc(ts_str)}</code>  <b>ID:</b> <code>{esc(event.event_id)}</code>"
    lines.append(footer)

    if _is_safe_https_url(dashboard_url):
        lines.append(f'🔗 <a href="{esc(dashboard_url)}">Open Dashboard</a>')

    return truncate_lines_safely(lines)
