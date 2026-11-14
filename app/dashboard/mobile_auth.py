"""Persistent, hashed, revocable native read-only sessions and one-use PKCE handoffs.

This sidecar contains no exchange credentials and never touches a campaign ledger.
Only server-verified identities may issue handoffs; opaque tokens are returned once.
"""
from __future__ import annotations

import base64
import hashlib
import re
import os
import secrets
import sqlite3
import time
from collections import deque
from threading import Lock
from contextlib import contextmanager
from pathlib import Path

from fastapi import HTTPException

AUDIENCE = 'tech.prottushjd.violet'  # Existing uploaded Android applicationId.
REDIRECT = 'tech.prottushjd.violet://auth/callback'  # Existing native callback.
PKCE = re.compile(r'^[A-Za-z0-9._~-]{43,128}$')
CHALLENGE = re.compile(r'^[A-Za-z0-9_-]{43}$')
STATE = re.compile(r'^[A-Za-z0-9_-]{32,128}$')
TOKEN = re.compile(r'^[A-Za-z0-9_-]{43,128}$')


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('ascii')).hexdigest()


def challenge(verifier: str) -> str:
    if not PKCE.fullmatch(verifier):
        raise HTTPException(400, 'Invalid PKCE verifier')
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode().rstrip('=')


def validate_launch(params) -> dict:
    if (params.get('app_id') != AUDIENCE or params.get('redirect_uri') != REDIRECT or
            params.get('code_challenge_method') != 'S256' or
            not CHALLENGE.fullmatch(params.get('code_challenge', '')) or
            not STATE.fullmatch(params.get('state', ''))):
        raise HTTPException(400, 'Invalid native authorization request')
    return {'audience': AUDIENCE, 'redirect_uri': REDIRECT, 'state': params['state'],
            'challenge': params['code_challenge']}


