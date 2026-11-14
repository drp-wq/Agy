import React, { useState, useMemo } from 'react';
import { Search, ArrowUpDown } from 'lucide-react';
import {
  AccountMode,
  ExchangeId,
  FuturesPosition,
  MarketType,
  MiniAppStateResponse,
  PositionStatus
} from '../../types/quant';
import { EXCHANGE_META, EmptyStatePanel, SectionHeader } from '../common/TerminalPrimitives';
import { formatPrice, formatSignedPct, formatSignedUsd, formatUsd } from '../../utils/formatters';

interface PositionsTerminalViewProps {
  state: MiniAppStateResponse;
  onInspectPosition: (pos: FuturesPosition) => void;
}

type SortKey = 'notional' | 'pnl' | 'symbol';

export const PositionsTerminalView: React.FC<PositionsTerminalViewProps> = ({
  state,
  onInspectPosition
}) => {
  const [exchangeFilter, setExchangeFilter] = useState<'all' | ExchangeId>('all');
  const [accountMode, setAccountMode] = useState<AccountMode>('paper');
  const [marketType, setMarketType] = useState<MarketType>('futures');
  const [statusFilter, setStatusFilter] = useState<PositionStatus>('open');
  const [symbolSearch, setSymbolSearch] = useState('');
  const [sortBy, setSortBy] = useState<SortKey>('notional');
  const [sortAsc, setSortAsc] = useState(false);

  const visibleExchanges: ExchangeId[] =
    exchangeFilter === 'all' ? ['binance', 'bybit', 'okx'] : [exchangeFilter];

  // Filtered Futures Positions
  const filteredFutures = useMemo(() => {
    const list = state.futures_positions.filter((p) => {
      const matchEx = exchangeFilter === 'all' || p.exchange === exchangeFilter;
      const matchMode = p.account_mode === accountMode;
      const matchStatus = p.status === statusFilter;
      const matchSym = p.symbol.toLowerCase().includes(symbolSearch.toLowerCase());
      return matchEx && matchMode && matchStatus && matchSym;
    });

    return [...list].sort((a, b) => {
      let cmp = 0;
      if (sortBy === 'notional') cmp = a.notional_usd - b.notional_usd;
      else if (sortBy === 'pnl') {
        const pnlA = a.status === 'open' ? a.unrealized_pnl_usd : a.realized_pnl_usd;
        const pnlB = b.status === 'open' ? b.unrealized_pnl_usd : b.realized_pnl_usd;
        cmp = pnlA - pnlB;
      } else {
        cmp = a.symbol.localeCompare(b.symbol);
      }
      return sortAsc ? cmp : -cmp;
    });
  }, [state.futures_positions, exchangeFilter, accountMode, statusFilter, symbolSearch, sortBy, sortAsc]);

  // Filtered Spot Holdings (unleveraged inventory)
  const filteredSpot = useMemo(() => {
    return state.spot_holdings.filter((s) => {
      const matchEx = exchangeFilter === 'all' || s.exchange === exchangeFilter;
      const matchMode = s.account_mode === accountMode;
      const matchSym =
        s.asset.toLowerCase().includes(symbolSearch.toLowerCase()) ||
        s.symbol.toLowerCase().includes(symbolSearch.toLowerCase());
      return matchEx && matchMode && matchSym;
    });
  }, [state.spot_holdings, exchangeFilter, accountMode, symbolSearch]);

  const totalExposure =
    marketType === 'futures'
      ? filteredFutures.reduce((acc, p) => acc + p.notional_usd, 0)
      : filteredSpot.reduce((acc, s) => acc + s.valuation_usd, 0);

  const toggleSort = (key: SortKey) => {
    if (sortBy === key) setSortAsc(!sortAsc);
    else {
      setSortBy(key);
      setSortAsc(false);
    }
  };

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Dedicated Positions Terminal"
        subtitle="Exchange-attributed exposure · Spot inventories strictly separated from leveraged Futures contracts"
        actions={
          <div className="text-right font-mono">
            <div className="text-[11px] text-slate-400 font-sans">Filtered Exposure</div>
            <div className="text-base font-bold text-[#ff3555]">{formatUsd(totalExposure)}</div>
          </div>
        }
      />

      {/* Multi-Dimensional Filter Toolbar */}
      <div className="nq-panel rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-3 justify-between">
          {/* Exchange Selector */}
          <div className="flex items-center p-1 rounded-lg bg-[#080305] border border-white/[0.08]">
            {(['all', 'binance', 'bybit', 'okx'] as const).map((ex) => (
              <button
                key={ex}
                onClick={() => setExchangeFilter(ex)}
                className={`px-3 py-1.5 min-h-[36px] rounded-md text-xs font-medium uppercase whitespace-nowrap shrink-0 transition-colors ${
                  exchangeFilter === ex
                    ? 'bg-[#ff3555]/20 text-[#ff3555]'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {ex}
              </button>
            ))}
          </div>

          {/* Account Mode Selector (Paper vs Live Read-Only) */}
          <div className="flex items-center p-1 rounded-lg bg-[#080305] border border-white/[0.08]">
            <button
              onClick={() => setAccountMode('paper')}
              className={`px-3 py-1.5 min-h-[36px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                accountMode === 'paper'
                  ? 'bg-[#10B981]/20 text-[#10B981]'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Paper Engine
            </button>
            <button
              onClick={() => setAccountMode('live_readonly')}
              className={`px-3 py-1.5 min-h-[36px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                accountMode === 'live_readonly'
                  ? 'bg-amber-500/20 text-amber-300'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Live Read-Only
            </button>
          </div>

          {/* Market Type Selector (Spot vs Futures) */}
          <div className="flex items-center p-1 rounded-lg bg-[#080305] border border-white/[0.08]">
            <button
              onClick={() => setMarketType('futures')}
              className={`px-3 py-1.5 min-h-[36px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                marketType === 'futures'
                  ? 'bg-[#ff6375]/25 text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Futures
            </button>
            <button
              onClick={() => setMarketType('spot')}
              className={`px-3 py-1.5 min-h-[36px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                marketType === 'spot'
                  ? 'bg-[#ff6375]/25 text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              Spot Inventory
            </button>
          </div>
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-white/[0.06]">
          {/* Status Selector (relevant for Futures) */}
          {marketType === 'futures' ? (
            <div className="flex items-center p-1 rounded-lg bg-[#080305] border border-white/[0.08]">
              {(['open', 'closed', 'historical'] as PositionStatus[]).map((st) => (
                <button
                  key={st}
                  onClick={() => setStatusFilter(st)}
                  className={`px-3 py-1.5 min-h-[34px] rounded-md text-xs font-medium capitalize whitespace-nowrap shrink-0 transition-colors ${
                    statusFilter === st ? 'bg-white/15 text-white' : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {st}
                </button>
              ))}
            </div>
          ) : (
            <span className="text-xs text-slate-400">
              Showing unleveraged Spot balances (never misclassified as Futures)
            </span>
          )}

          {/* Symbol Search & Sort */}
          <div className="flex items-center gap-2 w-full sm:w-auto">
            <div className="relative flex-1 sm:w-56">
              <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={symbolSearch}
                onChange={(e) => setSymbolSearch(e.target.value)}
                placeholder="Filter symbol..."
                className="w-full pl-9 pr-3 py-1.5 min-h-[38px] rounded-lg bg-[#080305] border border-white/[0.1] text-xs text-white placeholder-slate-500 focus:outline-none focus:border-[#ff3555]"
              />
            </div>

            {marketType === 'futures' && (
              <button
                onClick={() => toggleSort(sortBy === 'notional' ? 'pnl' : sortBy === 'pnl' ? 'symbol' : 'notional')}
                className="px-3 py-1.5 min-h-[38px] rounded-lg bg-[#080305] border border-white/[0.1] text-xs text-slate-300 hover:text-white flex items-center gap-1.5 whitespace-nowrap shrink-0"
              >
                <ArrowUpDown className="w-3.5 h-3.5 text-[#ff3555]" />
                <span className="capitalize">Sort: {sortBy}</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* Live Read-Only Account Snapshots (Per-Exchange Verified Telemetry) */}
      {accountMode === 'live_readonly' && (
        (() => {
          const anyAuthed = visibleExchanges.some((exId) => Boolean(state.exchanges[exId]?.private_auth_ok));
          if (!anyAuthed) {
            return (
              <EmptyStatePanel
                title="Live Read-Only Exchange Accounts Not Authenticated"
                description="Public market data feeds are online, but private exchange API keys are isolated or unconfigured in this session. Switch to Paper Engine to inspect active simulated positions."
                actionLabel="Switch to Paper Engine"
                onAction={() => setAccountMode('paper')}
              />
            );
          }
          return (
            <div className="space-y-4">
              {visibleExchanges.map((exId) => {
                const ex = state.exchanges[exId];
                const meta = EXCHANGE_META[exId];
                const snap = ex.live_readonly_snapshot;
                const balances = snap?.status === 'ok' ? Object.entries(snap.balances || {}) : [];
                const livePos = snap?.status === 'ok' ? (snap.positions || []) : [];
                const filteredPos = livePos.filter((p) =>
                  p.symbol.toLowerCase().includes(symbolSearch.toLowerCase())
                );
                const filteredBal = balances.filter(([asset]) =>
                  asset.toLowerCase().includes(symbolSearch.toLowerCase())
                );
                return (
                  <div key={exId} className="nq-panel rounded-xl overflow-hidden">
                    <div className="px-4 py-3 border-b border-white/[0.08] bg-[#120508] flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2 text-sm font-display font-semibold">
                        <span className={meta.accentText}>{meta.name}</span>
                        <span className="text-slate-300">Live Read-Only Snapshot</span>
                      </div>
                      <span
                        className={`text-xs font-mono ${
                          ex.private_auth_ok && snap?.status === 'ok' ? 'text-[#10B981]' : 'text-amber-300'
                        }`}
                      >
                        {ex.private_auth_ok && snap?.status === 'ok'
                          ? `Verified Read-Only (${balances.length} assets, ${livePos.length} positions)`
                          : ex.private_auth_error || 'No verified snapshot'}
                      </span>
                    </div>

                    {ex.private_auth_ok && snap?.status === 'ok' ? (
                      <div className="p-4 space-y-4">
                        <div>
                          <div className="text-[11px] font-mono text-slate-400 mb-2">
                            Verified Read-Only Asset Balances (Raw Exchange Units · No Inferred FX Sum)
                          </div>
                          {filteredBal.length === 0 ? (
                            <div className="text-xs text-slate-400">No nonzero spot/account balances match filter.</div>
                          ) : (
                            <div className="flex flex-wrap gap-2">
                              {filteredBal.map(([asset, qty]) => (
                                <span
                                  key={asset}
                                  className="px-3 py-1.5 rounded-lg bg-white/[0.04] border border-white/[0.06] text-xs font-mono text-slate-200"
                                >
                                  {asset}: {Number(qty).toLocaleString(undefined, { maximumFractionDigits: 8 })}
                                </span>
                              ))}
                            </div>
                          )}
                        </div>

                        <div>
                          <div className="text-[11px] font-mono text-slate-400 mb-2">
                            Live Derivative Positions ({filteredPos.length})
                          </div>
                          {filteredPos.length === 0 ? (
                            <div className="text-xs text-slate-400">No open live derivative contracts on {meta.name}.</div>
                          ) : (
                            <div className="overflow-x-auto">
                              <table className="w-full text-left border-collapse text-xs font-mono">
                                <thead>
                                  <tr className="border-b border-white/[0.06] text-[11px] text-slate-400">
                                    <th className="py-2 pr-4">Contract</th>
                                    <th className="py-2 px-3">Side</th>
                                    <th className="py-2 px-3 text-right">Contracts</th>
                                    <th className="py-2 px-3 text-right">Entry</th>
                                    <th className="py-2 pl-3 text-right">Unrealized PnL</th>
                                  </tr>
                                </thead>
                                <tbody className="divide-y divide-white/[0.06]">
                                  {filteredPos.map((lp, idx) => (
                                    <tr key={`${lp.symbol}-${idx}`}>
                                      <td className="py-2 pr-4 text-white font-semibold">{lp.symbol}</td>
                                      <td className={`py-2 px-3 ${lp.side?.toUpperCase().includes('LONG') ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                                        {lp.side}
                                      </td>
                                      <td className="py-2 px-3 text-right text-slate-200">{lp.contracts}</td>
                                      <td className="py-2 px-3 text-right text-slate-300">{formatPrice(lp.entry_price)}</td>
                                      <td className={`py-2 pl-3 text-right font-semibold ${lp.unrealized_pnl >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                                        {formatSignedUsd(lp.unrealized_pnl)}
                                      </td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </div>
                      </div>
                    ) : (
                      <div className="p-4 text-xs text-amber-200">
                        {ex.private_auth_error || 'Live read-only snapshot unavailable for this exchange.'}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          );
        })()
      )}

      {/* Independent Exchange Sections */}
      {accountMode === 'paper' && (
        <div className="space-y-6">
          {visibleExchanges.map((exId) => {
            const meta = EXCHANGE_META[exId];
            if (marketType === 'futures') {
              const exRows = filteredFutures.filter((p) => p.exchange === exId);
              const exExposure = exRows.reduce((sum, r) => sum + r.notional_usd, 0);

              return (
                <div key={exId} className="nq-panel rounded-xl overflow-hidden">
                  <div className="px-4 py-3 border-b border-white/[0.08] bg-[#120508] flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm font-display font-semibold">
                      <span className={meta.accentText}>{meta.name}</span>
                      <span className="text-slate-300">Futures ({statusFilter})</span>
                    </div>
                    <div className="text-xs font-mono text-slate-400">
                      Exposure: <strong className="text-white">{formatUsd(exExposure)}</strong>
                    </div>
                  </div>

                  {exRows.length === 0 ? (
                    <div className="p-6 text-center text-xs text-slate-400">
                      No {statusFilter} {meta.name} Futures positions match your filters.
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left border-collapse text-xs font-mono">
                        <thead>
                          <tr className="border-b border-white/[0.06] text-[11px] text-slate-400">
                            <th className="py-2.5 px-4">Pair / Contract</th>
                            <th className="py-2.5 px-3">Side / Lev</th>
                            <th className="py-2.5 px-3 text-right">Qty / Notional</th>
                            <th className="py-2.5 px-3 text-right">Entry / Mark</th>
                            <th className="py-2.5 px-3 text-right">Margin / Liq Dist</th>
                            <th className="py-2.5 px-3 text-right">PnL ($) / (%)</th>
                            <th className="py-2.5 px-4 text-right">SL / TP</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-white/[0.06]">
                          {exRows.map((pos) => {
                            const pnl = pos.status === 'open' ? pos.unrealized_pnl_usd : pos.realized_pnl_usd;
                            return (
                              <tr
                                key={pos.id}
                                onClick={() => onInspectPosition(pos)}
                                className="hover:bg-white/[0.03] cursor-pointer transition-colors"
                              >
                                <td className="py-3 px-4">
                                  <div className="text-white font-semibold">{pos.symbol}</div>
                                  <div className="text-[10px] text-slate-500">{pos.instrument_id}</div>
                                </td>
                                <td className="py-3 px-3">
                                  <span className={pos.side === 'LONG' ? 'text-[#10B981]' : 'text-[#EF4444]'}>
                                    {pos.side}
                                  </span>{' '}
                                  <span className="text-slate-300">{pos.leverage}x</span>
                                </td>
                                <td className="py-3 px-3 text-right">
                                  <div className="text-white">{pos.quantity}</div>
                                  <div className="text-[10px] text-slate-400">{formatUsd(pos.notional_usd)}</div>
                                </td>
                                <td className="py-3 px-3 text-right">
                                  <div className="text-slate-300">{formatPrice(pos.entry_price)}</div>
                                  <div className="text-white">{formatPrice(pos.mark_price)}</div>
                                </td>
                                <td className="py-3 px-3 text-right">
                                  <div className="text-slate-200">{formatUsd(pos.margin_usd)}</div>
                                  <div className="text-[10px] text-amber-400">
                                    {pos.liquidation_distance_pct ? `${pos.liquidation_distance_pct.toFixed(1)}%` : 'Closed'}
                                  </div>
                                </td>
                                <td className="py-3 px-3 text-right">
                                  <div className={`font-semibold ${pnl >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                                    {formatSignedUsd(pnl)}
                                  </div>
                                  {pos.status === 'open' && (
                                    <div className={`text-[10px] ${pos.unrealized_pnl_pct >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                                      {formatSignedPct(pos.unrealized_pnl_pct)}
                                    </div>
                                  )}
                                </td>
                                <td className="py-3 px-4 text-right text-slate-300">
                                  <div>{pos.sl_tp_status}</div>
                                  <div className="text-[10px] text-slate-500">
                                    {formatPrice(pos.stop_loss)} / {formatPrice(pos.take_profit)}
                                  </div>
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            } else {
              // Spot Inventory
              const exSpot = filteredSpot.filter((s) => s.exchange === exId);
              const exVal = exSpot.reduce((sum, s) => sum + s.valuation_usd, 0);

              return (
                <div key={exId} className="nq-panel rounded-xl overflow-hidden">
                  <div className="px-4 py-3 border-b border-white/[0.08] bg-[#120508] flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm font-display font-semibold">
                      <span className={meta.accentText}>{meta.name}</span>
                      <span className="text-slate-300">Spot Asset Inventory</span>
                    </div>
                    <div className="text-xs font-mono text-slate-400">
                      Valuation: <strong className="text-white">{formatUsd(exVal)}</strong>
                    </div>
                  </div>

                  {exSpot.length === 0 ? (
                    <div className="p-6 text-center text-xs text-slate-400">
                      No {meta.name} Spot holdings match your search.
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-left border-collapse text-xs font-mono">
                        <thead>
                          <tr className="border-b border-white/[0.06] text-[11px] text-slate-400">
                            <th className="py-2.5 px-4">Asset</th>
                            <th className="py-2.5 px-3 text-right">Available</th>
                            <th className="py-2.5 px-3 text-right">Locked</th>
                            <th className="py-2.5 px-3 text-right">Total</th>
                            <th className="py-2.5 px-3 text-right">Mark Price</th>
                            <th className="py-2.5 px-3 text-right">Valuation</th>
                            <th className="py-2.5 px-4 text-right">Unrealized PnL</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-white/[0.06]">
                          {exSpot.map((h) => (
                            <tr key={h.id} className="hover:bg-white/[0.02]">
                              <td className="py-3 px-4 font-semibold text-white">{h.asset}</td>
                              <td className="py-3 px-3 text-right text-slate-300">{h.available}</td>
                              <td className="py-3 px-3 text-right text-slate-400">{h.locked}</td>
                              <td className="py-3 px-3 text-right text-white">{h.total}</td>
                              <td className="py-3 px-3 text-right text-slate-300">{formatPrice(h.mark_price)}</td>
                              <td className="py-3 px-3 text-right text-white">{formatUsd(h.valuation_usd)}</td>
                              <td className="py-3 px-4 text-right">
                                {h.unrealized_pnl_usd !== null ? (
                                  <span className={h.unrealized_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}>
                                    {formatSignedUsd(h.unrealized_pnl_usd)}
                                  </span>
                                ) : (
                                  '—'
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            }
          })}
        </div>
      )}
    </div>
  );
};
