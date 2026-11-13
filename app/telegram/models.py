"""Domain models for Telegram alerts, commands, and health metrics."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"
    CRITICAL = "critical"


class AlertEventType(str, Enum):
    EXCHANGE_STATUS = "exchange_status"
    OPPORTUNITY = "opportunity"
    ORDER_INTENT = "order_intent"
    ORDER_FILL = "order_fill"
    ORDER_PARTIAL_FILL = "order_partial_fill"
    ORDER_REJECTED = "order_rejected"
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    PNL_UPDATE = "pnl_update"
    TP_SL_TRIGGERED = "tp_sl_triggered"
    RISK_ALERT = "risk_alert"
    CIRCUIT_BREAKER = "circuit_breaker"
    CONNECTIVITY_FAILURE = "connectivity_failure"
    READINESS_TRANSITION = "readiness_transition"


class TelegramAlertEvent(BaseModel):
    """Structured read-only alert event derived strictly from real engine/monitor data."""

    event_id: str = Field(default_factory=lambda: uuid4().hex[:10])
    event_type: AlertEventType
    severity: AlertSeverity = AlertSeverity.INFO
    title: str
    exchange: str | None = None
    symbol: str | None = None
    market_type: str | None = None
    side: str | None = None
    price: float | None = None
    quantity: float | None = None
    pnl_usd: float | None = None
    details: str = ""
    dedup_key: str | None = None
    cooldown_sec: float | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)
