"""Explicit, idempotent PAPER perpetual funding settlements.

Funding is never inferred from a ticker or live exchange account. A caller must
supply a documented settlement event and amount. No exchange I/O occurs here.
"""
from __future__ import annotations
from decimal import Decimal
from datetime import datetime, timezone
from app.core.security import assert_paper_only
from app.treasury.models import AttributionCategory


def settle_paper_funding(db, treasury, *, settlement_id: str, bot_id: str,
                         exchange: str, symbol: str, signed_usd: str,
                         evidence: str) -> dict:
    assert_paper_only('paper')
    if not settlement_id.strip() or not evidence.strip():
        raise ValueError('Settlement ID and evidence are required')
    amount = Decimal(signed_usd)
    if not amount.is_finite() or amount == 0:
        raise ValueError('Funding must be a finite, nonzero USD amount')
    with db.transaction() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS paper_funding_settlements (
            settlement_id TEXT PRIMARY KEY, bot_id TEXT NOT NULL,
            exchange TEXT NOT NULL, symbol TEXT NOT NULL,
            signed_usd TEXT NOT NULL, evidence TEXT NOT NULL,
            created_at TEXT NOT NULL, is_paper INTEGER NOT NULL DEFAULT 1 CHECK(is_paper=1))''')
        existing = conn.execute('SELECT * FROM paper_funding_settlements WHERE settlement_id=?',
                                (settlement_id,)).fetchone()
        if existing:
            if (existing['bot_id'],existing['exchange'],existing['symbol'],existing['signed_usd'],existing['evidence']) != (bot_id,exchange,symbol,str(amount),evidence):
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
    treasury.record_attribution(idempotency_key=f'paper_funding:{settlement_id}',
        agent_id=bot_id,exchange=exchange,account_id=f'{exchange}:paper',
        strategy='paper_funding_evidenced',symbol=symbol,asset='USD',
        category=AttributionCategory.FUNDING,amount=amount)
    return {'settlement_id':settlement_id,'duplicate':duplicate,'amount_usd':str(amount)}
