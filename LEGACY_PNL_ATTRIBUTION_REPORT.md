# Legacy PAPER PnL Attribution Backfill & Detection Report

**Branch:** `fix/legacy-pnl-attribution` (branched from production `HEAD` `17aa6e0861c766188dfb23bd329024f021327585`)  
**Worktree:** `/root/multi-exchange-paper-legacy-pnl`  
**Restricted Staging Dir:** `/root/legacy-pnl-attribution-staging-20261113` (`chmod 0700`)  
**Mode:** Staging-only validation; production source (`/root/multi-exchange-paper`), live database (`/root/multi-exchange-paper/data/paper_engine.sqlite3`), and systemd service were **not** modified or restarted.

---

## 1. Key Findings & State Discrepancy Notice

1. **Current `HEAD` (`17aa6e0`) Already Preserves Reversal Fix:**  
   `app/execution/paper_engine.py` (`_apply_fill_to_position_and_account`, lines 376–434, introduced in `a78a6f0`) and `tests/test_paper_reversal_orders.py` are intact on `17aa6e0` and preserved without modification.
2. **Operational Discrepancy Observed on Live Database (Read-Only Check):**  
   A read-only inspection and online backup (`live_snapshot_copy.sqlite3`, SHA-256 `525a197959b104660ff6db732fb717ee0d43923a7e18b02d9584b50dd6602142`) of `/root/multi-exchange-paper/data/paper_engine.sqlite3` revealed that an earlier remediation run at `2026-11-13T16:05:55.484141+00:00` (`audit_logs` id `3512`, `event_type='HISTORICAL_SLTP_EVIDENCE_RECONSTRUCTED'`, `actor='p0p1_remediation'`) **already executed** the untracked `/root/p0p1-staging-20261113/remediate_historical_sltp.py` script against the live DB (`107` fills, `35` closures, `0` missing fills/closures, `gap = -7.7E-16`). However, `HEAD` `17aa6e0` lacked any repository-tracked backfill code, missing-fill/closure detection in `audit_paper_pnl_coverage`, or tests for missing-evidence detection.
3. **Forensic Report Split Note:**  
   In `/root/p0p1-staging-20261113/pnl-forensics-report.md` (Section 1), the pre-repair attribution totals were written as `+5.244974` (`paper_close_pnl`) and `-10.484870` (`paper_fill_fee`), whereas the actual pre-repair database (`paper_engine_pre_repair_20261113T160555Z.sqlite3`, SHA-256 `7de26a074681e415564e052f1895da3ea39cdd09624994b44ef4c45aaae42f4d`) has `+5.554961` (`17` closures) and `-10.794857` (`89` fees). Both sum identically to `-5.239896 USD`, and the 18 missing legacy `sl_tp_exit` records match the exact forensic figures: **`5.484766 USD`** exit fees + **`-76.729666 USD`** gross realized PnL = **`-82.214432 USD`** gap.

---

## 2. Implemented Changes (`fix/legacy-pnl-attribution`)

- **`app/treasury/reconciliation_audit.py`:**
  - Added `detect_missing_paper_order_evidence(db)` to detect filled `paper_orders` missing `paper_fills` rows and filled `sl_tp_exit` orders missing `paper_realized_closures` rows.
  - Updated `audit_paper_pnl_coverage(db)` to expose `filled_orders`, `orders_missing_fills`, `sltp_orders_missing_closures`, and `orders_evidence_complete`, and to require `evidence["complete"]` in `recorded_evidence_coverage_ok` (while retaining `historical_completeness_certified: False`).
- **`app/treasury/legacy_pnl_backfill.py`:**
  - `replay_paper_orders(...)`: Chronologically replays all `status='filled' AND is_paper=1` `paper_orders` (`ORDER BY created_at ASC, order_id ASC`) per `(bot_id, exchange, symbol, market_type)` and enforces a fail-closed `1e-6` match against `paper_accounts` (`cash_usd`, `realized_pnl_usd`, `reserved_margin_usd`) and `paper_positions` (`direction`, `qty`, `entry_price`). Raises `LegacyReplayMismatchError` before any writes if any mismatch occurs.
  - `backfill_legacy_paper_pnl(...)`: Runs pre-flight `PRAGMA integrity_check` and `PRAGMA foreign_key_check`, replays orders, atomically inserts only missing `paper_fills` (`fill_backfill_<order_id>`, `timestamp = order.created_at`) and missing `paper_realized_closures` (`created_at = order.created_at`) plus an `audit_logs` provenance record (`HISTORICAL_SLTP_EVIDENCE_BACKFILLED`) when rows are inserted, idempotently attributes missing `paper_fill_fee:*` and `paper_close_pnl:*` events into `treasury_attribution_events`, and verifies post-backfill invariants (`paper_accounts`, `paper_orders`, `treasury_journal_entries`, `treasury_journal_lines` untouched; uniqueness & integrity checks pass).
