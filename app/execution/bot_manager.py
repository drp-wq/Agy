"""Concurrent multi-bot manager running independent paper bots on OKX and Bybit."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.models import (
    BotConfig,
    ExchangeId,
    MarketSnapshot,
    MarketType,
    PaperOrder,
    StrategySignal,
)
from app.exchanges.registry import ExchangeRegistry
from app.execution.paper_engine import PaperExecutionEngine


def default_bot_configs() -> list[BotConfig]:
    return [
        BotConfig(
            bot_id="binance_spot_paper",
            name="Binance Spot Paper",
            exchange=ExchangeId.BINANCE,
            market_type=MarketType.SPOT,
            symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            allocated_capital_usd=2500.0,
            risk_per_trade_pct=0.01,
            leverage=1.0,
        ),
        BotConfig(
            bot_id="binance_perp_paper",
            name="Binance Perp Paper",
            exchange=ExchangeId.BINANCE,
            market_type=MarketType.PERP,
            symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            allocated_capital_usd=1500.0,
            risk_per_trade_pct=0.01,
            leverage=2.0,
        ),
        BotConfig(
            bot_id="okx_spot_alpha",
            name="OKX Spot Alpha",
            exchange=ExchangeId.OKX,
            market_type=MarketType.SPOT,
            symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            allocated_capital_usd=3000.0,
            risk_per_trade_pct=0.015,
            leverage=1.0,
        ),
        BotConfig(
            bot_id="okx_perp_momentum",
            name="OKX Perp Momentum",
            exchange=ExchangeId.OKX,
            market_type=MarketType.PERP,
            symbols=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
            allocated_capital_usd=2500.0,
            risk_per_trade_pct=0.012,
            leverage=2.0,
        ),
        BotConfig(
            bot_id="bybit_spot_swing",
            name="Bybit Spot Swing",
            exchange=ExchangeId.BYBIT,
            market_type=MarketType.SPOT,
            symbols=["BTC/USDT", "ETH/USDT", "XRP/USDT"],
            allocated_capital_usd=2500.0,
            risk_per_trade_pct=0.015,
            leverage=1.0,
        ),
        BotConfig(
            bot_id="bybit_perp_funding",
            name="Bybit Perp Funding",
            exchange=ExchangeId.BYBIT,
            market_type=MarketType.PERP,
            symbols=["BTC/USDT", "ETH/USDT", "AVAX/USDT"],
            allocated_capital_usd=2000.0,
            risk_per_trade_pct=0.012,
            leverage=2.0,
        ),
    ]


class MultiBotManager:
    """Runs multiple independent paper-trading bots concurrently across OKX and Bybit."""

    def __init__(
        self,
        paper_engine: PaperExecutionEngine,
        registry: ExchangeRegistry,
        bots: list[BotConfig] | None = None,
    ) -> None:
        self.engine = paper_engine
        self.registry = registry
        self.bots: dict[str, BotConfig] = {
            b.bot_id: b for b in (bots or default_bot_configs())
        }
        for b in self.bots.values():
            self.engine.ensure_bot_account(b.bot_id, b.exchange, b.allocated_capital_usd)

    def toggle_bot(self, bot_id: str, enabled: bool) -> BotConfig:
        if bot_id not in self.bots:
            raise KeyError(f"Unknown bot '{bot_id}'")
        self.bots[bot_id].enabled = enabled
        return self.bots[bot_id]

    async def process_signals_concurrently(
        self,
        signals: list[StrategySignal],
        snapshots: dict[str, MarketSnapshot],
    ) -> list[PaperOrder]:
        async def _run_bot(bot: BotConfig) -> list[PaperOrder]:
            if not bot.enabled:
                return []
            bot_orders: list[PaperOrder] = []
            adapter = self.registry.get(bot.exchange)
            healthy = adapter.health_status()["healthy"]
            acct = self.engine.db.get_account(bot.bot_id)
            cash = acct["cash_usd"] if acct else bot.allocated_capital_usd

            matching = [
                s
                for s in signals
                if s.exchange == bot.exchange
                and s.market_type == bot.market_type
                and s.symbol in bot.symbols
                and (not bot.strategies or s.strategy_name in bot.strategies)
            ]
            for sig in matching[:2]:
                key = f"{sig.exchange.value}:{sig.market_type.value}:{sig.symbol}"
                snap = snapshots.get(key)
                notional = max(25.0, cash * bot.risk_per_trade_pct * 5.0)
                bucket_ts = int(datetime.now(timezone.utc).timestamp() // 60)
                client_oid = f"{bot.bot_id}:{sig.exchange.value}:{sig.symbol}:{sig.strategy_name}:{bucket_ts}"
                order = await asyncio.to_thread(
                    self.engine.submit_signal_order,
                    bot.bot_id,
                    sig,
                    notional,
                    bot.leverage,
                    snap,
                    client_oid,
                    healthy,
                )
                bot_orders.append(order)
            return bot_orders

        results = await asyncio.gather(*(_run_bot(b) for b in self.bots.values()))
        return [ord_item for sublist in results for ord_item in sublist]

    def list_bots_status(self) -> list[dict[str, Any]]:
        out = []
        positions = self.engine.db.list_positions()
        for bot in self.bots.values():
            acct = self.engine.db.get_account(bot.bot_id) or {}
            bot_pos = [p for p in positions if p.bot_id == bot.bot_id]
            out.append(
                {
                    **bot.model_dump(),
                    "cash_usd": round(acct.get("cash_usd", bot.allocated_capital_usd), 2),
                    "realized_pnl_usd": round(acct.get("realized_pnl_usd", 0.0), 2),
                    "open_positions": len(bot_pos),
                }
            )
        return out
