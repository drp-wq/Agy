#!/usr/bin/env python3
"""Read-only Binance intelligence and AI collaboration verification runner.

Never submits orders, never enables live execution, and never logs credentials.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import load_environment
from app.core.models import ExchangeId, MarketType
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED, assert_paper_only
from app.exchanges.registry import ExchangeRegistry
from app.intelligence import AICollaborationCoordinator, BinanceIntelligenceService
from app.scanner.market_scanner import GlobalMarketScanner

ROOT = Path("/root/multi-exchange-paper")
RECONCILE_PATH = ROOT / "data" / "binance_order_reconcile.json"
READINESS_PATH = ROOT / "data" / "readiness_watch.json"
DB_PATH = ROOT / "data" / "paper_engine.sqlite3"
EVIDENCE_LOG = ROOT / "logs" / "binance-agy-integration-20261113.log"
AUDIT_SYMBOLS = ("BTC/USDT", "ETH/USDT", "SOL/USDT")


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _read_paper_evidence() -> dict[str, Any]:
    if not DB_PATH.exists():
        return {"status": "missing"}
    db = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        orders = db.execute(
            "SELECT exchange, status, count(*) FROM paper_orders WHERE is_paper=1 GROUP BY exchange, status"
        ).fetchall()
        fills = db.execute(
            "SELECT exchange, count(*) FROM paper_fills WHERE is_paper=1 GROUP BY exchange"
        ).fetchall()
        return {
            "status": "ok",
            "orders_by_exchange_status": {f"{ex}:{st}": cnt for ex, st, cnt in orders},
            "fills_by_exchange": {ex: cnt for ex, cnt in fills},
        }
    finally:
        db.close()


async def main() -> None:
    assert_paper_only("paper")
    env = load_environment(require_secrets_file=True)
    registry = ExchangeRegistry.from_credentials(env.credentials)
    scanner = GlobalMarketScanner(registry, symbols=AUDIT_SYMBOLS, max_concurrency=3)
    intel = BinanceIntelligenceService()
    coordinator = AICollaborationCoordinator()

    try:
        binance_adapter = registry.get(ExchangeId.BINANCE)
        scan_tasks = []
        for sym in AUDIT_SYMBOLS:
            scan_tasks.append(scanner.scan_pair(binance_adapter, sym, MarketType.SPOT, fetch_all_tfs=False))
            scan_tasks.append(scanner.scan_pair(binance_adapter, sym, MarketType.PERP, fetch_all_tfs=False))
        await asyncio.gather(*scan_tasks)

        market_report = intel.analyze_market_snapshots(scanner.latest_snapshots)
        reconcile_report = _load_json(RECONCILE_PATH)
        readiness_report = _load_json(READINESS_PATH)
        account_obs = await intel.observe_account(binance_adapter, reconcile_report=reconcile_report)
        brief = coordinator.build_brief(
            market_report=market_report,
            account_obs=account_obs,
            readiness_report=readiness_report,
        )
        paper_evidence = _read_paper_evidence()

        payload = {
            "run_at_utc": datetime.now(timezone.utc).isoformat(),
            "live_execution_permanently_disabled": LIVE_EXECUTION_PERMANENTLY_DISABLED,
            "market_report": market_report.model_dump(),
            "account_observation": account_obs.model_dump(),
            "ai_collaboration_brief": brief.model_dump(),
            "paper_execution_evidence": paper_evidence,
            "readiness_watch": readiness_report,
        }

        EVIDENCE_LOG.parent.mkdir(parents=True, exist_ok=True)
        EVIDENCE_LOG.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "evidence_log": str(EVIDENCE_LOG),
                    "markets_scanned": market_report.markets_scanned,
                    "auth_status": account_obs.auth_status,
                    "reconciliation_coverage": brief.reconciliation_coverage.coverage_type,
                    "completeness_claim_allowed": brief.completeness_claim_allowed,
                    "live_execution_allowed": brief.live_execution_allowed,
                    "recommended_actions": len(brief.recommended_actions),
                    "paper_fills": paper_evidence.get("fills_by_exchange", {}),
                },
                indent=2,
            )
        )
    finally:
        registry.close_all()


if __name__ == "__main__":
    asyncio.run(main())
