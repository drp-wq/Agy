import { TradeSymbolLink } from '../charts/TradeSymbolLink';
import React, { useEffect, useState } from 'react';
import {
  ArrowLeftRight,
  ArrowUpRight,
  Bell,
  BrainCircuit,
  Cpu,
  Landmark,
  Server,
  ShieldAlert,
  ShieldCheck,
  Activity,
  TrendingUp
} from 'lucide-react';
import {
  ExchangeId,
  FuturesPosition,
  MarketType,
  MiniAppStateResponse,
  NavSectionId,
  Timeframe
} from '../../types/quant';
import { EXCHANGE_META, SectionHeader } from '../common/TerminalPrimitives';
import { ProMarketChart } from '../charts/ProMarketChart';
import { formatAgeSeconds, formatPrice, formatSignedPct, formatSignedUsd, formatUsd } from '../../utils/formatters';
import {
  formatExchangeRealizedPnl,
  summarizeDrawdown,
  summarizeExposureAndAllocation,
  summarizeLiveReadOnlyAuth,
  summarizeRealizedUnrealized
} from '../../utils/telemetryMetrics';
import {
  fetchPersistenceHealth,
  fetchUnifiedAccount,
  fetchUnifiedBrainStatus,
  fetchVioletResearch,
  fetchVioletSolana,
  PersistenceHealthPayload,
  SolanaSummaryPayload,
  UnifiedAccountPayload,
  UnifiedBrainStatusPayload,
  VioletResearchPayload
} from '../../services/violetResearchApi';
import { VioletCampaignPanel } from './VioletCampaignPanel';

interface GlobalOverviewViewProps {
  state: MiniAppStateResponse;
  initData?: string;
  onNavigate: (section: NavSectionId) => void;
  onInspectPosition: (pos: FuturesPosition) => void;
}

