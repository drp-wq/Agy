"""Shared domain models for market data, signals, risk, paper orders, and bots."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ExchangeId(str, Enum):
    OKX = "okx"
    BYBIT = "bybit"
    BINANCE = "binance"
    GATEIO = "gateio"


class MarketType(str, Enum):
    SPOT = "spot"
    PERP = "perp"


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class Direction(str, Enum):
    LONG = "long"
    SHORT = "short"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    CREATED = "created"
    VALIDATED = "validated"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"


class MarketRegime(str, Enum):
    TRENDING_BULLISH = "trending_bullish"
    TRENDING_BEARISH = "trending_bearish"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_LIQUIDITY = "low_liquidity"
    UNCERTAIN = "uncertain"


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"


class InstrumentSpec(BaseModel):
    exchange: ExchangeId
    symbol: str
    market_type: MarketType
    base: str
    quote: str
    price_precision: float = 0.01
    amount_precision: float = 0.0001
    min_amount: float = 0.0001
    min_cost: float = 5.0
    contract_size: float = 1.0
    max_leverage: float = 10.0
    maker_fee: float = 0.0008
    taker_fee: float = 0.0010


class OrderBookSnapshot(BaseModel):
    exchange: ExchangeId
    symbol: str
    market_type: MarketType
    source_timestamp_ms: float | None = None
    timestamp: datetime
    best_bid: float
    best_ask: float
    spread_bps: float
    bid_depth_usd: float
    ask_depth_usd: float
    bids: list[tuple[float, float]] = Field(default_factory=list)
    asks: list[tuple[float, float]] = Field(default_factory=list)


class Candle(BaseModel):
    timestamp: int  # epoch ms
    open: float
    high: float
    low: float
    close: float
    volume: float


class MarketSnapshot(BaseModel):
    exchange: ExchangeId
    symbol: str
    market_type: MarketType
    ticker_source_timestamp_ms: float | None = None
    book_source_timestamp_ms: float | None = None
    timestamp: datetime
    last_price: float
    bid: float
    ask: float
    spread_bps: float
    volume_24h_usd: float
    relative_volume: float = 1.0
    volatility: float = 0.0
    trend_strength: float = 0.0
    rsi: float = 50.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_hist: float = 0.0
    atr: float = 0.0
    ema_fast: float = 0.0
    ema_slow: float = 0.0
    ema_trend: float = 0.0
    ema_alignment: str = "neutral"
    support: float = 0.0
    resistance: float = 0.0
    funding_rate: float | None = None
    open_interest_usd: float | None = None
    liquidation_signal: str | None = None
    order_book_depth_usd: float = 0.0
    liquidity_score: float = 0.0
    regime: MarketRegime = MarketRegime.UNCERTAIN
    opportunity_score: float = 0.0
    bids: list[tuple[float, float]] = Field(default_factory=list)
    asks: list[tuple[float, float]] = Field(default_factory=list)
    candles_by_tf: dict[str, list[Candle]] = Field(default_factory=dict)


class StrategySignal(BaseModel):
    signal_id: str = Field(default_factory=lambda: f"sig_{uuid4().hex[:12]}")
    created_at: datetime = Field(default_factory=utc_now)
    strategy_name: str
    exchange: ExchangeId
    symbol: str
    market_type: MarketType
    timeframe: str = "15m"
    direction: Direction
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_targets: list[float]
    invalidation_condition: str
    estimated_fee_pct: float
    estimated_slippage_pct: float
    risk_reward_ratio: float
    confidence_score: float = Field(ge=0.0, le=1.0)
    rationale: str
    regime: MarketRegime = MarketRegime.UNCERTAIN
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaperOrder(BaseModel):
    order_id: str = Field(default_factory=lambda: f"ord_{uuid4().hex[:12]}")
    client_order_id: str
    bot_id: str
    signal_id: str
    exchange: ExchangeId
    symbol: str
    market_type: MarketType
    side: Side
    order_type: OrderType = OrderType.MARKET
    requested_qty: float
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    limit_price: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    leverage: float = 1.0
    fee_usd: float = 0.0
    slippage_bps: float = 0.0
    status: OrderStatus = OrderStatus.CREATED
    is_paper: bool = True
    reject_reason: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class PaperFill(BaseModel):
    fill_id: str = Field(default_factory=lambda: f"fill_{uuid4().hex[:12]}")
    order_id: str
    bot_id: str
    exchange: ExchangeId
    symbol: str
    market_type: MarketType
    side: Side
    qty: float
    price: float
    fee_usd: float
    slippage_bps: float
    is_paper: bool = True
    timestamp: datetime = Field(default_factory=utc_now)


class PaperPosition(BaseModel):
    position_id: str = Field(default_factory=lambda: f"pos_{uuid4().hex[:12]}")
    bot_id: str
    exchange: ExchangeId
    symbol: str
    market_type: MarketType
    direction: Direction
    qty: float
    entry_price: float
    mark_price: float
    leverage: float = 1.0
    stop_loss: float | None = None
    take_profit: float | None = None
    unrealized_pnl_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    is_paper: bool = True
    opened_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class RiskDecision(BaseModel):
    approved: bool
    reason: str = "approved"
    adjusted_qty: float = 0.0
    alerts: list[str] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utc_now)


class BotConfig(BaseModel):
    bot_id: str
    name: str
    exchange: ExchangeId
    market_type: MarketType = MarketType.SPOT
    symbols: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT"])
    strategies: list[str] = Field(default_factory=list)
    allocated_capital_usd: float = 2500.0
    risk_per_trade_pct: float = 0.01
    leverage: float = 1.0
    enabled: bool = True