- **`scripts/backfill_legacy_paper_pnl.py`:**
  - CLI entrypoint defaulting to `--dry-run` (writes require `--apply`) and refusing the live production DB path unless `--allow-production-db` is explicitly supplied.
- **`tests/test_legacy_pnl_backfill_and_detection.py`:**
  - Added 4 regression tests covering missing-fill/closure detection, multi-bot deterministic/idempotent backfill with invariant checks, fail-closed `LegacyReplayMismatchError` abort, and `PaperExecutionEngine` reversal-order compatibility.

---

## 3. Staging Verification & Before / After Figures

Validated against two isolated SQLite copies in `/root/legacy-pnl-attribution-staging-20261113`:
1. **`pre_repair_work.sqlite3`** (copied from `paper_engine_pre_repair_20261113T160555Z.sqlite3`, SHA-256 `7de26a074681e415564e052f1895da3ea39cdd09624994b44ef4c45aaae42f4d`):
   - **Dry-run:** `replayed_orders = 107`, `missing_fills_found = 18`, `missing_closures_found = 18`, `missing_fees_usd = 5.484766`, `missing_gross_usd = -76.729666`, `unexplained_usd = -82.21443200000000077`.
   - **Apply Run 1:** `inserted_fills = 18`, `inserted_closures = 18`, `inserted_fee_attributions = 18`, `inserted_closure_attributions = 18`, `invariants_unchanged = True`, `integrity.ok = True`, `orders_evidence_complete = True`, `recorded_evidence_coverage_ok = True`, `unexplained_usd = -7.7E-16`.
   - **Apply Run 2 (Idempotency):** `missing_fills_found = 0`, `missing_closures_found = 0`, `inserted_fills = 0`, `inserted_closures = 0`, `inserted_fee_attributions = 0`, `inserted_closure_attributions = 0`, `invariants_unchanged = True`, `audit_logs` backfill rows = `1`.
2. **`live_snapshot_work.sqlite3`** (copied from live DB via read-only SQLite `.backup()`):
   - **Dry-run & Apply Runs 1–2:** `replayed_orders = 107`, `missing_fills_found = 0`, `missing_closures_found = 0`, `inserted_fills = 0`, `inserted_closures = 0`, `inserted_fee_attributions = 0`, `inserted_closure_attributions = 0`, `recorded_evidence_coverage_ok = True`, `unexplained_usd = -7.7E-16`.

### Per-Bot Before / After Reconciliation (`pre_repair_copy.sqlite3` -> `pre_repair_work.sqlite3`)

| Bot ID | Exchange | DB `realized_pnl_usd` (Unchanged) | Attributed Before (Fills / Closures) | Gap Before | Attributed After (Fills / Closures) | Gap After |
|---|---|---:|---:|---:|---:|---:|
| `binance_perp_paper` | `binance` | `+0.207373` | `+0.932842` (`11` / `4`) | `-0.725469` | `+0.207373` (`12` / `5`) | `0.000000` |
| `binance_spot_paper` | `binance` | `+1.993243` | `+3.178283` (`14` / `4`) | `-1.185040` | `+1.993243` (`15` / `5`) | `0.000000` |
| `bybit_perp_funding` | `bybit` | `-30.196191` | `-2.082299` (`23` / `3`) | `-28.113892` | `-30.196191` (`26` / `6`) | `0.000000` |
| `bybit_spot_swing` | `bybit` | `-30.918538` | `-2.130127` (`17` / `1`) | `-28.788411` | `-30.918538` (`22` / `6`) | `0.000000` |
| `okx_perp_momentum` | `okx` | `-13.206554` | `-4.213245` (`10` / `2`) | `-8.993309` | `-13.206554` (`14` / `6`) | `0.000000` |
| `okx_spot_alpha` | `okx` | `-15.333661` | `-0.925350` (`14` / `3`) | `-14.408311` | `-15.333661` (`18` / `7`) | `0.000000` |
| **TOTAL** | — | **`-87.454328`** | **`-5.239896` (`89` / `17`)** | **`-82.214432`** | **`-87.454328` (`107` / `35`)** | **`0.000000`** (`-7.7E-16`) |

