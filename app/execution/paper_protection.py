"""Durable paper protection plans using existing risk-state and fill/account tables.

Tick-based simulation: gaps execute at observable book/mark prices, not crossed
barrier prices. Futures use an explicitly simplified cross-margin maintenance
model (default 0.5% of marked notional), not a certified exchange tier schedule.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from app.core.models import Direction, MarketType, PaperOrder, PaperFill, Side, OrderType, OrderStatus
from app.core.security import assert_paper_only

PREFIX = 'paper_protection:v1:'


def validate(signal):
    targets = list(signal.take_profit_targets)
    if any(not math.isfinite(t) or t <= 0 for t in targets):
        raise ValueError('invalid_take_profit_levels')
    ordered = sorted(set(targets), reverse=signal.direction == Direction.SHORT)
    if targets != ordered:
        raise ValueError('unordered_or_duplicate_take_profit_levels')
    entry = (signal.entry_low + signal.entry_high) / 2
    if any((t <= entry if signal.direction == Direction.LONG else t >= entry) for t in targets):
        raise ValueError('unprofitable_take_profit_level')
    trail = signal.metadata.get('trailing_stop_pct')
    if trail is not None and (isinstance(trail, bool) or not math.isfinite(float(trail)) or not 0 < float(trail) < 1):
        raise ValueError('invalid_trailing_stop_pct')
    mmr = float(signal.metadata.get('paper_maintenance_margin_rate', 0.005))
    if not math.isfinite(mmr) or not 0 < mmr <= 0.25:
        raise ValueError('invalid_paper_maintenance_margin_rate')
    return targets, float(trail) if trail is not None else None, mmr


def save(db, conn, position_id, plan):
    conn.execute('''INSERT INTO risk_state (key,value_json,updated_at) VALUES (?,?,?)
        ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at''',
                 (PREFIX + position_id, json.dumps(plan, sort_keys=True), datetime.now(timezone.utc).isoformat()))


def load(conn, position_id):
    row = conn.execute('SELECT value_json FROM risk_state WHERE key=?', (PREFIX+position_id,)).fetchone()
    return json.loads(row[0]) if row else None


def install(engine, signal, order, snapshot, conn):
    targets, trail, mmr = validate(signal)
    if len(targets) < 2 and trail is None and signal.market_type != MarketType.PERP:
        return
    positions = [p for p in engine.db.list_positions(order.bot_id)
                 if p.exchange == order.exchange and p.symbol == order.symbol and p.market_type == order.market_type]
    if not positions or positions[0].direction != signal.direction:
        return  # A reduction does not replace the surviving position's policy.
    pos = positions[0]
    previous = load(conn,pos.position_id)
    if previous and not previous.get('closed'):
        completed = {t['price'] for t in previous['targets'] if t['remaining_qty']==0}
        targets = [t for t in targets if t not in completed]
        if not targets:
            targets = [t['price'] for t in previous['targets'] if t['remaining_qty']>0]
        if trail is None:
            trail = previous['trailing_stop_pct']
        if 'paper_maintenance_margin_rate' not in signal.metadata:
            mmr = previous['maintenance_margin_rate']
    quantities = []
    left = pos.qty
    for i, target in enumerate(targets):
        qty = left if i == len(targets)-1 else round(pos.qty/len(targets), 6)
        quantities.append({'price': target, 'remaining_qty': qty})
        left = round(left-qty, 6)
    plan = {'version': 1, 'position_id': pos.position_id, 'targets': quantities,
            'trailing_stop_pct': trail, 'anchor': pos.entry_price,
            'maintenance_margin_rate': mmr, 'liquidation_model': 'paper_cross_margin',
            'last_quote_at': max(snapshot.timestamp,order.updated_at).isoformat(), 'closed': False,
            'active_stop_type': 'stop_loss', 'active_stop_price': pos.stop_loss}
    if previous and not previous.get('closed'):
        if previous.get('active_stop_price') == pos.stop_loss:
            plan['active_stop_type'] = previous.get('active_stop_type','stop_loss')
        plan['anchor'] = max(previous['anchor'],pos.entry_price,snapshot.last_price) if pos.direction==Direction.LONG else min(previous['anchor'],pos.entry_price,snapshot.last_price)
        plan['last_quote_at'] = max(datetime.fromisoformat(previous['last_quote_at']),snapshot.timestamp,order.updated_at).isoformat()
    save(engine.db, conn, pos.position_id, plan)
    engine.db.log_audit('PAPER_PROTECTION_INSTALLED', pos.bot_id,
                       {'position_id': pos.position_id, 'plan': plan, 'is_paper': True}, conn=conn)


def retire(engine, position_id, conn=None):
    if conn is None:
        with engine.db.transaction() as transaction:
            return retire(engine,position_id,transaction)
    plan = load(conn,position_id)
    if plan is not None:
        plan['closed'] = True
        for target in plan['targets']:
            target['remaining_qty'] = 0
        save(engine.db,conn,position_id,plan)


def _liquidatable(engine, pos, snapshots, mmr):
    if pos.market_type != MarketType.PERP:
        return False
    account = engine.db.get_account(pos.bot_id)
    if account is None:
        return False
    equity = float(account['cash_usd'])
    maintenance = 0.0
    for other in engine.db.list_positions(pos.bot_id):
        key = f'{other.exchange.value}:{other.market_type.value}:{other.symbol}'
        quote = snapshots.get(key)
        if quote is None or not math.isfinite(quote.last_price) or quote.last_price <= 0:
            return False  # Never infer a current account liquidation from missing marks.
        signed = 1 if other.direction == Direction.LONG else -1
        equity += signed * (quote.last_price-other.entry_price)*other.qty
        if other.market_type == MarketType.PERP:
            other_plan = load(engine.db._conn,other.position_id)
            if other_plan is None:
                return False  # Legacy account positions have no certified maintenance policy.
            maintenance += other.qty * quote.last_price * other_plan['maintenance_margin_rate']
    return equity <= maintenance


def _close(engine, pos, snap, qty, reason, conn):
    side = Side.SELL if pos.direction == Direction.LONG else Side.BUY
    fee_sched = engine.fee_schedule.get((pos.exchange, pos.market_type, pos.symbol)) or engine.fee_schedule.get(
        (pos.exchange, pos.market_type), {'taker': 0.001})
    slices = engine._simulate_fill_slices(qty, snap.last_price, side, snap, fee_sched['taker'])
    order = PaperOrder(client_order_id=f'protect:{pos.position_id}:{snap.timestamp.isoformat()}:{reason}',
                       bot_id=pos.bot_id, signal_id='sl_tp_exit', exchange=pos.exchange,
                       symbol=pos.symbol, market_type=pos.market_type, side=side,
                       order_type=OrderType.MARKET, requested_qty=qty, leverage=pos.leverage,
                       status=OrderStatus.CANCELED, reject_reason='no_executable_liquidity')
    total = sum(s[0] for s in slices)
    if total > 0:
        order.filled_qty = round(total, 6)
        order.avg_fill_price = round(sum(q*p for q,p,_,_ in slices)/total, 6)
        order.fee_usd = round(sum(s[2] for s in slices), 6)
        order.slippage_bps = round(sum(q*s for q,_,_,s in slices)/total, 3)
        order.status = OrderStatus.FILLED if order.filled_qty >= qty-1e-6 else OrderStatus.PARTIALLY_FILLED
        order.reject_reason = None
    engine.db.save_order(order, conn=conn)
    for quantity, price, fee, slippage in slices:
        engine.db.save_fill(PaperFill(order_id=order.order_id,bot_id=pos.bot_id,exchange=pos.exchange,
            symbol=pos.symbol,market_type=pos.market_type,side=side,qty=quantity,price=price,
            fee_usd=fee,slippage_bps=slippage,is_paper=True),conn=conn)
    if order.filled_qty:
        engine._apply_fill_to_position_and_account(order, snap.last_price, conn=conn)
    engine.db.log_audit('PAPER_PROTECTION_EXECUTION',pos.bot_id,
                       {'position_id':pos.position_id,'order_id':order.order_id,'reason':reason,
                        'requested_qty':qty,'filled_qty':order.filled_qty,'is_paper':True},conn=conn)
    return order


def process(engine, snapshots):
    """Returns (position IDs handled, actually filled exit orders). Each tick is atomic."""
    assert_paper_only('paper')
    handled, exits = set(), []
    for candidate in engine.db.list_positions():
        with engine.db.transaction() as conn:
            row = conn.execute('SELECT * FROM paper_positions WHERE position_id=?', (candidate.position_id,)).fetchone()
            if row is None:
                continue
            pos = engine.db._row_to_position(row)
            plan = load(conn, pos.position_id)
            if plan is None:
                continue
            handled.add(pos.position_id)
            key = f'{pos.exchange.value}:{pos.market_type.value}:{pos.symbol}'
            snap = snapshots.get(key)
            if snap is None or not math.isfinite(snap.last_price) or snap.last_price <= 0:
                continue
            if not math.isfinite(snap.spread_bps) or snap.spread_bps<0 or any(
                    not math.isfinite(p) or not math.isfinite(q) or p<=0 or q<0 for p,q in snap.bids+snap.asks):
                continue
            if snap.timestamp.tzinfo is None or snap.timestamp <= datetime.fromisoformat(plan['last_quote_at']):
                continue
            px = snap.last_price
            signed = 1 if pos.direction == Direction.LONG else -1
            pos.mark_price = px
            pos.unrealized_pnl_usd = round(signed*(px-pos.entry_price)*pos.qty, 4)
            pos.updated_at = datetime.now(timezone.utc)
            plan['last_quote_at'] = snap.timestamp.isoformat()
            trail = plan['trailing_stop_pct']
            if trail is not None:
                plan['anchor'] = max(plan['anchor'], px) if signed == 1 else min(plan['anchor'], px)
                stop = plan['anchor']*(1-trail) if signed == 1 else plan['anchor']*(1+trail)
                if pos.stop_loss is None or signed*(stop-pos.stop_loss)>0:
                    pos.stop_loss = round(stop, 6)
                    plan['active_stop_type'] = 'trailing_stop'
            plan['active_stop_price'] = pos.stop_loss
            pending = [t for t in plan['targets'] if t['remaining_qty']>0]
            planned = sum(t['remaining_qty'] for t in pending)
            if pending and abs(planned-pos.qty)>1e-6:
                # Resize remaining levels to the actual surviving quantity after
                # manual reductions. Never resurrect already consumed levels.
                left = pos.qty
                for i,t in enumerate(pending):
                    value = left if i==len(pending)-1 else round(t['remaining_qty']*pos.qty/planned,6)
                    t['remaining_qty'] = value
                    left = round(left-value,6)
            reason, qty, triggered = None, 0.0, []
            if _liquidatable(engine, pos, snapshots, plan['maintenance_margin_rate']):
                reason, qty = 'paper_liquidation_cross_margin', pos.qty
            elif pos.stop_loss is not None and signed*(px-pos.stop_loss)<=0:
                reason, qty = plan.get('active_stop_type','stop_loss'), pos.qty
            else:
                triggered = [t for t in pending if signed*(px-t['price'])>=0]
                if triggered:
                    reason, qty = 'take_profit_levels', round(sum(t['remaining_qty'] for t in triggered),6)
            engine.db.upsert_position(pos, conn=conn)
            if reason is not None and qty>0:
                order = _close(engine,pos,snap,min(qty,pos.qty),reason,conn)
                filled = order.filled_qty
                if triggered:
                    for level in triggered:
                        used = min(filled,level['remaining_qty'])
                        level['remaining_qty'] = round(level['remaining_qty']-used,6)
                        filled = round(filled-used,6)
                if order.filled_qty:
                    exits.append(order)
                plan['closed'] = not conn.execute('SELECT 1 FROM paper_positions WHERE position_id=?',(pos.position_id,)).fetchone()
                if plan['closed']:
                    for t in plan['targets']:t['remaining_qty']=0
            current = conn.execute('SELECT * FROM paper_positions WHERE position_id=?',(pos.position_id,)).fetchone()
            if current:
                remaining_pos = engine.db._row_to_position(current)
                remaining_pos.take_profit = next((t['price'] for t in plan['targets'] if t['remaining_qty']>0),None)
                engine.db.upsert_position(remaining_pos,conn=conn)
            save(engine.db,conn,pos.position_id,plan)
    return handled, exits
