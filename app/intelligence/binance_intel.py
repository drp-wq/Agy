"""Read-only Binance market intelligence, account observation, and reconciliation coverage guardrails."""

from __future__ import annotations

from datetime import datetime, timezone
from statistics import median
from typing import Any, Iterable, Literal

from pydantic import BaseModel, Field

from app.core.models import ExchangeId, MarketRegime, MarketSnapshot, MarketType
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED, assert_paper_only
from app.exchanges.binance import BinanceAdapter


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReconciliationCoverageAssessment(BaseModel):
    """Distinguishes selected-symbol snapshots from complete account reconciliation."""

    coverage_type: Literal["SELECTED_SYMBOLS_SNAPSHOT", "COMPLETE_ACCOUNT", "UNAVAILABLE"]
    complete_account_reconciliation: bool = False
    completeness_claim_allowed: bool = False
    symbols_checked: int = 0
    futures_orders_and_trades_checked: bool = False
    uncovered_areas: list[str] = Field(default_factory=list, max_length=10)
    summary: str = Field(..., max_length=400)


def assess_reconciliation_coverage(
    reconcile_report: dict[str, Any] | None,
) -> ReconciliationCoverageAssessment:
    """Evaluate reconciliation scope and block unsupported completeness claims."""
    if not reconcile_report or not isinstance(reconcile_report, dict):
        return ReconciliationCoverageAssessment(
            coverage_type="UNAVAILABLE",
            complete_account_reconciliation=False,
            completeness_claim_allowed=False,
            symbols_checked=0,
            futures_orders_and_trades_checked=False,
            uncovered_areas=[
                "spot_orders_and_trades",
                "futures_orders_and_trades",
                "deposits_withdrawals_transfers",
            ],
            summary="No Binance reconciliation report available; completeness claim blocked.",
        )

    symbols = reconcile_report.get("symbols") or {}
    futures = reconcile_report.get("futures") or {}
    scope = str(reconcile_report.get("scope") or "unknown")
    status = str(reconcile_report.get("status") or "UNKNOWN")
    futures_orders_checked = bool(futures.get("orders_and_trades_checked", False))

    uncovered: list[str] = []
    if scope != "full_account":
        uncovered.append("non_watchlist_spot_symbols")
    if not futures_orders_checked:
        uncovered.append("futures_orders_and_trades")
    if not bool(reconcile_report.get("transfers_checked", False)):
        uncovered.append("deposits_withdrawals_transfers")

    has_unexhausted_pages = False
    has_symbol_errors = False
    for sym_entry in symbols.values():
        if not isinstance(sym_entry, dict):
            continue
        for endpoint_key in ("open_orders", "recent_orders", "recent_trades"):
            ep = sym_entry.get(endpoint_key)
            if not isinstance(ep, dict):
                continue
            if ep.get("status") != "ok":
                has_symbol_errors = True
            if endpoint_key != "open_orders" and ep.get("exhausted") is False:
                has_unexhausted_pages = True

    if has_unexhausted_pages:
        uncovered.append("truncated_order_or_trade_history")
    if has_symbol_errors:
        uncovered.append("errored_symbol_endpoints")

    raw_complete = bool(reconcile_report.get("complete_account_reconciliation", False))
    is_complete = (
        raw_complete
        and scope == "full_account"
        and status == "COMPLETE"
        and futures_orders_checked
        and not uncovered
    )

    if is_complete:
        return ReconciliationCoverageAssessment(
            coverage_type="COMPLETE_ACCOUNT",
            complete_account_reconciliation=True,
            completeness_claim_allowed=True,
            symbols_checked=len(symbols),
            futures_orders_and_trades_checked=True,
            uncovered_areas=[],
            summary=f"Complete account reconciliation verified across {len(symbols)} symbols.",
        )

    return ReconciliationCoverageAssessment(
        coverage_type="SELECTED_SYMBOLS_SNAPSHOT" if symbols else "UNAVAILABLE",
        complete_account_reconciliation=False,
        completeness_claim_allowed=False,
        symbols_checked=len(symbols),
        futures_orders_and_trades_checked=futures_orders_checked,
        uncovered_areas=uncovered[:10],
        summary=(
            f"Partial snapshot only ({status}, scope={scope}, symbols={len(symbols)}); "
            "complete account reconciliation NOT verified."
        )[:400],
    )


