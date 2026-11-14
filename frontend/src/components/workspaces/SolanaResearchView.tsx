import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  ArrowUpDown,
  CheckCircle2,
  Clock,
  ExternalLink,
  Lock,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck
} from 'lucide-react';
import { SectionHeader } from '../common/TerminalPrimitives';
import { TelegramAuthError } from '../../services/quantApi';
import {
  SolanaRouteObservation,
  SolanaSummaryPayload,
  VioletResearchPayload,
  fetchVioletResearch,
  fetchVioletSolana
} from '../../services/violetResearchApi';

interface SolanaResearchViewProps {
  initData?: string;
}

type RouteSortKey = 'dataset' | 'observations' | 'net_lamports';
type RouteFreshnessFilter = 'ALL' | 'FRESH' | 'STALE';

const STALE_AGE_THRESHOLD_SEC = 60;
const POLL_INTERVAL_MS = 30000;

function formatLamports(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return 'Unavailable';
  return `${Math.round(value).toLocaleString('en-US')} lamports`;
}

function formatCampaignValue(val: unknown): string {
  if (val === null || val === undefined || val === '') return '—';
  if (typeof val === 'number') return Number.isInteger(val) ? String(val) : val.toFixed(2);
  if (typeof val === 'boolean') return val ? 'YES' : 'NO';
  return String(val);
}

export interface SolanaResearchContentProps {
  research: VioletResearchPayload | null;
  solana: SolanaSummaryPayload | null;
  fetchedAtMs: number | null;
  nowMs: number;
  loading: boolean;
  refreshing: boolean;
  authError: string | null;
  fetchError: string | null;
  onRefresh: () => void;
}

