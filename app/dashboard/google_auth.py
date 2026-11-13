"""Google OpenID Connect for dashboard, disabled until credentials and allowlist exist."""
from __future__ import annotations
import os
import secrets
from pathlib import Path
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware

ENV_PATH = Path("/root/trading-secrets/google-oauth.env")
CALLBACK = "https://trade.187-52-118-25.sslip.io/auth/google/callback"

def load_config():
    data = {}
    if ENV_PATH.exists():
        if ENV_PATH.stat().st_mode & 0o077:
            raise RuntimeError("OAuth credentials file must be mode 0600")
        for line in ENV_PATH.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    return data

def install(app):
    cfg = load_config()
    enabled = all(cfg.get(k) for k in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_ALLOWED_EMAIL", "SESSION_SECRET"))
    app.add_middleware(SessionMiddleware, secret_key=cfg.get("SESSION_SECRET") or secrets.token_urlsafe(48), https_only=True, same_site="lax", max_age=3600)
    oauth = OAuth()
    if enabled:
        oauth.register(name="google", client_id=cfg["GOOGLE_CLIENT_ID"], client_secret=cfg["GOOGLE_CLIENT_SECRET"],
                       server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
                       client_kwargs={"scope": "openid email profile"})
    return cfg, oauth, enabled
