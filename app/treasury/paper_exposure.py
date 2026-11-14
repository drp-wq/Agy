"""Immutable futures exposure transitions, committed with financial execution.

Coverage begins at the first captured pre-execution snapshot. Earlier historical
exposure is not invented. Equal timestamps are ordered by a durable sequence;
execution changes exactly on a funding boundary apply AFTER that boundary.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from app.core.models import Direction, MarketType
from app.core.security import assert_paper_only


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS paper_exposure_events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
        source_order_id TEXT UNIQUE, bot_id TEXT NOT NULL, exchange TEXT NOT NULL,
        symbol TEXT NOT NULL, effective_at TEXT NOT NULL,
        phase TEXT NOT NULL CHECK(phase IN ('baseline','execution')),
        signed_qty TEXT NOT NULL, evidence_json TEXT NOT NULL,
        is_paper INTEGER NOT NULL DEFAULT 1 CHECK(is_paper=1))''')
    conn.execute('''CREATE INDEX IF NOT EXISTS paper_exposure_key_time
        ON paper_exposure_events(bot_id,exchange,symbol,effective_at,seq)''')
    conn.execute('''CREATE TABLE IF NOT EXISTS paper_exposure_watermarks (
        bot_id TEXT NOT NULL, exchange TEXT NOT NULL, symbol TEXT NOT NULL,
        settled_through TEXT NOT NULL, PRIMARY KEY(bot_id,exchange,symbol))''')


def exists(conn):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE name='paper_exposure_events' AND type='table'").fetchone() is not None


def decimal_text(value):
    dec = Decimal(str(value))
    if not dec.is_finite():
        raise ValueError('Non-finite exposure economics')
    return '0' if dec==0 else format(dec.normalize(), 'f')


def fingerprint(order):
    fields = {'order_id':order.order_id,'bot_id':order.bot_id,'exchange':order.exchange.value,
              'symbol':order.symbol,'market_type':order.market_type.value,'side':order.side.value,
              'qty':decimal_text(order.filled_qty),'price':decimal_text(order.avg_fill_price),
              'fee':decimal_text(order.fee_usd),
              'effective_at':order.updated_at.astimezone(timezone.utc).isoformat()}
    return hashlib.sha256(json.dumps(fields,sort_keys=True).encode()).hexdigest()


def current_quantity(db, order):
    positions = [p for p in db.list_positions(order.bot_id) if p.exchange==order.exchange
                 and p.symbol==order.symbol and p.market_type==MarketType.PERP]
    if len(positions)>1:
        raise ValueError('Ambiguous futures exposure')
    if not positions:
        return '0'
    pos = positions[0]
    return decimal_text(pos.qty if pos.direction==Direction.LONG else -pos.qty)


def before_execution(db, conn, order):
    """Returns False only when this exact financial execution already committed."""
    assert_paper_only('paper')
    ensure_schema(conn)
    at = order.updated_at
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Execution exposure requires a timezone-aware timestamp')
    timestamp = at.astimezone(timezone.utc).isoformat()
    old = conn.execute('SELECT evidence_json FROM paper_exposure_events WHERE source_order_id=?',
                       (order.order_id,)).fetchone()
    if old:
        if json.loads(old[0]).get('order_fingerprint') != fingerprint(order):
            raise ValueError('Conflicting repeated execution')
        return False
    key = (order.bot_id,order.exchange.value,order.symbol)
    settled = conn.execute('''SELECT settled_through FROM paper_exposure_watermarks
        WHERE bot_id=? AND exchange=? AND symbol=?''',key).fetchone()
    if settled and timestamp < settled[0]:
        raise ValueError('Execution would change already settled funding exposure')
    latest = conn.execute('''SELECT * FROM paper_exposure_events
        WHERE bot_id=? AND exchange=? AND symbol=? ORDER BY effective_at DESC,seq DESC LIMIT 1''',key).fetchone()
    prior = current_quantity(db,order)
    if latest:
        if timestamp < latest['effective_at']:
            raise ValueError('Execution precedes captured exposure history')
        if Decimal(latest['signed_qty']) != Decimal(prior):
            raise ValueError('Current position disagrees with captured exposure history')
    else:
        evidence = {'coverage':'observed_pre_execution_baseline','source_order_id':order.order_id,
                    'order_fingerprint':fingerprint(order),'boundary_phase':'before_execution'}
        conn.execute('''INSERT INTO paper_exposure_events
            (event_id,source_order_id,bot_id,exchange,symbol,effective_at,phase,signed_qty,evidence_json)
            VALUES (?,NULL,?,?,?,?,?,?,?)''',
            (f'baseline:{order.order_id}',*key,timestamp,'baseline',prior,json.dumps(evidence,sort_keys=True)))
    return True


def after_execution(db, conn, order):
    signed = current_quantity(db,order)
    evidence = {'source':'committed_paper_execution','order_fingerprint':fingerprint(order),
                'source_order_id':order.order_id,'filled_qty':decimal_text(order.filled_qty),
                'side':order.side.value}
    conn.execute('''INSERT INTO paper_exposure_events
        (event_id,source_order_id,bot_id,exchange,symbol,effective_at,phase,signed_qty,evidence_json)
        VALUES (?,?,?,?,?,?,?,?,?)''',
        (f'execution:{order.order_id}',order.order_id,order.bot_id,order.exchange.value,order.symbol,
         order.updated_at.astimezone(timezone.utc).isoformat(),'execution',signed,json.dumps(evidence,sort_keys=True)))


def seal_boundary(conn, *, bot_id, exchange, symbol, at):
    """After cash settlement, reject newly delivered backdated executions.

    Exact-boundary executions remain valid because they affect the next interval.
    Advancing this watermark does not certify delivery of intervening funding.
    """
    ensure_schema(conn)
    conn.execute('''INSERT INTO paper_exposure_watermarks VALUES (?,?,?,?)
        ON CONFLICT(bot_id,exchange,symbol) DO UPDATE
        SET settled_through=MAX(settled_through,excluded.settled_through)''',
        (bot_id,exchange,symbol,at.astimezone(timezone.utc).isoformat()))


def keys(db):
    with db._lock:
        if not exists(db._conn):
            return []
        return [dict(row) for row in db._conn.execute('''SELECT bot_id,exchange,symbol,
            MIN(effective_at) AS coverage_from FROM paper_exposure_events GROUP BY bot_id,exchange,symbol''')]


def exposure_at(db, *, bot_id, exchange, symbol, at):
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError('Timezone-aware settlement required')
    instant = at.astimezone(timezone.utc).isoformat()
    with db._lock:
        if not exists(db._conn):
            raise ValueError('No captured exposure history')
        row = db._conn.execute('''SELECT * FROM paper_exposure_events
            WHERE bot_id=? AND exchange=? AND symbol=?
            AND (effective_at<? OR (effective_at=? AND phase='baseline'))
            ORDER BY effective_at DESC,seq DESC LIMIT 1''',
            (bot_id,exchange,symbol,instant,instant)).fetchone()
        if row is None:
            raise ValueError('Settlement precedes captured exposure coverage')
        return {'timestamp':row['effective_at'],'signed_qty':row['signed_qty'],
                'event_id':row['event_id'],'source_order_id':row['source_order_id'],
                'boundary_before':row['phase']=='baseline'}
