"""Explicit, idempotent PAPER perpetual funding settlements.

Funding is never inferred from a ticker or live exchange account. A caller must
supply a documented settlement event and amount. No exchange I/O occurs here.
"""
from __future__ import annotations
from decimal import Decimal, ROUND_HALF_EVEN
from contextlib import nullcontext
import math
from datetime import datetime, timezone
from app.core.security import assert_paper_only
from app.treasury.models import AttributionCategory


def settle_paper_funding(db, treasury, *, settlement_id: str, bot_id: str,
                         exchange: str, symbol: str, signed_usd: str,
                         evidence: str, conn=None) -> dict:
    assert_paper_only('paper')
    if not settlement_id.strip() or not evidence.strip():
        raise ValueError('Settlement ID and evidence are required')
    amount = Decimal(signed_usd)
    if not amount.is_finite() or not math.isfinite(float(amount)):
        raise ValueError('Funding must be a finite USD amount')
    if treasury.db is not db:
        raise ValueError('Funding and attribution must use the same database')
    with (db.transaction() if conn is None else nullcontext(conn)) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_funding_settlements (
            settlement_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL,
            exchange TEXT NOT NULL, symbol TEXT NOT NULL,
            signed_usd TEXT NOT NULL, evidence TEXT NOT NULL,
            created_at TEXT NOT NULL, is_paper INTEGER NOT NULL DEFAULT 1 CHECK(is_paper=1))''')
        existing = conn.execute('SELECT * FROM paper_funding_settlements WHERE settlement_id=?',
                                (settlement_id,)).fetchone()
        if existing:
            if (existing['bot_id'],existing['exchange'],existing['symbol'],Decimal(existing['signed_usd']),existing['evidence']) != (bot_id,exchange,symbol,amount,evidence):
                raise ValueError('Conflicting duplicate funding settlement')
            duplicate = True
        else:
            duplicate = False
        acct = conn.execute('SELECT bot_id FROM paper_accounts WHERE bot_id=? AND exchange=?',
                            (bot_id,exchange)).fetchone()
        if acct is None:
            raise ValueError('Matching paper account required')
        if not duplicate:
            now = datetime.now(timezone.utc).isoformat()
            conn.execute('INSERT INTO paper_funding_settlements VALUES (?,?,?,?,?,?,?,1)',
                         (settlement_id,bot_id,exchange,symbol,str(amount),evidence,now))
            conn.execute('''UPDATE paper_accounts SET cash_usd=cash_usd+?,
                realized_pnl_usd=realized_pnl_usd+?, updated_at=? WHERE bot_id=?''',
                (float(amount),float(amount),now,bot_id))
        attribution = treasury.record_attribution(idempotency_key=f'paper_funding:{settlement_id}',
        agent_id=bot_id,exchange=exchange,account_id=f'{exchange}:paper',
        strategy='paper_funding_evidenced',symbol=symbol,asset='USD',
        category=AttributionCategory.FUNDING,amount=amount,conn=conn)
        if (attribution.agent_id, attribution.exchange, attribution.symbol, attribution.event_category,
                attribution.amount, attribution.asset) != (bot_id, exchange, symbol, AttributionCategory.FUNDING, amount, 'USD'):
            raise ValueError('Conflicting existing funding attribution')
    return {'settlement_id':settlement_id,'duplicate':duplicate,'amount_usd':str(amount)}


def settle_position_funding(db, treasury, *, bot_id: str, exchange: str,
                            symbol: str, settlement_at: datetime,
                            interval_seconds: int, rate: str, mark_price: str,
                            exposure_history: list[dict], evidence: str,
                            interval_start_at: datetime | None = None, conn=None) -> dict:
    """Settle one evidenced linear USDT perpetual interval, never current exposure.

    History rows are immutable post-change signed base quantities and UTC timestamps.
    Exposure immediately BEFORE the boundary applies; changes at the exact boundary
    belong to the next interval. Call once per evidenced missed interval, with its
    own rate/mark/history. This function does not guess unavailable funding data.
    Positive rates debit longs and credit shorts. Zero settlements remain auditable.
    """
    import json

    if not symbol.endswith('/USDT'):
        raise ValueError('Only linear USDT perpetual funding is supported')
    if settlement_at.tzinfo is None or settlement_at.utcoffset() is None:
        raise ValueError('Timezone-aware settlement timestamp required')
    if not isinstance(interval_seconds, int) or isinstance(interval_seconds, bool) or interval_seconds <= 0:
        raise ValueError('Positive integer funding interval required')
    if settlement_at.microsecond:
        raise ValueError('Settlement must use an exact second boundary')
    if interval_start_at is None:
        if int(settlement_at.timestamp()) % interval_seconds:
            raise ValueError('Settlement must be an exact UTC interval boundary')
    elif interval_start_at.tzinfo is None or interval_start_at.utcoffset() is None or (settlement_at-interval_start_at).total_seconds()!=interval_seconds:
        raise ValueError('Evidenced interval start does not match settlement interval')
    if not evidence.strip() or not exposure_history:
        raise ValueError('Funding source and exposure history required')
    funding_rate, mark = Decimal(rate), Decimal(mark_price)
    if not funding_rate.is_finite() or not mark.is_finite() or mark <= 0:
        raise ValueError('Finite rate and positive mark required')
    eligible = []
    seen = set()
    for row in exposure_history:
        at = datetime.fromisoformat(row['timestamp'])
        qty = Decimal(str(row['signed_qty']))
        if at.tzinfo is None or at.utcoffset() is None or not qty.is_finite():
            raise ValueError('Invalid exposure snapshot')
        if at in seen:
            raise ValueError('Ambiguous exposure timestamps')
        seen.add(at)
        if at < settlement_at or (at==settlement_at and row.get('boundary_before') is True):
            eligible.append((at, qty))
    if not eligible:
        raise ValueError('No evidenced exposure before settlement')
    effective_at, quantity = max(eligible, key=lambda row: row[0])
    raw_amount = -quantity * mark * funding_rate
    # Explicit paper-USDT settlement precision; preserve the exact calculation
    # and rounding difference in evidence rather than hiding precision drift.
    amount = raw_amount.quantize(Decimal('0.00000001'),rounding=ROUND_HALF_EVEN)
    instant = settlement_at.astimezone(timezone.utc).isoformat()
    source = json.dumps(dict(source=evidence, settlement_at=instant,
                             interval_seconds=interval_seconds, rate=str(funding_rate.normalize()),
                             mark_price=str(mark.normalize()), signed_qty=str(quantity.normalize()),
                             exposure_at=effective_at.astimezone(timezone.utc).isoformat(),
                             interval_start_at=interval_start_at.astimezone(timezone.utc).isoformat() if interval_start_at else None,
                             raw_amount_usd=str(raw_amount.normalize()), rounding_delta_usd=str((amount-raw_amount).normalize()),
                             settlement_precision=8),
                        sort_keys=True)
    result = settle_paper_funding(
        db, treasury, settlement_id=f'linear:{exchange}:{bot_id}:{symbol}:{instant}',
        bot_id=bot_id, exchange=exchange, symbol=symbol,
        signed_usd=str(amount), evidence=source, conn=conn)
    return result | {'signed_qty': str(quantity), 'rate': str(funding_rate),
                     'notional_usd': str(abs(quantity) * mark), 'settlement_at': instant,
                     'raw_amount_usd': str(raw_amount), 'rounding_delta_usd': str(amount-raw_amount)}
