"""Main unified trading engine service orchestrating scanner, AI orchestrator, bots, risk, and persistence."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.core.config import EngineConfig, LoadedEnvironment, load_environment
from app.core.logging_setup import setup_logging
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED, assert_paper_only
from app.exchanges.registry import ExchangeRegistry
from app.execution.bot_manager import MultiBotManager
from app.execution.mid_low_cap_runner import MidLowCapAutoTradeRunner
from app.execution.paper_engine import PaperExecutionEngine
from app.persistence.database import PaperDatabase
from app.risk.guardian import GlobalRiskGuardian
from app.scanner.market_scanner import GlobalMarketScanner
from app.strategies.orchestrator import AIStrategyOrchestrator
from app.treasury import AgentStrategyAllocator, UnifiedTreasuryLedger, AttributionCategory
from app.treasury.reconciliation_audit import audit_paper_pnl_coverage
from app.treasury.paper_funding import settle_paper_funding
from app.treasury.funding_coordinator import settle_captured_funding
from app.treasury.funding_worker import PaperFundingWorker
from app.core.models import MarketType
from app.wallets import SolanaWatchOnlyMonitor, load_watch_only_wallets
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
        self.risk = GlobalRiskGuardian(self.config.risk, db=self.db)
        self.paper_engine = PaperExecutionEngine(self.db, self.risk)
        self.bot_manager = MultiBotManager(self.paper_engine, self.registry)
        self.mid_low_cap_runner = MidLowCapAutoTradeRunner(
            config=self.config.mid_low_cap,
            registry=self.registry,
            scanner=self.scanner,
            orchestrator=self.orchestrator,
            paper_engine=self.paper_engine,
        )
        self.treasury: UnifiedTreasuryLedger | None = (
            UnifiedTreasuryLedger(self.db, self.config.unified_treasury)
            if self.config.unified_treasury.enabled
            else None
        )
        if self.treasury is not None:
            self.treasury.initialize_default_slots(self.config.unified_treasury.max_agent_slots)
        self.treasury_allocator: AgentStrategyAllocator | None = (
            AgentStrategyAllocator(self.treasury, self.risk)
            if self.treasury is not None
            else None
        )
        self.funding_worker = PaperFundingWorker(self.db,self.treasury,self.registry,
            probe_keys={(bot.bot_id,bot.exchange.value,symbol)
                        for bot in self.bot_manager.bots.values() if bot.market_type==MarketType.PERP
                        for symbol in bot.symbols})
        self.paper_engine.funding_gate = self.funding_worker.can_increase
        self.watch_only_wallets = load_watch_only_wallets(
            self.config.watch_only_wallets.registry_path
        )
        self.watch_only_monitor = SolanaWatchOnlyMonitor(
            wallets=self.watch_only_wallets,
            rpc_url=self.config.watch_only_wallets.solana_rpc_url,
            timeout_sec=self.config.watch_only_wallets.rpc_timeout_sec,
            max_retries=self.config.watch_only_wallets.rpc_max_retries,
            min_request_interval_sec=self.config.watch_only_wallets.rpc_min_interval_sec,
            cache_ttl_sec=self.config.watch_only_wallets.cache_ttl_sec,
        )

        if self.treasury is not None:
            self.sync_paper_fill_fees_to_treasury()
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

    def sync_paper_fill_fees_to_treasury(self) -> dict[str, Any]:
        """Idempotently attribute verified paper fill fees; never invent realized PnL.

        This is accounting-only: no exchange writes, transfers, or orders.
        """
        if self.treasury is None:
            return {"enabled": False}
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT * FROM paper_fills ORDER BY timestamp, fill_id"
            ).fetchall()
        processed = 0
        skipped = 0
        for row in rows:
            if not bool(row["is_paper"]):
                skipped += 1
                continue
            try:
                self.treasury.record_attribution(
                    idempotency_key=f"paper_fill_fee:{row['fill_id']}",
                    agent_id=row["bot_id"],
                    exchange=row["exchange"],
                    account_id=f"{row['exchange']}:paper",
                    strategy="paper_fill_unmapped_strategy",
                    symbol=row["symbol"],
                    asset="USD",
                    category=AttributionCategory.TRADING_FEE,
                    amount=-Decimal(str(row["fee_usd"])),
                )
                processed += 1
            except Exception:
                logger.exception("Treasury fee attribution failed for paper fill")
                skipped += 1
        with self.db._lock:
            closures = self.db._conn.execute("SELECT * FROM paper_realized_closures ORDER BY created_at, order_id").fetchall()
        close_count = 0
        for row in closures:
            try:
                self.treasury.record_attribution(
                    idempotency_key=f"paper_close_pnl:{row['order_id']}",
                    agent_id=row['bot_id'], exchange=row['exchange'],
                    account_id=f"{row['exchange']}:paper", strategy="paper_close_unmapped_strategy",
                    symbol=row['symbol'], asset="USD",
                    category=AttributionCategory.TRADE_REALIZED_PNL,
                    amount=Decimal(row['gross_realized_usd']),
                )
                close_count += 1
            except Exception:
                logger.exception("Treasury close PnL attribution failed")
                skipped += 1
        return {"paper_fills_seen": len(rows), "fees_processed_idempotently": processed,
                "closures_attributed_idempotently": close_count,
                "skipped": skipped, "realized_pnl_complete": False,
                "note": "Recorded closures attributed; historical and funding completeness unverified."}

    def paper_pnl_audit(self) -> dict[str, Any]:
        """Read-only reconciliation coverage including unresolved legacy gaps."""
        return audit_paper_pnl_coverage(self.db)

    def record_evidenced_paper_funding(self, **settlement: str) -> dict[str, Any]:
        """Only for explicitly evidenced paper settlements; no inferred schedule."""
        if self.treasury is None:
            raise RuntimeError("Paper treasury is disabled")
        return settle_paper_funding(self.db, self.treasury, **settlement)

    def settle_captured_paper_funding(self, **settlement: Any) -> dict[str, Any]:
        """Internal paper-only ingestion; never accepts caller-supplied exposure.

        Venue delivery/schedule completeness must be independently established.
        This is deliberately not exposed as an HTTP or exchange-order route.
        """
        if self.treasury is None:
            raise RuntimeError("Paper treasury is disabled")
        return settle_captured_funding(self.db, self.treasury, **settlement)

    async def run_cycle(self, fetch_all_tfs: bool = False) -> dict[str, Any]:
        assert_paper_only("paper")
        funding_report = await self.funding_worker.run_once()
        snapshots_list = await self.scanner.scan_all(fetch_all_tfs=fetch_all_tfs)
        closed_exits = self.paper_engine.mark_to_market_and_check_exits(self.scanner.latest_snapshots)
        signals = self.orchestrator.generate_signals(snapshots_list)
        orders = await self.bot_manager.process_signals_concurrently(
            signals, self.scanner.latest_snapshots
        )
        mid_low_cap_summary: dict[str, Any] = {"enabled": False, "orders_processed": 0}
        if self.config.mid_low_cap.enabled:
            mid_low_cap_summary = await self.mid_low_cap_runner.run_cycle()
        if self.treasury is not None:
            self.sync_paper_fill_fees_to_treasury()
        total_eq = self.paper_engine.total_portfolio_equity(self.scanner.latest_snapshots)
        accounts = self.db.list_accounts()
        initial_eq = sum(float(a.get("initial_equity_usd") or 0.0) for a in accounts)
        self.risk.update_equity_watermarks(total_eq, initial_equity_usd=initial_eq if initial_eq > 0 else None)
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
            "mid_low_cap": mid_low_cap_summary,
            "funding": funding_report,
        }

    async def sync_watch_only_wallets(self, force: bool = False) -> list[dict[str, Any]]:
        try:
            snaps = await self.watch_only_monitor.refresh_all(force=force)
            return [s.model_dump(mode="json") for s in snaps]
        except Exception as exc:
            logger.warning("Watch-only wallet sync error: %s", type(exc).__name__)
            return self.watch_only_monitor.all_cached_snapshots()

    async def _loop(self) -> None:
        await self.verify_auth_once()
        while not self._stop_event.is_set():
            if asyncio.get_running_loop().time() - self._last_readonly_sync_monotonic >= 120:
                self._last_readonly_sync_monotonic = asyncio.get_running_loop().time()
                await self.sync_readonly_accounts()
                await self.sync_watch_only_wallets()
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
        now_dt = datetime.now(timezone.utc)
        now_ts = int(now_dt.timestamp())
        positions = self.db.list_positions()
        total_eq = self.paper_engine.total_portfolio_equity(self.scanner.latest_snapshots)
        accounts = self.db.list_accounts()
        initial_eq = sum(a["initial_equity_usd"] for a in accounts) or self.config.initial_paper_equity_usd
        total_pnl = round(total_eq - initial_eq, 2)
        realized_pnl = round(sum(float(a.get("realized_pnl_usd") or 0.0) for a in accounts), 2)
        unrealized_pnl = round(sum(float(p.unrealized_pnl_usd or 0.0) for p in positions), 2)
        total_cash = round(sum(float(a.get("cash_usd") or 0.0) for a in accounts), 2)
        reserved_margin = round(sum(float(a.get("reserved_margin_usd") or 0.0) for a in accounts), 2)
        free_cash = round(max(0.0, total_cash - reserved_margin), 2)
        orders = self.db.list_orders(limit=50)
        fills = self.db.list_fills(limit=50)
        last_cycle_age_sec = (
            round(max(0.0, (now_dt - self.last_cycle_at).total_seconds()), 1)
            if self.last_cycle_at
            else None
        )
        available_pairs = sorted(
            {
                *self.scanner.symbols,
                *(s.symbol for s in self.scanner.latest_snapshots.values()),
                *(sym for b in self.bot_manager.bots.values() for sym in b.symbols),
            }
        )
        ro_timestamps = [
            int(v["timestamp"])
            for v in self.readonly_account_snapshots.values()
            if isinstance(v, dict) and isinstance(v.get("timestamp"), (int, float))
        ]
        readonly_sync_at = max(ro_timestamps) if ro_timestamps else None

        return {
            "mode": "PAPER_ONLY",
            "live_execution_disabled": LIVE_EXECUTION_PERMANENTLY_DISABLED,
            "paper_funding": self.funding_worker.report,
            "generated_at": now_ts,
            "cycle_count": self.cycle_count,
            "last_cycle_at": self.last_cycle_at.isoformat() if self.last_cycle_at else None,
            "last_cycle_age_sec": last_cycle_age_sec,
            "available_pairs": available_pairs,
            "readonly_sync_at": readonly_sync_at,
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
                "realized_pnl_usd": realized_pnl,
                "unrealized_pnl_usd": unrealized_pnl,
                "cash_usd": total_cash,
                "reserved_margin_usd": reserved_margin,
                "free_cash_usd": free_cash,
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
            "mid_low_cap": self.mid_low_cap_runner.status_summary(),
            "unified_treasury": (
                self.treasury.overview_snapshot(readonly_snapshots=self.readonly_account_snapshots)
                if self.treasury is not None
                else {
                    "enabled": False,
                    "mode": "PAPER_ONLY",
                    "live_order_submission": False,
                    "external_writes": False,
                    "signing_enabled": False,
                    "transfers_enabled": False,
                }
            ),
            "watch_only_wallets": {
                "mode": "watch_only",
                "read_only": True,
                "signing_enabled": False,
                "transfers_enabled": False,
                "external_writes": False,
                "counted_as_trading_cash": False,
                "wallets": self.watch_only_monitor.all_cached_snapshots(),
            },
            "telegram": {
                "enabled": self.telegram_adapter.is_enabled,
                "metrics": self.telegram_state.get_metrics() if self.telegram_state else {},
                "recent_alerts": self.telegram_state.list_recent_alerts(limit=30) if self.telegram_state else [],
            },
        }
