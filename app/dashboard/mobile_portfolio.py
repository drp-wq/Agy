"""Read-only native projection of the existing authoritative campaign database.

No account is initialized, no positions are reset, and no simulator balance is used.
Money is serialized as decimal strings so clients choose presentation precision.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from app.core.security import assert_paper_only


def money(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise HTTPException(503, 'Non-finite campaign accounting value')
    return result


def build_mobile_portfolio(runner):
    assert_paper_only("paper")
    if not hasattr(runner, 'db') or not getattr(runner, 'campaign_id', None):
        raise HTTPException(503, 'Authoritative paper campaign unavailable')
    # Hold one database transaction for a coherent cross-table snapshot, even if
    # another process writes the same campaign. No schema or source-record writes.
    with runner.db._lock:
        conn = runner.db._conn
        if conn.in_transaction:
            raise HTTPException(503, 'Campaign snapshot temporarily busy')
        conn.execute('BEGIN')
        try:
            accounts = [dict(r) for r in conn.execute('SELECT * FROM paper_accounts ORDER BY bot_id')]
            positions = [dict(r) for r in conn.execute('SELECT * FROM paper_positions ORDER BY position_id')]
            orders = [dict(r) for r in conn.execute('SELECT * FROM paper_orders ORDER BY created_at DESC, order_id DESC LIMIT 200')]
            fees = {r[0]: money(r[1]) for r in conn.execute('SELECT bot_id,SUM(fee_usd) FROM paper_fills GROUP BY bot_id')}
            funding = {}
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='paper_funding_settlements'").fetchone():
                for row in conn.execute('SELECT bot_id,signed_usd FROM paper_funding_settlements WHERE is_paper=1'):
                    funding[row[0]] = funding.get(row[0], Decimal(0)) + money(row[1])
            events = [dict(r) for r in conn.execute('SELECT * FROM paper_ledger_events ORDER BY seq DESC LIMIT 200')]
            if conn.execute('SELECT COUNT(*) FROM paper_positions WHERE is_paper!=1').fetchone()[0] or conn.execute('SELECT COUNT(*) FROM paper_orders WHERE is_paper!=1').fetchone()[0]:
                raise HTTPException(503, 'Non-paper evidence in campaign')
            order_count = conn.execute('SELECT COUNT(*) FROM paper_orders').fetchone()[0]
            event_count = conn.execute('SELECT COUNT(*) FROM paper_ledger_events').fetchone()[0]
            source_at = datetime.now(timezone.utc).isoformat()
        finally:
            conn.rollback()  # End the read snapshot; never commit to the ledger.
    if len(accounts) != 5 or any(money(a['initial_equity_usd']) != Decimal('100') for a in accounts):
        raise HTTPException(503, 'Expected authoritative five-sleeve 500 USDT campaign')
    if any(not p['is_paper'] for p in positions) or any(not o['is_paper'] for o in orders):
        raise HTTPException(503, 'Non-paper evidence in campaign')
    owners = {a['bot_id'] for a in accounts}
    if any(p['bot_id'] not in owners for p in positions) or (set(fees) | set(funding)) - owners:
        raise HTTPException(503, 'Orphan campaign position')
    total_cash = sum(money(a['cash_usd']) for a in accounts)
    total_reserved = sum(money(a['reserved_margin_usd']) for a in accounts)
    total_unrealized = sum((money(p['unrealized_pnl_usd']) for p in positions), Decimal(0))
    total_realized = sum(money(a['realized_pnl_usd']) for a in accounts)
    if abs(total_cash - Decimal('500') - total_realized) > Decimal('0.00001'):
        raise HTTPException(503, 'Campaign cash/P&L equation does not reconcile')
    sleeves = []
    for index, account in enumerate(accounts, 1):
        bot = account['bot_id']
        cash, reserved = money(account['cash_usd']), money(account['reserved_margin_usd'])
        unrealized = sum((money(p['unrealized_pnl_usd']) for p in positions if p['bot_id'] == bot), Decimal(0))
        sleeves.append({'sleeve_id': index, 'bot_id': bot,
                        'initial_usdt': str(money(account['initial_equity_usd'])),
                        'available_usdt': str(cash - reserved), 'reserved_usdt': str(reserved),
                        'realized_usdt': str(money(account['realized_pnl_usd'])),
                        'unrealized_usdt': str(unrealized), 'fees_usdt': str(fees.get(bot, Decimal(0))),
                        'funding_usdt': str(funding.get(bot, Decimal(0)))})
    return {'schema_version': 1, 'mode': 'PAPER_ONLY', 'live_execution_disabled': True,
            'source': 'authoritative_vps_campaign', 'campaign_id': runner.campaign_id,
            'snapshot_at': source_at, 'initial_capital_usdt': '500',
            'cash_usdt': str(total_cash), 'available_cash_usdt': str(total_cash-total_reserved),
            'reserved_margin_usdt': str(total_reserved), 'realized_pnl_usdt': str(total_realized),
            'unrealized_pnl_usdt': str(total_unrealized), 'equity_usdt': str(total_cash+total_unrealized),
            'fees_usdt': str(sum(fees.values(), Decimal(0))),
            'funding_usdt': str(sum(funding.values(), Decimal(0))),
            'sleeves': sleeves, 'positions': positions, 'orders': orders, 'ledger_events': events,
            'orders_total': order_count, 'ledger_events_total': event_count,
            'history_truncated': order_count > len(orders) or event_count > len(events),
            'accounting': 'cash_includes_reserved;equity=cash+unrealized;available=cash-reserved'}
