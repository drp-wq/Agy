#!/usr/bin/env python3
"""Worktree-relative observational paper validation; no services or ledger writes."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import sys
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from app.dashboard.diagnostics import ledger_diagnostics, observation_status


def collect(db_path, root, base_url=None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    record = {'timestamp': now.isoformat(), 'mode': 'PAPER_ONLY',
              'previous_observation': observation_status(root / 'latest.json', now)}
    window_path = root / 'window.json'
    if window_path.exists():
        try:
            end = dt.datetime.fromisoformat(json.loads(window_path.read_text())['end_utc'].replace('Z', '+00:00'))
            if end.tzinfo is None:
                raise ValueError('timezone required')
            record['seconds_remaining'] = max(0, int((end - now).total_seconds()))
            record['window_complete'] = now >= end
        except (ValueError, KeyError, TypeError):
            record['window_error'] = 'invalid'
    # mode=ro prevents SQLite from creating or modifying the source ledger.
    try:
        with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5) as conn:
            conn.execute('PRAGMA query_only=ON')
            record['exchanges'] = ledger_diagnostics(conn, now)
            record['all_orders_paper'] = conn.execute('SELECT COUNT(*) FROM paper_orders WHERE is_paper IS NOT 1').fetchone()[0] == 0
            record['all_fills_paper'] = conn.execute('SELECT COUNT(*) FROM paper_fills WHERE is_paper IS NOT 1').fetchone()[0] == 0
    except sqlite3.Error as exc:
        record['ledger_error_type'] = type(exc).__name__
    if base_url:
        for name, endpoint in [('public_health', '/api/health'), ('private_auth_guard', '/api/miniapp/diagnostics')]:
            try:
                with urllib.request.urlopen(base_url.rstrip('/') + endpoint, timeout=7) as response:
                    record[name] = response.status
            except urllib.error.HTTPError as exc:
                record[name] = exc.code
            except (OSError, ValueError):
                record[name] = None
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=REPO / 'validation72')
    parser.add_argument('--db', type=Path, default=REPO / 'data' / 'paper_engine.sqlite3')
    parser.add_argument('--base-url', default=os.environ.get('PAPER_MONITOR_BASE_URL'))
    args = parser.parse_args()
    record = collect(args.db, args.root, args.base_url)
    args.root.mkdir(parents=True, exist_ok=True)
    with (args.root / 'observations.jsonl').open('a') as stream:
        os.chmod(stream.name, 0o600)
        stream.write(json.dumps(record, separators=(',', ':')) + '\n')
    tmp = args.root / 'latest.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(json.dumps(record, indent=2))
    tmp.replace(args.root / 'latest.json')
    if record.get('window_complete'):
        (args.root / 'complete.flag').touch(exist_ok=True)


if __name__ == '__main__':
    main()
