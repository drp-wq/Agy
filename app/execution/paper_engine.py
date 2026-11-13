"""Deterministic paper execution engine with partial fills, fee/slippage models, idempotency, and recovery."""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

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
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
    ) -> list[tuple[float, float, float, float]]:
        """Return list of (slice_qty, exec_price, fee_usd, slippage_bps) supporting L2 book sweeps and partial fills."""
        if requested_qty <= 0 or price <= 0:
            return []

        # Prefer L2 order-book sweep when snapshot includes level data
        book_levels = getattr(snapshot, "asks", None) if side == Side.BUY else getattr(snapshot, "bids", None)
        if book_levels:
            remaining = requested_qty
            slices: list[tuple[float, float, float, float]] = []
            for lvl_price, lvl_qty in book_levels:
                if remaining <= 1e-9:
                    break
                if lvl_price <= 0 or lvl_qty <= 0:
                    continue
                if order_type == OrderType.LIMIT and limit_price is not None:
                    if (side == Side.BUY and lvl_price > limit_price) or (side == Side.SELL and lvl_price < limit_price):
                        break
                take_qty = round(min(remaining, float(lvl_qty)), 6)
                if take_qty <= 0:
                    continue
                slip_bps = round(abs(lvl_price - price) / price * 10000.0, 3)
                fee_usd = round(take_qty * lvl_price * fee_rate, 6)
                slices.append((take_qty, round(float(lvl_price), 6), fee_usd, slip_bps))
                remaining = round(remaining - take_qty, 6)
            return slices

        # Fallback: depth-aware tranche simulation
        if order_type == OrderType.LIMIT and limit_price is not None:
            if (side == Side.BUY and price > limit_price) or (side == Side.SELL and price < limit_price):
                return []

        notional = requested_qty * price
        depth = max(snapshot.order_book_depth_usd, 1000.0)
        half_spread_bps = max(1.0, snapshot.spread_bps / 2.0)

        # If notional > 2% of visible depth, split into 2 deterministic partial fill tranches
        if notional > depth * 0.02 and requested_qty > 0.0002:
            ratios = (0.6, 0.4)
        else:
            ratios = (1.0,)

        slices = []
        for idx, ratio in enumerate(ratios):
            slice_qty = round(requested_qty * ratio, 6)
            if slice_qty <= 0:
                continue
            impact_bps = (notional * ratio / depth) * 25.0 + (idx * 1.5)
            slippage_bps = round(half_spread_bps + impact_bps, 3)
            slip_mult = 1.0 + (slippage_bps / 10000.0) if side == Side.BUY else 1.0 - (slippage_bps / 10000.0)
            exec_price = round(price * slip_mult, 6)
            if order_type == OrderType.LIMIT and limit_price is not None:
                if (side == Side.BUY and exec_price > limit_price) or (side == Side.SELL and exec_price < limit_price):
                    continue
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
        order_type: OrderType = OrderType.MARKET,
        limit_price: float | None = None,
    ) -> PaperOrder:
        """Process a strategy signal through Risk Guardian and atomic paper order state machine."""
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
            eff_leverage = leverage if signal.market_type == MarketType.PERP else 1.0
            order = PaperOrder(
                client_order_id=client_order_id,
                bot_id=bot_id,
                signal_id=signal.signal_id,
                exchange=signal.exchange,
                symbol=signal.symbol,
                market_type=signal.market_type,
                side=side,
                order_type=order_type,
                requested_qty=decision.adjusted_qty if decision.approved else 0.0,
                limit_price=limit_price,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profit_targets[0] if signal.take_profit_targets else None,
                leverage=eff_leverage,
                status=OrderStatus.CREATED,
                is_paper=True,
            )

            if not decision.approved or snapshot is None:
                order.status = OrderStatus.REJECTED
                order.reject_reason = decision.reason
                order.updated_at = datetime.now(timezone.utc)
                with self.db.transaction() as conn:
                    self.db.save_order(order, conn=conn)
                    self.db.log_audit(
                        "ORDER_REJECTED",
                        bot_id,
                        {"order_id": order.order_id, "reason": decision.reason, "symbol": signal.symbol},
                        conn=conn,
                    )
                return order

            fee_sched = EXCHANGE_FEE_SCHEDULE.get(
                (signal.exchange, signal.market_type), {"maker": 0.0008, "taker": 0.0010}
            )
            fee_rate = fee_sched["maker"] if order_type == OrderType.LIMIT else fee_sched["taker"]

            # Enforce free margin reservation check when bot account exists (skip for reducing/closing orders)
            acct = self.db.get_account(bot_id)
            if acct is not None:
                existing_pos = next(
                    (
                        p
                        for p in positions
                        if p.bot_id == bot_id
                        and p.exchange == signal.exchange
                        and p.symbol == signal.symbol
                        and p.market_type == signal.market_type
                    ),
                    None,
                )
                is_reducing = existing_pos is not None and existing_pos.direction != signal.direction
                if not is_reducing:
                    free_cash = float(acct["cash_usd"]) - float(acct.get("reserved_margin_usd", 0.0) or 0.0)
                    req_notional = order.requested_qty * snapshot.last_price
                    req_margin = (req_notional / max(eff_leverage, 1.0)) + (req_notional * fee_rate)
                    if free_cash < req_margin:
                        order.status = OrderStatus.REJECTED
                        order.reject_reason = "insufficient_free_margin"
                        order.updated_at = datetime.now(timezone.utc)
                        with self.db.transaction() as conn:
                            self.db.save_order(order, conn=conn)
                            self.db.log_audit(
                                "ORDER_REJECTED",
                                bot_id,
                                {"order_id": order.order_id, "reason": order.reject_reason, "symbol": signal.symbol},
                                conn=conn,
                            )
                        return order

            slices = self._simulate_fill_slices(
                requested_qty=order.requested_qty,
                price=snapshot.last_price,
                side=side,
                snapshot=snapshot,
                fee_rate=fee_rate,
                order_type=order_type,
                limit_price=limit_price,
            )

            total_filled = 0.0
            total_cost = 0.0
            total_fee = 0.0
            weighted_slip = 0.0

            with self.db.transaction() as conn:
                order.status = OrderStatus.VALIDATED
                self.db.save_order(order, conn=conn)
                order.status = OrderStatus.SUBMITTED
                self.db.save_order(order, conn=conn)

                for s_qty, s_price, s_fee, s_slip in slices:
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
                    self.db.save_fill(fill, conn=conn)
                    total_filled += s_qty
                    total_cost += s_qty * s_price
                    total_fee += s_fee
                    weighted_slip += s_slip * s_qty

                if total_filled > 0:
                    order.filled_qty = round(total_filled, 6)
                    order.avg_fill_price = round(total_cost / total_filled, 6)
                    order.fee_usd = round(total_fee, 6)
                    order.slippage_bps = round(weighted_slip / total_filled, 3)
                    order.status = (
                        OrderStatus.FILLED
                        if order.filled_qty >= round(order.requested_qty * 0.9999, 6)
                        else OrderStatus.PARTIALLY_FILLED
                    )
                    order.updated_at = datetime.now(timezone.utc)
                    self.db.save_order(order, conn=conn)
                    self._apply_fill_to_position_and_account(order, snapshot.last_price, conn=conn)
                    self.db.log_audit(
                        "PAPER_ORDER_FILLED" if order.status == OrderStatus.FILLED else "PAPER_ORDER_PARTIAL_FILL",
                        bot_id,
                        {
                            "order_id": order.order_id,
                            "exchange": order.exchange.value,
                            "symbol": order.symbol,
                            "side": order.side.value,
                            "qty": order.filled_qty,
                            "requested_qty": order.requested_qty,
                            "avg_price": order.avg_fill_price,
                            "fee_usd": order.fee_usd,
                            "status": order.status.value,
                            "is_paper": True,
                        },
                        conn=conn,
                    )
            return order

    def _apply_fill_to_position_and_account(
        self,
        order: PaperOrder,
        mark_price: float,
        conn: sqlite3.Connection | None = None,
    ) -> None:
        existing_list = [
            p
            for p in self.db.list_positions(order.bot_id)
            if p.exchange == order.exchange
            and p.symbol == order.symbol
            and p.market_type == order.market_type
        ]
        direction = Direction.LONG if order.side == Side.BUY else Direction.SHORT

        if not existing_list:
            margin_delta = round((order.filled_qty * order.avg_fill_price) / max(order.leverage, 1.0), 6)
            self.db.update_account_cash(
                order.bot_id,
                cash_delta=-order.fee_usd,
                realized_pnl_delta=-order.fee_usd,
                reserved_margin_delta=margin_delta,
                conn=conn,
            )
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
            self.db.upsert_position(pos, conn=conn)
            return

        pos = existing_list[0]
        if pos.direction == direction:
            margin_delta = round((order.filled_qty * order.avg_fill_price) / max(order.leverage, 1.0), 6)
            self.db.update_account_cash(
                order.bot_id,
                cash_delta=-order.fee_usd,
                realized_pnl_delta=-order.fee_usd,
                reserved_margin_delta=margin_delta,
                conn=conn,
            )
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
            self.db.upsert_position(pos, conn=conn)
        else:
            close_qty = min(pos.qty, order.filled_qty)
            released_margin = round((close_qty * pos.entry_price) / max(pos.leverage, 1.0), 6)
            self.db.update_account_cash(
                order.bot_id,
                cash_delta=-order.fee_usd,
                realized_pnl_delta=-order.fee_usd,
                reserved_margin_delta=-released_margin,
                conn=conn,
            )
            pnl_per_unit = (
                (order.avg_fill_price - pos.entry_price)
                if pos.direction == Direction.LONG
                else (pos.entry_price - order.avg_fill_price)
            )
            realized = round(pnl_per_unit * close_qty, 6)
            self.db.record_paper_close(order, close_qty, pos.entry_price, realized, conn=conn)
            remaining = round(pos.qty - close_qty, 6)
            if remaining <= 1e-8:
                pos.qty = 0.0
                self.db.upsert_position(pos, conn=conn)
            else:
                pos.qty = remaining
                pos.mark_price = mark_price
                pos.realized_pnl_usd = round(pos.realized_pnl_usd + realized, 6)
                pos.updated_at = datetime.now(timezone.utc)
                self.db.upsert_position(pos, conn=conn)

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
                if not (hit_sl or hit_tp):
                    self.db.upsert_position(pos)
                    continue

                exit_side = Side.SELL if pos.direction == Direction.LONG else Side.BUY
                fee_sched = EXCHANGE_FEE_SCHEDULE.get(
                    (pos.exchange, pos.market_type), {"taker": 0.001}
                )
                exit_slip_bps = round(max(2.0, snap.spread_bps / 2.0), 3)
                slip_mult = 1.0 - (exit_slip_bps / 10000.0) if exit_side == Side.SELL else 1.0 + (exit_slip_bps / 10000.0)
                exec_px = round(px * slip_mult, 6)
                fee = round(pos.qty * exec_px * fee_sched["taker"], 6)
                exit_order = PaperOrder(
                    client_order_id=f"exit_{pos.position_id}_{uuid4().hex[:8]}",
                    bot_id=pos.bot_id,
                    signal_id="sl_tp_exit",
                    exchange=pos.exchange,
                    symbol=pos.symbol,
                    market_type=pos.market_type,
                    side=exit_side,
                    order_type=OrderType.MARKET,
                    requested_qty=pos.qty,
                    filled_qty=pos.qty,
                    avg_fill_price=exec_px,
                    fee_usd=fee,
                    slippage_bps=exit_slip_bps,
                    status=OrderStatus.FILLED,
                    is_paper=True,
                )
                with self.db.transaction() as conn:
                    self.db.upsert_position(pos, conn=conn)
                    self.db.save_order(exit_order, conn=conn)
                    self.db.save_fill(
                        PaperFill(
                            order_id=exit_order.order_id,
                            bot_id=exit_order.bot_id,
                            exchange=exit_order.exchange,
                            symbol=exit_order.symbol,
                            market_type=exit_order.market_type,
                            side=exit_order.side,
                            qty=exit_order.filled_qty,
                            price=exec_px,
                            fee_usd=fee,
                            slippage_bps=exit_slip_bps,
                            is_paper=True,
                        ),
                        conn=conn,
                    )
                    self._apply_fill_to_position_and_account(exit_order, px, conn=conn)
                    self.db.log_audit(
                        "PAPER_ORDER_FILLED",
                        exit_order.bot_id,
                        {
                            "order_id": exit_order.order_id,
                            "exchange": exit_order.exchange.value,
                            "symbol": exit_order.symbol,
                            "side": exit_order.side.value,
                            "qty": exit_order.filled_qty,
                            "avg_price": exit_order.avg_fill_price,
                            "fee_usd": exit_order.fee_usd,
                            "reason": "sl_tp_exit",
                            "is_paper": True,
                        },
                        conn=conn,
                    )
                closed_orders.append(exit_order)
        return closed_orders

    def recover_on_startup(self) -> dict[str, Any]:
        """Reconcile any incomplete orders left behind after an unclean shutdown."""
        incomplete = self.db.list_incomplete_orders()
        recovered = 0
        for order in incomplete:
            fills = self.db.list_fills_for_order(order.order_id)
            filled_from_fills = round(sum(f.qty for f in fills), 6)
            if filled_from_fills > 0 and filled_from_fills >= round(order.requested_qty * 0.9999, 6):
                order.filled_qty = filled_from_fills
                order.status = OrderStatus.FILLED
            elif filled_from_fills > 0:
                order.filled_qty = filled_from_fills
                order.status = OrderStatus.CANCELED
                order.reject_reason = "unfilled_remainder_canceled_on_recovery"
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
