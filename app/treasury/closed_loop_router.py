"""Closed-loop paper asset router across the Unified Hub, 3 CEXs, and Solana DEX."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED
from app.risk.guardian import GlobalRiskGuardian
from app.treasury.models import (
    DuplicateIdempotencyError,
    InsufficientFundsError,
    LiveTransferBlockedError,
    QUANT_2,
    TreasuryError,
    TreasuryInvariantError,
    to_decimal,
)


class ClosedLoopAllowlistError(TreasuryError):
    """Raised when a transfer targets a venue or address outside the closed loop."""


class ClosedLoopRiskBlockedError(TreasuryError):
    """Raised when GlobalRiskGuardian blocks a transfer."""


class ClosedLoopFeeLimitError(TreasuryError):
    """Raised when estimated transfer fee exceeds max_transfer_fee_pct."""


class ClosedLoopConcentrationError(TreasuryError):
    """Raised when a transfer would exceed max_venue_concentration_pct."""


class ClosedLoopVenue(str, Enum):
    HUB_BINANCE_MASTER = "hub_binance_master"
    CEX_BINANCE = "cex_binance"
    CEX_OKX = "cex_okx"
    CEX_BYBIT = "cex_bybit"
    DEX_SOLANA = "dex_solana"


class ClosedLoopTransferState(str, Enum):
    REQUESTED = "REQUESTED"
    RISK_CHECKED = "RISK_CHECKED"
    SUBMITTED = "SUBMITTED"
    CONFIRMING = "CONFIRMING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"


DEFAULT_ALLOWLIST_ADDRESSES: dict[ClosedLoopVenue, str] = {
    ClosedLoopVenue.HUB_BINANCE_MASTER: "loop://binance/master-hub",
    ClosedLoopVenue.CEX_BINANCE: "loop://binance/trading-sub",
    ClosedLoopVenue.CEX_OKX: "loop://okx/trading-sub",
    ClosedLoopVenue.CEX_BYBIT: "loop://bybit/trading-sub",
    ClosedLoopVenue.DEX_SOLANA: "loop://solana/treasury-dex-wallet",
}

DEFAULT_FEE_BPS: dict[tuple[ClosedLoopVenue, ClosedLoopVenue], Decimal] = {
    (ClosedLoopVenue.HUB_BINANCE_MASTER, ClosedLoopVenue.CEX_BINANCE): Decimal("0"),
    (ClosedLoopVenue.CEX_BINANCE, ClosedLoopVenue.HUB_BINANCE_MASTER): Decimal("0"),
    (ClosedLoopVenue.HUB_BINANCE_MASTER, ClosedLoopVenue.DEX_SOLANA): Decimal("5"),
    (ClosedLoopVenue.DEX_SOLANA, ClosedLoopVenue.HUB_BINANCE_MASTER): Decimal("5"),
}


class ClosedLoopTransferRecord(BaseModel):
    """Immutable audit record of a simulated closed-loop capital transfer."""

    transfer_id: str = Field(default_factory=lambda: f"clt_{uuid4().hex[:12]}")
    idempotency_key: str
    source_venue: ClosedLoopVenue
    destination_venue: ClosedLoopVenue
    destination_address: str
    asset: str = "USDT"
    amount_usd: Decimal
    fee_usd: Decimal
    net_amount_usd: Decimal
    state: ClosedLoopTransferState
    reason: str
    requested_by: str = "unified_ai_brain"
    mode: str = "PAPER_SIMULATION_ONLY"
    created_at_utc: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    settled_at_utc: str | None = None


class ClosedLoopAssetRouter:
    """Deterministic closed-loop router enforcing allowlists, fee caps, concentration caps, and equity conservation."""

    def __init__(
        self,
        initial_balances_usd: dict[ClosedLoopVenue, Decimal | int | float | str] | None = None,
        risk_guardian: GlobalRiskGuardian | None = None,
        allowlist: dict[ClosedLoopVenue, str] | None = None,
        max_transfer_fee_pct: Decimal = Decimal("0.0025"),
        max_venue_concentration_pct: Decimal = Decimal("0.35"),
        default_fee_bps: Decimal = Decimal("4"),
    ) -> None:
        self.risk_guardian = risk_guardian
        self.allowlist: dict[ClosedLoopVenue, str] = dict(allowlist or DEFAULT_ALLOWLIST_ADDRESSES)
        self.max_transfer_fee_pct = to_decimal(max_transfer_fee_pct, Decimal("0.0001"))
        self.max_venue_concentration_pct = to_decimal(max_venue_concentration_pct, Decimal("0.0001"))
        self.default_fee_bps = default_fee_bps

        defaults: dict[ClosedLoopVenue, Decimal] = {
            ClosedLoopVenue.HUB_BINANCE_MASTER: Decimal("12000.00"),
            ClosedLoopVenue.CEX_BINANCE: Decimal("4000.00"),
            ClosedLoopVenue.CEX_OKX: Decimal("5500.00"),
            ClosedLoopVenue.CEX_BYBIT: Decimal("4500.00"),
            ClosedLoopVenue.DEX_SOLANA: Decimal("1500.00"),
        }
        if initial_balances_usd is not None:
            for venue in ClosedLoopVenue:
                defaults[venue] = to_decimal(initial_balances_usd.get(venue, Decimal("0")), QUANT_2)

        self._balances: dict[ClosedLoopVenue, Decimal] = defaults
        self._in_flight_usd: Decimal = Decimal("0.00")
        self._cumulative_fees_usd: Decimal = Decimal("0.00")
        self._initial_equity_usd: Decimal = self._sum_venue_balances()
        self._records: list[ClosedLoopTransferRecord] = []
        self._by_idempotency: dict[str, ClosedLoopTransferRecord] = {}

    def _sum_venue_balances(self) -> Decimal:
        total = Decimal("0.00")
        for bal in self._balances.values():
            total += bal
        return to_decimal(total, QUANT_2)

    def _estimate_fee(self, source: ClosedLoopVenue, destination: ClosedLoopVenue, amount_usd: Decimal) -> Decimal:
        bps = DEFAULT_FEE_BPS.get((source, destination), self.default_fee_bps)
        return to_decimal(amount_usd * bps / Decimal("10000"), QUANT_2)

    def check_equity_invariant(self) -> dict[str, Any]:
        """Verify Total Equity == Hub + Sum(CEX) + Sum(DEX) + InFlight + CumulativeFees (conservation)."""
        current_balances = self._sum_venue_balances()
        accounted_total = to_decimal(current_balances + self._in_flight_usd + self._cumulative_fees_usd, QUANT_2)
        drift = to_decimal(accounted_total - self._initial_equity_usd, QUANT_2)
        ok = abs(drift) <= Decimal("0.01")
        if not ok:
            raise TreasuryInvariantError(
                f"Closed-loop equity conservation failed: expected={self._initial_equity_usd}, actual={accounted_total}"
            )
        return {
            "conservation_ok": ok,
            "initial_equity_usd": str(self._initial_equity_usd),
            "current_venue_total_usd": str(current_balances),
            "in_flight_usd": str(self._in_flight_usd),
            "cumulative_fees_usd": str(self._cumulative_fees_usd),
            "drift_usd": str(drift),
        }

    def simulate_transfer(
        self,
        source_venue: ClosedLoopVenue | str,
        destination_venue: ClosedLoopVenue | str,
        amount_usd: Decimal | int | float | str,
        idempotency_key: str,
        reason: str = "agent_capital_rebalance",
        destination_address: str | None = None,
        requested_by: str = "unified_ai_brain",
        live_mode: bool = False,
    ) -> ClosedLoopTransferRecord:
        """Simulate an idempotent closed-loop transfer in PaperSimulationTransport."""
        if live_mode or not LIVE_EXECUTION_PERMANENTLY_DISABLED:
            raise LiveTransferBlockedError("Live transfers are permanently disabled in Paper Mode.")

        try:
            src = ClosedLoopVenue(source_venue)
            dst = ClosedLoopVenue(destination_venue)
        except ValueError as exc:
            raise ClosedLoopAllowlistError(f"Unknown or non-allowlisted venue: {exc}") from exc

        if src == dst:
            raise ClosedLoopAllowlistError("Source and destination venues must differ.")

        amt = to_decimal(amount_usd, QUANT_2)
        if amt <= Decimal("0.00"):
            raise TreasuryError("Transfer amount must be positive.")

        expected_addr = self.allowlist.get(dst)
        if not expected_addr:
            raise ClosedLoopAllowlistError(f"Destination venue {dst.value} has no allowlisted address.")
        resolved_addr = destination_address or expected_addr
        if resolved_addr != expected_addr:
            raise ClosedLoopAllowlistError(
                f"Destination address '{resolved_addr}' is not in the closed-loop allowlist for {dst.value}."
            )

        existing = self._by_idempotency.get(idempotency_key)
        if existing is not None:
            if (
                existing.source_venue != src
                or existing.destination_venue != dst
                or existing.amount_usd != amt
                or existing.destination_address != resolved_addr
            ):
                raise DuplicateIdempotencyError(
                    f"Idempotency key '{idempotency_key}' already used with different transfer parameters."
                )
            return existing

        if self.risk_guardian is not None:
            if self.risk_guardian.emergency_stop_active:
                raise ClosedLoopRiskBlockedError(
                    f"Blocked by GlobalRiskGuardian emergency stop: {self.risk_guardian.emergency_stop_reason}"
                )
            if self.risk_guardian.circuit_breaker_tripped:
                raise ClosedLoopRiskBlockedError(
                    f"Blocked by GlobalRiskGuardian circuit breaker: {self.risk_guardian.circuit_breaker_reason}"
                )

        if self._balances[src] < amt:
            raise InsufficientFundsError(
                f"Insufficient balance on {src.value}: available={self._balances[src]}, requested={amt}"
            )

        fee = self._estimate_fee(src, dst, amt)
        fee_ratio = fee / amt
        if fee_ratio > self.max_transfer_fee_pct:
            raise ClosedLoopFeeLimitError(
                f"Estimated fee ratio {fee_ratio:.6f} exceeds max_transfer_fee_pct {self.max_transfer_fee_pct}"
            )

        # Enforce concentration cap on non-Hub destinations
        if dst != ClosedLoopVenue.HUB_BINANCE_MASTER:
            total_equity = self._sum_venue_balances()
            projected_dst = self._balances[dst] + (amt - fee)
            if total_equity > Decimal("0.00") and (projected_dst / total_equity) > self.max_venue_concentration_pct:
                raise ClosedLoopConcentrationError(
                    f"Projected {dst.value} balance {projected_dst} exceeds {self.max_venue_concentration_pct:.0%} cap."
                )

        net_amt = to_decimal(amt - fee, QUANT_2)
        self._balances[src] = to_decimal(self._balances[src] - amt, QUANT_2)
        self._balances[dst] = to_decimal(self._balances[dst] + net_amt, QUANT_2)
        self._cumulative_fees_usd = to_decimal(self._cumulative_fees_usd + fee, QUANT_2)

        record = ClosedLoopTransferRecord(
            idempotency_key=idempotency_key,
            source_venue=src,
            destination_venue=dst,
            destination_address=resolved_addr,
            amount_usd=amt,
            fee_usd=fee,
            net_amount_usd=net_amt,
            state=ClosedLoopTransferState.SETTLED,
            reason=reason,
            requested_by=requested_by,
            settled_at_utc=datetime.now(timezone.utc).isoformat(),
        )
        self._records.insert(0, record)
        self._by_idempotency[idempotency_key] = record
        self.check_equity_invariant()
        return record

    def auto_sweep_idle_capital(
        self,
        venue: ClosedLoopVenue | str,
        target_usd: Decimal | int | float | str,
        hysteresis_usd: Decimal | int | float | str = Decimal("50.00"),
        idempotency_prefix: str = "auto_sweep",
    ) -> ClosedLoopTransferRecord | None:
        """Sweep excess idle capital above target + hysteresis back to HUB_BINANCE_MASTER."""
        src = ClosedLoopVenue(venue)
        if src == ClosedLoopVenue.HUB_BINANCE_MASTER:
            return None
        target = to_decimal(target_usd, QUANT_2)
        hyst = to_decimal(hysteresis_usd, QUANT_2)
        excess = to_decimal(self._balances[src] - target, QUANT_2)
        if excess <= hyst:
            return None
        key = f"{idempotency_prefix}:{src.value}:{len(self._records)}:{excess}"
        return self.simulate_transfer(
            source_venue=src,
            destination_venue=ClosedLoopVenue.HUB_BINANCE_MASTER,
            amount_usd=excess,
            idempotency_key=key,
            reason="auto_sweep_idle_capital",
            requested_by="treasury_auto_sweep",
        )

    def summary(self) -> dict[str, Any]:
        invariant = self.check_equity_invariant()
        risk_state = {
            "emergency_stop_active": bool(self.risk_guardian.emergency_stop_active) if self.risk_guardian else False,
            "circuit_breaker_tripped": bool(self.risk_guardian.circuit_breaker_tripped) if self.risk_guardian else False,
        }
        return {
            "mode": "PAPER_SIMULATION_ONLY",
            "live_execution_enabled": False,
            "active_cex_venues": [
                ClosedLoopVenue.CEX_BINANCE.value,
                ClosedLoopVenue.CEX_OKX.value,
                ClosedLoopVenue.CEX_BYBIT.value,
            ],
            "active_dex_venues": [ClosedLoopVenue.DEX_SOLANA.value],
            "hub_venue": ClosedLoopVenue.HUB_BINANCE_MASTER.value,
            "balances_usd": {v.value: str(b) for v, b in self._balances.items()},
            "allowlist": {v.value: addr for v, addr in self.allowlist.items()},
            "invariant": invariant,
            "risk_gate": risk_state,
            "transfer_count": len(self._records),
            "transfers": [r.model_dump(mode="json") for r in self._records[:50]],
        }