class BinanceMarketSignalSummary(BaseModel):
    """Bounded read-only market intelligence summary for a single Binance instrument."""

    symbol: str = Field(..., max_length=32)
    market_type: MarketType
    last_price: float
    spread_bps: float
    volume_24h_usd: float
    order_book_depth_usd: float
    liquidity_score: float
    regime: MarketRegime
    trend_strength: float
    rsi: float
    ema_alignment: str = Field(..., max_length=24)
    opportunity_score: float
    funding_rate: float | None = None
    liquidation_signal: str | None = Field(default=None, max_length=32)
    cross_exchange_basis_bps: float | None = None
    risk_flags: list[str] = Field(default_factory=list, max_length=6)


class BinanceMarketReport(BaseModel):
    """Aggregated read-only Binance market scanner report."""

    generated_at_utc: str
    exchange: Literal["binance"] = "binance"
    read_only: Literal[True] = True
    markets_scanned: int = 0
    spot_count: int = 0
    perp_count: int = 0
    regime_breakdown: dict[str, int] = Field(default_factory=dict)
    top_opportunities: list[BinanceMarketSignalSummary] = Field(default_factory=list, max_length=10)
    cross_exchange_dislocations: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    warnings: list[str] = Field(default_factory=list, max_length=10)


class BinanceAccountObservation(BaseModel):
    """Read-only Binance account observation without credential exposure."""

    observed_at_utc: str
    exchange: Literal["binance"] = "binance"
    read_only: Literal[True] = True
    live_execution_disabled: Literal[True] = True
    writes_allowed: Literal[False] = False
    auth_status: str = Field(..., max_length=32)
    configured: bool
    non_zero_asset_count: int = 0
    observed_assets: list[str] = Field(default_factory=list, max_length=25)
    open_futures_positions: int = 0
    reconciliation: ReconciliationCoverageAssessment


