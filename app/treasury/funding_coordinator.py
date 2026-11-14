"""Settle evidenced funding against durable paper execution exposure.

This coordinator performs no network I/O and does not infer missing intervals.
Callers must deliver each verified venue event; unobserved history stays blocked.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from app.core.security import assert_paper_only
from app.treasury import paper_exposure
from app.treasury.paper_funding import settle_position_funding


def settle_captured_funding(db, treasury, *, bot_id: str, exchange: str,
                            symbol: str, settlement_at: datetime,
                            interval_seconds: int, rate: str, mark_price: str,
                            source: dict, interval_start_at: datetime | None = None,
                            now: datetime | None = None) -> dict:
    """Exposure, cash, attribution and dashboard event commit together.

    A closed position remains discoverable through ``paper_exposure.keys``.
    Re-delivery after restart uses the same immutable exposure and settlement ID.
    No cursor claims that delivery of all intervening venue events is complete.
    """
    assert_paper_only('paper')
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('Timezone-aware observation required')
    if settlement_at.tzinfo is None or settlement_at.utcoffset() is None or settlement_at > now:
        raise ValueError('Funding event must already have settled')
    if treasury is None:
        raise ValueError('Paper treasury is required')
    if not isinstance(source, dict) or not source.get('endpoint') or not source.get('venue_event_id'):
        raise ValueError('Venue endpoint and event identity required')
    # Reject NaN and unsupported objects rather than stringify ambiguous evidence.
    source_json = json.dumps(source, sort_keys=True, allow_nan=False)
    with db.transaction() as conn:
        exposure = paper_exposure.exposure_at(db, bot_id=bot_id, exchange=exchange,
                                               symbol=symbol, at=settlement_at)
        evidence = json.dumps({'venue': json.loads(source_json),
                               'exposure_event_id': exposure['event_id'],
                               'model': 'paper_linear_usdt_8dp_v1'}, sort_keys=True)
        result = settle_position_funding(db, treasury, bot_id=bot_id,
            exchange=exchange, symbol=symbol, settlement_at=settlement_at,
            interval_seconds=interval_seconds, rate=rate, mark_price=mark_price,
            exposure_history=[exposure], evidence=evidence,
            interval_start_at=interval_start_at, conn=conn)
        # Funding is a separate cash component. Do not also put it into the
        # event's trade realized-P&L field, which downstream sums separately.
        db.append_ledger_event(event_id=f"funding:{result['settlement_id']}",
            idempotency_key=f"funding:{result['settlement_id']}",
            timestamp_utc=result['settlement_at'], event_type='FUNDING',
            bot_id=bot_id, exchange=exchange, symbol=symbol,
            funding_usd=result['amount_usd'], details={
                'schema_version': 2, 'settlement': result, 'evidence': json.loads(evidence),
                'cash_includes_funding': True}, conn=conn)
        event = conn.execute('SELECT * FROM paper_ledger_events WHERE event_id=?',
                             (f"funding:{result['settlement_id']}",)).fetchone()
        if event is None or (
            event['event_type'], event['bot_id'], event['exchange'], event['symbol'],
            Decimal(event['funding_usd']), Decimal(event['realized_pnl_usd']),
            Decimal(event['fee_usd']), Decimal(event['amount_usd'])
        ) != ('FUNDING', bot_id, exchange, symbol, Decimal(result['amount_usd']),
              Decimal(0), Decimal(0), Decimal(0)):
            raise ValueError('Conflicting funding dashboard journal')
        paper_exposure.seal_boundary(conn, bot_id=bot_id, exchange=exchange,
                                     symbol=symbol, at=settlement_at)
        return result