### SQL Evidence Summary

- `paper_orders`: `3516` total / `107` filled (`before == after`, unchanged).
- `paper_accounts`: `cash_usd`, `realized_pnl_usd`, `reserved_margin_usd` identical across all 6 bots (`before == after`).
- `treasury_journal_entries`: `1` (`before == after`); `treasury_journal_lines`: `2` (`before == after`).
- `paper_fills`: `89` (`10.794857` fee) -> `107` (`16.279623` fee); delta `+18` rows, `+5.484766 USD`.
- `paper_realized_closures`: `17` (`+5.554961`) -> `35` (`-71.174705`); delta `+18` rows, `-76.729666 USD`.
- `treasury_attribution_events`: `89` fee (`-10.794857`) + `17` close (`+5.554961`) -> `107` fee (`-16.279623`) + `35` close (`-71.174705`).
- `PRAGMA integrity_check = ok`, `PRAGMA foreign_key_check = []`, zero duplicate `fill_id`, `paper_realized_closures.order_id`, or `treasury_attribution_events.idempotency_key`.

---

## 4. Test Suite Verification

Executed inside `/root/multi-exchange-paper-legacy-pnl`:
```bash
PYTHONDONTWRITEBYTECODE=1 /root/multi-exchange-paper/.venv/bin/pytest -q -p no:cacheprovider
```
Result: **`86 passed, 2 warnings in 8.95s`** (including all existing tests, `test_paper_reversal_orders.py`, and the 4 new tests in `tests/test_legacy_pnl_backfill_and_detection.py`).

---

## 5. Deployment & Rollback Instructions (For Separate Review)

> **Note:** Because the live DB already received the 18 missing rows at `16:05:55Z` (`audit_logs` id `3512`), running the backfill script on production after merging `fix/legacy-pnl-attribution` will be an idempotent no-op (`0` rows inserted), while deploying the code adds `orders_missing_fills` / `sltp_orders_missing_closures` monitoring to `audit_paper_pnl_coverage` and commits the deterministic backfill tool + tests to the repository.

### Deployment Steps (When Approved)
1. Review and merge `fix/legacy-pnl-attribution` into `main` (fast-forward or merge commit after code review).
2. Run full test suite on the target checkout:
   ```bash
   PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest -q -p no:cacheprovider
   ```
3. Take an online SQLite backup before touching anything:
   ```bash
   python3 -c "
   import sqlite3
   src = sqlite3.connect('file:/root/multi-exchange-paper/data/paper_engine.sqlite3?mode=ro', uri=True)
   dst = sqlite3.connect('/root/legacy-pnl-attribution-staging-20261113/prod_pre_deploy_backup.sqlite3')
   src.backup(dst); dst.close(); src.close()
   "
   ```
4. Run dry-run against a copy (or read-only dry-run):
   ```bash
   .venv/bin/python scripts/backfill_legacy_paper_pnl.py --db-path /root/legacy-pnl-attribution-staging-20261113/prod_pre_deploy_backup.sqlite3
   ```
   (Confirm `missing_fills_found == 0`, `missing_closures_found == 0`, `recorded_evidence_coverage_ok == true`.)
5. If deploying to an un-repaired DB during a maintenance window (with `multi-exchange-paper.service` stopped):
   ```bash
   .venv/bin/python scripts/backfill_legacy_paper_pnl.py --db-path /root/multi-exchange-paper/data/paper_engine.sqlite3 --apply --allow-production-db
   ```
6. Restart service and verify `/api/health` and `paper_pnl_audit` (`recorded_evidence_coverage_ok == true`, `orders_missing_fills == 0`, `sltp_orders_missing_closures == 0`).

### Rollback Steps
1. Stop `multi-exchange-paper.service` (`systemctl stop multi-exchange-paper.service`).
2. Restore the pre-deployment SQLite online backup (removing stale `-wal`/`-shm` sidecars before starting):
   ```bash
   cp /root/legacy-pnl-attribution-staging-20261113/prod_pre_deploy_backup.sqlite3 /root/multi-exchange-paper/data/paper_engine.sqlite3
   rm -f /root/multi-exchange-paper/data/paper_engine.sqlite3-wal /root/multi-exchange-paper/data/paper_engine.sqlite3-shm
   ```
3. Revert the code commit (`git revert <merge_or_commit_sha>` or `git checkout 17aa6e0`) and restart the service (`systemctl start multi-exchange-paper.service`).
