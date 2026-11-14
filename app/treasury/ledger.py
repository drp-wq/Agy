"""Unified 18-agent paper treasury ledger, double-entry journal, FX valuation, and reconciliation."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from app.core.config import UnifiedTreasuryConfig
from app.core.models import ExchangeId
from app.core.security import LIVE_EXECUTION_PERMANENTLY_DISABLED, assert_paper_only
from app.persistence.database import PaperDatabase
from app.treasury.models import (
    AgentSlotConfig,
    AgentSlotStatus,
    AttributionCategory,
    AttributionRecord,
    DuplicateIdempotencyError,
    FxRateSnapshot,
    InsufficientFundsError,
    InvalidTransferTransitionError,
    JournalEntry,
    JournalEntryLine,
    LedgerScope,
    LiveTransferBlockedError,
    MasterPoolState,
    NON_TRADING_PROFIT_CATEGORIES,
    QUANT_2,
    QUANT_8,
    TransferLifecycleState,
    TransferRequestRecord,
    TreasuryError,
    TreasuryInvariantError,
    VALID_TRANSFER_TRANSITIONS,
    to_decimal,
    utc_now,
)


class UnifiedTreasuryLedger:
    """Single-master-pool, 18-slot paper treasury with append-only double-entry accounting."""

    def __init__(self, db: PaperDatabase, config: UnifiedTreasuryConfig | None = None) -> None:
        assert_paper_only("paper")
        if not LIVE_EXECUTION_PERMANENTLY_DISABLED:
            raise LiveTransferBlockedError("Live execution must be permanently disabled.")
        self.db = db
        self.config = config or UnifiedTreasuryConfig()
        self._ensure_initialized()

    def _ensure_initialized(self) -> None:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM treasury_master_pool WHERE pool_id = 'master_pool'"
            ).fetchone()
            if row is None:
                now = utc_now().isoformat()
                initial = to_decimal(self.config.initial_master_capital)
                asset = self.config.master_pool_currency.upper()
                fx_rate = to_decimal(self.config.default_usdt_usd_rate)
                conn.execute(
                    """
                    INSERT INTO treasury_master_pool (
                        pool_id, asset, total_capital, free_capital, reserved_capital,
                        ledger_scope, is_paper, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (
                        "master_pool",
                        asset,
                        str(initial),
                        str(initial),
                        str(to_decimal(0)),
                        LedgerScope.PAPER_SIMULATED.value,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO treasury_fx_rates (
                        snapshot_id, base_asset, quote_asset, rate, source, as_of
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"fx_{uuid4().hex[:12]}",
                        "USDT",
                        "USD",
                        str(fx_rate),
                        "config_default_paper_feed",
                        now,
                    ),
                )
                if initial > Decimal("0"):
                    entry_id = f"jrn_{uuid4().hex[:12]}"
                    usd_val = self._compute_usd_value(initial, asset, fx_rate)
                    conn.execute(
                        """
                        INSERT INTO treasury_journal_entries (
                            entry_id, idempotency_key, event_type, reference_id, ledger_scope,
                            is_paper, fx_rate_to_usd, fx_timestamp, metadata_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                        """,
                        (
                            entry_id,
                            "init_master_pool_seed",
                            "MASTER_POOL_INIT",
                            "master_pool",
                            LedgerScope.PAPER_SIMULATED.value,
                            str(fx_rate if asset == "USDT" else to_decimal(1)),
                            now,
                            json.dumps({"note": "Initial paper master pool capital"}),
                            now,
                        ),
                    )
                    lines = [
                        ("master_pool:free", asset, str(initial), str(to_decimal(0)), str(usd_val)),
                        ("external:initial_paper_equity", asset, str(to_decimal(0)), str(initial), str(usd_val)),
                    ]
                    for acct, l_asset, debit, credit, u_val in lines:
                        conn.execute(
                            """
                            INSERT INTO treasury_journal_lines (
                                entry_id, account_code, asset, debit, credit, usd_value
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (entry_id, acct, l_asset, debit, credit, u_val),
                        )

    # ------------------------------------------------------------------
    # FX Valuation (explicit USD vs USDT distinction)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_usd_value(amount: Decimal, asset: str, usdt_usd_rate: Decimal) -> Decimal:
        upper = asset.strip().upper()
        if upper == "USD":
            return to_decimal(amount)
        if upper == "USDT":
            return to_decimal(amount * usdt_usd_rate)
        raise TreasuryError(f"Unsupported treasury valuation asset '{asset}'; only USD and USDT are supported.")

    def record_fx_rate(
        self,
        base_asset: str,
        quote_asset: str,
        rate: Decimal | float | str,
        source: str = "paper_fx_feed",
        as_of: datetime | None = None,
    ) -> FxRateSnapshot:
        snap = FxRateSnapshot(
            base_asset=base_asset,
            quote_asset=quote_asset,
            rate=to_decimal(rate),
            source=source,
            as_of=as_of or utc_now(),
        )
        with self.db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO treasury_fx_rates (snapshot_id, base_asset, quote_asset, rate, source, as_of)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    snap.snapshot_id,
                    snap.base_asset,
                    snap.quote_asset,
                    str(snap.rate),
                    snap.source,
                    snap.as_of.isoformat(),
                ),
            )
        return snap

    def get_latest_fx_rate(self, base_asset: str = "USDT", quote_asset: str = "USD") -> FxRateSnapshot:
        base = base_asset.strip().upper()
        quote = quote_asset.strip().upper()
        if base == quote:
            return FxRateSnapshot(base_asset=base, quote_asset=quote, rate=to_decimal(1), source="identity")
        with self.db._lock:
            row = self.db._conn.execute(
                """
                SELECT * FROM treasury_fx_rates
                WHERE base_asset = ? AND quote_asset = ?
                ORDER BY as_of DESC, rowid DESC LIMIT 1
                """,
                (base, quote),
            ).fetchone()
        if row is None:
            return FxRateSnapshot(
                base_asset=base,
                quote_asset=quote,
                rate=to_decimal(self.config.default_usdt_usd_rate),
                source="config_fallback",
            )
        return FxRateSnapshot(
            snapshot_id=row["snapshot_id"],
            base_asset=row["base_asset"],
            quote_asset=row["quote_asset"],
            rate=to_decimal(row["rate"]),
            source=row["source"],
            as_of=datetime.fromisoformat(row["as_of"]),
        )

    def value_in_usd(
        self,
        amount: Decimal | float | str,
        asset: str,
        fx_snapshot: FxRateSnapshot | None = None,
    ) -> tuple[Decimal, Decimal, datetime]:
        dec_amt = to_decimal(amount)
        upper = asset.strip().upper()
        if upper == "USD":
            return dec_amt, to_decimal(1), utc_now()
        snap = fx_snapshot or self.get_latest_fx_rate(upper, "USD")
        usd_val = self._compute_usd_value(dec_amt, upper, snap.rate)
        return usd_val, snap.rate, snap.as_of

    # ------------------------------------------------------------------
    # Master Pool & 18 Agent Slots
    # ------------------------------------------------------------------

    def get_master_pool(self) -> MasterPoolState:
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT * FROM treasury_master_pool WHERE pool_id = 'master_pool'"
            ).fetchone()
        if row is None:
            raise TreasuryInvariantError("Master pool missing from database.")
        return MasterPoolState(
            pool_id=row["pool_id"],
            asset=row["asset"],
            total_capital=to_decimal(row["total_capital"]),
            free_capital=to_decimal(row["free_capital"]),
            reserved_capital=to_decimal(row["reserved_capital"]),
            ledger_scope=LedgerScope(row["ledger_scope"]),
            is_paper=bool(row["is_paper"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def configure_agent_slot(self, slot: AgentSlotConfig) -> AgentSlotConfig:
        assert_paper_only("paper")
        if slot.slot_index < 1 or slot.slot_index > self.config.max_agent_slots:
            raise TreasuryError(f"slot_index must be between 1 and {self.config.max_agent_slots}.")
        now = utc_now()
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM treasury_agent_slots WHERE agent_id = ?",
                (slot.agent_id,),
            ).fetchone()
            reserved = to_decimal(existing["reserved_capital"]) if existing else to_decimal(slot.reserved_capital)
            available = to_decimal(existing["available_capital"]) if existing else to_decimal(slot.available_capital)
            trading = to_decimal(existing["trading_capital"]) if existing else to_decimal(slot.trading_capital)
            conn.execute(
                """
                INSERT INTO treasury_agent_slots (
                    agent_id, slot_index, name, strategy, exchange, account_id,
                    allowed_symbols_json, capital_budget, reserved_capital,
                    available_capital, trading_capital, max_risk_per_trade_pct,
                    daily_loss_limit_pct, max_drawdown_pct, max_leverage,
                    status, is_paper, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    slot_index = excluded.slot_index,
                    name = excluded.name,
                    strategy = excluded.strategy,
                    exchange = excluded.exchange,
                    account_id = excluded.account_id,
                    allowed_symbols_json = excluded.allowed_symbols_json,
                    capital_budget = excluded.capital_budget,
                    max_risk_per_trade_pct = excluded.max_risk_per_trade_pct,
                    daily_loss_limit_pct = excluded.daily_loss_limit_pct,
                    max_drawdown_pct = excluded.max_drawdown_pct,
                    max_leverage = excluded.max_leverage,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (
                    slot.agent_id,
                    slot.slot_index,
                    slot.name,
                    slot.strategy,
                    slot.exchange.value,
                    slot.account_id,
                    json.dumps(slot.allowed_symbols),
                    str(to_decimal(slot.capital_budget)),
                    str(reserved),
                    str(available),
                    str(trading),
                    str(to_decimal(slot.max_risk_per_trade_pct)),
                    str(to_decimal(slot.daily_loss_limit_pct)),
                    str(to_decimal(slot.max_drawdown_pct)),
                    str(to_decimal(slot.max_leverage)),
                    slot.status.value,
                    now.isoformat(),
                ),
            )
        return self.get_agent_slot(slot.agent_id)

    def initialize_default_slots(self, count: int = 18) -> list[AgentSlotConfig]:
        if count < 1 or count > self.config.max_agent_slots:
            raise TreasuryError(f"Slot count must be between 1 and {self.config.max_agent_slots}.")
        exchanges = (ExchangeId.BINANCE, ExchangeId.OKX, ExchangeId.BYBIT)
        strategies = ("trend_following", "breakout_retest", "mean_reversion")
        created: list[AgentSlotConfig] = []
        for idx in range(1, count + 1):
            agent_id = f"agent_{idx:02d}"
            existing = self._get_agent_slot_or_none(agent_id)
            if existing is not None:
                created.append(existing)
                continue
            ex = exchanges[(idx - 1) % len(exchanges)]
            strat = strategies[(idx - 1) % len(strategies)]
            slot = AgentSlotConfig(
                agent_id=agent_id,
                slot_index=idx,
                name=f"Agent {idx:02d} ({ex.value.upper()} {strat})",
                strategy=strat,
                exchange=ex,
                account_id=f"{ex.value}:paper_sub_{idx:02d}",
                allowed_symbols=["BTC/USDT", "ETH/USDT"],
                capital_budget=Decimal("0"),
                status=AgentSlotStatus.IDLE,
            )
            created.append(self.configure_agent_slot(slot))
        return created

    def _row_to_slot(self, row: Any) -> AgentSlotConfig:
        return AgentSlotConfig(
            agent_id=row["agent_id"],
            slot_index=int(row["slot_index"]),
            name=row["name"],
            strategy=row["strategy"],
            exchange=ExchangeId(row["exchange"]),
            account_id=row["account_id"],
            allowed_symbols=json.loads(row["allowed_symbols_json"]),
            capital_budget=to_decimal(row["capital_budget"]),
            reserved_capital=to_decimal(row["reserved_capital"]),
            available_capital=to_decimal(row["available_capital"]),
            trading_capital=to_decimal(row["trading_capital"]),
            max_risk_per_trade_pct=to_decimal(row["max_risk_per_trade_pct"]),
            daily_loss_limit_pct=to_decimal(row["daily_loss_limit_pct"]),
            max_drawdown_pct=to_decimal(row["max_drawdown_pct"]),
            max_leverage=to_decimal(row["max_leverage"]),
            status=AgentSlotStatus(row["status"]),
            is_paper=bool(row["is_paper"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _get_agent_slot_or_none(self, agent_id: str) -> AgentSlotConfig | None:
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT * FROM treasury_agent_slots WHERE agent_id = ?", (agent_id,)
            ).fetchone()
        return self._row_to_slot(row) if row is not None else None

    def get_agent_slot(self, agent_id: str) -> AgentSlotConfig:
        slot = self._get_agent_slot_or_none(agent_id)
        if slot is None:
            raise TreasuryError(f"Unknown agent slot '{agent_id}'.")
        return slot

    def list_agent_slots(self) -> list[AgentSlotConfig]:
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT * FROM treasury_agent_slots ORDER BY slot_index ASC"
            ).fetchall()
        return [self._row_to_slot(r) for r in rows]

    # ------------------------------------------------------------------
    # Double-Entry Append-Only Journal
    # ------------------------------------------------------------------

    def post_journal_entry(self, entry: JournalEntry) -> JournalEntry:
        assert_paper_only("paper")
        if not entry.is_paper:
            raise LiveTransferBlockedError("Only paper journal entries are permitted.")
        if not entry.lines:
            raise TreasuryInvariantError("Journal entry must contain at least one line.")

        by_asset_debits: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        by_asset_credits: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
        for line in entry.lines:
            asset = line.asset.strip().upper()
            by_asset_debits[asset] += to_decimal(line.debit)
            by_asset_credits[asset] += to_decimal(line.credit)
        for asset, deb_sum in by_asset_debits.items():
            if deb_sum != by_asset_credits[asset]:
                raise TreasuryInvariantError(
                    f"Double-entry imbalance for {asset}: debits={deb_sum} != credits={by_asset_credits[asset]}"
                )

        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM treasury_journal_entries WHERE idempotency_key = ?",
                (entry.idempotency_key,),
            ).fetchone()
            if existing is not None:
                if existing["reference_id"] != entry.reference_id or existing["event_type"] != entry.event_type:
                    raise DuplicateIdempotencyError(
                        f"Idempotency key '{entry.idempotency_key}' already used for different event."
                    )
                return self._load_journal_entry(conn, existing)

            conn.execute(
                """
                INSERT INTO treasury_journal_entries (
                    entry_id, idempotency_key, event_type, reference_id, ledger_scope,
                    is_paper, fx_rate_to_usd, fx_timestamp, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    entry.entry_id,
                    entry.idempotency_key,
                    entry.event_type,
                    entry.reference_id,
                    entry.ledger_scope.value,
                    str(to_decimal(entry.fx_rate_to_usd)),
                    entry.fx_timestamp.isoformat(),
                    json.dumps(entry.metadata),
                    entry.created_at.isoformat(),
                ),
            )
            for line in entry.lines:
                conn.execute(
                    """
                    INSERT INTO treasury_journal_lines (
                        entry_id, account_code, asset, debit, credit, usd_value
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry.entry_id,
                        line.account_code,
                        line.asset.strip().upper(),
                        str(to_decimal(line.debit)),
                        str(to_decimal(line.credit)),
                        str(to_decimal(line.usd_value)),
                    ),
                )
        return entry

    def _load_journal_entry(self, conn: Any, row: Any) -> JournalEntry:
        line_rows = conn.execute(
            "SELECT * FROM treasury_journal_lines WHERE entry_id = ? ORDER BY id ASC",
            (row["entry_id"],),
        ).fetchall()
        return JournalEntry(
            entry_id=row["entry_id"],
            idempotency_key=row["idempotency_key"],
            event_type=row["event_type"],
            reference_id=row["reference_id"],
            ledger_scope=LedgerScope(row["ledger_scope"]),
            is_paper=bool(row["is_paper"]),
            fx_rate_to_usd=to_decimal(row["fx_rate_to_usd"]),
            fx_timestamp=datetime.fromisoformat(row["fx_timestamp"]),
            lines=[
                JournalEntryLine(
                    account_code=lr["account_code"],
                    asset=lr["asset"],
                    debit=to_decimal(lr["debit"]),
                    credit=to_decimal(lr["credit"]),
                    usd_value=to_decimal(lr["usd_value"]),
                )
                for lr in line_rows
            ],
            metadata=json.loads(row["metadata_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_journal_entries(self, limit: int = 100) -> list[JournalEntry]:
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT * FROM treasury_journal_entries ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self._load_journal_entry(self.db._conn, r) for r in rows]

    # ------------------------------------------------------------------
    # Explicit Transfer Lifecycle (Single Master Pool -> Agent Virtual Allocation)
    # ------------------------------------------------------------------

    def _row_to_transfer(self, row: Any) -> TransferRequestRecord:
        return TransferRequestRecord(
            transfer_id=row["transfer_id"],
            idempotency_key=row["idempotency_key"],
            agent_id=row["agent_id"],
            source_account=row["source_account"],
            destination_account=row["destination_account"],
            asset=row["asset"],
            amount=to_decimal(row["amount"]),
            fee_amount=to_decimal(row["fee_amount"]),
            fx_rate_to_usd=to_decimal(row["fx_rate_to_usd"]),
            fx_timestamp=datetime.fromisoformat(row["fx_timestamp"]),
            state=TransferLifecycleState(row["state"]),
            ledger_scope=LedgerScope(row["ledger_scope"]),
            is_paper=bool(row["is_paper"]),
            ownership_verified=bool(row["ownership_verified"]),
            approved_by=row["approved_by"],
            readonly_evidence_ref=row["readonly_evidence_ref"],
            manual_instruction=row["manual_instruction"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def get_transfer(self, transfer_id: str) -> TransferRequestRecord:
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT * FROM treasury_transfers WHERE transfer_id = ?", (transfer_id,)
            ).fetchone()
        if row is None:
            raise TreasuryError(f"Unknown transfer '{transfer_id}'.")
        return self._row_to_transfer(row)

    def list_transfers(self, limit: int = 100) -> list[TransferRequestRecord]:
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT * FROM treasury_transfers ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_transfer(r) for r in rows]

    def reserve_capital(
        self,
        agent_id: str,
        amount: Decimal | float | str,
        idempotency_key: str,
        asset: str | None = None,
        transfer_id: str | None = None,
        fx_snapshot: FxRateSnapshot | None = None,
    ) -> TransferRequestRecord:
        """Reserve virtual allocation from the single master pool for an agent slot."""
        assert_paper_only("paper")
        dec_amt = to_decimal(amount)
        if dec_amt <= Decimal("0"):
            raise InsufficientFundsError("Reservation amount must be positive.")

        slot = self.get_agent_slot(agent_id)
        pool = self.get_master_pool()
        trf_asset = (asset or pool.asset).strip().upper()
        if trf_asset != pool.asset:
            raise TreasuryError(f"Master pool asset is {pool.asset}, cannot reserve in {trf_asset}.")

        usd_val, fx_rate, fx_ts = self.value_in_usd(dec_amt, trf_asset, fx_snapshot)
        now = utc_now()
        tid = transfer_id or f"trf_{uuid4().hex[:12]}"

        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM treasury_transfers WHERE idempotency_key = ? OR transfer_id = ?",
                (idempotency_key, tid),
            ).fetchone()
            if existing is not None:
                if existing["agent_id"] != agent_id or to_decimal(existing["amount"]) != dec_amt:
                    raise DuplicateIdempotencyError(
                        f"Duplicate transfer/idempotency key '{idempotency_key}' with mismatched parameters."
                    )
                return self._row_to_transfer(existing)

            pool_row = conn.execute(
                "SELECT * FROM treasury_master_pool WHERE pool_id = 'master_pool'"
            ).fetchone()
            free_cap = to_decimal(pool_row["free_capital"])
            res_cap = to_decimal(pool_row["reserved_capital"])
            if dec_amt > free_cap:
                raise InsufficientFundsError(
                    f"Insufficient free capital in master pool: requested={dec_amt}, available={free_cap}."
                )

            new_free = to_decimal(free_cap - dec_amt)
            new_res = to_decimal(res_cap + dec_amt)
            conn.execute(
                """
                UPDATE treasury_master_pool
                SET free_capital = ?, reserved_capital = ?, updated_at = ?
                WHERE pool_id = 'master_pool'
                """,
                (str(new_free), str(new_res), now.isoformat()),
            )
            new_slot_res = to_decimal(slot.reserved_capital + dec_amt)
            conn.execute(
                """
                UPDATE treasury_agent_slots
                SET reserved_capital = ?, updated_at = ?
                WHERE agent_id = ?
                """,
                (str(new_slot_res), now.isoformat(), agent_id),
            )
            conn.execute(
                """
                INSERT INTO treasury_transfers (
                    transfer_id, idempotency_key, agent_id, source_account, destination_account,
                    asset, amount, fee_amount, fx_rate_to_usd, fx_timestamp, state,
                    ledger_scope, is_paper, ownership_verified, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, '0', ?, ?, ?, ?, 1, 0, ?, ?)
                """,
                (
                    tid,
                    idempotency_key,
                    agent_id,
                    "master_pool:unallocated",
                    slot.account_id,
                    trf_asset,
                    str(dec_amt),
                    str(fx_rate),
                    fx_ts.isoformat(),
                    TransferLifecycleState.RESERVED.value,
                    LedgerScope.PAPER_SIMULATED.value,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )

        self.post_journal_entry(
            JournalEntry(
                idempotency_key=f"jrn_{idempotency_key}_reserve",
                event_type="TREASURY_RESERVE",
                reference_id=tid,
                ledger_scope=LedgerScope.PAPER_SIMULATED,
                fx_rate_to_usd=fx_rate,
                fx_timestamp=fx_ts,
                lines=[
                    JournalEntryLine(
                        account_code=f"master_pool:reserved:{agent_id}",
                        asset=trf_asset,
                        debit=dec_amt,
                        usd_value=usd_val,
                    ),
                    JournalEntryLine(
                        account_code="master_pool:free",
                        asset=trf_asset,
                        credit=dec_amt,
                        usd_value=usd_val,
                    ),
                ],
                metadata={"agent_id": agent_id, "state": TransferLifecycleState.RESERVED.value},
            )
        )
        self.db.log_audit(
            "TREASURY_RESERVE",
            agent_id,
            {"transfer_id": tid, "amount": str(dec_amt), "asset": trf_asset, "is_paper": True},
        )
        return self.get_transfer(tid)

    def _transition_transfer(
        self,
        transfer_id: str,
        target_state: TransferLifecycleState,
        updates: dict[str, Any] | None = None,
    ) -> TransferRequestRecord:
        assert_paper_only("paper")
        now = utc_now().isoformat()
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM treasury_transfers WHERE transfer_id = ?", (transfer_id,)
            ).fetchone()
            if row is None:
                raise TreasuryError(f"Unknown transfer '{transfer_id}'.")
            current = TransferLifecycleState(row["state"])
            allowed = VALID_TRANSFER_TRANSITIONS.get(current, set())
            if target_state not in allowed:
                raise InvalidTransferTransitionError(
                    f"Invalid transfer transition {current.value} -> {target_state.value}."
                )
            fields = {"state": target_state.value, "updated_at": now}
            if updates:
                fields.update(updates)
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(
                f"UPDATE treasury_transfers SET {set_clause} WHERE transfer_id = ?",
                (*fields.values(), transfer_id),
            )
        return self.get_transfer(transfer_id)

    def propose_transfer(
        self,
        transfer_id: str,
        manual_instruction: str | None = None,
    ) -> TransferRequestRecord:
        trf = self.get_transfer(transfer_id)
        instruction = manual_instruction or (
            f"MANUAL PAPER INSTRUCTION: Prepare {trf.amount} {trf.asset} "
            f"for {trf.destination_account} (no exchange write API called)."
        )
        return self._transition_transfer(
            transfer_id,
            TransferLifecycleState.TRANSFER_PROPOSED,
            {"manual_instruction": instruction},
        )

    def approve_transfer(
        self,
        transfer_id: str,
        approved_by: str,
        ownership_verified: bool = True,
    ) -> TransferRequestRecord:
        if not approved_by or not approved_by.strip():
            raise TreasuryError("Explicit approver identity is required.")
        if self.config.require_ownership_verification and not ownership_verified:
            raise TreasuryError("Explicit account ownership verification is required before approval.")
        return self._transition_transfer(
            transfer_id,
            TransferLifecycleState.USER_APPROVED,
            {"approved_by": approved_by.strip(), "ownership_verified": 1 if ownership_verified else 0},
        )

    def mark_external_transfer_pending(self, transfer_id: str) -> TransferRequestRecord:
        trf = self.get_transfer(transfer_id)
        if not trf.approved_by or (self.config.require_ownership_verification and not trf.ownership_verified):
            raise TreasuryError("Cannot mark transfer pending without user approval and ownership verification.")
        return self._transition_transfer(transfer_id, TransferLifecycleState.EXTERNAL_TRANSFER_PENDING)

    def confirm_from_readonly_evidence(
        self,
        transfer_id: str,
        readonly_evidence_ref: str,
        fee_amount: Decimal | float | str = "0",
    ) -> TransferRequestRecord:
        if not readonly_evidence_ref or not readonly_evidence_ref.strip():
            raise TreasuryError("Read-only exchange evidence reference is required.")
        dec_fee = to_decimal(fee_amount)
        if dec_fee < Decimal("0"):
            raise TreasuryError("Transfer fee cannot be negative.")

        trf = self._transition_transfer(
            transfer_id,
            TransferLifecycleState.CONFIRMED_FROM_EXCHANGE_READONLY_EVIDENCE,
            {
                "readonly_evidence_ref": readonly_evidence_ref.strip(),
                "fee_amount": str(dec_fee),
            },
        )
        if dec_fee > Decimal("0"):
            slot = self.get_agent_slot(trf.agent_id)
            self.record_attribution(
                idempotency_key=f"att_trf_fee_{transfer_id}",
                agent_id=trf.agent_id,
                exchange=slot.exchange.value,
                account_id=slot.account_id,
                strategy=slot.strategy,
                symbol=f"{trf.asset}/USD",
                asset=trf.asset,
                category=AttributionCategory.TRANSFER_FEE,
                amount=-dec_fee,
            )
        return trf

    def release_to_agent_available(self, transfer_id: str) -> TransferRequestRecord:
        trf = self._transition_transfer(transfer_id, TransferLifecycleState.AGENT_AVAILABLE)
        now = utc_now()
        with self.db.transaction() as conn:
            slot = self.get_agent_slot(trf.agent_id)
            if slot.reserved_capital < trf.amount:
                raise TreasuryInvariantError("Slot reserved capital less than transfer amount.")
            conn.execute(
                """
                UPDATE treasury_agent_slots
                SET reserved_capital = ?, available_capital = ?, updated_at = ?
                WHERE agent_id = ?
                """,
                (
                    str(to_decimal(slot.reserved_capital - trf.amount)),
                    str(to_decimal(slot.available_capital + trf.amount)),
                    now.isoformat(),
                    trf.agent_id,
                ),
            )
        usd_val, fx_rate, fx_ts = self.value_in_usd(trf.amount, trf.asset)
        self.post_journal_entry(
            JournalEntry(
                idempotency_key=f"jrn_{trf.idempotency_key}_available",
                event_type="AGENT_AVAILABLE",
                reference_id=trf.transfer_id,
                ledger_scope=trf.ledger_scope,
                fx_rate_to_usd=fx_rate,
                fx_timestamp=fx_ts,
                lines=[
                    JournalEntryLine(
                        account_code=f"agent_available:{trf.agent_id}",
                        asset=trf.asset,
                        debit=trf.amount,
                        usd_value=usd_val,
                    ),
                    JournalEntryLine(
                        account_code=f"master_pool:reserved:{trf.agent_id}",
                        asset=trf.asset,
                        credit=trf.amount,
                        usd_value=usd_val,
                    ),
                ],
                metadata={"destination_account": trf.destination_account},
            )
        )
        return trf

    def allocate_to_paper_trading(self, transfer_id: str) -> TransferRequestRecord:
        trf = self._transition_transfer(transfer_id, TransferLifecycleState.PAPER_TRADING)
        now = utc_now()
        with self.db.transaction() as conn:
            slot = self.get_agent_slot(trf.agent_id)
            if slot.available_capital < trf.amount:
                raise TreasuryInvariantError("Slot available capital less than transfer amount.")
            conn.execute(
                """
                UPDATE treasury_agent_slots
                SET available_capital = ?, trading_capital = ?, updated_at = ?
                WHERE agent_id = ?
                """,
                (
                    str(to_decimal(slot.available_capital - trf.amount)),
                    str(to_decimal(slot.trading_capital + trf.amount)),
                    now.isoformat(),
                    trf.agent_id,
                ),
            )
        usd_val, fx_rate, fx_ts = self.value_in_usd(trf.amount, trf.asset)
        self.post_journal_entry(
            JournalEntry(
                idempotency_key=f"jrn_{trf.idempotency_key}_trading",
                event_type="PAPER_TRADING_ALLOCATED",
                reference_id=trf.transfer_id,
                ledger_scope=trf.ledger_scope,
                fx_rate_to_usd=fx_rate,
                fx_timestamp=fx_ts,
                lines=[
                    JournalEntryLine(
                        account_code=f"agent_trading:{trf.agent_id}",
                        asset=trf.asset,
                        debit=trf.amount,
                        usd_value=usd_val,
                    ),
                    JournalEntryLine(
                        account_code=f"agent_available:{trf.agent_id}",
                        asset=trf.asset,
                        credit=trf.amount,
                        usd_value=usd_val,
                    ),
                ],
                metadata={"destination_account": trf.destination_account},
            )
        )
        return trf

    def cancel_or_reject_transfer(
        self,
        transfer_id: str,
        reject: bool = False,
        reason: str = "Cancelled by operator",
    ) -> TransferRequestRecord:
        trf = self.get_transfer(transfer_id)
        target = TransferLifecycleState.REJECTED if reject else TransferLifecycleState.CANCELLED
        updated = self._transition_transfer(transfer_id, target, {"manual_instruction": reason})

        # Release reservation back to master_pool:free if capital was still reserved
        if trf.state in {
            TransferLifecycleState.RESERVED,
            TransferLifecycleState.TRANSFER_PROPOSED,
            TransferLifecycleState.USER_APPROVED,
            TransferLifecycleState.EXTERNAL_TRANSFER_PENDING,
            TransferLifecycleState.CONFIRMED_FROM_EXCHANGE_READONLY_EVIDENCE,
        }:
            now = utc_now()
            with self.db.transaction() as conn:
                pool = self.get_master_pool()
                slot = self.get_agent_slot(trf.agent_id)
                conn.execute(
                    """
                    UPDATE treasury_master_pool
                    SET free_capital = ?, reserved_capital = ?, updated_at = ?
                    WHERE pool_id = 'master_pool'
                    """,
                    (
                        str(to_decimal(pool.free_capital + trf.amount)),
                        str(to_decimal(pool.reserved_capital - trf.amount)),
                        now.isoformat(),
                    ),
                )
                conn.execute(
                    """
                    UPDATE treasury_agent_slots
                    SET reserved_capital = ?, updated_at = ?
                    WHERE agent_id = ?
                    """,
                    (str(to_decimal(slot.reserved_capital - trf.amount)), now.isoformat(), trf.agent_id),
                )
            usd_val, fx_rate, fx_ts = self.value_in_usd(trf.amount, trf.asset)
            self.post_journal_entry(
                JournalEntry(
                    idempotency_key=f"jrn_{trf.idempotency_key}_release",
                    event_type="TREASURY_RESERVE_RELEASED",
                    reference_id=trf.transfer_id,
                    ledger_scope=trf.ledger_scope,
                    fx_rate_to_usd=fx_rate,
                    fx_timestamp=fx_ts,
                    lines=[
                        JournalEntryLine(
                            account_code="master_pool:free",
                            asset=trf.asset,
                            debit=trf.amount,
                            usd_value=usd_val,
                        ),
                        JournalEntryLine(
                            account_code=f"master_pool:reserved:{trf.agent_id}",
                            asset=trf.asset,
                            credit=trf.amount,
                            usd_value=usd_val,
                        ),
                    ],
                    metadata={"reason": reason},
                )
            )
        return updated

    def simulate_paper_transfer_e2e(
        self,
        agent_id: str,
        amount: Decimal | float | str,
        idempotency_key: str,
        approved_by: str,
        readonly_evidence_ref: str = "paper_sim_evidence_ok",
    ) -> TransferRequestRecord:
        """Run the full lifecycle in paper_simulated scope without touching real exchanges."""
        trf = self.reserve_capital(agent_id=agent_id, amount=amount, idempotency_key=idempotency_key)
        if trf.state == TransferLifecycleState.PAPER_TRADING:
            return trf
        if trf.state == TransferLifecycleState.RESERVED:
            trf = self.propose_transfer(trf.transfer_id)
        if trf.state == TransferLifecycleState.TRANSFER_PROPOSED:
            trf = self.approve_transfer(trf.transfer_id, approved_by=approved_by, ownership_verified=True)
        if trf.state == TransferLifecycleState.USER_APPROVED:
            trf = self.mark_external_transfer_pending(trf.transfer_id)
        if trf.state == TransferLifecycleState.EXTERNAL_TRANSFER_PENDING:
            trf = self.confirm_from_readonly_evidence(trf.transfer_id, readonly_evidence_ref)
        if trf.state == TransferLifecycleState.CONFIRMED_FROM_EXCHANGE_READONLY_EVIDENCE:
            trf = self.release_to_agent_available(trf.transfer_id)
        if trf.state == TransferLifecycleState.AGENT_AVAILABLE:
            trf = self.allocate_to_paper_trading(trf.transfer_id)
        return trf

    # ------------------------------------------------------------------
    # External Deposits & Withdrawals (NOT Trading Profit)
    # ------------------------------------------------------------------

    def record_external_deposit(
        self,
        amount: Decimal | float | str,
        idempotency_key: str,
        asset: str = "USDT",
        source_label: str = "external:paper_deposit",
    ) -> MasterPoolState:
        assert_paper_only("paper")
        dec_amt = to_decimal(amount)
        if dec_amt <= Decimal("0"):
            raise TreasuryError("Deposit amount must be positive.")
        pool = self.get_master_pool()
        dep_asset = asset.strip().upper()
        if dep_asset != pool.asset:
            raise TreasuryError(f"Master pool asset is {pool.asset}, cannot deposit {dep_asset}.")
        usd_val, fx_rate, fx_ts = self.value_in_usd(dec_amt, dep_asset)

        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT 1 FROM treasury_journal_entries WHERE idempotency_key = ?",
                (f"jrn_{idempotency_key}",),
            ).fetchone()
            if existing is not None:
                return self.get_master_pool()

            now = utc_now().isoformat()
            conn.execute(
                """
                UPDATE treasury_master_pool
                SET total_capital = ?, free_capital = ?, updated_at = ?
                WHERE pool_id = 'master_pool'
                """,
                (
                    str(to_decimal(pool.total_capital + dec_amt)),
                    str(to_decimal(pool.free_capital + dec_amt)),
                    now,
                ),
            )

        self.post_journal_entry(
            JournalEntry(
                idempotency_key=f"jrn_{idempotency_key}",
                event_type="EXTERNAL_DEPOSIT",
                reference_id=idempotency_key,
                ledger_scope=LedgerScope.PAPER_SIMULATED,
                fx_rate_to_usd=fx_rate,
                fx_timestamp=fx_ts,
                lines=[
                    JournalEntryLine(account_code="master_pool:free", asset=dep_asset, debit=dec_amt, usd_value=usd_val),
                    JournalEntryLine(account_code=source_label, asset=dep_asset, credit=dec_amt, usd_value=usd_val),
                ],
            )
        )
        self.record_attribution(
            idempotency_key=f"att_{idempotency_key}",
            agent_id="treasury_master",
            exchange="treasury",
            account_id="master_pool",
            strategy="external_funding",
            symbol=f"{dep_asset}/USD",
            asset=dep_asset,
            category=AttributionCategory.EXTERNAL_DEPOSIT,
            amount=dec_amt,
        )
        return self.get_master_pool()

    def record_external_withdrawal(
        self,
        amount: Decimal | float | str,
        idempotency_key: str,
        asset: str = "USDT",
        dest_label: str = "external:paper_withdrawal",
    ) -> MasterPoolState:
        assert_paper_only("paper")
        dec_amt = to_decimal(amount)
        if dec_amt <= Decimal("0"):
            raise TreasuryError("Withdrawal amount must be positive.")
        pool = self.get_master_pool()
        w_asset = asset.strip().upper()
        if w_asset != pool.asset:
            raise TreasuryError(f"Master pool asset is {pool.asset}, cannot withdraw {w_asset}.")
        if dec_amt > pool.free_capital:
            raise InsufficientFundsError(
                f"Cannot withdraw {dec_amt} {w_asset}: only {pool.free_capital} free in master pool."
            )
        usd_val, fx_rate, fx_ts = self.value_in_usd(dec_amt, w_asset)

        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT 1 FROM treasury_journal_entries WHERE idempotency_key = ?",
                (f"jrn_{idempotency_key}",),
            ).fetchone()
            if existing is not None:
                return self.get_master_pool()

            now = utc_now().isoformat()
            conn.execute(
                """
                UPDATE treasury_master_pool
                SET total_capital = ?, free_capital = ?, updated_at = ?
                WHERE pool_id = 'master_pool'
                """,
                (
                    str(to_decimal(pool.total_capital - dec_amt)),
                    str(to_decimal(pool.free_capital - dec_amt)),
                    now,
                ),
            )

        self.post_journal_entry(
            JournalEntry(
                idempotency_key=f"jrn_{idempotency_key}",
                event_type="EXTERNAL_WITHDRAWAL",
                reference_id=idempotency_key,
                ledger_scope=LedgerScope.PAPER_SIMULATED,
                fx_rate_to_usd=fx_rate,
                fx_timestamp=fx_ts,
                lines=[
                    JournalEntryLine(account_code=dest_label, asset=w_asset, debit=dec_amt, usd_value=usd_val),
                    JournalEntryLine(account_code="master_pool:free", asset=w_asset, credit=dec_amt, usd_value=usd_val),
                ],
            )
        )
        self.record_attribution(
            idempotency_key=f"att_{idempotency_key}",
            agent_id="treasury_master",
            exchange="treasury",
            account_id="master_pool",
            strategy="external_funding",
            symbol=f"{w_asset}/USD",
            asset=w_asset,
            category=AttributionCategory.EXTERNAL_WITHDRAWAL,
            amount=-dec_amt,
        )
        return self.get_master_pool()

    # ------------------------------------------------------------------
    # PnL & Cost Attribution
    # ------------------------------------------------------------------

    def record_attribution(
        self,
        idempotency_key: str,
        agent_id: str,
        exchange: str,
        account_id: str,
        strategy: str,
        symbol: str,
        asset: str,
        category: AttributionCategory,
        amount: Decimal | float | str,
        fx_snapshot: FxRateSnapshot | None = None,
    ) -> AttributionRecord:
        assert_paper_only("paper")
        dec_amt = to_decimal(amount)
        usd_val, fx_rate, _ = self.value_in_usd(dec_amt, asset, fx_snapshot)
        is_trading_profit = category not in NON_TRADING_PROFIT_CATEGORIES
        rec = AttributionRecord(
            idempotency_key=idempotency_key,
            agent_id=agent_id,
            exchange=exchange,
            account_id=account_id,
            strategy=strategy,
            symbol=symbol,
            asset=asset.strip().upper(),
            event_category=category,
            amount=dec_amt,
            fx_rate_to_usd=fx_rate,
            usd_value=usd_val,
            is_trading_profit=is_trading_profit,
        )
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM treasury_attribution_events WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                return AttributionRecord(
                    attribution_id=existing["attribution_id"],
                    idempotency_key=existing["idempotency_key"],
                    agent_id=existing["agent_id"],
                    exchange=existing["exchange"],
                    account_id=existing["account_id"],
                    strategy=existing["strategy"],
                    symbol=existing["symbol"],
                    asset=existing["asset"],
                    event_category=AttributionCategory(existing["event_category"]),
                    amount=to_decimal(existing["amount"]),
                    fx_rate_to_usd=to_decimal(existing["fx_rate_to_usd"]),
                    usd_value=to_decimal(existing["usd_value"]),
                    is_trading_profit=bool(existing["is_trading_profit"]),
                    is_paper=bool(existing["is_paper"]),
                    created_at=datetime.fromisoformat(existing["created_at"]),
                )
            conn.execute(
                """
                INSERT INTO treasury_attribution_events (
                    attribution_id, idempotency_key, agent_id, exchange, account_id,
                    strategy, symbol, asset, event_category, amount, fx_rate_to_usd,
                    usd_value, is_trading_profit, is_paper, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    rec.attribution_id,
                    rec.idempotency_key,
                    rec.agent_id,
                    rec.exchange,
                    rec.account_id,
                    rec.strategy,
                    rec.symbol,
                    rec.asset,
                    rec.event_category.value,
                    str(rec.amount),
                    str(rec.fx_rate_to_usd),
                    str(rec.usd_value),
                    1 if rec.is_trading_profit else 0,
                    rec.created_at.isoformat(),
                ),
            )
        return rec

    def get_attribution_summary(self, group_by: str = "agent_id") -> dict[str, dict[str, str]]:
        allowed_dims = {"agent_id", "exchange", "account_id", "strategy", "symbol"}
        if group_by not in allowed_dims:
            raise TreasuryError(f"group_by must be one of {sorted(allowed_dims)}")

        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT * FROM treasury_attribution_events"
            ).fetchall()

        buckets: dict[str, dict[str, Decimal]] = defaultdict(
            lambda: {
                "realized_pnl_usd": Decimal("0"),
                "unrealized_pnl_usd": Decimal("0"),
                "trading_fees_usd": Decimal("0"),
                "funding_usd": Decimal("0"),
                "slippage_usd": Decimal("0"),
                "transfer_fees_usd": Decimal("0"),
                "external_deposits_usd": Decimal("0"),
                "external_withdrawals_usd": Decimal("0"),
                "net_trading_profit_usd": Decimal("0"),
            }
        )
        for r in rows:
            key = str(r[group_by])
            cat = AttributionCategory(r["event_category"])
            usd_val = to_decimal(r["usd_value"])
            b = buckets[key]
            if cat == AttributionCategory.TRADE_REALIZED_PNL:
                b["realized_pnl_usd"] += usd_val
            elif cat == AttributionCategory.TRADE_UNREALIZED_PNL:
                b["unrealized_pnl_usd"] += usd_val
            elif cat == AttributionCategory.TRADING_FEE:
                b["trading_fees_usd"] += usd_val
            elif cat == AttributionCategory.FUNDING:
                b["funding_usd"] += usd_val
            elif cat == AttributionCategory.SLIPPAGE:
                b["slippage_usd"] += usd_val
            elif cat == AttributionCategory.TRANSFER_FEE:
                b["transfer_fees_usd"] += usd_val
            elif cat == AttributionCategory.EXTERNAL_DEPOSIT:
                b["external_deposits_usd"] += usd_val
            elif cat == AttributionCategory.EXTERNAL_WITHDRAWAL:
                b["external_withdrawals_usd"] += usd_val

            if bool(r["is_trading_profit"]):
                b["net_trading_profit_usd"] += usd_val

        return {
            k: {metric: str(to_decimal(val, QUANT_2)) for metric, val in metrics.items()}
            for k, metrics in sorted(buckets.items())
        }

    # ------------------------------------------------------------------
    # Reconciliation & Overview Snapshot
    # ------------------------------------------------------------------

    def reconcile(self, readonly_snapshots: dict[str, Any] | None = None) -> dict[str, Any]:
        pool = self.get_master_pool()
        slots = self.list_agent_slots()
        entries = self.list_journal_entries(limit=10000)

        journal_imbalances: list[str] = []
        for e in entries:
            debits: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
            credits: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
            for line in e.lines:
                debits[line.asset] += line.debit
                credits[line.asset] += line.credit
            for asset in debits:
                if debits[asset] != credits[asset]:
                    journal_imbalances.append(f"{e.entry_id}:{asset}")

        slot_allocated_sum = sum(
            (s.reserved_capital + s.available_capital + s.trading_capital for s in slots),
            Decimal("0"),
        )
        pool_conservation_ok = (
            to_decimal(pool.free_capital + pool.reserved_capital) == pool.total_capital
            and to_decimal(slot_allocated_sum) == pool.reserved_capital
        )

        fx = self.get_latest_fx_rate("USDT", "USD")
        total_usd, _, _ = self.value_in_usd(pool.total_capital, pool.asset, fx)
        free_usd, _, _ = self.value_in_usd(pool.free_capital, pool.asset, fx)
        reserved_usd, _, _ = self.value_in_usd(pool.reserved_capital, pool.asset, fx)

        return {
            "balanced": len(journal_imbalances) == 0 and pool_conservation_ok,
            "journal_entries_checked": len(entries),
            "journal_imbalances": journal_imbalances,
            "pool_conservation_ok": pool_conservation_ok,
            "master_pool": {
                "asset": pool.asset,
                "total_capital": str(pool.total_capital),
                "free_capital": str(pool.free_capital),
                "reserved_capital": str(pool.reserved_capital),
                "slot_allocated_sum": str(to_decimal(slot_allocated_sum)),
            },
            "usd_valuation": {
                "fx_pair": f"{fx.base_asset}/{fx.quote_asset}",
                "fx_rate": str(fx.rate),
                "fx_timestamp": fx.as_of.isoformat(),
                "total_usd": str(to_decimal(total_usd, QUANT_2)),
                "free_usd": str(to_decimal(free_usd, QUANT_2)),
                "reserved_usd": str(to_decimal(reserved_usd, QUANT_2)),
            },
            "ledger_scope": LedgerScope.PAPER_SIMULATED.value,
            "exchange_readonly_balances": readonly_snapshots or {},
            "paper_separated_from_real": True,
            "fill_attribution_scope": "fees_and_recorded_closures",
            "pnl_evidence_scope": "recorded_only; historical gaps unresolved",
            "realized_pnl_complete": False,
            "note": "Historical closures without durable evidence and funding remain unreconciled.",
        }

    def overview_snapshot(self, readonly_snapshots: dict[str, Any] | None = None) -> dict[str, Any]:
        slots = self.list_agent_slots()
        reconciliation = self.reconcile(readonly_snapshots=readonly_snapshots)
        by_agent = self.get_attribution_summary("agent_id")
        return {
            "enabled": self.config.enabled,
            "mode": "PAPER_ONLY",
            "live_order_submission": False,
            "external_writes": False,
            "signing_enabled": False,
            "transfers_enabled": False,
            "custody_model": "single_master_pool_accounting_only",
            "max_agent_slots": self.config.max_agent_slots,
            "configured_slots": len(slots),
            "active_slots": sum(1 for s in slots if s.status == AgentSlotStatus.ACTIVE),
            "risk_limits": {
                "max_single_agent_concentration_pct": self.config.max_single_agent_concentration_pct,
                "max_aggregate_allocated_pct": self.config.max_aggregate_allocated_pct,
                "agent_daily_loss_cap_pct": self.config.agent_daily_loss_cap_pct,
                "rebalance_cooldown_sec": self.config.rebalance_cooldown_sec,
            },
            "slots": [s.model_dump(mode="json") for s in slots],
            "reconciliation": reconciliation,
            "attribution_by_agent": by_agent,
            "attribution_by_exchange": self.get_attribution_summary("exchange"),
            "attribution_by_strategy": self.get_attribution_summary("strategy"),
        }


def post_closed_loop_journal(conn: Any, router: Any) -> None:
    """Journal router USD custody in its own paper namespace, in the caller's transaction.

    These accounts never debit the strategy master pool or a campaign ledger.
    """
    def post(key: str, lines: list[tuple[str, Decimal]], event: str) -> None:
        existing = conn.execute(
            "SELECT 1 FROM treasury_journal_entries WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if existing:
            return
        if sum((amount for _, amount in lines), Decimal(0)) != 0:
            raise TreasuryInvariantError("Unbalanced closed-loop journal entry")
        now = utc_now().isoformat()
        entry_id = f"jrn_{uuid4().hex}"
        conn.execute(
            "INSERT INTO treasury_journal_entries VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
            (entry_id, key, event, key, LedgerScope.PAPER_SIMULATED.value, "1", now,
             json.dumps({"namespace": "closed_loop", "valuation": "USD"}), now),
        )
        for account, amount in lines:
            conn.execute(
                "INSERT INTO treasury_journal_lines "
                "(entry_id, account_code, asset, debit, credit, usd_value) VALUES (?, ?, 'USD', ?, ?, ?)",
                (entry_id, f"closed_loop:{account}", str(max(amount, Decimal(0))),
                 str(max(-amount, Decimal(0))), str(abs(amount))),
            )

    # Seed only before the first transfer. Existing snapshots with history fail closed.
    if not router._records:
        post("closed_loop:seed", [(v.value, b) for v, b in router._balances.items()] +
             [("equity", -router._initial_equity_usd)], "CLOSED_LOOP_PAPER_SEED")
    for record in reversed(router._records):
        post(f"closed_loop:transfer:{record.idempotency_key}", [
            (record.source_venue.value, -record.amount_usd),
            (record.destination_venue.value, record.net_amount_usd),
            ("fees", record.fee_usd),
        ], "CLOSED_LOOP_PAPER_TRANSFER")