export const SolanaResearchContent: React.FC<SolanaResearchContentProps> = ({
  research,
  solana,
  fetchedAtMs,
  nowMs,
  loading,
  refreshing,
  authError,
  fetchError,
  onRefresh
}) => {
  const [routeQuery, setRouteQuery] = useState('');
  const [freshnessFilter, setFreshnessFilter] = useState<RouteFreshnessFilter>('ALL');
  const [sortBy, setSortBy] = useState<RouteSortKey>('observations');
  const [sortAsc, setSortAsc] = useState(false);

  const effectiveSolana: SolanaSummaryPayload | null = solana ?? research?.solana ?? null;
  const routes = effectiveSolana?.routes ?? [];
  const staleRoutesCount = routes.filter((r) => r.stale).length;
  const ageSec = fetchedAtMs !== null ? Math.max(0, Math.floor((nowMs - fetchedAtMs) / 1000)) : null;

  const isStatusHealthy = effectiveSolana?.status === 'healthy';
  const isWorkerStale = Boolean(research?.violet.worker_health?.toLowerCase().includes('stale'));
  const isTelemetryStale =
    (ageSec !== null && ageSec > STALE_AGE_THRESHOLD_SEC) ||
    staleRoutesCount > 0 ||
    (effectiveSolana !== null && !isStatusHealthy) ||
    isWorkerStale;

  const numericNetLamports = useMemo(
    () =>
      routes
        .map((r) => r.mean_conservative_net_lamports)
        .filter((v): v is number => typeof v === 'number' && Number.isFinite(v)),
    [routes]
  );

  const bestNetLamports = numericNetLamports.length > 0 ? Math.max(...numericNetLamports) : null;
  const worstNetLamports = numericNetLamports.length > 0 ? Math.min(...numericNetLamports) : null;
  const avgNetLamports =
    numericNetLamports.length > 0
      ? numericNetLamports.reduce((sum, v) => sum + v, 0) / numericNetLamports.length
      : null;

  const filteredRoutes = useMemo(() => {
    const q = routeQuery.trim().toLowerCase();
    const list = routes.filter((r) => {
      const matchQuery = !q || r.dataset.toLowerCase().includes(q);
      const matchFresh =
        freshnessFilter === 'ALL' ||
        (freshnessFilter === 'FRESH' && !r.stale) ||
        (freshnessFilter === 'STALE' && r.stale);
      return matchQuery && matchFresh;
    });

    return [...list].sort((a, b) => {
      let cmp = 0;
      if (sortBy === 'dataset') {
        cmp = a.dataset.localeCompare(b.dataset);
      } else if (sortBy === 'observations') {
        cmp = (a.observations ?? -1) - (b.observations ?? -1);
      } else {
        cmp = (a.mean_conservative_net_lamports ?? Number.NEGATIVE_INFINITY) -
          (b.mean_conservative_net_lamports ?? Number.NEGATIVE_INFINITY);
      }
      return sortAsc ? cmp : -cmp;
    });
  }, [routes, routeQuery, freshnessFilter, sortBy, sortAsc]);

  const toggleSort = (key: RouteSortKey) => {
    if (sortBy === key) {
      setSortAsc((prev) => !prev);
    } else {
      setSortBy(key);
      setSortAsc(key === 'dataset');
    }
  };

  const verdict = effectiveSolana?.verdict || 'UNKNOWN';
  const verdictTone =
    verdict === 'UNKNOWN'
      ? 'text-[#ffb16c]'
      : 'text-[#ff6375]';

  const campaignEntries = useMemo(() => {
    if (!research?.violet.campaign) return [];
    return Object.entries(research.violet.campaign)
      .filter(([, val]) => val !== null && val !== undefined && typeof val !== 'object')
      .slice(0, 6);
  }, [research]);

  const blockers = effectiveSolana?.blockers ?? [];
  const alerts = effectiveSolana?.alerts ?? [];

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Solana Read-Only Research Terminal"
        subtitle="Authenticated loopback telemetry via GET /api/violet/research & GET /api/violet/solana · Isolated from crypto paper ledgers"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span className="px-2.5 py-1.5 rounded-lg bg-[#10b981]/10 border border-[#10b981]/35 text-[#10b981] text-[11px] font-mono inline-flex items-center gap-1.5">
              <Lock className="w-3.5 h-3.5" />
              <span>LIVE ORDERS: DISABLED</span>
            </span>
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              className="px-3.5 py-2 min-h-[38px] rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/45 text-[#ffe3e5] text-xs font-medium flex items-center gap-1.5 whitespace-nowrap shrink-0 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 text-[#ff6375] ${refreshing ? 'animate-spin' : ''}`} />
              <span>Sync Research</span>
            </button>
          </div>
        }
      />

      {/* Auth Error */}
      {authError && (
        <div
          role="alert"
          className="nq-panel rounded-xl p-5 border border-[#ef4444]/50 bg-[#ef4444]/10 flex flex-col sm:flex-row sm:items-center justify-between gap-4"
        >
          <div className="flex items-start gap-3">
            <ShieldAlert className="w-5 h-5 text-[#ef4444] shrink-0 mt-0.5" />
            <div>
              <h3 className="text-sm font-semibold text-[#ffe3e5]">Solana Research Authentication Required</h3>
              <p className="text-xs text-[#f6c4ca] mt-1">{authError}</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onRefresh}
            className="px-4 py-2 min-h-[40px] rounded-lg bg-[#ff3555]/20 hover:bg-[#ff3555]/30 border border-[#ff3555]/50 text-[#ffe3e5] text-xs font-medium shrink-0"
          >
            Retry Auth
          </button>
        </div>
      )}

      {/* Initial Loading Skeleton */}
      {loading && !effectiveSolana && !authError && (
        <div className="space-y-4 animate-pulse" role="status" aria-label="Loading Solana research telemetry">
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {[0, 1, 2, 3, 4, 5].map((i) => (
              <div key={i} className="nq-panel rounded-xl h-36" />
            ))}
          </div>
          <div className="nq-panel rounded-xl h-64" />
        </div>
      )}

      {/* Hard Fetch Error (no data available) */}
      {fetchError && !authError && !effectiveSolana && !loading && (
        <div
          role="alert"
          className="nq-panel rounded-xl p-6 border border-[#ef4444]/45 space-y-3 text-xs"
        >
          <div className="flex items-center gap-2 text-sm font-semibold text-[#ff6375]">
            <ShieldAlert className="w-4 h-4 text-[#ef4444]" />
            <span>Solana Research Telemetry Unavailable</span>
          </div>
          <p className="text-[#f3c6cc]">{fetchError}</p>
          <p className="text-[11px] font-mono text-[#c28e95]">
            Paper-only safety unchanged · Zero live execution permitted · No simulated data substituted.
          </p>
          <button
            type="button"
            onClick={onRefresh}
            className="px-4 py-2 min-h-[38px] rounded-lg bg-[#ff3555]/20 hover:bg-[#ff3555]/30 border border-[#ff3555]/45 text-[#ffe3e5] font-medium"
          >
            Retry Telemetry
          </button>
        </div>
      )}

      {/* Partial / Refresh Warning Strip */}
      {fetchError && !authError && effectiveSolana && (
        <div
          role="alert"
          className="rounded-xl px-4 py-2.5 bg-[#ffb16c]/10 border border-[#ffb16c]/35 text-xs text-[#ffd7b3] flex flex-wrap items-center justify-between gap-2"
        >
          <span>Research refresh warning: {fetchError}. Displaying last verified read-only snapshot.</span>
          <button
            type="button"
            onClick={onRefresh}
            className="px-2.5 py-1 rounded bg-[#ffb16c]/20 hover:bg-[#ffb16c]/30 text-[#ffe3e5] font-mono text-[11px]"
          >
            Retry
          </button>
        </div>
      )}

      {/* Stale / Degraded Data Warning Strip */}
      {effectiveSolana && !authError && isTelemetryStale && (
        <div
          role="status"
          className="rounded-xl px-4 py-2.5 bg-[#ffb16c]/10 border border-[#ffb16c]/35 text-xs text-[#ffd7b3] flex flex-wrap items-center justify-between gap-2"
        >
          <div className="flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 text-[#ffb16c] shrink-0" />
            <span>
              <strong>DATA FRESHNESS NOTICE:</strong>{' '}
              {!isStatusHealthy
                ? `Solana bridge reports status "${effectiveSolana.status}". `
                : ''}
              {staleRoutesCount > 0
                ? `${staleRoutesCount} of ${routes.length} route datasets marked STALE. `
                : ''}
              {ageSec !== null && ageSec > STALE_AGE_THRESHOLD_SEC
                ? `Last synced ${ageSec}s ago. `
                : ''}
              {isWorkerStale ? 'VIOLET worker heartbeat stale.' : ''}
            </span>
          </div>
          <button
            type="button"
            onClick={onRefresh}
            className="text-xs font-mono text-[#ff6375] hover:underline flex items-center gap-1 whitespace-nowrap"
          >
            <RefreshCw className="w-3 h-3" />
            <span>Refresh Now</span>
          </button>
        </div>
      )}

      {/* Independent Responsive Research Tiles */}
      {effectiveSolana && !authError && (
        <>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {/* Tile 01: Solana Read-Only Status & Security Gate */}
            <section aria-label="Solana read-only status" className="nq-panel rounded-xl p-4 flex flex-col justify-between gap-3">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-mono tracking-wider text-[#ff8b98] uppercase">
                    01 / Solana Read-Only Status
                  </span>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold border ${
                      isStatusHealthy
                        ? 'bg-[#10b981]/15 border-[#10b981]/40 text-[#10b981]'
                        : effectiveSolana.status === 'safety_check_failed'
                          ? 'bg-[#ef4444]/15 border-[#ef4444]/40 text-[#ef4444]'
                          : 'bg-[#ffb16c]/15 border-[#ffb16c]/40 text-[#ffb16c]'
                    }`}
                  >
                    {effectiveSolana.status.toUpperCase()}
                  </span>
                </div>
                <div className="mt-2.5 text-lg font-mono font-bold text-[#ffe3e5]">
                  {effectiveSolana.mode}
                </div>
                <div className="mt-2 space-y-1 text-xs font-mono text-[#d4a5ac]">
                  <div className="flex items-center justify-between">
                    <span>Live Orders</span>
                    <span className="text-[#10b981] font-semibold">
                      {effectiveSolana.live_orders_enabled ? 'UNEXPECTED' : 'DISABLED (FALSE)'}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    <span>Ledger Isolation</span>
                    <span className="text-[#ffe3e5] truncate">
                      {research?.accounting || 'SEPARATE_PAPER_LEDGERS'}
                    </span>
                  </div>
                </div>
              </div>
              <div className="pt-2.5 border-t border-[#ff3555]/20 flex items-center justify-between text-[11px] font-mono">
                <span className="text-[#b8868e]">Loopback 127.0.0.1:8795</span>
                <a
                  href="https://solana.187-52-118-25.sslip.io/"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[#ff6375] hover:text-[#ffe3e5] inline-flex items-center gap-1"
                >
                  <span>SOLANA HOST</span>
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            </section>

            {/* Tile 02: Research Verdict */}
            <section aria-label="Solana research verdict" className="nq-panel rounded-xl p-4 flex flex-col justify-between gap-3">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-mono tracking-wider text-[#ff8b98] uppercase">
                    02 / Research Verdict
                  </span>
                  <ShieldCheck className="w-4 h-4 text-[#ff6375]" />
                </div>
                <div className={`mt-2.5 text-lg font-mono font-bold ${verdictTone}`}>
                  {verdict}
                </div>
                <p className="mt-2 text-xs text-[#d4a5ac] leading-relaxed">
                  Hypothetical conservative quote stress is observational research only. Estimates are{' '}
                  <strong className="text-[#ffe3e5]">not realized trading P&amp;L</strong> and are never rolled into crypto paper balances.
                </p>
              </div>
              <div className="pt-2.5 border-t border-[#ff3555]/20 text-[11px] font-mono text-[#b8868e]">
                Fail-closed validation · Read-only diagnostics
              </div>
            </section>

            {/* Tile 03: Quote Observations & Data Freshness */}
            <section aria-label="Quote observations and data freshness" className="nq-panel rounded-xl p-4 flex flex-col justify-between gap-3">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-mono tracking-wider text-[#ff8b98] uppercase">
                    03 / Quote Observations &amp; Freshness
                  </span>
                  <Clock className="w-4 h-4 text-[#ff6375]" />
                </div>
                <div className="mt-2.5 flex items-baseline justify-between gap-2">
                  <div className="text-2xl font-mono font-bold text-[#ffe3e5]">
                    {effectiveSolana.observations !== null
                      ? effectiveSolana.observations.toLocaleString('en-US')
                      : 'Unavailable'}
                  </div>
                  <span
                    className={`px-2 py-0.5 rounded text-[10px] font-mono font-semibold ${
                      isTelemetryStale
                        ? 'bg-[#ffb16c]/15 text-[#ffb16c]'
                        : 'bg-[#10b981]/15 text-[#10b981]'
                    }`}
                  >
                    {isTelemetryStale ? 'STALE / DEGRADED' : 'FRESH'}
                  </span>
                </div>
                <div className="mt-2 space-y-1 text-xs font-mono text-[#d4a5ac]">
                  <div className="flex justify-between">
                    <span>Route Datasets</span>
                    <span className="text-[#ffe3e5]">{routes.length}</span>
                  </div>
                  <div className="flex justify-between">
                    <span>Stale Datasets</span>
                    <span className={staleRoutesCount > 0 ? 'text-[#ffb16c]' : 'text-[#10b981]'}>
                      {staleRoutesCount}
                    </span>
                  </div>
                </div>
              </div>
              <div className="pt-2.5 border-t border-[#ff3555]/20 flex items-center justify-between text-[11px] font-mono text-[#b8868e]">
                <span>
                  {fetchedAtMs ? `Synced ${new Date(fetchedAtMs).toLocaleTimeString()}` : 'Not synced yet'}
                </span>
                <span>{ageSec !== null ? `${ageSec}s old` : '—'}</span>
              </div>
            </section>

            {/* Tile 04: Hypothetical Net Cost (Conservative Lamports) */}
            <section aria-label="Hypothetical net cost" className="nq-panel rounded-xl p-4 flex flex-col justify-between gap-3">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-mono tracking-wider text-[#ff8b98] uppercase">
                    04 / Hypothetical Net Cost
                  </span>
                  <Activity className="w-4 h-4 text-[#ff6375]" />
                </div>
                <div className="mt-2.5 text-xl font-mono font-bold text-[#ffe3e5]">
                  {avgNetLamports !== null ? formatLamports(avgNetLamports) : 'Unavailable'}
                </div>
                <div className="text-[11px] font-mono text-[#b8868e] mt-0.5">
                  Mean across {numericNetLamports.length} numeric route{numericNetLamports.length === 1 ? '' : 's'}
                </div>
                <div className="mt-2 space-y-1 text-xs font-mono text-[#d4a5ac]">
                  <div className="flex justify-between">
                    <span>Best Route Mean</span>
                    <span className={bestNetLamports !== null && bestNetLamports >= 0 ? 'text-[#10b981]' : 'text-[#ff6375]'}>
                      {formatLamports(bestNetLamports)}
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span>Worst Route Mean</span>
                    <span className="text-[#ffb16c]">{formatLamports(worstNetLamports)}</span>
                  </div>
                </div>
              </div>
              <div className="pt-2.5 border-t border-[#ff3555]/20 text-[11px] font-mono text-[#b8868e]">
                Conservative quote stress · Zero wallet transfers
              </div>
            </section>

            {/* Tile 05: Independent VIOLET Crypto Campaign Status */}
            <section aria-label="Independent VIOLET crypto campaign" className="nq-panel rounded-xl p-4 flex flex-col justify-between gap-3">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-mono tracking-wider text-[#ff8b98] uppercase">
                    05 / VIOLET Crypto Campaign
                  </span>
                  <span className="text-[10px] font-mono text-[#d4a5ac]">
                    {research?.violet.source || 'isolated_violet_paper_ledger'}
                  </span>
                </div>
                <div className="mt-2.5 flex items-baseline justify-between gap-2 font-mono">
                  <div className="text-lg font-bold text-[#ffe3e5]">
                    {research?.violet.worker_health
                      ? research.violet.worker_health.toUpperCase()
                      : research
                        ? 'UNKNOWN'
                        : 'UNAVAILABLE'}
                  </div>
                  <div className="text-xs text-[#ff6375]">
                    Cycles: {research?.violet.cycles ?? '—'}
                  </div>
                </div>
                {campaignEntries.length > 0 ? (
                  <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] font-mono text-[#d4a5ac]">
                    {campaignEntries.map(([k, v]) => (
                      <div key={k} className="flex justify-between gap-1 truncate">
                        <span className="text-[#b8868e] truncate">{k}:</span>
                        <span className="text-[#ffe3e5] truncate">{formatCampaignValue(v)}</span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="mt-2 text-xs text-[#b8868e]">
                    {research
                      ? 'Campaign telemetry active in isolated crypto paper runner.'
                      : 'Combined /api/violet/research payload unavailable.'}
                  </p>
                )}
              </div>
              <div className="pt-2.5 border-t border-[#ff3555]/20 text-[11px] font-mono text-[#b8868e]">
                Binance · Bybit · OKX independent paper ledger
              </div>
            </section>

            {/* Tile 06: Blockers & Research Alerts */}
            <section aria-label="Solana blockers and alerts" className="nq-panel rounded-xl p-4 flex flex-col justify-between gap-3">
              <div>
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[11px] font-mono tracking-wider text-[#ff8b98] uppercase">
                    06 / Blockers &amp; Alerts
                  </span>
                  <span className="text-[11px] font-mono text-[#d4a5ac]">
                    {blockers.length} blockers · {alerts.length} alerts
                  </span>
                </div>

                {blockers.length === 0 && alerts.length === 0 ? (
                  <div className="mt-3 flex items-center gap-2 text-xs font-mono text-[#10b981]">
                    <CheckCircle2 className="w-4 h-4 shrink-0" />
                    <span>No active blockers or alerts reported.</span>
                  </div>
                ) : (
                  <div className="mt-2.5 space-y-2 max-h-36 overflow-y-auto pr-1 text-xs">
                    {blockers.map((blocker, idx) => (
                      <div
                        key={`blocker-${idx}`}
                        className="px-2.5 py-1.5 rounded-lg bg-[#ef4444]/10 border border-[#ef4444]/35 text-[#fecaca] font-mono text-[11px]"
                      >
                        BLOCKER: {blocker}
                      </div>
                    ))}
                    {alerts.map((alertMsg, idx) => (
                      <div
                        key={`alert-${idx}`}
                        className="px-2.5 py-1.5 rounded-lg bg-[#ffb16c]/10 border border-[#ffb16c]/30 text-[#ffd7b3] text-[11px]"
                      >
                        {alertMsg}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div className="pt-2.5 border-t border-[#ff3555]/20 text-[11px] font-mono text-[#b8868e]">
                Fail-closed gatekeeper · Read-only notifications
              </div>
            </section>
          </div>

          {/* Tile 07: Route Comparison Table & Mobile Cards */}
          <section aria-label="Solana route comparisons" className="nq-panel rounded-xl overflow-hidden">
            <div className="px-4 py-3.5 border-b border-[#ff3555]/20 flex flex-col lg:flex-row lg:items-center justify-between gap-3">
              <div>
                <h3 className="font-display font-semibold text-sm text-[#ffe3e5]">
                  07 / Route Comparisons &amp; Conservative Net Stress ({filteredRoutes.length})
                </h3>
                <p className="text-xs text-[#c28e95] mt-0.5">
                  Per-dataset quote coverage, freshness, and hypothetical net lamports (never realized fills)
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <div
                  role="group"
                  aria-label="Filter routes by freshness"
                  className="flex items-center p-1 rounded-lg bg-[#080305] border border-[#ff3555]/25"
                >
                  {(['ALL', 'FRESH', 'STALE'] as const).map((f) => (
                    <button
                      key={f}
                      type="button"
                      onClick={() => setFreshnessFilter(f)}
                      aria-pressed={freshnessFilter === f}
                      className={`px-2.5 py-1 min-h-[32px] rounded text-[11px] font-mono transition-colors ${
                        freshnessFilter === f
                          ? 'bg-[#ff3555]/25 text-[#ffe3e5]'
                          : 'text-[#c28e95] hover:text-[#ffe3e5]'
                      }`}
                    >
                      {f}
                    </button>
                  ))}
                </div>

                <div className="relative w-full sm:w-56">
                  <Search className="w-3.5 h-3.5 text-[#c28e95] absolute left-3 top-1/2 -translate-y-1/2" />
                  <input
                    type="text"
                    value={routeQuery}
                    onChange={(e) => setRouteQuery(e.target.value)}
                    placeholder="Filter dataset..."
                    aria-label="Filter Solana route datasets"
                    className="w-full pl-8 pr-3 py-1.5 min-h-[36px] rounded-lg bg-[#080305] border border-[#ff3555]/25 text-xs text-[#ffe3e5] placeholder-[#9e6d74] focus:border-[#ff6375]"
                  />
                </div>
              </div>
            </div>

            {filteredRoutes.length === 0 ? (
              <div className="p-8 text-center text-xs text-[#c28e95]">
                {routes.length === 0
                  ? 'No Solana route datasets returned by /api/violet/solana.'
                  : 'No Solana routes match your active filter.'}
              </div>
            ) : (
              <>
                {/* Desktop / Tablet Table */}
                <div className="hidden md:block overflow-x-auto">
                  <table className="w-full text-left border-collapse text-xs font-mono">
                    <thead>
                      <tr className="border-b border-[#ff3555]/20 bg-[#120508] text-[11px] text-[#d4a5ac]">
                        <th className="py-3 px-4">
                          <button
                            type="button"
                            onClick={() => toggleSort('dataset')}
                            className="inline-flex items-center gap-1 hover:text-[#ffe3e5]"
                          >
                            <span>Route Dataset</span>
                            <ArrowUpDown className="w-3 h-3 text-[#ff6375]" />
                          </button>
                        </th>
                        <th className="py-3 px-3 text-right">
                          <button
                            type="button"
                            onClick={() => toggleSort('observations')}
                            className="inline-flex items-center gap-1 hover:text-[#ffe3e5]"
                          >
                            <span>Quote Observations</span>
                            <ArrowUpDown className="w-3 h-3 text-[#ff6375]" />
                          </button>
                        </th>
                        <th className="py-3 px-3 text-right">
                          <button
                            type="button"
                            onClick={() => toggleSort('net_lamports')}
                            className="inline-flex items-center gap-1 hover:text-[#ffe3e5]"
                          >
                            <span>Hypothetical Net (Lamports)</span>
                            <ArrowUpDown className="w-3 h-3 text-[#ff6375]" />
                          </button>
                        </th>
                        <th className="py-3 px-4 text-right">Freshness</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#ff3555]/15">
                      {filteredRoutes.map((route: SolanaRouteObservation) => (
                        <tr key={route.dataset} className="hover:bg-[#ff3555]/5 transition-colors">
                          <td className="py-3 px-4 text-[#ffe3e5] font-semibold">{route.dataset}</td>
                          <td className="py-3 px-3 text-right text-[#f3c6cc]">
                            {route.observations !== null ? route.observations.toLocaleString('en-US') : 'Unavailable'}
                          </td>
                          <td
                            className={`py-3 px-3 text-right font-semibold ${
                              route.mean_conservative_net_lamports === null
                                ? 'text-[#c28e95]'
                                : route.mean_conservative_net_lamports >= 0
                                  ? 'text-[#10b981]'
                                  : 'text-[#ff6375]'
                            }`}
                          >
                            {formatLamports(route.mean_conservative_net_lamports)}
                          </td>
                          <td className="py-3 px-4 text-right">
                            <span
                              className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                                route.stale
                                  ? 'bg-[#ffb16c]/15 text-[#ffb16c]'
                                  : 'bg-[#10b981]/15 text-[#10b981]'
                              }`}
                            >
                              {route.stale ? 'STALE' : 'FRESH'}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Mobile Cards */}
                <div className="md:hidden divide-y divide-[#ff3555]/15">
                  {filteredRoutes.map((route: SolanaRouteObservation) => (
                    <div key={route.dataset} className="p-4 space-y-2 text-xs font-mono">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-[#ffe3e5] break-all">{route.dataset}</span>
                        <span
                          className={`px-2 py-0.5 rounded text-[10px] font-semibold shrink-0 ${
                            route.stale
                              ? 'bg-[#ffb16c]/15 text-[#ffb16c]'
                              : 'bg-[#10b981]/15 text-[#10b981]'
                          }`}
                        >
                          {route.stale ? 'STALE' : 'FRESH'}
                        </span>
                      </div>
                      <div className="flex items-center justify-between text-[#d4a5ac]">
                        <span>Quotes: {route.observations !== null ? route.observations.toLocaleString('en-US') : 'Unavailable'}</span>
                        <span
                          className={
                            route.mean_conservative_net_lamports === null
                              ? 'text-[#c28e95]'
                              : route.mean_conservative_net_lamports >= 0
                                ? 'text-[#10b981]'
                                : 'text-[#ff6375]'
                          }
                        >
                          {formatLamports(route.mean_conservative_net_lamports)}
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </section>
        </>
      )}
    </div>
  );
};

export const SolanaResearchView: React.FC<SolanaResearchViewProps> = ({ initData }) => {
  const [research, setResearch] = useState<VioletResearchPayload | null>(null);
  const [solana, setSolana] = useState<SolanaSummaryPayload | null>(null);
  const [fetchedAtMs, setFetchedAtMs] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState<number>(() => Date.now());
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);

  const loadResearch = useCallback(
    async (isBackground = false) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      if (!isBackground) setRefreshing(true);
      setAuthError(null);
      setFetchError(null);

      try {
        const [researchRes, solanaRes] = await Promise.allSettled([
          fetchVioletResearch(initData, controller.signal),
          fetchVioletSolana(initData, controller.signal)
        ]);

        if (controller.signal.aborted) return;

        const errors: string[] = [];
        let foundAuthError: TelegramAuthError | null = null;

        if (researchRes.status === 'fulfilled') {
          setResearch(researchRes.value);
        } else {
          const reason = researchRes.reason;
          if (reason instanceof DOMException && reason.name === 'AbortError') return;
          if (reason instanceof TelegramAuthError) foundAuthError = reason;
          else errors.push(reason instanceof Error ? reason.message : 'Research endpoint failed');
        }

        if (solanaRes.status === 'fulfilled') {
          setSolana(solanaRes.value);
        } else {
          const reason = solanaRes.reason;
          if (reason instanceof DOMException && reason.name === 'AbortError') return;
          if (reason instanceof TelegramAuthError) foundAuthError = reason;
          else errors.push(reason instanceof Error ? reason.message : 'Solana endpoint failed');
        }

        if (foundAuthError) {
          setAuthError(foundAuthError.message);
        } else {
          if (researchRes.status === 'fulfilled' || solanaRes.status === 'fulfilled') {
            setFetchedAtMs(Date.now());
          }
          if (errors.length > 0) {
            setFetchError(errors.join(' · '));
          }
        }
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [initData]
  );

  useEffect(() => {
    void loadResearch(false);

    const pollTimer = setInterval(() => {
      if (document.visibilityState === 'visible') {
        void loadResearch(true);
      }
    }, POLL_INTERVAL_MS);

    const ageTimer = setInterval(() => {
      setNowMs(Date.now());
    }, 1000);

    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        void loadResearch(true);
      }
    };

    document.addEventListener('visibilitychange', handleVisibility);
    return () => {
      clearInterval(pollTimer);
      clearInterval(ageTimer);
      document.removeEventListener('visibilitychange', handleVisibility);
      abortRef.current?.abort();
    };
  }, [loadResearch]);

  return (
    <SolanaResearchContent
      research={research}
      solana={solana}
      fetchedAtMs={fetchedAtMs}
      nowMs={nowMs}
      loading={loading}
      refreshing={refreshing}
      authError={authError}
      fetchError={fetchError}
      onRefresh={() => void loadResearch(false)}
    />
  );
};
