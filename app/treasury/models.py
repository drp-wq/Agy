"""Domain models, decimal helpers, and lifecycle invariants for the unified paper treasury."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.core.models import ExchangeId

QUANT_8 = Decimal("0.00000001")
QUANT_2 = Decimal("0.01")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_decimal(value: Decimal | int | float | str, quant: Decimal = QUANT_8) -> Decimal:
    """Convert numeric input to quantized Decimal without binary float drift."""
    if isinstance(value, Decimal):
        dec = value
    else:
        dec = Decimal(str(value))
    return dec.quantize(quant, rounding=ROUND_HALF_UP)


class TreasuryError(ValueError):
    """Base error for unified treasury domain violations."""


class InsufficientFundsError(TreasuryError):
    """Raised when a reservation, allocation, or withdrawal exceeds available funds."""


class DuplicateIdempotencyError(TreasuryError):
    """Raised when an idempotency key or transfer ID is reused inconsistently."""


class InvalidTransferTransitionError(TreasuryError):
    """Raised when a transfer lifecycle state transition is invalid."""


class TreasuryInvariantError(TreasuryError):
    """Raised when accounting conservation or double-entry invariants fail."""


class LiveTransferBlockedError(RuntimeError):
    """Raised if any real exchange transfer, withdrawal, or live order is attempted."""


class LedgerScope(str, Enum):
    PAPER_SIMULATED = "paper_simulated"
    EXCHANGE_READONLY = "exchange_readonly"


class AgentSlotStatus(str, Enum):
    IDLE = "idle"
    ACTIVE = "active"
    PAUSED = "paused"
    DISABLED = "disabled"


class TransferLifecycleState(str, Enum):
    TREASURY_UNALLOCATED = "treasury_unallocated"
    RESERVED = "reserved"
    TRANSFER_PROPOSED = "transfer_proposed"
    USER_APPROVED = "user_approved"
    EXTERNAL_TRANSFER_PENDING = "external_transfer_pending"
    CONFIRMED_FROM_EXCHANGE_READONLY_EVIDENCE = "confirmed_from_exchange_readonly_evidence"
    AGENT_AVAILABLE = "agent_available"
    PAPER_TRADING = "paper_trading"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


VALID_TRANSFER_TRANSITIONS: dict[TransferLifecycleState, set[TransferLifecycleState]] = {
    TransferLifecycleState.TREASURY_UNALLOCATED: {
        TransferLifecycleState.RESERVED,
        TransferLifecycleState.REJECTED,
        TransferLifecycleState.CANCELLED,
    },
    TransferLifecycleState.RESERVED: {
        TransferLifecycleState.TRANSFER_PROPOSED,
        TransferLifecycleState.REJECTED,
        TransferLifecycleState.CANCELLED,
    },
    TransferLifecycleState.TRANSFER_PROPOSED: {
        TransferLifecycleState.USER_APPROVED,
        TransferLifecycleState.REJECTED,
        TransferLifecycleState.CANCELLED,
    },
    TransferLifecycleState.USER_APPROVED: {
        TransferLifecycleState.EXTERNAL_TRANSFER_PENDING,
        TransferLifecycleState.REJECTED,
        TransferLifecycleState.CANCELLED,
    },
    TransferLifecycleState.EXTERNAL_TRANSFER_PENDING: {
        TransferLifecycleState.CONFIRMED_FROM_EXCHANGE_READONLY_EVIDENCE,
        TransferLifecycleState.REJECTED,
        TransferLifecycleState.CANCELLED,
    },
    TransferLifecycleState.CONFIRMED_FROM_EXCHANGE_READONLY_EVIDENCE: {
        TransferLifecycleState.AGENT_AVAILABLE,
        TransferLifecycleState.REJECTED,
    },
    TransferLifecycleState.AGENT_AVAILABLE: {
        TransferLifecycleState.PAPER_TRADING,
        TransferLifecycleState.CANCELLED,
    },
    TransferLifecycleState.PAPER_TRADING: set(),
    TransferLifecycleState.REJECTED: set(),
    TransferLifecycleState.CANCELLED: set(),
}


class AttributionCategory(str, Enum):
    TRADE_REALIZED_PNL = "trade_realized_pnl"
    TRADE_UNREALIZED_PNL = "trade_unrealized_pnl"
    TRADING_FEE = "trading_fee"
    FUNDING = "funding"
    SLIPPAGE = "slippage"
    TRANSFER_FEE = "transfer_fee"
    EXTERNAL_DEPOSIT = "external_deposit"
    EXTERNAL_WITHDRAWAL = "external_withdrawal"


NON_TRADING_PROFIT_CATEGORIES: frozenset[AttributionCategory] = frozenset(
    {
        AttributionCategory.EXTERNAL_DEPOSIT,
        AttributionCategory.EXTERNAL_WITHDRAWAL,
        AttributionCategory.TRANSFER_FEE,
    }
)


class FxRateSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=lambda: f"fx_{uuid4().hex[:12]}")
    base_asset: str
    quote_asset: str = "USD"
    rate: Decimal
    source: str = "manual_paper_feed"
    as_of: datetime = Field(default_factory=utc_now)

    @field_validator("base_asset", "quote_asset")
    @classmethod
    def _upper_asset(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("rate")
    @classmethod
    def _positive_rate(cls, value: Decimal) -> Decimal:
        dec = to_decimal(value)
        if dec <= Decimal("0"):
            raise ValueError("FX rate must be positive.")
        return dec


class MasterPoolState(BaseModel):
    pool_id: str = "master_pool"
    asset: str = "USDT"
    total_capital: Decimal
    free_capital: Decimal
    reserved_capital: Decimal
    ledger_scope: LedgerScope = LedgerScope.PAPER_SIMULATED
    is_paper: bool = True
    updated_at: datetime = Field(default_factory=utc_now)


class AgentSlotConfig(BaseModel):
    agent_id: str
    slot_index: int = Field(ge=1, le=18)
    name: str
    strategy: str
    exchange: ExchangeId
    account_id: str
    allowed_symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    capital_budget: Decimal = Decimal("0")
    reserved_capital: Decimal = Decimal("0")
    available_capital: Decimal = Decimal("0")
    trading_capital: Decimal = Decimal("0")
    max_risk_per_trade_pct: Decimal = Decimal("0.01")
    daily_loss_limit_pct: Decimal = Decimal("0.02")
    max_drawdown_pct: Decimal = Decimal("0.08")
    max_leverage: Decimal = Decimal("1.0")
    status: AgentSlotStatus = AgentSlotStatus.IDLE
    is_paper: bool = True
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("is_paper")
    @classmethod
    def _paper_only(cls, value: bool) -> bool:
        if not value:
            raise LiveTransferBlockedError("Agent slot must be paper-only.")
        return True


class JournalEntryLine(BaseModel):
    account_code: str
    asset: str
    debit: Decimal = Decimal("0")
    credit: Decimal = Decimal("0")
    usd_value: Decimal = Decimal("0")

    @field_validator("debit", "credit", "usd_value")
    @classmethod
    def _non_negative(cls, value: Decimal) -> Decimal:
        dec = to_decimal(value)
        if dec < Decimal("0"):
            raise ValueError("Journal line amounts cannot be negative.")
        return dec


class JournalEntry(BaseModel):
    entry_id: str = Field(default_factory=lambda: f"jrn_{uuid4().hex[:12]}")
    idempotency_key: str
    event_type: str
    reference_id: str
    ledger_scope: LedgerScope = LedgerScope.PAPER_SIMULATED
    is_paper: bool = True
    fx_rate_to_usd: Decimal
    fx_timestamp: datetime
    lines: list[JournalEntryLine]
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class TransferRequestRecord(BaseModel):
    transfer_id: str = Field(default_factory=lambda: f"trf_{uuid4().hex[:12]}")
    idempotency_key: str
    agent_id: str
    source_account: str
    destination_account: str
    asset: str
    amount: Decimal
    fee_amount: Decimal = Decimal("0")
    fx_rate_to_usd: Decimal
    fx_timestamp: datetime
    state: TransferLifecycleState = TransferLifecycleState.TREASURY_UNALLOCATED
    ledger_scope: LedgerScope = LedgerScope.PAPER_SIMULATED
    is_paper: bool = True
    ownership_verified: bool = False
    approved_by: str | None = None
    readonly_evidence_ref: str | None = None
    manual_instruction: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AttributionRecord(BaseModel):
    attribution_id: str = Field(default_factory=lambda: f"att_{uuid4().hex[:12]}")
    idempotency_key: str
    agent_id: str
    exchange: str
    account_id: str
    strategy: str
    symbol: str
    asset: str
    event_category: AttributionCategory
    amount: Decimal
    fx_rate_to_usd: Decimal
    usd_value: Decimal
    is_trading_profit: bool
    is_paper: bool = True
    created_at: datetime = Field(default_factory=utc_now)


class RebalanceProposal(BaseModel):
    proposal_id: str = Field(default_factory=lambda: f"reb_{uuid4().hex[:12]}")
    agent_id: str
    current_budget: Decimal
    proposed_budget: Decimal
    risk_adjusted_score: Decimal
    reason: str
    approved: bool = False
    blocked_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
