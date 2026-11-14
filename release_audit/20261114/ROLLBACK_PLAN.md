# ROLLBACK PLAN

Observed/reported: 2026-11-14T14:21:00.209895+00:00

Status meanings: VERIFIED = supported by this audit’s evidence; PARTIALLY VERIFIED = limited scope; BLOCKED = unresolved requirement; NOT TESTED = no completed evidence.

Overall: VERIFIED checkpoints; production restore procedure NOT TESTED.

Production code and services were not replaced by this candidate. Existing positions, campaign pointer and ledgers were not reset. Source dirty diffs were recorded before candidate edits in checkpoints/*.diff. Online SQLite backups of the original engine and active campaign were created via sqlite3 backup(), mode 0600, with SHA-256 in evidence/discovery.json. The uploaded source archive is immutable reference evidence.

Candidate code rollback: revert only this audit’s narrow maintenance commit, preserving preexisting untracked arbitrage_research.py and verification/ files. Android candidate is isolated; discard/revert only its candidate commit if rejected. No remote push is required for rollback.

If a later production deployment is authorized and passes gates: record unit/proxy settings and actual code hash, online-backup each database, stage a versioned source/artifact directory, test isolated candidate, switch only affected service, then verify authenticated endpoints/ledger recovery. On failure restore the previous code/artifact version and restart only the affected service. Do not restore an older database over active paper trades; reconcile first. Any destructive restore or irreversible migration requires explicit approval. Never remove WAL/SHM files from a running database.

BLOCKED: no completed release deployment to roll back and no production crash/recovery rehearsal for this audit. Checkpoint databases contain private operational data and are not Git artifacts.

New protection-plan rollback requirement: old coordinators do not implement staged plans. Before any deployment, validate a compatible recovery coordinator or preserve the candidate protection executor with entries quiesced until affected positions are flat. Do not reset positions, overwrite the ledger, or silently downgrade protection semantics. This backward-recovery gate remains BLOCKED.

Funding capture adds lazy paper_exposure_events and paper_exposure_watermarks tables during candidate futures execution, plus atomic dashboard FUNDING events during coordinator ingestion. These are absent from production because no deployment occurred. Preserve them if rolling back: old code cannot maintain exposure capture/watermarks or guarantee funding ingestion compatibility. Disable new entries and coordinate funding processing until a compatible executor resumes; do not delete settlement rows, restore an old balance snapshot over new activity, or replay cash with different identities. Checkpoint source and previous reports: checkpoints/funding-integration-20261114.
