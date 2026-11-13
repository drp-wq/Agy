"""Tests for read-only Binance intelligence, reconciliation coverage, and AI collaboration contract."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from app.core.config import ExchangeCredentials
from app.core.models import ExchangeId, MarketRegime, MarketSnapshot, MarketType
from app.core.security import SecretRedactionFilter
from app.exchanges.binance import BinanceAdapter
from app.intelligence import (
    AgentRole,
    AICollaborationBrief,
    AICollaborationCoordinator,
    BinanceIntelligenceService,
    ReconciliationCoverageAssessment,
    assess_reconciliation_coverage,
)


def _snap(
    exchange: ExchangeId,
    symbol: str = "BTC/USDT",
    market_type: MarketType = MarketType.SPOT,
    last_price: float = 100_000.0,
    spread_bps: float = 2.0,
    liquidity_score: float = 0.85,
    regime: MarketRegime = MarketRegime.TRENDING_BULLISH,
    rsi: float = 58.0,
    funding_rate: float | None = None,
    opportunity_score: float = 0.75,
) -> MarketSnapshot:
    return MarketSnapshot(
        exchange=exchange,
        symbol=symbol,
        market_type=market_type,
        timestamp=datetime.now(timezone.utc),
        last_price=last_price,
        bid=last_price * 0.9999,
        ask=last_price * 1.0001,
        spread_bps=spread_bps,
        volume_24h_usd=250_000_000.0,
        relative_volume=1.4,
        volatility=0.015,
        trend_strength=0.65,
        rsi=rsi,
        macd=10.0,
        macd_signal=8.0,
        macd_hist=2.0,
        atr=500.0,
        ema_fast=last_price * 0.998,
        ema_slow=last_price * 0.995,
        ema_trend=last_price * 0.990,
        ema_alignment="bullish",
        support=last_price * 0.98,
        resistance=last_price * 1.02,
        funding_rate=funding_rate,
        order_book_depth_usd=1_500_000.0,
        liquidity_score=liquidity_score,
        regime=regime,
        opportunity_score=opportunity_score,
    )


def test_reconciliation_coverage_distinguishes_partial_from_complete() -> None:
    missing = assess_reconciliation_coverage(None)
    assert missing.coverage_type == "UNAVAILABLE"
    assert missing.complete_account_reconciliation is False
    assert missing.completeness_claim_allowed is False

    partial_report = {
        "status": "PARTIAL_SNAPSHOT",
        "scope": "spot_selected_symbols_only",
        "complete_account_reconciliation": False,
        "futures": {"status": "ok", "open_positions": 0, "orders_and_trades_checked": False},
        "symbols": {
            "BTC/USDT": {
                "open_orders": {"status": "ok", "count": 0},
                "recent_orders": {"status": "ok", "count": 47, "exhausted": True},
                "recent_trades": {"status": "ok", "count": 97, "exhausted": True},
            }
        },
    }
    partial = assess_reconciliation_coverage(partial_report)
    assert partial.coverage_type == "SELECTED_SYMBOLS_SNAPSHOT"
    assert partial.complete_account_reconciliation is False
    assert partial.completeness_claim_allowed is False
    assert "non_watchlist_spot_symbols" in partial.uncovered_areas
    assert "futures_orders_and_trades" in partial.uncovered_areas

    # Guardrail: even if a caller sets complete_account_reconciliation=True on a partial scope, block it.
    spoofed = dict(partial_report, complete_account_reconciliation=True)
    spoofed_assessed = assess_reconciliation_coverage(spoofed)
    assert spoofed_assessed.complete_account_reconciliation is False
    assert spoofed_assessed.completeness_claim_allowed is False

    full_report = {
        "status": "COMPLETE",
        "scope": "full_account",
        "complete_account_reconciliation": True,
        "transfers_checked": True,
        "futures": {"status": "ok", "open_positions": 0, "orders_and_trades_checked": True},
        "symbols": {
            "BTC/USDT": {
                "open_orders": {"status": "ok", "count": 0},
                "recent_orders": {"status": "ok", "count": 10, "exhausted": True},
                "recent_trades": {"status": "ok", "count": 12, "exhausted": True},
            }
        },
    }
    full = assess_reconciliation_coverage(full_report)
    assert full.coverage_type == "COMPLETE_ACCOUNT"
    assert full.complete_account_reconciliation is True
    assert full.completeness_claim_allowed is True
    assert full.uncovered_areas == []


def test_binance_market_intelligence_and_dislocation_detection() -> None:
    svc = BinanceIntelligenceService()
    snapshots = [
        _snap(ExchangeId.OKX, "BTC/USDT", MarketType.SPOT, last_price=100_000.0),
        _snap(ExchangeId.BYBIT, "BTC/USDT", MarketType.SPOT, last_price=100_000.0),
        _snap(
            ExchangeId.BINANCE,
            "BTC/USDT",
            MarketType.SPOT,
            last_price=100_250.0,  # +25 bps vs peer median
            opportunity_score=0.91,
        ),
        _snap(
            ExchangeId.BINANCE,
            "ETH/USDT",
            MarketType.PERP,
            last_price=3_500.0,
            spread_bps=18.5,
            liquidity_score=0.25,
            regime=MarketRegime.LOW_LIQUIDITY,
            rsi=78.0,
            funding_rate=0.0009,
            opportunity_score=0.40,
        ),
    ]

    report = svc.analyze_market_snapshots(snapshots)
    assert report.read_only is True
    assert report.markets_scanned == 2
    assert report.spot_count == 1
    assert report.perp_count == 1
    assert report.top_opportunities[0].symbol == "BTC/USDT"
    assert report.top_opportunities[0].cross_exchange_basis_bps == 25.0
    assert len(report.cross_exchange_dislocations) == 1
    eth_flags = report.top_opportunities[1].risk_flags
    assert {"LOW_LIQUIDITY", "WIDE_SPREAD", "CROWDED_FUNDING", "RSI_OVERBOUGHT"}.issubset(
        set(eth_flags)
    )

    empty_report = svc.analyze_market_snapshots([])
    assert empty_report.markets_scanned == 0
    assert empty_report.warnings


@pytest.mark.asyncio
async def test_binance_account_observation_read_only() -> None:
    class _StubBinanceAdapter(BinanceAdapter):
        def __init__(self) -> None:
            super().__init__(
                credentials=ExchangeCredentials(
                    exchange="binance",
                    api_key="dummy-key-1234",
                    api_secret="dummy-secret-5678",
                )
            )

        async def fetch_read_only_balance(self) -> dict[str, Any]:
            return {"configured": True, "balances": {"USDT": {"total": 100.0}, "BTC": {"total": 0.1}}}

        async def fetch_read_only_positions(self) -> list[dict[str, Any]]:
            return []

    adapter = _StubBinanceAdapter()
    try:
        svc = BinanceIntelligenceService()
        obs = await svc.observe_account(
            adapter,
            reconcile_report={
                "status": "PARTIAL_SNAPSHOT",
                "scope": "spot_selected_symbols_only",
                "complete_account_reconciliation": False,
                "futures": {"orders_and_trades_checked": False},
                "symbols": {"BTC/USDT": {"open_orders": {"status": "ok"}}},
            },
        )
        assert obs.read_only is True
        assert obs.live_execution_disabled is True
        assert obs.writes_allowed is False
        assert obs.auth_status == "ok"
        assert obs.non_zero_asset_count == 2
        assert obs.observed_assets == ["BTC", "USDT"]
        assert obs.reconciliation.completeness_claim_allowed is False
    finally:
        adapter.close()


@pytest.mark.asyncio
async def test_ai_collaboration_contract_roles_bounds_and_redaction() -> None:
    svc = BinanceIntelligenceService()
    market_report = svc.analyze_market_snapshots(
        [_snap(ExchangeId.BINANCE, "BTC/USDT", MarketType.SPOT, opportunity_score=0.88)]
    )

    class _StubBinanceAdapter(BinanceAdapter):
        def __init__(self) -> None:
            super().__init__(credentials=ExchangeCredentials(exchange="binance"))

    adapter = _StubBinanceAdapter()
    try:
        account_obs = await svc.observe_account(adapter, reconcile_report=None)
    finally:
        adapter.close()

    redactor = SecretRedactionFilter(secrets=["super_secret_token_999"])
    coordinator = AICollaborationCoordinator(redactor=redactor)
    brief = coordinator.build_brief(
        market_report=market_report,
        account_obs=account_obs,
        readiness_report={
            "operational_checks_passed": True,
            "pending": ["multi-day paper soak", "api_key=super_secret_token_999"],
        },
    )

    assert brief.execution_mode == "PAPER_ADVISORY_ONLY"
    assert brief.live_execution_allowed is False
    assert brief.live_readiness == "NOT_APPROVED"
    assert brief.completeness_claim_allowed is False
    assert {r.role for r in brief.roles} == set(AgentRole)
    assert all(r.authority.can_execute_live is False for r in brief.roles)
    assert all(r.authority.can_write_exchange is False for r in brief.roles)
    assert all(r.authority.can_modify_credentials is False for r in brief.roles)
    assert len(brief.top_opportunities) <= 5
    assert len(brief.recommended_actions) <= 5
    assert len(brief.warnings) <= 10
    assert "super_secret_token_999" not in brief.model_dump_json()

    # Contract invariant: cannot claim completeness when reconciliation is partial.
    with pytest.raises(ValidationError):
        AICollaborationBrief(
            generated_at_utc=brief.generated_at_utc,
            roles=brief.roles,
            market_summary=brief.market_summary,
            reconciliation_coverage=ReconciliationCoverageAssessment(
                coverage_type="SELECTED_SYMBOLS_SNAPSHOT",
                complete_account_reconciliation=False,
                completeness_claim_allowed=False,
                summary="Partial only.",
            ),
            completeness_claim_allowed=True,
        )
