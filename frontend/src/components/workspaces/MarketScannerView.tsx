import React, { useState } from 'react';
import { Search, ArrowRightLeft } from 'lucide-react';
import { ExchangeId, MarketOpportunity, MiniAppStateResponse } from '../../types/quant';
import { EXCHANGE_META, EmptyStatePanel, SectionHeader } from '../common/TerminalPrimitives';
import { formatPrice, formatSignedPct, formatUsd } from '../../utils/formatters';

interface MarketScannerViewProps {
  state: MiniAppStateResponse;
  onInspectOpportunityChart: (opp: MarketOpportunity) => void;
}

export const MarketScannerView: React.FC<MarketScannerViewProps> = ({
  state,
  onInspectOpportunityChart
}) => {
  const [activeTab, setActiveTab] = useState<'scanner' | 'arbitrage'>('scanner');
  const [exchangeFilter, setExchangeFilter] = useState<'all' | ExchangeId>('all');
  const [search, setSearch] = useState('');

  const filtered = state.opportunities.filter((item) => {
    const matchEx = exchangeFilter === 'all' || item.exchange === exchangeFilter;
    const matchQ =
      item.symbol.toLowerCase().includes(search.toLowerCase()) ||
      item.estimates.strategy_name.toLowerCase().includes(search.toLowerCase());
    return matchEx && matchQ;
  });

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Multi-Exchange Market Scanner"
        subtitle="Confirmed real-time market microstructure cleanly separated from strategy model projections"
        actions={
          <div className="flex items-center p-1 rounded-lg bg-[#0B1120] border border-white/[0.1]">
            <button
              onClick={() => setActiveTab('scanner')}
              className={`px-3 py-1.5 min-h-[36px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                activeTab === 'scanner' ? 'bg-[#00E5FF]/20 text-[#00E5FF]' : 'text-slate-400 hover:text-white'
              }`}
            >
              Microstructure Scanner
            </button>
            <button
              onClick={() => setActiveTab('arbitrage')}
              className={`px-3 py-1.5 min-h-[36px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 transition-colors flex items-center gap-1.5 ${
                activeTab === 'arbitrage' ? 'bg-[#8B5CF6]/25 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              <ArrowRightLeft className="w-3.5 h-3.5" />
              <span>Cross-Exchange Spreads</span>
            </button>
          </div>
        }
      />

      {activeTab === 'scanner' ? (
        <>
          {/* Filters Bar */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center p-1 rounded-lg bg-[#0B1120] border border-white/[0.08]">
              {(['all', 'binance', 'bybit', 'okx'] as const).map((ex) => (
                <button
                  key={ex}
                  onClick={() => setExchangeFilter(ex)}
                  className={`px-3 py-1.5 min-h-[36px] rounded-md text-xs font-medium uppercase whitespace-nowrap shrink-0 transition-colors ${
                    exchangeFilter === ex
                      ? 'bg-[#00E5FF]/20 text-[#00E5FF]'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {ex}
                </button>
              ))}
            </div>

            <div className="relative w-full sm:w-64">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search pair or strategy..."
                className="w-full pl-9 pr-3 py-2 min-h-[40px] rounded-lg bg-[#0B1120] border border-white/[0.1] text-xs text-white placeholder-slate-500 focus:outline-none focus:border-[#00E5FF]"
              />
            </div>
          </div>

          {filtered.length === 0 ? (
            <EmptyStatePanel
              title="No Markets Match Active Filter"
              description="Adjust your exchange filter or search query to inspect available instruments."
              actionLabel="Reset Filters"
              onAction={() => {
                setExchangeFilter('all');
                setSearch('');
              }}
            />
          ) : (
            <div className="nq-panel rounded-xl overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-white/[0.08] bg-[#0B1120] text-[11px] font-mono text-slate-400">
                      <th className="py-3 px-4">Exchange / Pair</th>
                      <th className="py-3 px-3 text-right">Last Price</th>
                      <th className="py-3 px-3 text-right">24h %</th>
                      <th className="py-3 px-3 text-right">24h Vol</th>
                      <th className="py-3 px-3 text-right">Spread / Depth(1%)</th>
                      <th className="py-3 px-3 text-right">Vol / Mom</th>
                      <th className="py-3 px-3">Confirmed Regime</th>
                      <th className="py-3 px-3 border-l border-white/[0.08] text-[#8B5CF6]">Est. Signal & Conf</th>
                      <th className="py-3 px-3 text-right text-[#8B5CF6]">Est. Fee/Slip</th>
                      <th className="py-3 px-4 text-right">Action</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.06] text-xs font-mono">
                    {filtered.map((item) => {
                      const c = item.confirmed;
                      const e = item.estimates;
                      const meta = EXCHANGE_META[item.exchange];
                      return (
                        <tr key={item.id} className="hover:bg-white/[0.02] transition-colors">
                          <td className="py-3 px-4">
                            <div className="flex items-center gap-2">
                              <span className={`font-semibold uppercase ${meta.accentText}`}>{item.exchange}</span>
                              <span className="text-white font-semibold">{item.symbol}</span>
                            </div>
                            <div className="text-[10px] text-slate-500 uppercase">{item.market_type}</div>
                          </td>
                          <td className="py-3 px-3 text-right text-white font-medium">
                            {formatPrice(c.last_price)}
                          </td>
                          <td
                            className={`py-3 px-3 text-right font-semibold ${
                              c.change_24h_pct >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'
                            }`}
                          >
                            {formatSignedPct(c.change_24h_pct)}
                          </td>
                          <td className="py-3 px-3 text-right text-slate-300">
                            {formatUsd(c.volume_24h_usd, true)}
                          </td>
                          <td className="py-3 px-3 text-right">
                            <div className="text-slate-200">{c.spread_bps.toFixed(2)} bps</div>
                            <div className="text-[10px] text-slate-500">
                              {formatUsd(c.bid_depth_1pct_usd + c.ask_depth_1pct_usd, true)}
                            </div>
                          </td>
                          <td className="py-3 px-3 text-right">
                            <div className="text-slate-200">{c.realized_volatility_pct.toFixed(2)}%</div>
                            <div className="text-[10px] text-[#00E5FF]">Mom {c.momentum_score.toFixed(2)}</div>
                          </td>
                          <td className="py-3 px-3 text-[11px] text-slate-300">
                            {c.regime.replace(/_/g, ' ')}
                          </td>
                          <td className="py-3 px-3 border-l border-white/[0.08]">
                            <div
                              className={`font-semibold ${
                                e.direction === 'LONG'
                                  ? 'text-[#10B981]'
                                  : e.direction === 'SHORT'
                                  ? 'text-[#EF4444]'
                                  : 'text-slate-400'
                              }`}
                            >
                              {e.direction} · {e.confidence_pct.toFixed(0)}%
                            </div>
                            <div className="text-[10px] text-slate-400 truncate max-w-[140px]">
                              {e.strategy_name}
                            </div>
                          </td>
                          <td className="py-3 px-3 text-right text-slate-300">
                            <div>{(e.est_fees_bps + e.est_slippage_bps).toFixed(1)} bps</div>
                            <div className="text-[10px] text-slate-500">R:R {e.risk_reward_ratio.toFixed(2)}</div>
                          </td>
                          <td className="py-3 px-4 text-right">
                            <button
                              onClick={() => onInspectOpportunityChart(item)}
                              className="px-2.5 py-1.5 min-h-[32px] rounded bg-[#00E5FF]/10 hover:bg-[#00E5FF]/20 border border-[#00E5FF]/30 text-[#00E5FF] text-[11px] font-sans font-medium whitespace-nowrap"
                            >
                              Chart
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </>
      ) : (
        /* Cross-Exchange Arbitrage Comparison Table */
        <div className="nq-panel rounded-xl overflow-hidden">
          <div className="px-4 py-3 border-b border-white/[0.08] flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold text-white">Cross-Exchange Price & Spread Comparison</h3>
              <p className="text-xs text-slate-400">
                Real-time mark dispersion across Binance, Bybit, and OKX minus estimated taker execution cost.
              </p>
            </div>
            <span className="text-[11px] font-mono text-slate-400">Paper Arbitrage Observation Only</span>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs font-mono">
              <thead>
                <tr className="border-b border-white/[0.08] bg-[#0B1120] text-[11px] text-slate-400">
                  <th className="py-3 px-4">Pair</th>
                  <th className="py-3 px-3 text-right text-[#F0B90B]">Binance</th>
                  <th className="py-3 px-3 text-right text-[#00E5FF]">Bybit</th>
                  <th className="py-3 px-3 text-right text-[#8B5CF6]">OKX</th>
                  <th className="py-3 px-3 text-right">Gross Spread</th>
                  <th className="py-3 px-3 text-right">Est. Net Arb</th>
                  <th className="py-3 px-4">Assessment</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {state.cross_exchange_spreads.map((row) => (
                  <tr key={row.symbol} className="hover:bg-white/[0.02]">
                    <td className="py-3 px-4 font-semibold text-white">{row.symbol}</td>
                    <td className="py-3 px-3 text-right text-slate-200">{formatPrice(row.binance_price)}</td>
                    <td className="py-3 px-3 text-right text-slate-200">{formatPrice(row.bybit_price)}</td>
                    <td className="py-3 px-3 text-right text-slate-200">{formatPrice(row.okx_price)}</td>
                    <td className="py-3 px-3 text-right text-white">{row.max_spread_bps.toFixed(2)} bps</td>
                    <td
                      className={`py-3 px-3 text-right font-semibold ${
                        row.est_net_arb_bps > 0 ? 'text-[#10B981]' : 'text-[#EF4444]'
                      }`}
                    >
                      {row.est_net_arb_bps > 0 ? '+' : ''}
                      {row.est_net_arb_bps.toFixed(2)} bps
                    </td>
                    <td className="py-3 px-4 font-sans text-slate-300">
                      <span className="text-[11px]">
                        Buy <strong className="uppercase">{row.best_buy_exchange}</strong> / Sell{' '}
                        <strong className="uppercase">{row.best_sell_exchange}</strong> · {row.executable_note}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
