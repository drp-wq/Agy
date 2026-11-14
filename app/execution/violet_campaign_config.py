"""Durable configuration and archive summary helpers for the 500 USDT VIOLET All-Cap campaign."""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CAMPAIGN_ID = "violet-all-cap-500-v1"
DEFAULT_CAMPAIGN_DIRNAME = "violet-all-cap-500-v1"
DEFAULT_ARCHIVE_DIRNAME = "violet-all-cap"
DEFAULT_POINTER_FILENAME = "active-violet-campaign.json"
DEFAULT_TOTAL_BUDGET_USDT = 500.0
DEFAULT_SLEEVE_CAPITAL_USDT = 100.0
DEFAULT_CAMPAIGN_HOURS = 72
DEFAULT_MAX_RISK_PER_TRADE_PCT = 0.01
DEFAULT_MIN_ORDER_NOTIONAL_USD = 5.0


@dataclass(frozen=True)
class VioletCampaignSettings:
    campaign_id: str
    campaign_dir: Path
    historical_archive_dir: Path
    pointer_path: Path
    total_budget_usdt: float
    sleeve_capital_usdt: float
    campaign_hours: int
    max_risk_per_trade_pct: float
    min_order_notional_usd: float


def _parse_float(raw: str | None, default: float) -> float:
    if raw is None or not raw.strip():
        return default
    return float(raw.strip())


