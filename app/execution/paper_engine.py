"""Deterministic paper execution engine with partial fills, fee/slippage models, idempotency, and recovery."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import Any

from app.core.models import (
    Direction,
    ExchangeId,
    MarketSnapshot,
    MarketType,
    OrderStatus,
    OrderType,
    PaperFill,
    PaperOrder,
    PaperPosition,
    Side,
    StrategySignal,
)
from app.core.security import assert_paper_only
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian

logger = logging.getLogger("multi_exchange.execution")

# Exchange-specific fee schedules (taker / maker)
EXCHANGE_FEE_SCHEDULE: dict[tuple[ExchangeId, MarketType], dict[str, float]] = {
    (ExchangeId.OKX, MarketType.SPOT): {"maker": 0.0008, "taker": 0.0010},
    (ExchangeId.OKX, MarketType.PERP): {"maker": 0.0002, "taker": 0.0005},
    (ExchangeId.BINANCE, MarketType.SPOT): {"maker": 0.0010, "taker": 0.0010},
    (ExchangeId.BINANCE, MarketType.PERP): {"maker": 0.0002, "taker": 0.0005},
    (ExchangeId.BYBIT, MarketType.SPOT): {"maker": 0.0010, "taker": 0.0010},
    (ExchangeId.BYBIT, MarketType.PERP): {"maker": 0.0002, "taker": 0.00055},
}


class PaperExecutionEngine:
    """Thread-safe, idempotent, persistent paper order & position coordinator."""

    def __init__(self, db: PaperDatabase, risk_guardian: GlobalRiskGuardian) -> None:
        assert_paper_only("paper")
        self.db = db
        self.risk = risk_guardian
        self._lock = threading.RLock()

    def ensure_bot_account(self, bot_id: str, exchange: ExchangeId, initial_equity_usd: float) -> dict[str, Any]:
        return self.db.ensure_account(bot_id, exchange.value, initial_equity_usd)

    def total_portfolio_equity(self, snapshots: dict[str, MarketSnapshot] | None = None) -> float:
        accounts = self.db.list_accounts()
        positions = self.db.list_positions()
        cash_total = sum(a["cash_usd"] for a in accounts)
        unrealized = 0.0
        for pos in positions:
            mark = pos.mark_price
            if snapshots:
                key = f"{pos.exchange.value}:{pos.market_type.value}:{pos.symbol}"
                if key in snapshots:
                    mark = snapshots[key].last_price
            diff = (mark - pos.entry_price) if pos.direction == Direction.LONG else (pos.entry_price - mark)
            unrealized += diff * pos.qty
        return round(cash_total + unrealized, 2)

    @staticmethod
    def _simulate_fill_slices(
        requested_qty: float,
        price: float,
        side: Side,
        snapshot: MarketSnapshot,
        fee_rate: float,
    ) -> list[tuple[float, float, float, float]]:
        """Return list of (slice_qty, exec_price, fee_usd, slippage_bps) supporting partial fills."""
        notional = requested_qty * price
        depth = max(snapshot.order_book_depth_usd, 1000.0)
        half_spread_bps = max(1.0, snapshot.spread_bps / 2.0)

        # If notional > 2% of visible depth, split into 2 deterministic partial fill tranches
        if notional > depth * 0.02 and requested_qty > 0.0002:
            ratios = (0.6, 0.4)
        else:
            ratios = (1.0,)

        slices: list[tuple[float, float, float, float]] = []
        for idx, ratio in enumerate(ratios):
            slice_qty = round(requested_qty * ratio, 6)
            if slice_qty <= 0:
                continue
            impact_bps = (notional * ratio / depth) * 25.0 + (idx * 1.5)
            slippage_bps = round(half_spread_bps + impact_bps, 3)
            slip_mult = 1.0 + (slippage_bps / 10000.0) if side == Side.BUY else 1.0 - (slippage_bps / 10000.0)
            exec_price = round(price * slip_mult, 6)
            fee_usd = round(slice_qty * exec_price * fee_rate, 6)
            slices.append((slice_qty, exec_price, fee_usd, slippage_bps))
        return slices

    def submit_signal_order(
        self,
        bot_id: str,
        signal: StrategySignal,
        requested_notional_usd: float,
        leverage: float,
        snapshot: MarketSnapshot | None,
        client_order_id: str,
        exchange_healthy: bool = True,
    ) -> PaperOrder:
        """Process a strategy signal through Risk Guardian and deterministic paper order state machine."""
        assert_paper_only("paper")
        with self._lock:
            existing = self.db.get_order_by_client_id(client_order_id)
            if existing is not None:
                return existing

            positions = self.db.list_positions()
            total_equity = self.total_portfolio_equity()
            decision = self.risk.evaluate(
                signal=signal,
                requested_notional_usd=requested_notional_usd,
                leverage=leverage,
                total_equity_usd=total_equity,
                positions=positions,
                snapshot=snapshot,
                exchange_healthy=exchange_healthy,
            )

            side = Side.BUY if signal.direction == Direction.LONG else Side.SELL
            order = PaperOrder(
                client_order_id=client_order_id,
                bot_id=bot_id,
                signal_id=signal.signal_id,
                exchange=signal.exchange,
                symbol=signal.symbol,
                market_type=signal.market_type,
                side=side,
                order_type=OrderType.MARKET,
                requested_qty=decision.adjusted_qty if decision.approved else 0.0,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit_targets[0] if signal.take_profit_targets else None,
                leverage=leverage if signal.market_type == MarketType.PERP else 1.0,
                status=OrderStatus.CREATED,
                is_paper=True,
            )

            if not decision.approved or snapshot is None:
                order.status = OrderStatus.REJECTED
                order.reject_reason = decision.reason
                order.updated_at = datetime.now(timezone.utc)
                self.db.save_order(order)
                self.db.log_audit(
                    "ORDER_REJECTED",
                    bot_id,
                    {"order_id": order.order_id, "reason": decision.reason, "symbol": signal.symbol},
                )
                return order

            # Transition: CREATED -> VALIDATED -> SUBMITTED
            order.status = OrderStatus.VALIDATED
            self.db.save_order(order)
            order.status = OrderStatus.SUBMITTED
            self.db.save_order(order)

            fee_sched = EXCHANGE_FEE_SCHEDULE.get(
                (signal.exchange, signal.market_type), {"maker": 0.0008, "taker": 0.0010}
            )
            slices = self._simulate_fill_slices(
                requested_qty=order.requested_qty,
                price=snapshot.last_price,
                side=side,
                snapshot=snapshot,
                fee_rate=fee_sched["taker"],
            )

            total_filled = 0.0
            total_cost = 0.0
            total_fee = 0.0
            weighted_slip = 0.0

            for idx, (s_qty, s_price, s_fee, s_slip) in enumerate(slices):
                fill = PaperFill(
                    order_id=order.order_id,
                    bot_id=bot_id,
                    exchange=signal.exchange,
                    symbol=signal.symbol,
                    market_type=signal.market_type,
                    side=side,
                    qty=s_qty,
                    price=s_price,
                    fee_usd=s_fee,
                    slippage_bps=s_slip,
                    is_paper=True,
                )
                self.db.save_fill(fill)
                total_filled += s_qty
                total_cost += s_qty * s_price
                total_fee += s_fee
                weighted_slip += s_slip * s_qty

                order.filled_qty = round(total_filled, 6)
                order.avg_fill_price = round(total_cost / total_filled, 6)
                order.fee_usd = round(total_fee, 6)
                order.slippage_bps = round(weighted_slip / total_filled, 3)
                order.status = (
                    OrderStatus.PARTIALLY_FILLED if idx < len(slices) - 1 else OrderStatus.FILLED
                )
                order.updated_at = datetime.now(timezone.utc)
                self.db.save_order(order)

            self._apply_fill_to_position_and_account(order, snapshot.last_price)
            self.db.log_audit(
                "PAPER_ORDER_FILLED",
                bot_id,
                {
                    "order_id": order.order_id,
                    "exchange": order.exchange.value,
                    "symbol": order.symbol,
                    "side": order.side.value,
                    "qty": order.filled_qty,
                    "avg_price": order.avg_fill_price,
                    "fee_usd": order.fee_usd,
                    "is_paper": True,
                },
            )
            return order

    def _apply_fill_to_position_and_account(self, order: PaperOrder, mark_price: float) -> None:
        self.db.update_account_cash(order.bot_id, cash_delta=-order.fee_usd, realized_pnl_delta=-order.fee_usd)
        existing_list = [
            p
            for p in self.db.list_positions(order.bot_id)
            if p.exchange == order.exchange
            and p.symbol == order.symbol
            and p.market_type == order.market_type
        ]
        direction = Direction.LONG if order.side == Side.BUY else Direction.SHORT

        if not existing_list:
            pos = PaperPosition(
                bot_id=order.bot_id,
                exchange=order.exchange,
                symbol=order.symbol,
                market_type=order.market_type,
                direction=direction,
                qty=order.filled_qty,
                entry_price=order.avg_fill_price,
                mark_price=mark_price,
                leverage=order.leverage,
                stop_loss=order.stop_loss,
                take_profit=order.take_profit,
                unrealized_pnl_usd=0.0,
                is_paper=True,
            )
            self.db.upsert_position(pos)
            return

        pos = existing_list[0]
        if pos.direction == direction:
            new_qty = pos.qty + order.filled_qty
            pos.entry_price = round(
                ((pos.entry_price * pos.qty) + (order.avg_fill_price * order.filled_qty)) / new_qty,
                6,
            )
            pos.qty = round(new_qty, 6)
            pos.mark_price = mark_price
            pos.stop_loss = order.stop_loss or pos.stop_loss
            pos.take_profit = order.take_profit or pos.take_profit
            pos.updated_at = datetime.now(timezone.utc)
            self.db.upsert_position(pos)
        else:
            close_qty = min(pos.qty, order.filled_qty)
            pnl_per_unit = (
                (order.avg_fill_price - pos.entry_price)
                if pos.direction == Direction.LONG
                else (pos.entry_price - order.avg_fill_price)
            )
            realized = round(pnl_per_unit * close_qty, 6)
            self.db.update_account_cash(order.bot_id, cash_delta=realized, realized_pnl_delta=realized)
            remaining = round(pos.qty - close_qty, 6)
            if remaining <= 1e-8:
                pos.qty = 0.0
                self.db.upsert_position(pos)
            else:
                pos.qty = remaining
                pos.mark_price = mark_price
                pos.realized_pnl_usd = round(pos.realized_pnl_usd + realized, 6)
                pos.updated_at = datetime.now(timezone.utc)
                self.db.upsert_position(pos)

    def mark_to_market_and_check_exits(self, snapshots: dict[str, MarketSnapshot]) -> list[PaperOrder]:
        """Update unrealized PnL and trigger paper SL/TP exits deterministically."""
        closed_orders: list[PaperOrder] = []
        with self._lock:
            for pos in self.db.list_positions():
                key = f"{pos.exchange.value}:{pos.market_type.value}:{pos.symbol}"
                snap = snapshots.get(key)
                if snap is None:
                    continue
                px = snap.last_price
                diff = (px - pos.entry_price) if pos.direction == Direction.LONG else (pos.entry_price - px)
                pos.mark_price = px
                pos.unrealized_pnl_usd = round(diff * pos.qty, 4)
                pos.updated_at = datetime.now(timezone.utc)
                self.db.upsert_position(pos)

                hit_sl = (
                    pos.stop_loss is not None
                    and ((pos.direction == Direction.LONG and px <= pos.stop_loss)
                         or (pos.direction == Direction.SHORT and px >= pos.stop_loss))
                )
                hit_tp = (
                    pos.take_profit is not None
                    and ((pos.direction == Direction.LONG and px >= pos.take_profit)
                         or (pos.direction == Direction.SHORT and px <= pos.take_profit))
                )
                if hit_sl or hit_tp:
                    exit_side = Side.SELL if pos.direction == Direction.LONG else Side.BUY
                    fee_sched = EXCHANGE_FEE_SCHEDULE.get(
                        (pos.exchange, pos.market_type), {"taker": 0.001}
                    )
                    fee = round(pos.qty * px * fee_sched["taker"], 6)
                    exit_order = PaperOrder(
                        client_order_id=f"exit_{pos.position_id}_{int(datetime.now(timezone.utc).timestamp())}",
                        bot_id=pos.bot_id,
                        signal_id="sl_tp_exit",
                        exchange=pos.exchange,
                        symbol=pos.symbol,
                        market_type=pos.market_type,
                        side=exit_side,
                        order_type=OrderType.MARKET,
                        requested_qty=pos.qty,
                        filled_qty=pos.qty,
                        avg_fill_price=px,
                        fee_usd=fee,
                        slippage_bps=2.0,
                        status=OrderStatus.FILLED,
                        is_paper=True,
                    )
                    self.db.save_order(exit_order)
                    self._apply_fill_to_position_and_account(exit_order, px)
                    closed_orders.append(exit_order)
        return closed_orders

    def recover_on_startup(self) -> dict[str, Any]:
        """Reconcile any incomplete orders left behind after an unclean shutdown."""
        incomplete = self.db.list_incomplete_orders()
        recovered = 0
        for order in incomplete:
            if order.filled_qty > 0:
                order.status = OrderStatus.FILLED
            else:
                order.status = OrderStatus.CANCELED
                order.reject_reason = "canceled_on_crash_recovery"
            order.updated_at = datetime.now(timezone.utc)
            self.db.save_order(order)
            recovered += 1
        self.db.log_audit("STARTUP_RECOVERY", "system", {"recovered_orders": recovered})
        return {"recovered_orders": recovered, "open_positions": len(self.db.list_positions())}

    def reconcile_positions(self) -> dict[str, Any]:
        """Verify all positions and accounts have valid paper invariants."""
        accounts = self.db.list_accounts()
        positions = self.db.list_positions()
        orders = self.db.list_orders(limit=500)
        non_paper = [o.order_id for o in orders if not o.is_paper]
        return {
            "accounts": len(accounts),
            "open_positions": len(positions),
            "checked_orders": len(orders),
            "non_paper_violations": len(non_paper),
            "reconciled": len(non_paper) == 0,
        }