export const GlobalOverviewView: React.FC<GlobalOverviewViewProps> = ({
  state,
  initData,
  onNavigate,
  onInspectPosition
}) => {
  const [chartExchange, setChartExchange] = useState<ExchangeId>('binance');
  const [chartMarket, setChartMarket] = useState<MarketType>('futures');
  const [chartSymbol, setChartSymbol] = useState<string>('BTC/USDT');
  const [chartTf, setChartTf] = useState<Timeframe>('15m');

  const [brainStatus, setBrainStatus] = useState<UnifiedBrainStatusPayload | null>(null);
  const [unifiedAccount, setUnifiedAccount] = useState<UnifiedAccountPayload | null>(null);
  const [solanaSummary, setSolanaSummary] = useState<SolanaSummaryPayload | null>(null);
  const [persistenceHealth, setPersistenceHealth] = useState<PersistenceHealthPayload | null>(null);
  const [violetResearch, setVioletResearch] = useState<VioletResearchPayload | null>(null);
  const [violetError, setVioletError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.allSettled([
      fetchUnifiedBrainStatus(initData, controller.signal),
      fetchUnifiedAccount(initData, controller.signal),
      fetchVioletSolana(initData, controller.signal),
      fetchPersistenceHealth(initData, controller.signal),
      fetchVioletResearch(initData, controller.signal)
    ]).then(([brainRes, acctRes, solRes, persRes, violetRes]) => {
      if (controller.signal.aborted) return;
      if (brainRes.status === 'fulfilled') setBrainStatus(brainRes.value);
      if (acctRes.status === 'fulfilled') setUnifiedAccount(acctRes.value);
      if (solRes.status === 'fulfilled') setSolanaSummary(solRes.value);
      if (persRes.status === 'fulfilled') setPersistenceHealth(persRes.value);
      if (violetRes.status === 'fulfilled') {
        setVioletResearch(violetRes.value);
        setVioletError(null);
      } else {
        setVioletError(violetRes.reason instanceof Error ? violetRes.reason.message : 'VIOLET telemetry unavailable');
      }
    });
    return () => controller.abort();
  }, [initData, state.generated_at]);

  const openFutures = state.futures_positions.filter((p) => p.status === 'open');
  const matchingPosition = openFutures.find(
    (p) => p.exchange === chartExchange && p.symbol === chartSymbol
  ) || null;

  const approvedOpportunities = state.opportunities.filter(
    (o) => o.estimates.screening_decision === 'APPROVED'
  );
  const pnlSummary = summarizeRealizedUnrealized(state.risk);
  const ddSummary = summarizeDrawdown(state.risk);
  const exposureSummary = summarizeExposureAndAllocation(state.risk);
  const liveAuthSummary = summarizeLiveReadOnlyAuth(state);

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Global Multi-Exchange Overview"
        subtitle={`Paper-trading telemetry across Binance, Bybit, and OKX · Updated ${formatAgeSeconds(state.generated_at)}`}
        actions={
          <div className="flex items-center gap-2 text-xs font-mono text-[#10B981]">
            <ShieldCheck className="w-4 h-4" />
            <span>PAPER ENGINE ACTIVE</span>
          </div>
        }
      />

      {/* Active VIOLET 500 USDT All-Cap Paper Campaign (Isolated from Legacy & Closed-Loop Router) */}
      <VioletCampaignPanel research={violetResearch} error={violetError} />

      {/* Legacy Preview Protective Ledger Header */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-display text-xs uppercase tracking-[0.18em] text-[#c28e95]">
          Legacy Preview Protective Ledger (Separate from 500 USDT VIOLET Campaign)
        </h3>
        <span className="text-[11px] font-mono text-[#a1737a]">
          Protective exits only · No new legacy entries
        </span>
      </div>

      {/* Top KPI Strip (Single Elevation, Hairline Separated) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Legacy Preview Paper Equity</div>
          <div className="text-2xl font-mono font-bold text-white mt-1">
            {formatUsd(state.risk.total_paper_equity_usd)}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1.5" title={liveAuthSummary.detail}>
            Live Read-Only: <span className={liveAuthSummary.authedCount > 0 ? 'text-[#10B981]' : 'text-amber-400'}>{liveAuthSummary.headline}</span>
          </div>
          <div className="text-[10px] font-mono text-slate-500 mt-0.5 truncate" title={liveAuthSummary.detail}>
            {liveAuthSummary.detail}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">{pnlSummary.title}</div>
          <div
            className={`text-2xl font-mono font-bold mt-1 ${
              pnlSummary.primaryUsd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'
            }`}
          >
            {formatSignedUsd(pnlSummary.primaryUsd)}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1.5">
            {pnlSummary.realizedLabel}:{' '}
            {pnlSummary.realizedUsd !== null ? formatSignedUsd(pnlSummary.realizedUsd) : 'Unavailable'} · Open{' '}
            {formatSignedUsd(pnlSummary.unrealizedUsd)}
          </div>
          {pnlSummary.isDailyAvailable && pnlSummary.lifetimeRealizedUsd !== null && (
            <div className="text-[10px] font-mono text-slate-500 mt-0.5">
              Lifetime Realized: {formatSignedUsd(pnlSummary.lifetimeRealizedUsd)}
            </div>
          )}
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-[#d4a5ac]">Margin Reserved / Notional</div>
          <div className="text-2xl font-mono font-bold text-[#ff6375] mt-1">
            {formatUsd(exposureSummary.reservedMarginUsd)}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1.5">
            Notional {formatUsd(exposureSummary.grossNotionalUsd)} ({exposureSummary.notionalExposurePct.toFixed(1)}%)
          </div>
          <div className="text-[10px] font-mono text-slate-500 mt-0.5">
            Margin Alloc {exposureSummary.marginAllocationPct.toFixed(1)}% · Free {formatUsd(exposureSummary.freeCapitalUsd)} · {openFutures.length} Open
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Drawdown & Circuit Breaker</div>
          <div className={`text-2xl font-mono font-bold mt-1 ${ddSummary.available ? 'text-white' : 'text-amber-300'}`}>
            {ddSummary.displayValue}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1.5">
            {ddSummary.subtitle}
          </div>
          <div className={`text-[10px] font-mono mt-0.5 ${state.risk.emergency_stop_active ? 'text-[#EF4444]' : 'text-[#10B981]'}`}>
            {state.risk.circuit_breaker_state}
          </div>
        </div>
      </div>

      {/* Exchange Attribution Row (Strictly Independent Accounts) */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {(['binance', 'bybit', 'okx'] as ExchangeId[]).map((exId) => {
          const ex = state.exchanges[exId];
          const meta = EXCHANGE_META[exId];
          const exRealizedFmt = formatExchangeRealizedPnl(ex.realized_pnl_24h, ex.lifetime_realized_pnl);
          const exNetDisplay =
            ex.realized_pnl_24h !== null
              ? formatSignedUsd(ex.realized_pnl_24h + ex.unrealized_pnl)
              : formatSignedUsd(ex.unrealized_pnl);
          const exNetPositive =
            ex.realized_pnl_24h !== null
              ? ex.realized_pnl_24h + ex.unrealized_pnl >= 0
              : ex.unrealized_pnl >= 0;
          return (
            <div key={exId} className="nq-panel nq-panel-interactive rounded-xl p-4 flex flex-col justify-between">
              <div className="flex items-center justify-between">
                <button
                  onClick={() => onNavigate(exId)}
                  className={`font-display font-semibold text-base ${meta.accentText} hover:underline flex items-center gap-1`}
                >
                  <span>{meta.name} Workspace</span>
                  <ArrowUpRight className="w-3.5 h-3.5" />
                </button>
                <span className="text-[11px] font-mono text-slate-400">
                  {ex.public_feed_ok ? 'Public Feed OK' : 'Feed Offline'}
                </span>
              </div>

              <div className="mt-3 flex items-baseline justify-between gap-2">
                <div>
                  <div className="text-[11px] text-slate-400">Paper Equity</div>
                  <div className="text-lg font-mono font-bold text-white">{formatUsd(ex.paper_equity_usd)}</div>
                  <div className="text-[10px] font-mono text-slate-500">
                    Margin {formatUsd(ex.locked_margin_usd)} · Free {formatUsd(ex.available_margin_usd)}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-[11px] text-slate-400">
                    {ex.realized_pnl_24h !== null ? '24h Realized + Open' : 'Open Unrealized'}
                  </div>
                  <div
                    className={`text-sm font-mono font-semibold ${
                      exNetPositive ? 'text-[#10B981]' : 'text-[#EF4444]'
                    }`}
                  >
                    {exNetDisplay}
                  </div>
                  <div className="text-[10px] font-mono text-slate-500">
                    {exRealizedFmt.caption}: {exRealizedFmt.text}
                  </div>
                </div>
              </div>

              <div className="mt-3 pt-2.5 border-t border-white/[0.06] flex items-center justify-between text-[11px] font-mono text-slate-400">
                <span>
                  Auth:{' '}
                  <strong className={ex.private_auth_ok ? 'text-[#10B981]' : 'text-amber-400'}>
                    {ex.private_auth_ok ? 'Private Synced' : 'Paper Only (Private Unlinked)'}
                  </strong>
                </span>
                <span>{formatAgeSeconds(ex.last_sync_ts)}</span>
              </div>
            </div>
          );
        })}
      </div>

      {/* VIOLET Scarlet Command Hub — Dedicated Navigation & Real-Status Tiles */}
      <div className="space-y-2.5">
        <div className="flex items-center justify-between">
          <h3 className="font-display text-xs uppercase tracking-[0.18em] text-[#ff6375]">
            VIOLET Scarlet Command Modules
          </h3>
          <span className="text-[11px] font-mono text-[#c28e95]">
            Read-only telemetry · Tap any tile to open workspace
          </span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          <button
            type="button"
            onClick={() => onNavigate('brain')}
            className="nq-panel nq-panel-interactive rounded-xl p-3.5 text-left space-y-1.5"
          >
            <div className="flex items-center justify-between text-xs font-medium text-[#ffe3e5]">
              <span className="flex items-center gap-1.5">
                <BrainCircuit className="w-4 h-4 text-[#ff6375]" />
                <span>Unified AI Brain</span>
              </span>
              <ArrowUpRight className="w-3.5 h-3.5 text-[#c28e95]" />
            </div>
            <div className="text-sm font-mono font-bold text-[#ffe3e5]">
              {brainStatus ? brainStatus.mode : 'Unavailable'}
            </div>
            <div className="text-[11px] font-mono text-[#c28e95] truncate">
              {brainStatus
                ? `${brainStatus.agents.join(' + ')} · ${brainStatus.last_report ? (brainStatus.last_report.skipped_reasons[0] || 'Cycle OK') : 'No cycle yet'}`
                : 'Awaiting /api/violet/brain/status'}
            </div>
          </button>

          <button
            type="button"
            onClick={() => onNavigate('brain')}
            className="nq-panel nq-panel-interactive rounded-xl p-3.5 text-left space-y-1.5"
          >
            <div className="flex items-center justify-between text-xs font-medium text-[#ffe3e5]">
              <span className="flex items-center gap-1.5">
                <ArrowLeftRight className="w-4 h-4 text-[#ff6375]" />
                <span>Closed-Loop Router</span>
              </span>
              <ArrowUpRight className="w-3.5 h-3.5 text-[#c28e95]" />
            </div>
            <div className="text-sm font-mono font-bold text-[#10B981]">
              {unifiedAccount
                ? `$${Number(unifiedAccount.invariant.current_venue_total_usd).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                : 'Unavailable'}
            </div>
            <div className="text-[11px] font-mono text-[#c28e95]">
              {unifiedAccount
                ? `${unifiedAccount.invariant.conservation_ok ? 'Separate $10k Sim' : 'Drift Detected'} · ${Object.keys(unifiedAccount.balances_usd).length} venues`
                : 'Closed-loop router status'}
            </div>
          </button>

          <button
            type="button"
            onClick={() => onNavigate('treasury')}
            className="nq-panel nq-panel-interactive rounded-xl p-3.5 text-left space-y-1.5"
          >
            <div className="flex items-center justify-between text-xs font-medium text-[#ffe3e5]">
              <span className="flex items-center gap-1.5">
                <Landmark className="w-4 h-4 text-[#ff6375]" />
                <span>18-Agent Treasury</span>
              </span>
              <ArrowUpRight className="w-3.5 h-3.5 text-[#c28e95]" />
            </div>
            <div className="text-sm font-mono font-bold text-[#ffe3e5]">
              {state.unified_treasury?.enabled
                ? `${state.unified_treasury.active_slots ?? 0}/${state.unified_treasury.max_agent_slots ?? 18} Active`
                : 'Disabled on Preview'}
            </div>
            <div className="text-[11px] font-mono text-[#c28e95]">
              {state.unified_treasury?.enabled
                ? state.treasury_reconciliation?.balanced
                  ? 'Master Pool Balanced'
                  : 'Audit Check Needed'
                : 'Separate from 500 USDT VIOLET'}
            </div>
          </button>

          <button
            type="button"
            onClick={() => onNavigate('solana')}
            className="nq-panel nq-panel-interactive rounded-xl p-3.5 text-left space-y-1.5"
          >
            <div className="flex items-center justify-between text-xs font-medium text-[#ffe3e5]">
              <span className="flex items-center gap-1.5">
                <Cpu className="w-4 h-4 text-[#ff6375]" />
                <span>Solana Research</span>
              </span>
              <ArrowUpRight className="w-3.5 h-3.5 text-[#c28e95]" />
            </div>
            <div
              className={`text-sm font-mono font-bold ${
                solanaSummary?.status === 'healthy' ? 'text-[#10B981]' : 'text-[#ffb16c]'
              }`}
            >
              {solanaSummary ? solanaSummary.status.toUpperCase() : 'Unavailable'}
            </div>
            <div className="text-[11px] font-mono text-[#c28e95] truncate">
              {solanaSummary
                ? `${solanaSummary.verdict} · ${solanaSummary.observations ?? 0} obs`
                : 'Isolated Solana paper ledger'}
            </div>
          </button>

          <button
            type="button"
            onClick={() => onNavigate('arbitrage')}
            className="nq-panel nq-panel-interactive rounded-xl p-3.5 text-left space-y-1.5"
          >
            <div className="flex items-center justify-between text-xs font-medium text-[#ffe3e5]">
              <span className="flex items-center gap-1.5">
                <ArrowLeftRight className="w-4 h-4 text-[#ff6375]" />
                <span>Arbitrage Lab</span>
              </span>
              <ArrowUpRight className="w-3.5 h-3.5 text-[#c28e95]" />
            </div>
            <div className="text-sm font-mono font-bold text-[#ffe3e5]">READ-ONLY LAB</div>
            <div className="text-[11px] font-mono text-[#c28e95]">
              Public books · BTC / ETH / SOL
            </div>
          </button>

          <button
            type="button"
            onClick={() => onNavigate('risk')}
            className="nq-panel nq-panel-interactive rounded-xl p-3.5 text-left space-y-1.5"
          >
            <div className="flex items-center justify-between text-xs font-medium text-[#ffe3e5]">
              <span className="flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4 text-[#ff6375]" />
                <span>Risk Command</span>
              </span>
              <ArrowUpRight className="w-3.5 h-3.5 text-[#c28e95]" />
            </div>
            <div
              className={`text-sm font-mono font-bold ${
                state.risk.emergency_stop_active ? 'text-[#EF4444]' : 'text-[#10B981]'
              }`}
            >
              {state.risk.emergency_stop_active ? 'EMERGENCY STOP' : state.risk.circuit_breaker_state}
            </div>
            <div className="text-[11px] font-mono text-[#c28e95]">
              Drawdown limit {state.risk.drawdown_limit_pct}%
            </div>
          </button>

          <button
            type="button"
            onClick={() => onNavigate('alerts')}
            className="nq-panel nq-panel-interactive rounded-xl p-3.5 text-left space-y-1.5"
          >
            <div className="flex items-center justify-between text-xs font-medium text-[#ffe3e5]">
              <span className="flex items-center gap-1.5">
                <Bell className="w-4 h-4 text-[#ff6375]" />
                <span>Alerts Stream</span>
              </span>
              <ArrowUpRight className="w-3.5 h-3.5 text-[#c28e95]" />
            </div>
            <div className="text-sm font-mono font-bold text-[#ffe3e5]">
              {state.alerts.length} Active Alerts
            </div>
            <div className="text-[11px] font-mono text-[#c28e95]">
              {state.telegram_owner.authenticated ? 'Owner Verified' : 'Preview Session'}
            </div>
          </button>

          <button
            type="button"
            onClick={() => onNavigate('system')}
            className="nq-panel nq-panel-interactive rounded-xl p-3.5 text-left space-y-1.5"
          >
            <div className="flex items-center justify-between text-xs font-medium text-[#ffe3e5]">
              <span className="flex items-center gap-1.5">
                <Server className="w-4 h-4 text-[#ff6375]" />
                <span>Service Health</span>
              </span>
              <ArrowUpRight className="w-3.5 h-3.5 text-[#c28e95]" />
            </div>
            <div
              className={`text-sm font-mono font-bold ${
                persistenceHealth?.status === 'ok' ? 'text-[#10B981]' : 'text-[#ffb16c]'
              }`}
            >
              {persistenceHealth ? persistenceHealth.status.toUpperCase() : 'UNAVAILABLE'}
            </div>
            <div className="text-[11px] font-mono text-[#c28e95]">
              {persistenceHealth
                ? `SQLite ${persistenceHealth.sqlite.status} · Sched ${persistenceHealth.scheduler.status}`
                : 'Check /api/violet/persistence/health'}
            </div>
          </button>
        </div>
      </div>

      {/* Primary Interactive TradingView Lightweight Chart */}
      <ProMarketChart
        exchange={chartExchange}
        marketType={chartMarket}
        symbol={chartSymbol}
        timeframe={chartTf}
        onExchangeChange={setChartExchange}
        onMarketTypeChange={setChartMarket}
        onSymbolChange={setChartSymbol}
        onTimeframeChange={setChartTf}
        activePosition={matchingPosition}
        initData={initData}
      />

      {/* Bottom 2-Column Split: Open Paper Futures & Approved Signals */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-stretch">
        {/* Open Paper Futures Snapshot */}
        <div className="nq-panel rounded-xl p-4 flex flex-col">
          <div className="flex items-center justify-between pb-3 border-b border-[#ff3555]/20">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-[#ff6375]" />
              <h3 className="font-display font-semibold text-sm text-[#ffe3e5]">Open Paper Futures Positions</h3>
            </div>
            <button
              type="button"
              onClick={() => onNavigate('positions')}
              className="text-xs text-[#ff6375] hover:underline whitespace-nowrap shrink-0"
            >
              Open Positions Terminal →
            </button>
          </div>

          <div className="divide-y divide-white/[0.06] mt-1">
            {openFutures.length === 0 ? (
              <div className="py-6 text-center text-xs text-slate-400">
                No open paper futures positions across Binance, Bybit, or OKX.
              </div>
            ) : (
              openFutures.map((pos) => (
              <button
                key={pos.id}
                onClick={() => onInspectPosition(pos)}
                className="w-full py-3 flex items-center justify-between text-left hover:bg-white/[0.02] transition-colors px-1 rounded"
              >
                <div>
                  <div className="flex items-center gap-2 text-xs font-mono">
                    <span className={`font-semibold uppercase ${EXCHANGE_META[pos.exchange].accentText}`}>
                      {pos.exchange}
                    </span>
                    <TradeSymbolLink exchange={pos.exchange} marketType="futures" symbol={pos.symbol} />
                    <span className={pos.side === 'LONG' ? 'text-[#10B981]' : 'text-[#EF4444]'}>
                      {pos.side} {pos.leverage}x
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-slate-400 mt-0.5">
                    Entry {formatPrice(pos.entry_price)} · Mark {formatPrice(pos.mark_price)} · Notional {formatUsd(pos.notional_usd)}
                  </div>
                </div>
                <div className="text-right font-mono">
                  <div className={`text-xs font-semibold ${pos.unrealized_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                    {formatSignedUsd(pos.unrealized_pnl_usd)}
                  </div>
                  <div className={`text-[11px] ${pos.unrealized_pnl_pct >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                    {formatSignedPct(pos.unrealized_pnl_pct)}
                  </div>
                </div>
              </button>
              ))
            )}
          </div>
        </div>

        {/* Approved Strategy Opportunities */}
        <div className="nq-panel rounded-xl p-4 flex flex-col">
          <div className="flex items-center justify-between pb-3 border-b border-[#ff3555]/20">
            <div className="flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-[#10B981]" />
              <h3 className="font-display font-semibold text-sm text-[#ffe3e5]">Approved Opportunity Setups</h3>
            </div>
            <button
              type="button"
              onClick={() => onNavigate('opportunities')}
              className="text-xs text-[#ff6375] hover:underline whitespace-nowrap shrink-0"
            >
              View All Screening →
            </button>
          </div>

          <div className="divide-y divide-[#ff3555]/15 mt-1">
            {approvedOpportunities.length === 0 ? (
              <div className="py-6 text-center text-xs text-[#d4a5ac]">
                No APPROVED setups in the current scanner cycle. Inspect Screening for watchlist candidates.
              </div>
            ) : (
              approvedOpportunities.map((opp) => (
              <div key={opp.id} className="py-3 flex items-center justify-between gap-3 px-1">
                <div>
                  <div className="flex items-center gap-2 text-xs font-mono">
                    <span className={`font-semibold uppercase ${EXCHANGE_META[opp.exchange].accentText}`}>
                      {opp.exchange}
                    </span>
                    <TradeSymbolLink exchange={opp.exchange} marketType={opp.market_type} symbol={opp.symbol} />
                    <span className="text-[#d4a5ac]">{opp.confirmed.regime.replace(/_/g, ' ')}</span>
                  </div>
                  <div className="text-[11px] font-mono text-[#d4a5ac] mt-0.5">
                    {opp.estimates.strategy_name} · Est. R:R {opp.estimates.risk_reward_ratio.toFixed(2)} · Conf {opp.estimates.confidence_pct}%
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    setChartExchange(opp.exchange);
                    setChartMarket(opp.market_type);
                    setChartSymbol(opp.symbol);
                  }}
                  className="px-3 py-1.5 min-h-[34px] rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/40 text-[#ffe3e5] text-xs font-medium whitespace-nowrap shrink-0 transition-colors"
                >
                  Load Chart
                </button>
              </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
