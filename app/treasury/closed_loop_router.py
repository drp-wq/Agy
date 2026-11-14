"""Closed-loop paper asset router across the Unified Hub, 3 CEXs, and Solana DEX."""

from __future__ import annotations

import json
import sqlite3
import threading
import copy
from contextlib import contextmanager
from functools import wraps
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
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


def atomic_router(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self.transaction():
            return method(self, *args, **kwargs)
    return wrapped


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
        state_path: Path | str | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._connection = None
        self.risk_guardian = risk_guardian
        self.allowlist: dict[ClosedLoopVenue, str] = dict(allowlist or DEFAULT_ALLOWLIST_ADDRESSES)
        self.max_transfer_fee_pct = to_decimal(max_transfer_fee_pct, Decimal("0.0001"))
        self.max_venue_concentration_pct = to_decimal(max_venue_concentration_pct, Decimal("0.0001"))
        self.default_fee_bps = default_fee_bps
        self.state_path: Path | None = Path(state_path) if state_path is not None else None

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
        if self.state_path is not None:
            self._init_db()
            with self.transaction():
                self._save_state()

    def _init_db(self) -> None:
        if self.state_path is None:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        from app.persistence.database import PaperDatabase
        database = PaperDatabase(self.state_path)
        database.close()
        with sqlite3.connect(self.state_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS closed_loop_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    payload_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                )
                """
            )

    def _load_state(self) -> None:
        if self.state_path is None or not self.state_path.exists():
            return
        row = self._connection.execute("SELECT payload_json FROM closed_loop_state WHERE id = 1").fetchone()
        if not row:
            return
        data = json.loads(row[0])
        self._balances = {
            ClosedLoopVenue(k): to_decimal(v, QUANT_2) for k, v in data["balances"].items()
        }
        self._in_flight_usd = to_decimal(data.get("in_flight_usd", "0.00"), QUANT_2)
        self._cumulative_fees_usd = to_decimal(data.get("cumulative_fees_usd", "0.00"), QUANT_2)
        self._initial_equity_usd = to_decimal(data["initial_equity_usd"], QUANT_2)
        self._records = [ClosedLoopTransferRecord.model_validate(r) for r in data.get("records", [])]
        self._by_idempotency = {r.idempotency_key: r for r in self._records}

    def _save_state(self) -> None:
        if self.state_path is None:
            return
        payload = {
            "balances": {v.value: str(b) for v, b in self._balances.items()},
            "in_flight_usd": str(self._in_flight_usd),
            "cumulative_fees_usd": str(self._cumulative_fees_usd),
            "initial_equity_usd": str(self._initial_equity_usd),
            "records": [r.model_dump(mode="json") for r in self._records],
        }
        with self._connection_context() as conn:
            self._write_journal(conn)
            self.reconcile()
            conn.execute(
                """
                INSERT INTO closed_loop_state (id, payload_json, updated_at_utc)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (json.dumps(payload), datetime.now(timezone.utc).isoformat()),
            )

    @contextmanager
    def _connection_context(self):
        if self._connection is None:
            raise TreasuryInvariantError("Router writes require an atomic transaction")
        yield self._connection

    @contextmanager
    def transaction(self):
        """Serialize writers, refresh state, and commit journal, balances and cycles together."""
        with self._lock:
            if self._connection is not None or self.state_path is None:
                snapshot = copy.deepcopy((self._balances, self._cumulative_fees_usd,
                                          self._records, self._by_idempotency))
                conn = self._connection
                savepoint = f"router_{uuid4().hex}"
                if conn is not None:
                    conn.execute(f"SAVEPOINT {savepoint}")
                try:
                    yield
                    if conn is not None:
                        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                except BaseException:
                    if conn is not None:
                        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
                    (self._balances, self._cumulative_fees_usd,
                     self._records, self._by_idempotency) = snapshot
                    raise
                return
            conn = sqlite3.connect(self.state_path)
            self._connection = conn
            try:
                conn.execute("BEGIN IMMEDIATE")
                self._load_state()
                self.check_equity_invariant()
                self.reconcile()
                yield
                conn.commit()
            except BaseException:
                conn.rollback()
                self._load_state()
                raise
            finally:
                self._connection = None
                conn.close()

    def _write_journal(self, conn):
        from app.treasury.ledger import post_closed_loop_journal
        post_closed_loop_journal(conn, self)

    def reconcile(self):
        """Compare every router USD account against the shared double-entry journal."""
        if self._connection is None:
            if self.state_path is None:
                return {"balanced": True}
            with self.transaction():
                return self.reconcile()
        rows = self._connection.execute(
            "SELECT account_code, debit, credit FROM treasury_journal_lines "
            "WHERE account_code LIKE 'closed_loop:%'"
        ).fetchall()
        if not rows:
            if self._records:
                # Legacy snapshots must be migrated explicitly; never invent historical evidence.
                raise TreasuryInvariantError("Legacy router history has no double-entry journal")
            return {"balanced": True}
        totals = {}
        for account, debit, credit in rows:
            totals[account] = totals.get(account, Decimal(0)) + Decimal(debit) - Decimal(credit)
        expected = {f"closed_loop:{v.value}": b for v, b in self._balances.items()}
        expected["closed_loop:fees"] = self._cumulative_fees_usd
        expected["closed_loop:equity"] = -self._initial_equity_usd
        if any(totals.get(k, Decimal(0)) != v for k, v in expected.items()) or set(totals) - set(expected):
            raise TreasuryInvariantError("Router balances do not reconcile to double-entry journal")
        return {"balanced": True}

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

    @atomic_router
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
        self._save_state()
        return record

    @atomic_router
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
