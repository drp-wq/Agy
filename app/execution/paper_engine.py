"""Deterministic paper execution engine with partial fills, fee/slippage models, idempotency, and recovery."""

from __future__ import annotations

import logging
import math
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
    RiskDecision,
)
from app.core.security import assert_paper_only
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.execution import paper_protection
from app.treasury import paper_exposure

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

    def __init__(self, db: PaperDatabase, risk_guardian: GlobalRiskGuardian, fee_schedule=None, marketable_limit_taker=False) -> None:
        assert_paper_only("paper")
        self.db = db
        self.risk = risk_guardian
        self.fee_schedule = fee_schedule if fee_schedule is not None else dict(EXCHANGE_FEE_SCHEDULE)
        self.marketable_limit_taker = marketable_limit_taker
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
            invalid = not math.isfinite(requested_notional_usd) or requested_notional_usd<=0 or not math.isfinite(leverage)
            invalid = invalid or (signal.market_type==MarketType.PERP and leverage<1)
            invalid = invalid or any(not math.isfinite(v) or v<=0 for v in [signal.entry_low,signal.entry_high,signal.stop_loss])
            if snapshot is not None:
                invalid = invalid or any(not math.isfinite(v) or v<=0 for v in [snapshot.last_price,snapshot.bid,snapshot.ask])
                invalid = invalid or not math.isfinite(snapshot.spread_bps) or snapshot.spread_bps<0 or snapshot.bid>snapshot.ask
                invalid = invalid or snapshot.timestamp.tzinfo is None
                if snapshot.timestamp.tzinfo is not None:
                    invalid = invalid or (snapshot.timestamp-datetime.now(timezone.utc)).total_seconds()>1
            owned = next((p for p in positions if p.bot_id==bot_id and p.exchange==signal.exchange
                and p.symbol==signal.symbol and p.market_type==signal.market_type),None)
            opposite = owned is not None and owned.direction!=signal.direction
            requested_qty = round(requested_notional_usd/snapshot.last_price,6) if not invalid and snapshot is not None else 0
            if invalid:
                decision = RiskDecision(approved=False,reason='invalid_execution_inputs')
            elif snapshot is None:
                decision = RiskDecision(approved=False,reason='missing_market_snapshot')
            elif opposite and requested_qty<=owned.qty:
                age = (datetime.now(timezone.utc)-snapshot.timestamp).total_seconds()
                if not exchange_healthy or age>self.risk.policy.max_market_data_age_sec:
                    decision = RiskDecision(approved=False,reason='unavailable_or_stale_reduction_quote')
                else:
                    # Reductions remain possible at exposure caps and during an
                    # entry circuit breaker. They cannot create new exposure.
                    decision = RiskDecision(approved=True,reason='reduce_only',adjusted_qty=requested_qty)
            else:
                closing_qty = owned.qty if opposite else 0
                net_notional = max(0,requested_notional_usd-closing_qty*snapshot.last_price) if opposite else requested_notional_usd
                decision = self.risk.evaluate(
                    signal=signal, requested_notional_usd=net_notional, leverage=leverage,
                    total_equity_usd=total_equity,
                    positions=[p for p in positions if p.position_id!=owned.position_id] if opposite else positions,
                    snapshot=snapshot, exchange_healthy=exchange_healthy)
                if opposite and decision.approved:
                    decision.adjusted_qty = round(decision.adjusted_qty+closing_qty,6)


            try:
                paper_protection.validate(signal)
            except (ValueError, TypeError, OverflowError):
                decision.approved = False
                decision.reason = 'invalid_protection_plan'

            side = Side.BUY if signal.direction == Direction.LONG else Side.SELL
            eff_leverage = leverage if signal.market_type == MarketType.PERP and math.isfinite(leverage) and leverage>=1 else 1.0
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
                stop_loss=signal.stop_loss if math.isfinite(signal.stop_loss) and signal.stop_loss>0 else None,
                take_profit=signal.take_profit_targets[0] if signal.take_profit_targets and math.isfinite(signal.take_profit_targets[0]) else None,
                leverage=eff_leverage,
                status=OrderStatus.CREATED,
                is_paper=True,
            )

            if not decision.approved or snapshot is None:
                order.status = OrderStatus.REJECTED
                order.reject_reason = decision.reason
                order.updated_at = datetime.now(timezone.utc)
                with self.db.transaction() as conn:
                    winner = self.db.get_order_by_client_id(client_order_id)
                    if winner is not None:
                        return winner
                    self.db.save_order(order, conn=conn)
                    self.db.log_audit(
                        "ORDER_REJECTED",
                        bot_id,
                        {"order_id": order.order_id, "reason": decision.reason, "symbol": signal.symbol},
                        conn=conn,
                    )
                return order

            fee_sched = self.fee_schedule.get((signal.exchange, signal.market_type, signal.symbol)) or self.fee_schedule.get(
                (signal.exchange, signal.market_type), {"maker": 0.0008, "taker": 0.0010}
            )
            # Immediate book/depth fills consume liquidity even when a limit is
            # specified. A maker discount requires evidenced resting execution.
            fee_rate = fee_sched['taker']

            # A spot sell may reduce an owned long, never synthesize an unfunded short.
            acct = self.db.get_account(bot_id)
            existing_pos = next((p for p in positions if p.bot_id == bot_id
                and p.exchange == signal.exchange and p.symbol == signal.symbol
                and p.market_type == signal.market_type), None)
            reject = None
            if acct is None:
                reject = 'paper_account_required'
            elif signal.market_type == MarketType.SPOT and side == Side.SELL and (
                    existing_pos is None or existing_pos.direction != Direction.LONG or
                    order.requested_qty > existing_pos.qty + 1e-8):
                reject = 'spot_short_or_oversell_not_supported'
            elif existing_pos is not None and existing_pos.direction == signal.direction and abs(existing_pos.leverage-eff_leverage)>1e-9:
                reject = 'position_leverage_change_requires_explicit_resize'
            else:
                free_cash = float(acct['cash_usd']) - float(acct.get('reserved_margin_usd',0) or 0)
                if existing_pos is not None and existing_pos.direction != signal.direction:
                    closed_qty = min(order.requested_qty, existing_pos.qty)
                    residual = max(0.0, order.requested_qty-closed_qty)
                    released = closed_qty*existing_pos.entry_price/existing_pos.leverage
                    pnl = closed_qty*(snapshot.last_price-existing_pos.entry_price)*(1 if existing_pos.direction==Direction.LONG else -1)
                    required = residual*snapshot.last_price/max(eff_leverage,1) + order.requested_qty*snapshot.last_price*fee_rate
                    if residual>0 and free_cash+released+pnl < required:
                        reject = 'insufficient_free_margin_for_reversal'
                else:
                    required = order.requested_qty*snapshot.last_price*(1/max(eff_leverage,1)+fee_rate)
                    if free_cash < required:
                        reject = 'insufficient_free_margin'
            if reject is not None:
                order.status, order.reject_reason = OrderStatus.REJECTED, reject
                with self.db.transaction() as conn:
                    winner = self.db.get_order_by_client_id(client_order_id)
                    if winner is not None:
                        return winner
                    self.db.save_order(order,conn=conn)
                    self.db.log_audit('ORDER_REJECTED',bot_id,{'order_id':order.order_id,'reason':reject},conn=conn)
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

            if any(not all(math.isfinite(v) for v in item) or item[0]<=0 or item[1]<=0 or item[2]<0 for item in slices):
                order.status, order.reject_reason = OrderStatus.REJECTED, 'unsupported_or_invalid_execution_precision'
                with self.db.transaction() as conn:
                    winner = self.db.get_order_by_client_id(client_order_id)
                    if winner is not None:
                        return winner
                    self.db.save_order(order,conn=conn)
                return order

            total_filled = 0.0
            total_cost = 0.0
            total_fee = 0.0
            weighted_slip = 0.0

            with self.db.transaction() as conn:
                winner = self.db.get_order_by_client_id(client_order_id)
                if winner is not None:
                    return winner
                # Recheck actual execution cost under the write transaction, not
                # only the pre-fill mark-price estimate or a stale account row.
                latest_account = self.db.get_account(bot_id)
                latest_pos = next((p for p in self.db.list_positions(bot_id) if p.exchange==order.exchange
                    and p.symbol==order.symbol and p.market_type==order.market_type),None)
                signature = lambda p: None if p is None else (p.position_id,p.direction,p.qty,p.entry_price,p.leverage)
                failure = None
                if latest_account is None or signature(latest_pos)!=signature(existing_pos):
                    failure = 'account_or_position_changed_before_execution'
                else:
                    executed_qty = sum(item[0] for item in slices)
                    executed_cost = sum(item[0]*item[1] for item in slices)
                    execution_fee = sum(item[2] for item in slices)
                    free = latest_account['cash_usd']-latest_account['reserved_margin_usd']
                    if executed_qty>0 and latest_pos is not None and latest_pos.direction!=signal.direction:
                        closing = min(executed_qty,latest_pos.qty)
                        residual = max(0,executed_qty-closing)
                        average = executed_cost/executed_qty
                        freed = closing*latest_pos.entry_price/latest_pos.leverage
                        pnl = closing*(average-latest_pos.entry_price)*(1 if latest_pos.direction==Direction.LONG else -1)
                        if residual>0 and free+freed+pnl < residual*average/eff_leverage+execution_fee:
                            failure = 'insufficient_actual_execution_margin'
                    elif free < executed_cost/max(eff_leverage,1)+execution_fee:
                        failure = 'insufficient_actual_execution_margin'
                if failure:
                    order.status,order.reject_reason = OrderStatus.REJECTED,failure
                    self.db.save_order(order,conn=conn)
                    self.db.log_audit('ORDER_REJECTED',bot_id,{'order_id':order.order_id,'reason':failure},conn=conn)
                    return order
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
                    paper_protection.install(self, signal, order, snapshot, conn)
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

    def _apply_fill_to_position_and_account(self, order, mark_price, conn=None):
        """Financial state and futures exposure evidence share one transaction."""
        assert_paper_only('paper')
        if conn is None:
            with self.db.transaction() as transaction:
                return self._apply_fill_to_position_and_account(order,mark_price,transaction)
        if order.market_type==MarketType.PERP:
            if not paper_exposure.before_execution(self.db,conn,order):
                return
        self._apply_fill_financial_state(order,mark_price,conn)
        if order.market_type==MarketType.PERP:
            paper_exposure.after_execution(self.db,conn,order)

    def _apply_fill_financial_state(
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
                unrealized_pnl_usd=round((mark_price-order.avg_fill_price) * order.filled_qty *
                                          (1 if direction == Direction.LONG else -1), 4),
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
            if order.stop_loss is not None:
                pos.stop_loss = order.stop_loss if pos.stop_loss is None else (
                    max(pos.stop_loss,order.stop_loss) if pos.direction==Direction.LONG else min(pos.stop_loss,order.stop_loss))
            pos.take_profit = order.take_profit or pos.take_profit
            pos.unrealized_pnl_usd = round((mark_price-pos.entry_price)*pos.qty*
                                          (1 if pos.direction == Direction.LONG else -1), 4)
            pos.updated_at = datetime.now(timezone.utc)
            self.db.upsert_position(pos, conn=conn)
        else:
            close_qty = min(pos.qty, order.filled_qty)
            residual_qty = round(order.filled_qty - close_qty, 6)
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
                paper_protection.retire(self,pos.position_id,conn)
                if residual_qty > 1e-8:
                    # Open opposite-direction position for executed reversal remainder so no filled qty disappears
                    new_margin = round((residual_qty * order.avg_fill_price) / max(order.leverage, 1.0), 6)
                    self.db.update_account_cash(
                        order.bot_id,
                        cash_delta=0.0,
                        realized_pnl_delta=0.0,
                        reserved_margin_delta=new_margin,
                        conn=conn,
                    )
                    rev_diff = (
                        (mark_price - order.avg_fill_price)
                        if direction == Direction.LONG
                        else (order.avg_fill_price - mark_price)
                    )
                    rev_pos = PaperPosition(
                        bot_id=order.bot_id,
                        exchange=order.exchange,
                        symbol=order.symbol,
                        market_type=order.market_type,
                        direction=direction,
                        qty=residual_qty,
                        entry_price=order.avg_fill_price,
                        mark_price=mark_price,
                        leverage=order.leverage,
                        stop_loss=order.stop_loss,
                        take_profit=order.take_profit,
                        unrealized_pnl_usd=round(rev_diff * residual_qty, 4),
                        is_paper=True,
                    )
                    self.db.upsert_position(rev_pos, conn=conn)
            else:
                pos.qty = remaining
                pos.mark_price = mark_price
                pos.realized_pnl_usd = round(pos.realized_pnl_usd + realized, 6)
                pos.unrealized_pnl_usd = round((mark_price-pos.entry_price)*pos.qty*
                                              (1 if pos.direction == Direction.LONG else -1), 4)
                pos.updated_at = datetime.now(timezone.utc)
                self.db.upsert_position(pos, conn=conn)

    def mark_to_market_and_check_exits(self, snapshots: dict[str, MarketSnapshot]) -> list[PaperOrder]:
        """Update unrealized PnL and trigger paper SL/TP exits deterministically."""
        closed_orders: list[PaperOrder] = []
        with self._lock:
            handled, advanced_exits = paper_protection.process(self, snapshots)
            closed_orders.extend(advanced_exits)
            for pos in self.db.list_positions():
                if pos.position_id in handled:
                    continue
                key = f"{pos.exchange.value}:{pos.market_type.value}:{pos.symbol}"
                snap = snapshots.get(key)
                if snap is None or not math.isfinite(snap.last_price) or snap.last_price<=0:
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
                fee_sched = self.fee_schedule.get((pos.exchange, pos.market_type, pos.symbol)) or self.fee_schedule.get(
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
            if filled_from_fills > 0:
                order.avg_fill_price = round(sum(f.qty*f.price for f in fills)/filled_from_fills,6)
                order.fee_usd = round(sum(f.fee_usd for f in fills),6)
                order.slippage_bps = round(sum(f.qty*f.slippage_bps for f in fills)/filled_from_fills,3)
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

    def cancel_paper_order(self, client_order_id: str) -> PaperOrder | None:
        """Cancel only the unfilled remainder; executed quantity and cash never reverse."""
        assert_paper_only('paper')
        with self._lock, self.db.transaction() as conn:
            order = self.db.get_order_by_client_id(client_order_id)
            if order is None or order.status in {OrderStatus.FILLED,OrderStatus.CANCELED,OrderStatus.REJECTED}:
                return order
            order.status = OrderStatus.CANCELED
            order.reject_reason = 'paper_unfilled_remainder_canceled'
            order.updated_at = datetime.now(timezone.utc)
            self.db.save_order(order,conn=conn)
            self.db.log_audit('PAPER_ORDER_CANCELED',order.bot_id,
                {'order_id':order.order_id,'filled_qty':order.filled_qty,'is_paper':True},conn=conn)
            return order

    def reconcile_positions(self) -> dict[str, Any]:
        """Check materialized cash, margin, P&L and fill equations; never certify history."""
        accounts = self.db.list_accounts()
        positions = self.db.list_positions()
        errors = []
        with self.db._lock:
            conn = self.db._conn
            count = conn.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0]
            non_paper = conn.execute('SELECT COUNT(*) FROM paper_orders WHERE is_paper!=1').fetchone()[0]
            non_paper += conn.execute('SELECT COUNT(*) FROM paper_positions WHERE is_paper!=1').fetchone()[0]
            rows = conn.execute("""SELECT o.order_id,o.filled_qty,o.avg_fill_price,o.fee_usd,
                COALESCE(SUM(f.qty),0) AS quantity,COALESCE(SUM(f.qty*f.price),0) AS cost,
                COALESCE(SUM(f.fee_usd),0) AS fees FROM paper_orders o
                LEFT JOIN paper_fills f ON f.order_id=o.order_id GROUP BY o.order_id""").fetchall()
            for row in rows:
                if abs(row['filled_qty']-row['quantity'])>1e-6 or abs(row['fee_usd']-row['fees'])>1e-6:
                    errors.append({'order_id':row['order_id'],'reason':'fill_quantity_or_fee_mismatch'})
                elif row['quantity']>0 and abs(row['avg_fill_price']-row['cost']/row['quantity'])>1e-6:
                    errors.append({'order_id':row['order_id'],'reason':'fill_vwap_mismatch'})
        owners = {a['bot_id'] for a in accounts}
        for account in accounts:
            bot = account['bot_id']
            values = [account[k] for k in ['cash_usd','initial_equity_usd','realized_pnl_usd','reserved_margin_usd']]
            if not all(math.isfinite(v) for v in values):
                errors.append({'bot_id':bot,'reason':'nonfinite_account'})
                continue
            if abs(account['cash_usd']-account['initial_equity_usd']-account['realized_pnl_usd'])>1e-5:
                errors.append({'bot_id':bot,'reason':'cash_realized_equation'})
            margin = sum(p.qty*p.entry_price/max(p.leverage,1) for p in positions if p.bot_id==bot)
            if abs(account['reserved_margin_usd']-margin)>1e-5:
                errors.append({'bot_id':bot,'reason':'reserved_margin_equation'})
        for pos in positions:
            if pos.bot_id not in owners or not all(math.isfinite(v) for v in [pos.qty,pos.entry_price,pos.mark_price,pos.leverage]) or min(pos.qty,pos.entry_price,pos.mark_price)<=0 or pos.leverage<1:
                errors.append({'position_id':pos.position_id,'reason':'invalid_position'})
                continue
            unrealized = (pos.mark_price-pos.entry_price)*pos.qty*(1 if pos.direction==Direction.LONG else -1)
            if abs(pos.unrealized_pnl_usd-round(unrealized,4))>1e-5:
                errors.append({'position_id':pos.position_id,'reason':'stored_unrealized_equation'})
        return {'accounts':len(accounts),'open_positions':len(positions),'checked_orders':count,
                'non_paper_violations':non_paper,'equation_errors':errors,
                'reconciled':non_paper==0 and not errors,
                'historical_completeness_certified':False,
                'scope':'materialized cash/margin/unrealized and order/fill equations; historical attribution is audited separately'}
