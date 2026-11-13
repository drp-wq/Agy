"""Main unified trading engine service orchestrating scanner, AI orchestrator, bots, risk, and persistence."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import EngineConfig, LoadedEnvironment, load_environment
from app.core.logging_setup import setup_logging
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED, assert_paper_only
from app.exchanges.registry import ExchangeRegistry
from app.execution.bot_manager import MultiBotManager
from app.execution.paper_engine import PaperExecutionEngine
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.scanner.market_scanner import GlobalMarketScanner
from app.strategies.orchestrator import AIStrategyOrchestrator
from app.telegram.adapter import TelegramAlertAdapter
from app.telegram.commands import TelegramCommandHandler
from app.telegram.config import TelegramConfig, load_telegram_config
from app.telegram.poller import TelegramPollingWorker
from app.telegram.state import TelegramStateStore

logger = logging.getLogger("multi_exchange.service")


class TradingEngineService:
    """Central service coordinating OKX and Bybit paper trading."""

    def __init__(
        self,
        env: LoadedEnvironment | None = None,
        config: EngineConfig | None = None,
        telegram_config: TelegramConfig | None = None,
        enable_telegram: bool = True,
    ) -> None:
        assert_paper_only("paper")
        self.env = env or load_environment(config=config, require_secrets_file=False)
        self.config = self.env.config
        setup_logging(self.config.log_dir)

        self.db = PaperDatabase(self.config.db_path)
        self.registry = ExchangeRegistry.from_credentials(self.env.credentials)
        self.scanner = GlobalMarketScanner(self.registry)
        self.orchestrator = AIStrategyOrchestrator()
        self.risk = GlobalRiskGuardian(self.config.risk)
        self.paper_engine = PaperExecutionEngine(self.db, self.risk)
        self.bot_manager = MultiBotManager(self.paper_engine, self.registry)

        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self.cycle_count = 0
        self.last_cycle_at: datetime | None = None
        self.auth_verification: dict[str, Any] = {}
        self.readonly_account_snapshots: dict[str, Any] = {}
        self._last_readonly_sync_monotonic = 0.0
        self.recovery_report: dict[str, Any] = self.paper_engine.recover_on_startup()

        self.telegram_config: TelegramConfig | None = (
            telegram_config
            if telegram_config is not None
            else (load_telegram_config(require_file=False) if enable_telegram else None)
        )
        self.telegram_state: TelegramStateStore | None = (
            TelegramStateStore(self.telegram_config.state_path)
            if self.telegram_config and self.telegram_config.enabled
            else None
        )
        self.telegram_adapter = TelegramAlertAdapter(
            self.telegram_config, state_store=self.telegram_state
        )
        self.telegram_poller: TelegramPollingWorker | None = (
            TelegramPollingWorker(
                client=self.telegram_adapter.client,
                handler=TelegramCommandHandler(
                    service=self,
                    config=self.telegram_config,
                    state_store=self.telegram_state,
                ),
                state_store=self.telegram_state,
            )
            if self.telegram_adapter.is_enabled
            and self.telegram_adapter.client is not None
            and self.telegram_state is not None
            and self.telegram_config is not None
            else None
        )

    async def verify_auth_once(self) -> dict[str, Any]:
        self.auth_verification = await self.registry.verify_all_read_only_auth()
        return self.auth_verification

    async def sync_readonly_accounts(self) -> None:
        """Cache non-sensitive read-only balances and positions; never trade or value unknown assets."""
        async def collect(exchange_id, adapter):
            key = exchange_id.value
            if not adapter.credentials or not adapter.credentials.is_configured:
                self.readonly_account_snapshots[key] = {"status": "unconfigured", "balances": {}, "positions": [], "timestamp": None}
                return
            try:
                balance = await adapter.fetch_read_only_balance()
                positions = await adapter.fetch_read_only_positions()
                self.readonly_account_snapshots[key] = {
                    "status": "ok", "balances": balance.get("balances", {}),
                    "positions": positions, "timestamp": int(datetime.now(timezone.utc).timestamp()),
                }
            except Exception as exc:
                logger.warning("Read-only account sync failed for %s: %s", key, type(exc).__name__)
                self.readonly_account_snapshots[key] = {
                    "status": "error", "balances": {}, "positions": [],
                    "timestamp": None, "error_type": type(exc).__name__,
                }
        await asyncio.gather(*(collect(k, v) for k, v in self.registry.all().items()))

    async def run_cycle(self, fetch_all_tfs: bool = False) -> dict[str, Any]:
        assert_paper_only("paper")
        snapshots_list = await self.scanner.scan_all(fetch_all_tfs=fetch_all_tfs)
        closed_exits = self.paper_engine.mark_to_market_and_check_exits(self.scanner.latest_snapshots)
        signals = self.orchestrator.generate_signals(snapshots_list)
        orders = await self.bot_manager.process_signals_concurrently(
            signals, self.scanner.latest_snapshots
        )
        self.cycle_count += 1
        self.last_cycle_at = datetime.now(timezone.utc)
        try:
            await self.telegram_adapter.on_cycle_complete(self, orders, closed_exits)
        except Exception as exc:
            logger.warning("Telegram cycle hook error: %s", type(exc).__name__)
        return {
            "cycle": self.cycle_count,
            "snapshots": len(snapshots_list),
            "signals": len(signals),
            "orders_processed": len(orders),
        }

    async def _loop(self) -> None:
        await self.verify_auth_once()
        while not self._stop_event.is_set():
            if asyncio.get_running_loop().time() - self._last_readonly_sync_monotonic >= 120:
                self._last_readonly_sync_monotonic = asyncio.get_running_loop().time()
                await self.sync_readonly_accounts()
            try:
                await self.run_cycle(fetch_all_tfs=False)
            except Exception as exc:
                logger.exception("Engine cycle error: %s", type(exc).__name__)
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self.config.scanner_interval_sec
                )
            except asyncio.TimeoutError:
                continue

    def start_background(self) -> None:
        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._loop())
        if self.telegram_poller is not None:
            self.telegram_poller.start()

    async def shutdown(self) -> None:
        self._stop_event.set()
        if self.telegram_poller is not None:
            await self.telegram_poller.stop()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.telegram_adapter.close()
        self.registry.close_all()
        self.db.close()

    def state_snapshot(self) -> dict[str, Any]:
        positions = self.db.list_positions()
        total_eq = self.paper_engine.total_portfolio_equity(self.scanner.latest_snapshots)
        accounts = self.db.list_accounts()
        initial_eq = sum(a["initial_equity_usd"] for a in accounts) or self.config.initial_paper_equity_usd
        total_pnl = round(total_eq - initial_eq, 2)
        orders = self.db.list_orders(limit=50)
        fills = self.db.list_fills(limit=50)

        return {
            "mode": "PAPER_ONLY",
            "live_execution_disabled": LIVE_EXECUTION_PERMANENTLY_DISABLED,
            "cycle_count": self.cycle_count,
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "exchanges": self.registry.health_summary(),
            "auth_verification": self.auth_verification,
            "readonly_account_snapshots": self.readonly_account_snapshots,
            "credentials_metadata": {
                k: v.metadata() for k, v in self.env.credentials.items()
            },
            "portfolio": {
                "total_equity_usd": total_eq,
                "initial_equity_usd": round(initial_eq, 2),
                "total_pnl_usd": total_pnl,
                "total_pnl_pct": round((total_pnl / initial_eq) * 100.0, 2) if initial_eq > 0 else 0.0,
                "open_positions": [p.model_dump() for p in positions],
            },
            "risk": self.risk.status_summary(total_eq, positions),
            "bots": self.bot_manager.list_bots_status(),
            "scanner": self.scanner.summary_rows()[:25],
            "signals": [s.model_dump() for s in self.orchestrator.latest_signals[:25]],
            "orders": [o.model_dump() for o in orders],
            "fills": [f.model_dump() for f in fills],
            "calibration": self.orchestrator.calibrator.stats(),
            "reconciliation": self.paper_engine.reconcile_positions(),
            "recovery": self.recovery_report,
            "telegram": {
                "enabled": self.telegram_adapter.is_enabled,
                "metrics": self.telegram_state.get_metrics() if self.telegram_state else {},
                "recent_alerts": self.telegram_state.list_recent_alerts(limit=30) if self.telegram_state else [],
            },
        }