def _parse_int(raw: str | None, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    return int(raw.strip())


def resolve_violet_campaign_settings(
    root: Path,
    environ: dict[str, str] | None = None,
    persist_pointer: bool = True,
) -> VioletCampaignSettings:
    """Resolve active VIOLET campaign settings from durable pointer + env, failing closed on archive/budget errors."""
    env = environ if environ is not None else os.environ
    root_path = Path(root).resolve()

    pointer_raw = env.get("VIOLET_ACTIVE_CAMPAIGN_CONFIG")
    pointer_path = Path(pointer_raw).expanduser().resolve() if pointer_raw and pointer_raw.strip() else (root_path / DEFAULT_POINTER_FILENAME)

    pointer_data: dict[str, Any] = {}
    if pointer_path.exists():
        loaded = json.loads(pointer_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("invalid_active_violet_campaign_pointer")
        pointer_data = loaded

    campaign_id = (
        (env.get("VIOLET_CAMPAIGN_ID") or "").strip()
        or str(pointer_data.get("campaign_id") or "").strip()
        or DEFAULT_CAMPAIGN_ID
    )

    dir_raw = (env.get("VIOLET_CAMPAIGN_DIR") or "").strip() or str(pointer_data.get("campaign_dir") or "").strip()
    campaign_dir = Path(dir_raw).expanduser().resolve() if dir_raw else (root_path / DEFAULT_CAMPAIGN_DIRNAME)

    arch_raw = (env.get("VIOLET_HISTORICAL_ARCHIVE_DIR") or "").strip() or str(pointer_data.get("historical_archive_dir") or "").strip()
    historical_archive_dir = Path(arch_raw).expanduser().resolve() if arch_raw else (root_path / DEFAULT_ARCHIVE_DIRNAME)

    if campaign_dir == historical_archive_dir:
        raise ValueError("active_campaign_dir_cannot_equal_historical_archive_dir")

    if pointer_data:
        ptr_id = str(pointer_data.get("campaign_id") or "").strip()
        ptr_dir = str(pointer_data.get("campaign_dir") or "").strip()
        if ptr_id and ptr_id != campaign_id:
            raise ValueError(f"active_campaign_pointer_mismatch:{ptr_id}!={campaign_id}")
        if ptr_dir and Path(ptr_dir).resolve() != campaign_dir:
            raise ValueError("active_campaign_dir_pointer_mismatch")

    total_budget_usdt = _parse_float(
        env.get("VIOLET_TOTAL_BUDGET_USDT"),
        float(pointer_data.get("total_budget_usdt") or DEFAULT_TOTAL_BUDGET_USDT),
    )
    sleeve_capital_usdt = _parse_float(
        env.get("VIOLET_SLEEVE_CAPITAL_USDT"),
        float(pointer_data.get("sleeve_capital_usdt") or DEFAULT_SLEEVE_CAPITAL_USDT),
    )
    campaign_hours = _parse_int(
        env.get("VIOLET_CAMPAIGN_HOURS"),
        int(pointer_data.get("campaign_hours") or DEFAULT_CAMPAIGN_HOURS),
    )
    max_risk_per_trade_pct = _parse_float(
        env.get("VIOLET_MAX_RISK_PER_TRADE_PCT"),
        float(pointer_data.get("max_risk_per_trade_pct") or DEFAULT_MAX_RISK_PER_TRADE_PCT),
    )
    min_order_notional_usd = _parse_float(
        env.get("VIOLET_MIN_ORDER_NOTIONAL_USD"),
        float(pointer_data.get("min_order_notional_usd") or DEFAULT_MIN_ORDER_NOTIONAL_USD),
    )

    if abs(total_budget_usdt - 5.0 * sleeve_capital_usdt) > 1e-6:
        raise ValueError("violet_budget_must_equal_five_sleeves")
    if total_budget_usdt <= 0 or sleeve_capital_usdt < 100.0:
        raise ValueError("violet_sleeve_capital_must_be_at_least_100")
    if not 0 < campaign_hours <= 72:
        raise ValueError("violet_campaign_hours_must_be_within_72")

    settings = VioletCampaignSettings(
        campaign_id=campaign_id,
        campaign_dir=campaign_dir,
        historical_archive_dir=historical_archive_dir,
        pointer_path=pointer_path,
        total_budget_usdt=round(total_budget_usdt, 2),
        sleeve_capital_usdt=round(sleeve_capital_usdt, 2),
        campaign_hours=campaign_hours,
        max_risk_per_trade_pct=max_risk_per_trade_pct,
        min_order_notional_usd=min_order_notional_usd,
    )

    if persist_pointer and not pointer_path.exists():
        pointer_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "campaign_id": settings.campaign_id,
            "campaign_dir": str(settings.campaign_dir),
            "historical_archive_dir": str(settings.historical_archive_dir),
            "total_budget_usdt": settings.total_budget_usdt,
            "sleeve_capital_usdt": settings.sleeve_capital_usdt,
            "campaign_hours": settings.campaign_hours,
            "max_risk_per_trade_pct": settings.max_risk_per_trade_pct,
            "min_order_notional_usd": settings.min_order_notional_usd,
            "mode": "PAPER_ONLY",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp = pointer_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(pointer_path)

    return settings


def read_historical_archive_summary(archive_dir: Path | None) -> dict[str, Any]:
    """Read-only summary of the archived historical campaign without opening or mutating live ledgers."""
    if archive_dir is None:
        return {"status": "none_configured", "separate_from_active_campaign": True}

    arch_path = Path(archive_dir)
    db_path = arch_path / "violet-all-cap.sqlite3"
    report_path = arch_path / "72H_RUN_REPORT.json"

    if not db_path.exists():
        return {
            "status": "not_present",
            "archive_dir": arch_path.name,
            "initial_budget_usdt": None,
            "realized_pnl_usdt": None,
            "fills": 0,
            "orders": 0,
            "open_positions": 0,
            "separate_from_active_campaign": True,
        }

    report: dict[str, Any] = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            report = {}

    try:
        uri = db_path.resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=1) as conn:
            conn.execute("PRAGMA query_only=ON")
            accts = conn.execute(
                "SELECT COALESCE(SUM(initial_equity_usd), 0), COALESCE(SUM(cash_usd), 0), COALESCE(SUM(realized_pnl_usd), 0), COUNT(*) FROM paper_accounts"
            ).fetchone()
            orders_count = conn.execute("SELECT COUNT(*) FROM paper_orders").fetchone()[0]
            fills_count = conn.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
            open_pos_count = conn.execute("SELECT COUNT(*) FROM paper_positions WHERE qty > 0").fetchone()[0]
        return {
            "status": "archived_read_only",
            "archive_dir": arch_path.name,
            "started_at": report.get("started_at"),
            "ends_at": report.get("ends_at"),
            "cycles_observed": report.get("cycles_observed"),
            "initial_budget_usdt": round(float(accts[0] or 0.0), 2),
            "cash_usdt": round(float(accts[1] or 0.0), 2),
            "realized_pnl_usdt": round(float(accts[2] or 0.0), 4),
            "sleeve_count": int(accts[3] or 0),
            "orders": int(orders_count or 0),
            "fills": int(fills_count or 0),
            "open_positions": int(open_pos_count or 0),
            "separate_from_active_campaign": True,
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "archive_dir": arch_path.name,
            "reason": type(exc).__name__,
            "separate_from_active_campaign": True,
        }
