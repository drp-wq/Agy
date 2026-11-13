"""Unified entrypoint running the OKX & Bybit paper engine and authenticated dashboard."""

from __future__ import annotations

import signal
import sys
from contextlib import asynccontextmanager

import uvicorn

from app.core.config import load_environment
from app.dashboard.server import create_dashboard_app
from app.service import TradingEngineService


def main() -> None:
    env = load_environment(require_secrets_file=True)
    service = TradingEngineService(env=env)
    app = create_dashboard_app(service)

    @asynccontextmanager
    async def lifespan(_app):
        service.start_background()
        try:
            yield
        finally:
            await service.shutdown()

    app.router.lifespan_context = lifespan

    def _handle_sig(*_args):
        sys.exit(0)

    signal.signal(signal.SIGTERM, _handle_sig)
    uvicorn.run(
        app,
        host=env.config.dashboard_host,
        port=env.config.dashboard_port,
        log_level="info",
    )


if __name__ == "__main__":
    main()
