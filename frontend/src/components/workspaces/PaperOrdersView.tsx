import React, { useState } from 'react';
import { Download, Lock, ShieldCheck } from 'lucide-react';
import { ExchangeId, LIVE_EXECUTION_PERMANENTLY_DISABLED, LiveExecutionBlockedError, MiniAppStateResponse } from '../../types/quant';
import { quantApi } from '../../services/quantApi';
import { EXCHANGE_META, EmptyStatePanel, SectionHeader } from '../common/TerminalPrimitives';
import { formatPrice, formatTimeShort, formatUsd } from '../../utils/formatters';
import { EmbeddedSymbolChart, SymbolChartToggleButton } from '../charts/EmbeddedSymbolChart';

interface PaperOrdersViewProps {
  state: MiniAppStateResponse;
  initData?: string;
}

export const PaperOrdersView: React.FC<PaperOrdersViewProps> = ({ state, initData }) => {
  const [exchangeFilter, setExchangeFilter] = useState<'all' | ExchangeId>('all');
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'OPEN' | 'FILLED' | 'REJECTED'>('ALL');
  const [guardCheckResult, setGuardCheckResult] = useState<string | null>(null);
  const [expandedOrderId, setExpandedOrderId] = useState<string | null>(null);

  const filtered = state.orders.filter((o) => {
    const matchEx = exchangeFilter === 'all' || o.exchange === exchangeFilter;
    const matchSt = statusFilter === 'ALL' || o.status === statusFilter;
    return matchEx && matchSt;
  });

  const handleExportCsv = () => {
    const headers = ['ID', 'Exchange', 'Market', 'Symbol', 'Side', 'Type', 'Status', 'Price', 'AvgFill', 'Qty', 'FeeUSD', 'SlippageBps', 'Strategy'];
    const rows = filtered.map((o) =>
      [
        o.id,
        o.exchange,
        o.market_type,
        o.symbol,
        o.side,
        o.order_type,
        o.status,
        o.price,
        o.avg_fill_price ?? '',
        o.quantity,
        o.fee_usd,
        o.slippage_bps,
        o.strategy_name
      ].join(',')
    );
    const csv = 'data:text/csv;charset=utf-8,' + encodeURIComponent([headers.join(','), ...rows].join('\n'));
    const a = document.createElement('a');
    a.href = csv;
    a.download = `neon_quant_paper_orders_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
  };

  const handleTestExecutionGuard = () => {
    try {
      quantApi.submitLiveOrder();
    } catch (err) {
      if (err instanceof LiveExecutionBlockedError) {
        setGuardCheckResult(`Verified Blocked (${err.code}): ${err.message}`);
      }
    }
  };

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Paper Orders & Execution Fills"
        subtitle="Simulated order lifecycle, fee accounting, and slippage telemetry across exchanges"
        actions={
          <button
            type="button"
            onClick={handleExportCsv}
            className="px-3.5 py-2 min-h-[38px] rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/45 text-[#ffe3e5] text-xs font-medium flex items-center gap-1.5 whitespace-nowrap shrink-0 transition-colors"
          >
            <Download className="w-3.5 h-3.5 text-[#ff6375]" />
            <span>Export CSV</span>
          </button>
        }
      />

      {/* Non-Negotiable Read-Only Execution Guard Banner */}
      <div className="nq-panel rounded-xl p-4 border border-[#10B981]/30 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-start gap-2.5 text-xs">
          <Lock className="w-4 h-4 text-[#10B981] shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold text-white">
              Live Order Submission Disabled (LIVE_EXECUTION_PERMANENTLY_DISABLED = {String(LIVE_EXECUTION_PERMANENTLY_DISABLED)})
            </div>
            <p className="text-slate-400 mt-0.5">
              This terminal has zero live-order mutation endpoints, zero withdrawal routes, and zero API key modification paths.
            </p>
            {guardCheckResult && (
              <p className="text-[#10B981] font-mono text-[11px] mt-1">{guardCheckResult}</p>
            )}
          </div>
        </div>
        <button
          onClick={handleTestExecutionGuard}
          className="px-3 py-1.5 min-h-[36px] rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-xs text-slate-200 whitespace-nowrap shrink-0 flex items-center gap-1.5"
        >
          <ShieldCheck className="w-3.5 h-3.5 text-[#10B981]" />
          <span>Verify Guard</span>
        </button>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center p-1 rounded-lg bg-[#120508] border border-[#ff3555]/25">
          {(['all', 'binance', 'bybit', 'okx'] as const).map((ex) => (
            <button
              key={ex}
              type="button"
              onClick={() => setExchangeFilter(ex)}
              className={`px-3 py-1.5 min-h-[34px] rounded-md text-xs font-medium uppercase whitespace-nowrap shrink-0 ${
                exchangeFilter === ex ? 'bg-[#ff3555]/25 text-[#ffe3e5]' : 'text-[#d4a5ac] hover:text-[#ffe3e5]'
              }`}
            >
              {ex}
            </button>
          ))}
        </div>

        <div className="flex items-center p-1 rounded-lg bg-[#120508] border border-[#ff3555]/25">
          {(['ALL', 'OPEN', 'FILLED', 'REJECTED'] as const).map((st) => (
            <button
              key={st}
              type="button"
              onClick={() => setStatusFilter(st)}
              className={`px-3 py-1.5 min-h-[34px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 ${
                statusFilter === st ? 'bg-[#ff3555]/25 text-[#ffe3e5]' : 'text-[#d4a5ac] hover:text-[#ffe3e5]'
              }`}
            >
              {st}
            </button>
          ))}
        </div>
      </div>

      {filtered.length === 0 ? (
        <EmptyStatePanel
          title="No Paper Orders Match Active Filters"
          description="Try clearing the exchange or status filter to inspect paper orders and execution fills."
          actionLabel="Reset Order Filters"
          onAction={() => {
            setExchangeFilter('all');
            setStatusFilter('ALL');
          }}
        />
      ) : (
        <div className="nq-panel rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left border-collapse text-xs font-mono">
              <thead>
                <tr className="border-b border-[#ff3555]/20 bg-[#120508] text-[11px] text-[#d4a5ac]">
                  <th className="py-3 px-4">Order ID / Time</th>
                  <th className="py-3 px-3">Exchange / Pair</th>
                  <th className="py-3 px-3">Side / Type</th>
                  <th className="py-3 px-3">Status</th>
                  <th className="py-3 px-3 text-right">Limit / Fill</th>
                  <th className="py-3 px-3 text-right">Qty / Notional</th>
                  <th className="py-3 px-3 text-right">Fee / Slippage</th>
                  <th className="py-3 px-4">Strategy</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {filtered.map((ord) => {
                  const isExpanded = expandedOrderId === ord.id;
                  return (
                    <React.Fragment key={ord.id}>
                      <tr className="hover:bg-white/[0.02]">
                        <td className="py-3 px-4">
                          <div className="text-white font-semibold">{ord.id}</div>
                          <div className="text-[10px] text-slate-400">{formatTimeShort(ord.created_at)}</div>
                        </td>
                        <td className="py-3 px-3">
                          <div className={`flex items-center gap-2 font-semibold uppercase ${EXCHANGE_META[ord.exchange].accentText}`}>
                            <span>
                              {ord.exchange} · <span className="text-white">{ord.symbol}</span>
                            </span>
                            <SymbolChartToggleButton
                              symbol={ord.symbol}
                              expanded={isExpanded}
                              onToggle={() => setExpandedOrderId((prev) => (prev === ord.id ? null : ord.id))}
                            />
                          </div>
                          <div className="text-[10px] text-slate-400 uppercase">{ord.market_type} · {ord.account_mode}</div>
                        </td>
                        <td className="py-3 px-3">
                          <span className={ord.side === 'BUY' ? 'text-[#10B981]' : 'text-[#EF4444]'}>{ord.side}</span>{' '}
                          <span className="text-slate-300">{ord.order_type}</span>
                        </td>
                        <td className="py-3 px-3">
                          <span
                            className={
                              ord.status === 'FILLED'
                                ? 'text-[#10B981]'
                                : ord.status === 'OPEN'
                                ? 'text-[#ff6375]'
                                : ord.status === 'REJECTED'
                                ? 'text-[#EF4444]'
                                : 'text-slate-400'
                            }
                          >
                            {ord.status}
                          </span>
                        </td>
                        <td className="py-3 px-3 text-right">
                          <div className="text-slate-300">{formatPrice(ord.price)}</div>
                          <div className="text-white">{formatPrice(ord.avg_fill_price)}</div>
                        </td>
                        <td className="py-3 px-3 text-right">
                          <div className="text-white">{ord.quantity}</div>
                          <div className="text-[10px] text-slate-400">{formatUsd(ord.notional_usd)}</div>
                        </td>
                        <td className="py-3 px-3 text-right">
                          <div className="text-slate-200">{formatUsd(ord.fee_usd)}</div>
                          <div className="text-[10px] text-slate-400">{ord.slippage_bps.toFixed(2)} bps</div>
                        </td>
                        <td className="py-3 px-4 text-slate-400 truncate max-w-[160px]">{ord.strategy_name}</td>
                      </tr>
                      {isExpanded && (
                        <tr className="bg-[#080305]/90">
                          <td colSpan={8} className="p-3">
                            <EmbeddedSymbolChart
                              exchange={ord.exchange}
                              marketType={ord.market_type}
                              symbol={ord.symbol}
                              activePosition={
                                state.futures_positions.find(
                                  (p) =>
                                    p.status === 'open' &&
                                    p.exchange === ord.exchange &&
                                    p.symbol === ord.symbol
                                ) || null
                              }
                              initData={initData}
                              onClose={() => setExpandedOrderId(null)}
                            />
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