class BinanceIntelligenceService:
    """Produces read-only Binance market intelligence and account observations."""

    @staticmethod
    def _extract_risk_flags(snap: MarketSnapshot) -> list[str]:
        flags: list[str] = []
        if snap.regime == MarketRegime.LOW_LIQUIDITY or snap.liquidity_score < 0.35:
            flags.append("LOW_LIQUIDITY")
        if snap.spread_bps > 15.0:
            flags.append("WIDE_SPREAD")
        if snap.funding_rate is not None and abs(snap.funding_rate) > 0.0005:
            flags.append("CROWDED_FUNDING")
        if snap.rsi >= 75.0:
            flags.append("RSI_OVERBOUGHT")
        elif snap.rsi <= 25.0:
            flags.append("RSI_OVERSOLD")
        if snap.regime == MarketRegime.HIGH_VOLATILITY:
            flags.append("HIGH_VOLATILITY")
        return flags[:6]

    def analyze_market_snapshots(
        self,
        snapshots: dict[str, MarketSnapshot] | Iterable[MarketSnapshot],
    ) -> BinanceMarketReport:
        """Analyze Binance snapshots and compare prices against peer exchanges."""
        assert_paper_only("paper")
        all_snaps = list(snapshots.values()) if isinstance(snapshots, dict) else list(snapshots)

        peer_prices: dict[tuple[str, MarketType], list[float]] = {}
        binance_snaps: list[MarketSnapshot] = []
        for snap in all_snaps:
            if snap.exchange == ExchangeId.BINANCE:
                binance_snaps.append(snap)
            elif snap.last_price > 0:
                peer_prices.setdefault((snap.symbol, snap.market_type), []).append(snap.last_price)

        regime_counts: dict[str, int] = {}
        summaries: list[BinanceMarketSignalSummary] = []
        dislocations: list[dict[str, Any]] = []
        warnings: list[str] = []

        for snap in binance_snaps:
            regime_key = snap.regime.value
            regime_counts[regime_key] = regime_counts.get(regime_key, 0) + 1

            basis_bps: float | None = None
            peers = peer_prices.get((snap.symbol, snap.market_type), [])
            if peers and snap.last_price > 0:
                peer_med = median(peers)
                if peer_med > 0:
                    basis_bps = round(((snap.last_price - peer_med) / peer_med) * 10_000.0, 2)
                    if abs(basis_bps) >= 15.0:
                        dislocations.append(
                            {
                                "symbol": snap.symbol,
                                "market_type": snap.market_type.value,
                                "binance_price": snap.last_price,
                                "peer_median_price": round(peer_med, 6),
                                "basis_bps": basis_bps,
                            }
                        )

            summaries.append(
                BinanceMarketSignalSummary(
                    symbol=snap.symbol,
                    market_type=snap.market_type,
                    last_price=snap.last_price,
                    spread_bps=snap.spread_bps,
                    volume_24h_usd=snap.volume_24h_usd,
                    order_book_depth_usd=snap.order_book_depth_usd,
                    liquidity_score=snap.liquidity_score,
                    regime=snap.regime,
                    trend_strength=snap.trend_strength,
                    rsi=snap.rsi,
                    ema_alignment=snap.ema_alignment,
                    opportunity_score=snap.opportunity_score,
                    funding_rate=snap.funding_rate,
                    liquidation_signal=snap.liquidation_signal,
                    cross_exchange_basis_bps=basis_bps,
                    risk_flags=self._extract_risk_flags(snap),
                )
            )

        summaries.sort(key=lambda item: item.opportunity_score, reverse=True)
        dislocations.sort(key=lambda item: abs(item["basis_bps"]), reverse=True)

        if not binance_snaps:
            warnings.append("No Binance market snapshots available in current scan window.")
        if dislocations:
            warnings.append(
                f"Detected {len(dislocations)} Binance vs peer price dislocation(s) >= 15 bps."
            )

        return BinanceMarketReport(
            generated_at_utc=_utc_iso(),
            markets_scanned=len(binance_snaps),
            spot_count=sum(1 for s in binance_snaps if s.market_type == MarketType.SPOT),
            perp_count=sum(1 for s in binance_snaps if s.market_type == MarketType.PERP),
            regime_breakdown=regime_counts,
            top_opportunities=summaries[:10],
            cross_exchange_dislocations=dislocations[:10],
            warnings=warnings[:10],
        )

    async def observe_account(
        self,
        adapter: BinanceAdapter,
        reconcile_report: dict[str, Any] | None = None,
    ) -> BinanceAccountObservation:
        """Perform strictly read-only Binance account observation and coverage assessment."""
        assert_paper_only("paper")
        if not LIVE_EXECUTION_PERMANENTLY_DISABLED:
            raise RuntimeError("Live execution lock must remain permanently enabled.")

        health = adapter.health_status()
        if not health.get("read_only_enforced", False):
            raise RuntimeError("Binance adapter must enforce read-only boundaries.")

        configured = bool(adapter.credentials and adapter.credentials.is_configured)
        auth_status = "unconfigured"
        observed_assets: list[str] = []
        open_positions = 0

        if configured:
            try:
                bal = await adapter.fetch_read_only_balance()
                balances = bal.get("balances") or {}
                observed_assets = sorted(str(k) for k in balances.keys())[:25]
                auth_status = "ok" if bal.get("configured") else "unconfigured"
            except Exception as exc:
                auth_status = f"error:{type(exc).__name__}"[:32]

            try:
                positions = await adapter.fetch_read_only_positions()
                open_positions = len(positions)
            except Exception:
                pass

        coverage = assess_reconciliation_coverage(reconcile_report)
        return BinanceAccountObservation(
            observed_at_utc=_utc_iso(),
            auth_status=auth_status,
            configured=configured,
            non_zero_asset_count=len(observed_assets),
            observed_assets=observed_assets,
            open_futures_positions=open_positions,
            reconciliation=coverage,
        )
