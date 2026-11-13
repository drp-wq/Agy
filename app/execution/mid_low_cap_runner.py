"""Feature-flagged mid/low-cap adaptive paper autotrading runner."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from app.core.config import MidLowCapConfig
from app.core.models import (
    ExchangeId,
    MarketSnapshot,
    MarketType,
    PaperOrder,
    PaperPosition,
    StrategySignal,
)
from app.core.security import assert_paper_only
from app.exchanges.registry import ExchangeRegistry
from app.execution.paper_engine import PaperExecutionEngine
from app.scanner.market_cap import MarketCapProvider, MarketCapRecord
from app.scanner.market_scanner import GlobalMarketScanner
from app.scanner.mid_low_cap_discovery import (
    DiscoveredSpotMarket,
    DynamicSpotMarketDiscoverer,
    evaluate_safety_filters,
)
from app.strategies.mid_low_cap_selector import MidLowCapStrategySelector
from app.strategies.orchestrator import AIStrategyOrchestrator

logger = logging.getLogger("multi_exchange.mid_low_cap")

MID_LOW_CAP_BOT_ID = "mid_low_cap_spot_paper"


class MidLowCapAutoTradeRunner:
    """Coordinates discovery, safety gates, adaptive strategy selection, and 0.25%-risk paper execution."""

    def __init__(
        self,
        config: MidLowCapConfig,
        registry: ExchangeRegistry,
        scanner: GlobalMarketScanner,
        orchestrator: AIStrategyOrchestrator,
        paper_engine: PaperExecutionEngine,
        cap_provider: MarketCapProvider | None = None,
    ) -> None:
        assert_paper_only("paper")
        self.config = config
        self.registry = registry
        self.scanner = scanner
        self.orchestrator = orchestrator
        self.paper_engine = paper_engine
        self.cap_provider = cap_provider or MarketCapProvider(config=config)
        self.discoverer = DynamicSpotMarketDiscoverer(self.cap_provider, config=config)
        self.selector = MidLowCapStrategySelector(self.orchestrator, config=config)

        self._daily_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._daily_start_equity: float = 0.0
        self._sleeve_circuit_tripped: bool = False
        self._sleeve_circuit_reason: str | None = None

        self.last_run_at: datetime | None = None
        self.last_discovered_count: int = 0
        self.last_scanned_count: int = 0
        self.last_safe_count: int = 0
        self.last_signals_count: int = 0
        self.last_orders: list[dict[str, Any]] = []
        self.last_rejections: list[dict[str, Any]] = []

        if self.config.enabled:
            self.paper_engine.ensure_bot_account(
                MID_LOW_CAP_BOT_ID, ExchangeId.BINANCE, self.config.allocated_capital_usd
            )

    def sleeve_positions(self) -> list[PaperPosition]:
        return self.paper_engine.db.list_positions(MID_LOW_CAP_BOT_ID)

    def sleeve_equity_usd(self, snapshots: dict[str, MarketSnapshot] | None = None) -> float:
        acct = self.paper_engine.db.get_account(MID_LOW_CAP_BOT_ID)
        if not acct:
            return self.config.allocated_capital_usd
        cash = float(acct["cash_usd"])
        unrealized = 0.0
        for pos in self.sleeve_positions():
            mark = pos.mark_price
            if snapshots:
                key = f"{pos.exchange.value}:{pos.market_type.value}:{pos.symbol}"
                if key in snapshots:
                    mark = snapshots[key].last_price
            unrealized += (mark - pos.entry_price) * pos.qty
        return round(cash + unrealized, 2)

    def _check_daily_loss_limit(self, equity: float) -> bool:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_start_equity <= 0 or today != self._daily_date:
            self._daily_date = today
            self._daily_start_equity = equity
            self._sleeve_circuit_tripped = False
            self._sleeve_circuit_reason = None
        if self._daily_start_equity <= 0:
            return False
        loss_pct = (self._daily_start_equity - equity) / self._daily_start_equity
        if loss_pct >= self.config.daily_loss_limit_pct:
            self._sleeve_circuit_tripped = True
            self._sleeve_circuit_reason = f"sleeve_daily_loss_limit:{loss_pct:.2%}"
            return True
        return self._sleeve_circuit_tripped

    def compute_position_notional(
        self,
        signal: StrategySignal,
        snapshot: MarketSnapshot,
        sleeve_equity: float,
        available_cash: float,
    ) -> float:
        """Size position strictly from 0.25% max equity risk divided by ATR stop distance."""
        entry = snapshot.last_price
        if entry <= 0 or signal.stop_loss <= 0:
            return 0.0
        stop_dist_pct = abs(entry - signal.stop_loss) / entry
        if stop_dist_pct <= 0:
            return 0.0
        risk_pct = min(self.config.max_risk_per_trade_pct, 0.0025)
        risk_usd = sleeve_equity * risk_pct
        raw_notional = risk_usd / stop_dist_pct
        depth_cap = snapshot.order_book_depth_usd * self.config.max_notional_to_depth_ratio
        cash_cap = max(0.0, available_cash * 0.95)
        equity_cap = sleeve_equity * 0.35
        notional = min(raw_notional, depth_cap, cash_cap, equity_cap)
        return round(max(0.0, notional), 2)

    async def run_cycle(self) -> dict[str, Any]:
        """Run one bounded discovery -> safety -> strategy -> paper execution cycle."""
        assert_paper_only("paper")
        if not self.config.enabled:
            return {"enabled": False, "orders_processed": 0}

        self.paper_engine.ensure_bot_account(
            MID_LOW_CAP_BOT_ID, ExchangeId.BINANCE, self.config.allocated_capital_usd
        )
        self.last_run_at = datetime.now(timezone.utc)
        self.last_rejections = []
        self.last_orders = []

        # 1. Discover USDT spot markets in LOW/MID cap bands across healthy exchanges
        discovered_all: list[DiscoveredSpotMarket] = []
        batch_all: list[DiscoveredSpotMarket] = []
        for adapter in self.registry.all().values():
            if not adapter.health_status().get("healthy", False):
                continue
            try:
                disc = await self.discoverer.discover_for_exchange(adapter)
                discovered_all.extend(disc)
                batch_all.extend(self.discoverer.next_scan_batch(adapter.exchange_id, disc))
            except Exception as exc:
                logger.warning(
                    "Mid/low-cap discovery failed on %s: %s",
                    adapter.exchange_id.value,
                    type(exc).__name__,
                )

        self.last_discovered_count = len(discovered_all)

        # 2. Scan bounded batch (spot only)
        scan_tasks = [
            self.scanner.scan_pair(
                self.registry.get(m.exchange),
                m.symbol,
                MarketType.SPOT,
                fetch_all_tfs=False,
            )
            for m in batch_all
        ]
        scan_results = await asyncio.gather(*scan_tasks, return_exceptions=False) if scan_tasks else []
        snapshots = [s for s in scan_results if s is not None]
        self.last_scanned_count = len(snapshots)

        # 3. Hard safety filters
        open_positions = self.sleeve_positions()
        cap_by_base: dict[str, MarketCapRecord] = {m.base: m.cap_record for m in batch_all}
        safe_snapshots: list[MarketSnapshot] = []
        for snap in snapshots:
            base = snap.symbol.split("/")[0].upper()
            cap_rec = cap_by_base.get(base) or self.cap_provider.classify_symbol(base)
            res = evaluate_safety_filters(
                snapshot=snap,
                cap_record=cap_rec,
                config=self.config,
                existing_positions=open_positions,
            )
            if res.passed:
                safe_snapshots.append(snap)
            else:
                self.last_rejections.append(
                    {
                        "stage": "safety_filter",
                        "exchange": snap.exchange.value,
                        "symbol": snap.symbol,
                        "reasons": res.reasons,
                    }
                )
        self.last_safe_count = len(safe_snapshots)

        # 4. Adaptive strategy selection with OOS & net expectancy gating
        signals, strat_diags = self.selector.select_signals(safe_snapshots, cap_by_base)
        self.last_signals_count = len(signals)
        for d in strat_diags[:20]:
            self.last_rejections.append({"stage": "strategy_gate", **d})

        # 5. Sleeve risk limits & paper execution
        equity = self.sleeve_equity_usd(self.scanner.latest_snapshots)
        if self._check_daily_loss_limit(equity):
            self.last_rejections.append(
                {"stage": "sleeve_risk", "reason": self._sleeve_circuit_reason or "daily_loss_limit"}
            )
            return self.status_summary()

        acct = self.paper_engine.db.get_account(MID_LOW_CAP_BOT_ID)
        available_cash = float(acct["cash_usd"]) if acct else self.config.allocated_capital_usd
        orders: list[PaperOrder] = []

        open_bases = {p.symbol.split("/")[0].upper() for p in open_positions}
        open_count = len(open_positions)

        for sig in signals:
            if open_count >= self.config.max_simultaneous_positions:
                self.last_rejections.append(
                    {"stage": "sleeve_risk", "symbol": sig.symbol, "reason": "max_3_positions_reached"}
                )
                break

            base = sig.symbol.split("/")[0].upper()
            if base in open_bases:
                self.last_rejections.append(
                    {"stage": "sleeve_risk", "symbol": sig.symbol, "reason": "no_averaging_down_same_base"}
                )
                continue

            key = f"{sig.exchange.value}:{sig.market_type.value}:{sig.symbol}"
            snap = self.scanner.latest_snapshots.get(key)
            if snap is None:
                continue

            notional = self.compute_position_notional(sig, snap, equity, available_cash)
            if notional < 10.0:
                self.last_rejections.append(
                    {"stage": "sizing", "symbol": sig.symbol, "reason": f"notional_too_small:{notional}"}
                )
                continue

            healthy = self.registry.get(sig.exchange).health_status().get("healthy", False)
            bucket_ts = int(datetime.now(timezone.utc).timestamp() // 60)
            client_oid = f"{MID_LOW_CAP_BOT_ID}:{sig.exchange.value}:{sig.symbol}:{sig.strategy_name}:{bucket_ts}"

            order = await asyncio.to_thread(
                self.paper_engine.submit_signal_order,
                MID_LOW_CAP_BOT_ID,
                sig,
                notional,
                1.0,  # spot only, no leverage
                snap,
                client_oid,
                healthy,
            )
            orders.append(order)
            self.last_orders.append(order.model_dump())
            if order.filled_qty > 0:
                open_bases.add(base)
                open_count += 1
                available_cash = max(0.0, available_cash - order.fee_usd)

        return {
            "enabled": True,
            "discovered": self.last_discovered_count,
            "scanned": self.last_scanned_count,
            "safe": self.last_safe_count,
            "signals": self.last_signals_count,
            "orders_processed": len(orders),
        }

    def status_summary(self) -> dict[str, Any]:
        positions = self.sleeve_positions()
        return {
            "enabled": self.config.enabled,
            "bot_id": MID_LOW_CAP_BOT_ID,
            "spot_only": not self.config.allow_short,
            "max_leverage": self.config.max_leverage,
            "max_risk_per_trade_pct": self.config.max_risk_per_trade_pct,
            "daily_loss_limit_pct": self.config.daily_loss_limit_pct,
            "max_simultaneous_positions": self.config.max_simultaneous_positions,
            "min_risk_reward_ratio": self.config.min_risk_reward_ratio,
            "sleeve_equity_usd": self.sleeve_equity_usd(self.scanner.latest_snapshots),
            "open_positions_count": len(positions),
            "open_positions": [p.model_dump() for p in positions],
            "circuit_tripped": self._sleeve_circuit_tripped,
            "circuit_reason": self._sleeve_circuit_reason,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_discovered_count": self.last_discovered_count,
            "last_scanned_count": self.last_scanned_count,
            "last_safe_count": self.last_safe_count,
            "last_signals_count": self.last_signals_count,
            "last_orders": self.last_orders[:10],
            "last_rejections": self.last_rejections[:20],
            "market_cap_provider": self.cap_provider.status_summary(),
        }
