import React, { useCallback, useEffect, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowRightLeft,
  CheckCircle2,
  Cpu,
  Database,
  Lock,
  RefreshCw,
  ShieldCheck
} from 'lucide-react';
import {
  AuthErrorBanner,
  LoadingTerminalSkeleton,
  SectionHeader
} from '../common/TerminalPrimitives';
import { TelegramAuthError } from '../../services/quantApi';
import {
  fetchPersistenceHealth,
  fetchUnifiedAccount,
  fetchUnifiedBrainStatus,
  PersistenceHealthPayload,
  UnifiedAccountPayload,
  UnifiedBrainStatusPayload
} from '../../services/violetResearchApi';

interface UnifiedBrainAccountViewProps {
  initData?: string;
}

export interface UnifiedBrainAccountContentProps {
  brain: UnifiedBrainStatusPayload | null;
  account: UnifiedAccountPayload | null;
  persistence: PersistenceHealthPayload | null;
  loading: boolean;
  refreshing: boolean;
  authError: string | null;
  fetchError: string | null;
  onRefresh: () => void;
}

function fmtDec(val: string | number | undefined | null, digits = 2): string {
  if (val === undefined || val === null || val === '') return '—';
  const num = Number(val);
  if (!Number.isFinite(num)) return String(val);
  return num.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  });
}

function formatIsoShort(ts: string | null | undefined): string {
  if (!ts) return 'Unavailable';
  const parsed = Date.parse(ts);
  if (Number.isNaN(parsed)) return ts;
  return new Date(parsed).toISOString().replace('T', ' ').slice(0, 19) + ' UTC';
}

