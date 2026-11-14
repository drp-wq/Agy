import React, { useState } from 'react';
import { ArrowUpRight, ShieldCheck, Activity, TrendingUp } from 'lucide-react';
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

      {/* Top KPI Strip (Single Elevation, Hairline Separated) */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Aggregate Paper Equity</div>
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
                    <span className="text-white font-semibold">{pos.symbol}</span>
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
                    <span className="text-[#ffe3e5] font-semibold">{opp.symbol}</span>
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
