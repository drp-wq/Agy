"""Dashboard authentication using constant-time verification and 0600 secret storage."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from app.core.logging_setup import register_sensitive_value
from app.core.security import validate_secrets_file_permissions

security = HTTPBasic(auto_error=False)
DEFAULT_DASHBOARD_ENV = Path("/root/trading-secrets/dashboard.env")


class DashboardAuthConfig:
    """Loads or provisions dashboard credentials with strict 0600 permissions."""

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        register_sensitive_value(password)

    @classmethod
    def load_or_create(cls, env_path: Path = DEFAULT_DASHBOARD_ENV) -> DashboardAuthConfig:
        env_user = os.getenv("DASHBOARD_USER")
        env_pass = os.getenv("DASHBOARD_PASSWORD")
        if env_user and env_pass:
            return cls(env_user, env_pass)

        if env_path.exists():
            validate_secrets_file_permissions(env_path)
            data: dict[str, str] = {}
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    data[k.strip()] = v.strip()
            if data.get("DASHBOARD_USER") and data.get("DASHBOARD_PASSWORD"):
                return cls(data["DASHBOARD_USER"], data["DASHBOARD_PASSWORD"])

        username = "trader"
        password = secrets.token_urlsafe(24)
        env_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(env_path.parent, 0o700)
        env_path.write_text(
            f"DASHBOARD_USER={username}\nDASHBOARD_PASSWORD={password}\n",
            encoding="utf-8",
        )
        os.chmod(env_path, 0o600)
        return cls(username, password)

    def verify(self, credentials: HTTPBasicCredentials | None) -> str:
        if credentials is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Basic"},
            )
        user_ok = secrets.compare_digest(credentials.username.encode(), self.username.encode())
        pass_ok = secrets.compare_digest(credentials.password.encode(), self.password.encode())
        if not (user_ok and pass_ok):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid credentials",
                headers={"WWW-Authenticate": "Basic"},
            )
        return credentials.username


def make_auth_dependency(auth_cfg: DashboardAuthConfig):
    def _require_auth(
        request: Request,
        credentials: HTTPBasicCredentials | None = Depends(security),
    ) -> str:
        from app.dashboard.mobile_auth import authenticate_native
        native = authenticate_native(request)
        return native if native is not None else auth_cfg.verify(credentials)

    return _require_auth
