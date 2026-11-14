"""Read-only persistence diagnostics; missing observations never imply health."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import sqlite3

from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED


def persistence_health(app):
    result = {
        'status': 'unavailable', 'live_execution_enabled': False,
        'live_execution_permanently_disabled': LIVE_EXECUTION_PERMANENTLY_DISABLED,
        'sqlite': {'status': 'unavailable'},
        'last_brain_cycle_timestamp': None,
        'scheduler': {'status': 'unavailable', 'heartbeat_timestamp': None},
        'risk_guardian': {'status': 'unavailable'},
    }
    router = getattr(app.state, 'closed_loop_router', None)
    brain = getattr(app.state, 'unified_brain', None)
    guardian = getattr(brain, 'risk_guardian', None)
    if guardian is not None and guardian is getattr(router, 'risk_guardian', None):
        halted = guardian.emergency_stop_active or guardian.circuit_breaker_tripped
        result['risk_guardian'] = {
            'status': 'halted' if halted else 'ok',
            'emergency_stop_active': guardian.emergency_stop_active,
            'circuit_breaker_tripped': guardian.circuit_breaker_tripped,
        }
    heartbeat = getattr(app.state, 'persistence_scheduler_heartbeat', None)
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(heartbeat)).total_seconds()
        result['scheduler'] = {'status': 'ok' if 0 <= age <= 120 else 'stale',
                               'heartbeat_timestamp': heartbeat, 'age_seconds': age}
    except (TypeError, ValueError):
        pass
    if router is not None and router.state_path is not None:
        try:
            # A separate read-only connection and snapshot never refresh or mutate the live router.
            with sqlite3.connect(router.state_path.resolve().as_uri() + '?mode=ro', uri=True,
                                 timeout=1) as conn:
                conn.execute('PRAGMA query_only=ON')
                conn.execute('BEGIN')
                integrity = [row[0] for row in conn.execute('PRAGMA integrity_check')]
                if integrity != ['ok']:
                    raise ValueError('integrity_check_failed')
                snapshot = copy.copy(router)
                snapshot._connection = conn
                if conn.execute('SELECT 1 FROM closed_loop_state WHERE id=1').fetchone() is None:
                    raise ValueError('router_state_missing')
                snapshot._load_state()
                invariant = snapshot.check_equity_invariant()
                reconciliation = snapshot.reconcile()
                row = conn.execute('SELECT report_json FROM unified_brain_cycles ORDER BY rowid DESC LIMIT 1').fetchone()
                if row:
                    timestamp = json.loads(row[0])['timestamp_utc']
                    datetime.fromisoformat(timestamp)
                    result['last_brain_cycle_timestamp'] = timestamp
                result['sqlite'] = {'status': 'ok', 'integrity': 'ok',
                                    'reconciliation': reconciliation, 'invariant': invariant}
        except Exception as exc:
            # Do not expose database paths, contents, or exception messages over HTTP.
            result['sqlite'] = {'status': 'unavailable', 'reason': type(exc).__name__}
    if (result['sqlite']['status'] == 'ok' and result['scheduler']['status'] == 'ok'
            and result['risk_guardian']['status'] == 'ok'
            and result['last_brain_cycle_timestamp'] is not None
            and LIVE_EXECUTION_PERMANENTLY_DISABLED):
        result['status'] = 'ok'
    return result