class MobileSessionStore:
    ACCESS_SECONDS = 900
    FAMILY_SECONDS = 7 * 86400
    HANDOFF_SECONDS = 90

    def __init__(self, path: Path, allowed_email: str, clock=time.time):
        self.path = Path(path)
        self.allowed_email = allowed_email.strip().lower()
        self.clock = clock

    @contextmanager
    def connection(self):
        # Provision lazily: ordinary dashboard reads do not create a sidecar.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        if self.path.stat().st_mode & 0o077:
            raise RuntimeError('Native session database must be mode 0600')
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS mobile_sessions (
                    family_id TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL,
                    audience TEXT NOT NULL, access_hash TEXT UNIQUE NOT NULL,
                    refresh_hash TEXT UNIQUE NOT NULL, access_until REAL NOT NULL,
                    family_until REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS mobile_handoffs (
                    code_hash TEXT PRIMARY KEY, email TEXT NOT NULL, name TEXT NOT NULL,
                    audience TEXT NOT NULL, redirect_uri TEXT NOT NULL,
                    challenge TEXT NOT NULL, expires REAL NOT NULL, used INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS mobile_used_refresh (
                    token_hash TEXT PRIMARY KEY, family_id TEXT NOT NULL REFERENCES mobile_sessions(family_id));
            ''')
            conn.execute('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _identity(self, email: str):
        if not self.allowed_email or email.lower() != self.allowed_email:
            raise HTTPException(403, 'Account not authorized')

    def issue_handoff(self, launch: dict, email: str, name: str) -> str:
        self._identity(email)
        code = secrets.token_urlsafe(32)
        with self.connection() as conn:
            conn.execute('INSERT INTO mobile_handoffs VALUES (?,?,?,?,?,?,?,0)',
                         (digest(code), email.lower(), name[:120], launch['audience'],
                          launch['redirect_uri'], launch['challenge'], self.clock() + self.HANDOFF_SECONDS))
        return code

    def _issue(self, conn, email: str, name: str, *, family_id=None, family_until=None) -> dict:
        self._identity(email)
        now = self.clock()
        family_id = family_id or secrets.token_urlsafe(24)
        family_until = family_until or now + self.FAMILY_SECONDS
        access_until = min(now + self.ACCESS_SECONDS, family_until)
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        conn.execute('''INSERT INTO mobile_sessions VALUES (?,?,?,?,?,?,?,?,0)
            ON CONFLICT(family_id) DO UPDATE SET access_hash=excluded.access_hash,
              refresh_hash=excluded.refresh_hash, access_until=excluded.access_until''',
                     (family_id, email.lower(), name[:120], AUDIENCE, digest(access), digest(refresh),
                      access_until, family_until))
        return {'session_token': access, 'refresh_token': refresh, 'email': email.lower(),
                'name': name[:120], 'audience': AUDIENCE, 'scope': 'paper:read',
                'issued_at_ms': int(now * 1000), 'expires_at_ms': int(access_until * 1000),
                'refresh_expires_at_ms': int(family_until * 1000)}

    def issue_authenticated(self, email: str, name: str) -> dict:
        with self.connection() as conn:
            return self._issue(conn, email, name)

    def exchange(self, code: str, verifier: str, redirect_uri: str, audience: str) -> dict:
        if not TOKEN.fullmatch(code) or audience != AUDIENCE or redirect_uri != REDIRECT:
            raise HTTPException(400, 'Invalid handoff request')
        expected = challenge(verifier)
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM mobile_handoffs WHERE code_hash=?', (digest(code),)).fetchone()
            if (row is None or row['used'] or row['expires'] <= self.clock() or
                    row['audience'] != audience or row['redirect_uri'] != redirect_uri or
                    not secrets.compare_digest(row['challenge'], expected)):
                raise HTTPException(401, 'Expired or invalid handoff')
            conn.execute('UPDATE mobile_handoffs SET used=1 WHERE code_hash=?', (digest(code),))
            return self._issue(conn, row['email'], row['name'])

    def verify(self, token: str) -> str:
        if not TOKEN.fullmatch(token) or not self.path.exists():
            raise HTTPException(401, 'Invalid native session')
        with self.connection() as conn:
            row = conn.execute('SELECT * FROM mobile_sessions WHERE access_hash=?', (digest(token),)).fetchone()
            if (row is None or row['revoked'] or row['access_until'] <= self.clock() or
                    row['family_until'] <= self.clock() or row['audience'] != AUDIENCE):
                raise HTTPException(401, 'Expired or invalid native session')
            self._identity(row['email'])
            return row['email']

    def refresh(self, token: str, audience: str) -> dict:
        if not TOKEN.fullmatch(token) or audience != AUDIENCE:
            raise HTTPException(401, 'Invalid refresh request')
        replay = False
        result = None
        with self.connection() as conn:
            old = conn.execute('SELECT family_id FROM mobile_used_refresh WHERE token_hash=?', (digest(token),)).fetchone()
            if old:
                # Commit revocation even though the caller receives an error.
                conn.execute('UPDATE mobile_sessions SET revoked=1 WHERE family_id=?', (old['family_id'],))
                replay = True
            else:
                row = conn.execute('SELECT * FROM mobile_sessions WHERE refresh_hash=?', (digest(token),)).fetchone()
                if row is None or row['revoked'] or row['family_until'] <= self.clock():
                    raise HTTPException(401, 'Expired or invalid refresh token')
                conn.execute('INSERT INTO mobile_used_refresh VALUES (?,?)', (digest(token), row['family_id']))
                result = self._issue(conn, row['email'], row['name'], family_id=row['family_id'], family_until=row['family_until'])
        if replay:
            raise HTTPException(401, 'Refresh replay: session revoked')
        return result

    def revoke(self, access: str):
        if not TOKEN.fullmatch(access) or not self.path.exists():
            return
        with self.connection() as conn:
            conn.execute('UPDATE mobile_sessions SET revoked=1 WHERE access_hash=?', (digest(access),))


def authenticate_native(request) -> str | None:
    header = request.headers.get('authorization', '')
    if not header.lower().startswith('bearer '):
        return None
    if request.url.scheme != 'https':
        raise HTTPException(400, 'Native sessions require HTTPS')
    store = getattr(request.app.state, 'mobile_sessions', None)
    if store is None:
        raise HTTPException(401, 'Native sessions unavailable')
    email = store.verify(header[7:])
    if request.method not in {'GET', 'HEAD'}:
        raise HTTPException(403, 'Native sessions are read-only')
    return email


class MobileAttemptLimiter:
    """Bounded per-peer attempt limiter; no credential or token values retained."""
    def __init__(self, limit=20, window=60, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._peers = {}
        self._lock = Lock()

    def check(self, peer):
        now = self.clock()
        with self._lock:
            self._peers = {key: value for key, value in self._peers.items()
                           if value and now - value[-1] < self.window}
            if peer not in self._peers and len(self._peers) >= 4096:
                raise HTTPException(429, 'Native authentication busy', headers={'Retry-After': '60'})
            attempts = self._peers.setdefault(peer, deque())
            while attempts and now - attempts[0] >= self.window:
                attempts.popleft()
            if len(attempts) >= self.limit:
                raise HTTPException(429, 'Native authentication rate limited', headers={'Retry-After': '60'})
            attempts.append(now)
