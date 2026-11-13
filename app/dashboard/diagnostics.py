"""Read-only paper ledger diagnostics. No reconciliation or exchange calls."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

EXCHANGES = ('binance', 'bybit', 'okx')


def freshness(timestamp, now=None, max_age_seconds=180):
    now = now or datetime.now(timezone.utc)
    if timestamp is None:
        return {'status': 'missing', 'timestamp': None, 'age_seconds': None}
    try:
        parsed = datetime.fromisoformat(str(timestamp).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('timezone required')
        age = (now - parsed).total_seconds()
        if age < -5:
            raise ValueError('future timestamp')
        return {'status': 'fresh' if age <= max_age_seconds else 'stale',
                'timestamp': parsed.isoformat(), 'age_seconds': round(max(0, age), 1)}
    except (ValueError, TypeError, OverflowError):
        return {'status': 'invalid', 'timestamp': None, 'age_seconds': None}


def observation_status(path: Path, now=None):
    try:
        # Bound reads; never return arbitrary observation contents or error text.
        with path.open() as stream:
            content = stream.read(65537)
        if len(content) > 65536:
            raise ValueError('oversized observation')
        value = json.loads(content)
        if not isinstance(value, dict) or not value.get('timestamp'):
            raise ValueError('missing timestamp')
        return freshness(value['timestamp'], now)
    except FileNotFoundError:
        return freshness(None, now)
    except (OSError, ValueError, TypeError):
        return {'status': 'invalid', 'timestamp': None, 'age_seconds': None}


def ledger_diagnostics(conn: sqlite3.Connection, now=None):
    """All-time paper totals, independently aggregated to avoid join fanout."""
    result = {}
    for exchange in EXCHANGES:
        accounts = conn.execute('SELECT COALESCE(SUM(realized_pnl_usd),0), MAX(updated_at) FROM paper_accounts WHERE exchange=?', (exchange,)).fetchone()
        fills = conn.execute('SELECT COUNT(*), COALESCE(SUM(fee_usd),0), MAX(timestamp) FROM paper_fills WHERE exchange=? AND is_paper=1', (exchange,)).fetchone()
        positions = conn.execute('SELECT COALESCE(SUM(unrealized_pnl_usd),0) FROM paper_positions WHERE exchange=? AND is_paper=1', (exchange,)).fetchone()
        statuses = dict(conn.execute('SELECT status, COUNT(*) FROM paper_orders WHERE exchange=? AND is_paper=1 GROUP BY status', (exchange,)))
        reasons = dict(conn.execute("SELECT COALESCE(NULLIF(reject_reason,''),'unspecified'), COUNT(*) FROM paper_orders WHERE exchange=? AND is_paper=1 AND lower(status)='rejected' GROUP BY 1", (exchange,)))
        last_order = conn.execute('SELECT MAX(updated_at) FROM paper_orders WHERE exchange=? AND is_paper=1', (exchange,)).fetchone()[0]
        result[exchange] = {
            'orders_by_status': statuses, 'rejections_by_reason': reasons,
            'fills': fills[0], 'fees_usd': fills[1],
            'realized_pnl_usd': accounts[0], 'unrealized_pnl_usd': positions[0],
            'account_freshness': freshness(accounts[1], now),
            'order_freshness': freshness(last_order, now),
            'fill_freshness': freshness(fills[2], now),
        }
    return result


def build_diagnostics(service):
    now = datetime.now(timezone.utc)
    with service.db._lock:
        exchanges = ledger_diagnostics(service.db._conn, now)
    return {
        'mode': 'PAPER_ONLY', 'read_only': True, 'generated_at': now.isoformat(),
        'scope': 'all_time_paper_ledger',
        'pnl_semantics': 'Account realized PnL is net of execution fees; fees are reported separately, not subtracted again.',
        'exchanges': exchanges,
        'system': {
            'cycle': freshness(service.last_cycle_at, now),
            'observation': observation_status(service.config.base_dir / 'validation72' / 'latest.json', now),
        },
        'screening': {'cached_market_snapshots': len(service.scanner.latest_snapshots),
                      'active_signals': len(service.orchestrator.latest_signals),
                      'note': 'Order rejection counts are execution outcomes, not screening rejection counts.'},
        'risk': {'emergency_stop_active': service.risk.emergency_stop_active,
                 'live_execution_disabled': True},
    }