export const UnifiedBrainAccountContent: React.FC<UnifiedBrainAccountContentProps> = ({
  brain,
  account,
  persistence,
  loading,
  refreshing,
  authError,
  fetchError,
  onRefresh
}) => {
  const riskHalted =
    Boolean(brain?.risk_gate.emergency_stop_active || brain?.risk_gate.circuit_breaker_tripped) ||
    Boolean(account?.risk_gate.emergency_stop_active || account?.risk_gate.circuit_breaker_tripped);

  const invariant = account?.invariant ?? brain?.invariant;
  const lastReport = brain?.last_report ?? null;

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Unified AI Brain & Closed-Loop Account"
        subtitle="AGY + Codex shared governance · Isolated $10k closed-loop custody simulation (separate from 500 USDT VIOLET campaign)"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span className="px-2.5 py-1 rounded-md bg-[#10B981]/15 border border-[#10B981]/35 text-[#10B981] text-[11px] font-mono inline-flex items-center gap-1.5">
              <Lock className="w-3 h-3" />
              <span>LIVE EXECUTION BLOCKED</span>
            </span>
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              className="px-3.5 py-2 min-h-[38px] rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/45 text-[#ffe3e5] text-xs font-medium flex items-center gap-1.5 whitespace-nowrap shrink-0 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 text-[#ff6375] ${refreshing ? 'animate-spin' : ''}`} />
              <span>Refresh Brain</span>
            </button>
          </div>
        }
      />

      {authError && <AuthErrorBanner message={authError} onRetry={onRefresh} />}

      {fetchError && !authError && !brain && !account && (
        <div className="nq-panel rounded-xl p-6 border border-[#EF4444]/45 space-y-3 text-xs">
          <div className="text-sm font-semibold text-[#ff6375]">Unified Brain &amp; Account Unavailable</div>
          <p className="text-[#f3c6cc]">{fetchError}</p>
          <button
            type="button"
            onClick={onRefresh}
            className="px-4 py-2 min-h-[38px] rounded-lg bg-[#ff3555]/20 border border-[#ff3555]/45 text-[#ffe3e5] font-medium"
          >
            Retry Sync
          </button>
        </div>
      )}

      {fetchError && !authError && (brain || account) && (
        <div className="px-4 py-2.5 rounded-xl bg-[#ffb16c]/10 border border-[#ffb16c]/35 text-xs text-[#ffd7b3] flex items-center justify-between gap-3">
          <span>Partial telemetry refresh error: {fetchError}. Showing last synchronized snapshot.</span>
          <button
            type="button"
            onClick={onRefresh}
            className="px-2.5 py-1 rounded bg-[#ffb16c]/20 hover:bg-[#ffb16c]/30 text-[#ffe3e5] whitespace-nowrap shrink-0"
          >
            Retry
          </button>
        </div>
      )}

      {loading && !brain && !account && !authError && !fetchError && <LoadingTerminalSkeleton />}

      {(brain || account || persistence) && !authError && (
        <>
          {/* Core Brain & Persistence KPI Strip */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="nq-panel rounded-xl p-4">
              <div className="text-xs text-[#c28e95] flex items-center justify-between">
                <span>AI Brain Mode</span>
                <Cpu className="w-4 h-4 text-[#ff6375]" />
              </div>
              <div className="text-base font-mono font-bold text-[#ffe3e5] mt-1.5">
                {brain?.mode || account?.mode || 'UNAVAILABLE'}
              </div>
              <div className="text-[11px] font-mono text-[#d4a5ac] mt-1">
                Agents: {(brain?.agents || account?.brain_sync?.agents || ['AGY', 'Codex']).join(' + ')}
              </div>
            </div>

            <div className="nq-panel rounded-xl p-4">
              <div className="text-xs text-[#c28e95] flex items-center justify-between">
                <span>Risk Guardian Gate</span>
                <ShieldCheck className="w-4 h-4 text-[#10B981]" />
              </div>
              <div
                className={`text-base font-mono font-bold mt-1.5 ${
                  riskHalted ? 'text-[#EF4444]' : 'text-[#10B981]'
                }`}
              >
                {riskHalted ? 'HALTED / TRIPPED' : 'ARMED NOMINAL'}
              </div>
              <div className="text-[11px] font-mono text-[#d4a5ac] mt-1">
                Skill: {brain?.shared_skill || 'unified-treasury-router'}
              </div>
            </div>

            <div className="nq-panel rounded-xl p-4">
              <div className="text-xs text-[#c28e95] flex items-center justify-between">
                <span>Equity Conservation</span>
                <ArrowRightLeft className="w-4 h-4 text-[#ff6375]" />
              </div>
              <div
                className={`text-base font-mono font-bold mt-1.5 ${
                  invariant?.conservation_ok ? 'text-[#10B981]' : 'text-[#ffb16c]'
                }`}
              >
                {invariant
                  ? invariant.conservation_ok
                    ? 'CONSERVED ($0.00 DRIFT)'
                    : `DRIFT $${fmtDec(invariant.drift_usd)}`
                  : 'UNAVAILABLE'}
              </div>
              <div className="text-[11px] font-mono text-[#d4a5ac] mt-1">
                Total: {invariant ? `$${fmtDec(invariant.current_venue_total_usd)}` : '—'} · Fees:{' '}
                {invariant ? `$${fmtDec(invariant.cumulative_fees_usd)}` : '—'}
              </div>
            </div>

            <div className="nq-panel rounded-xl p-4">
              <div className="text-xs text-[#c28e95] flex items-center justify-between">
                <span>Persistence &amp; SQLite</span>
                <Database className="w-4 h-4 text-[#ff6375]" />
              </div>
              <div
                className={`text-base font-mono font-bold mt-1.5 ${
                  persistence?.status === 'ok' ? 'text-[#10B981]' : 'text-[#ffb16c]'
                }`}
              >
                {persistence ? persistence.status.toUpperCase() : 'UNAVAILABLE'}
              </div>
              <div className="text-[11px] font-mono text-[#d4a5ac] mt-1">
                SQLite: {persistence?.sqlite.status || '—'} · Scheduler: {persistence?.scheduler.status || '—'}
              </div>
            </div>
          </div>

          {/* Last Unified Brain Cycle Report */}
          <div className="nq-panel rounded-xl p-4 sm:p-5 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5 border-b border-[#ff3555]/20">
              <h3 className="text-sm font-display font-semibold text-[#ffe3e5] flex items-center gap-2">
                <Activity className="w-4 h-4 text-[#ff6375]" />
                <span>Latest Unified AI Brain Cycle</span>
              </h3>
              <span className="text-[11px] font-mono text-[#c28e95]">
                {lastReport ? formatIsoShort(lastReport.timestamp_utc) : 'No cycle recorded yet'}
              </span>
            </div>

            {lastReport ? (
              <div className="space-y-3 text-xs font-mono">
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                  <div className="rounded-lg bg-[#080305] border border-[#ff3555]/20 p-3">
                    <div className="text-[#b8868e]">Cycle Mode</div>
                    <div className="text-sm font-bold text-[#ffe3e5] mt-1">{lastReport.mode}</div>
                  </div>
                  <div className="rounded-lg bg-[#080305] border border-[#ff3555]/20 p-3">
                    <div className="text-[#b8868e]">Actions Executed</div>
                    <div className="text-sm font-bold text-[#ff6375] mt-1">
                      {lastReport.actions_executed.length}
                    </div>
                  </div>
                  <div className="rounded-lg bg-[#080305] border border-[#ff3555]/20 p-3">
                    <div className="text-[#b8868e]">Halted By Guardian</div>
                    <div
                      className={`text-sm font-bold mt-1 ${
                        lastReport.halted_by_risk_guardian ? 'text-[#EF4444]' : 'text-[#10B981]'
                      }`}
                    >
                      {lastReport.halted_by_risk_guardian ? 'YES' : 'NO'}
                    </div>
                  </div>
                </div>

                {lastReport.skipped_reasons.length > 0 && (
                  <div className="rounded-lg bg-[#080305] border border-[#ffb16c]/30 p-3 space-y-1">
                    <div className="text-[#ffb16c] font-semibold flex items-center gap-1.5">
                      <AlertTriangle className="w-3.5 h-3.5" />
                      <span>Fail-Closed Cycle Notes (No Simulated Allocation Performed)</span>
                    </div>
                    <ul className="list-disc list-inside text-[#ffd7b3] space-y-0.5">
                      {lastReport.skipped_reasons.map((reason, idx) => (
                        <li key={idx} className="break-all">
                          {reason}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            ) : (
              <div className="text-xs font-mono text-[#c28e95]">
                No brain cycle report recorded yet. In read-only preview, brain cycles fail closed when trading workers are inactive.
              </div>
            )}
          </div>

          {/* Closed-Loop Venue Balances & Allowlist */}
          {account && (
            <div className="nq-panel rounded-xl p-4 sm:p-5 space-y-4">
              <div className="flex flex-wrap items-center justify-between gap-2 pb-2.5 border-b border-[#ff3555]/20">
                <div>
                  <h3 className="text-sm font-display font-semibold text-[#ffe3e5]">
                    Closed-Loop Venue Balances &amp; Allowlist Custody
                  </h3>
                  <p className="text-[11px] text-[#c28e95]">
                    Strict closed-loop routes only · Preview enforces HTTP 405 on write operations
                  </p>
                </div>
                <span className="px-2 py-1 rounded bg-[#10B981]/15 border border-[#10B981]/30 text-[#10B981] text-[10px] font-mono">
                  ALLOWLIST ONLY · {account.transfer_count} TRANSFERS
                </span>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
                {Object.entries(account.balances_usd).map(([venue, bal]) => (
                  <div
                    key={venue}
                    className="bg-[#080305] border border-[#ff3555]/20 rounded-xl p-3 space-y-1"
                  >
                    <div className="text-[10px] font-mono uppercase tracking-wider text-[#b8868e]">
                      {venue.replace(/_/g, ' ')}
                    </div>
                    <div className="text-sm font-mono font-bold text-[#ffe3e5]">${fmtDec(bal)}</div>
                    <div className="text-[10px] font-mono text-[#a1737a] truncate" title={account.allowlist[venue]}>
                      {account.allowlist[venue] ?? 'allowlisted'}
                    </div>
                  </div>
                ))}
              </div>

              {account.transfers.length > 0 ? (
                <div className="overflow-x-auto">
                  <table className="w-full text-left text-[11px] font-mono">
                    <thead className="text-[#c28e95] border-b border-[#ff3555]/20">
                      <tr>
                        <th className="py-2 pr-3">Route</th>
                        <th className="py-2 pr-3">Amount</th>
                        <th className="py-2 pr-3">Fee</th>
                        <th className="py-2 pr-3">State</th>
                        <th className="py-2">Reason</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-white/[0.05]">
                      {account.transfers.slice(0, 10).map((t) => (
                        <tr key={t.transfer_id} className="text-[#f3c6cc]">
                          <td className="py-2 pr-3">
                            {t.source_venue} → {t.destination_venue}
                          </td>
                          <td className="py-2 pr-3">${fmtDec(t.amount_usd)}</td>
                          <td className="py-2 pr-3">${fmtDec(t.fee_usd)}</td>
                          <td className="py-2 pr-3 text-[#10B981]">{t.state}</td>
                          <td className="py-2 text-[#c28e95]">{t.reason}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="text-xs font-mono text-[#c28e95]">
                  No closed-loop simulated transfers recorded in isolated router ledger.
                </div>
              )}
            </div>
          )}

          {/* Persistence Diagnostics Matrix */}
          {persistence && (
            <div className="nq-panel rounded-xl p-4 sm:p-5 space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2 pb-2 border-b border-[#ff3555]/20">
                <h3 className="text-sm font-display font-semibold text-[#ffe3e5]">
                  SQLite &amp; Scheduler Persistence Diagnostics
                </h3>
                <span
                  className={`text-[11px] font-mono inline-flex items-center gap-1 ${
                    persistence.status === 'ok' ? 'text-[#10B981]' : 'text-[#ffb16c]'
                  }`}
                >
                  {persistence.status === 'ok' ? (
                    <CheckCircle2 className="w-3.5 h-3.5" />
                  ) : (
                    <AlertTriangle className="w-3.5 h-3.5" />
                  )}
                  <span>HTTP {persistence.http_status ?? (persistence.status === 'ok' ? 200 : 503)}</span>
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs font-mono">
                <div className="rounded-lg bg-[#080305] border border-[#ff3555]/20 p-3">
                  <div className="text-[#b8868e]">SQLite Integrity</div>
                  <div className="text-sm font-bold text-[#ffe3e5] mt-1">
                    {persistence.sqlite.status.toUpperCase()}
                    {persistence.sqlite.integrity ? ` (${persistence.sqlite.integrity})` : ''}
                  </div>
                  {persistence.sqlite.reason && (
                    <div className="text-[11px] text-[#ffb16c] mt-0.5">{persistence.sqlite.reason}</div>
                  )}
                </div>
                <div className="rounded-lg bg-[#080305] border border-[#ff3555]/20 p-3">
                  <div className="text-[#b8868e]">Scheduler Heartbeat</div>
                  <div className="text-sm font-bold text-[#ffe3e5] mt-1">
                    {persistence.scheduler.status.toUpperCase()}
                    {typeof persistence.scheduler.age_seconds === 'number'
                      ? ` (${Math.round(persistence.scheduler.age_seconds)}s ago)`
                      : ''}
                  </div>
                  <div className="text-[11px] text-[#c28e95] mt-0.5">
                    {formatIsoShort(persistence.scheduler.heartbeat_timestamp)}
                  </div>
                </div>
                <div className="rounded-lg bg-[#080305] border border-[#ff3555]/20 p-3">
                  <div className="text-[#b8868e]">Last Brain Snapshot</div>
                  <div className="text-sm font-bold text-[#ffe3e5] mt-1">
                    {formatIsoShort(persistence.last_brain_cycle_timestamp)}
                  </div>
                  <div className="text-[11px] text-[#c28e95] mt-0.5">
                    Risk Guardian: {persistence.risk_guardian.status.toUpperCase()}
                  </div>
                </div>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

export const UnifiedBrainAccountView: React.FC<UnifiedBrainAccountViewProps> = ({ initData }) => {
  const [brain, setBrain] = useState<UnifiedBrainStatusPayload | null>(null);
  const [account, setAccount] = useState<UnifiedAccountPayload | null>(null);
  const [persistence, setPersistence] = useState<PersistenceHealthPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const loadAll = useCallback(
    async (isBackground = false) => {
      if (!isBackground) setRefreshing(true);
      setAuthError(null);
      setFetchError(null);

      const results = await Promise.allSettled([
        fetchUnifiedBrainStatus(initData),
        fetchUnifiedAccount(initData),
        fetchPersistenceHealth(initData)
      ]);

      const firstAuthErr = results.find(
        (r) => r.status === 'rejected' && r.reason instanceof TelegramAuthError
      ) as PromiseRejectedResult | undefined;

      if (firstAuthErr) {
        setAuthError((firstAuthErr.reason as Error).message);
      } else {
        const errors: string[] = [];
        if (results[0].status === 'fulfilled') setBrain(results[0].value);
        else errors.push(results[0].reason instanceof Error ? results[0].reason.message : 'Brain status error');

        if (results[1].status === 'fulfilled') setAccount(results[1].value);
        else errors.push(results[1].reason instanceof Error ? results[1].reason.message : 'Unified account error');

        if (results[2].status === 'fulfilled') setPersistence(results[2].value);
        else errors.push(results[2].reason instanceof Error ? results[2].reason.message : 'Persistence health error');

        if (errors.length > 0) {
          setFetchError(errors.join(' · '));
        }
      }

      setLoading(false);
      setRefreshing(false);
    },
    [initData]
  );

  useEffect(() => {
    void loadAll(false);
    const interval = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadAll(true);
      }
    }, 30000);
    return () => clearInterval(interval);
  }, [loadAll]);

  return (
    <UnifiedBrainAccountContent
      brain={brain}
      account={account}
      persistence={persistence}
      loading={loading}
      refreshing={refreshing}
      authError={authError}
      fetchError={fetchError}
      onRefresh={() => void loadAll(false)}
    />
  );
};

export default UnifiedBrainAccountView;
