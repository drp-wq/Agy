import React, { useEffect, useRef } from 'react';
import { createChart, AreaSeries, ColorType, Time } from 'lightweight-charts';
import { ExchangeId, MiniAppStateResponse } from '../../types/quant';
import { EXCHANGE_META, SectionHeader } from '../common/TerminalPrimitives';
import { formatSignedUsd, formatUsd } from '../../utils/formatters';
import { normalizeEquityCurve, summarizeDrawdown } from '../../utils/telemetryMetrics';

interface PortfolioAnalyticsViewProps {
  state: MiniAppStateResponse;
}

export const PortfolioAnalyticsView: React.FC<PortfolioAnalyticsViewProps> = ({ state }) => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const { analytics } = state;
  const ddSummary = summarizeDrawdown(state.risk);
  const normalizedCurve = normalizeEquityCurve(analytics.equity_curve);
  const maxHistDd =
    analytics.max_historical_drawdown_pct !== undefined
      ? analytics.max_historical_drawdown_pct
      : state.risk.max_drawdown_pct;

  useEffect(() => {
    const container = chartContainerRef.current;
    if (!container || normalizedCurve.length === 0) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: 280,
      layout: {
        background: { type: ColorType.Solid, color: '#080305' },
        textColor: '#d4a5ac',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11
      },
      grid: {
        vertLines: { color: 'rgba(255, 53, 85, 0.07)' },
        horzLines: { color: 'rgba(255, 53, 85, 0.07)' }
      },
      rightPriceScale: { borderColor: 'rgba(255, 53, 85, 0.2)' },
      timeScale: { borderColor: 'rgba(255, 53, 85, 0.2)', timeVisible: false }
    });

    const series = chart.addSeries(AreaSeries, {
      lineColor: '#ff3555',
      topColor: 'rgba(255, 53, 85, 0.28)',
      bottomColor: 'rgba(8, 3, 5, 0.0)',
      lineWidth: 2
    });

    series.setData(
      normalizedCurve.map((pt) => ({
        time: pt.time as Time,
        value: pt.equity
      }))
    );
    chart.timeScale().fitContent();

    const handleResize = () => {
      if (container) chart.applyOptions({ width: container.clientWidth });
    };
    window.addEventListener('resize', handleResize);
    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, [analytics.equity_curve]);

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Portfolio Analytics & Attribution"
        subtitle="Verified paper-engine equity trajectory, closed-trade ratios, and strategy performance attribution"
      />

      {/* Core Performance KPIs */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">
            Win Rate ({analytics.closed_trades ?? analytics.total_trades} Closed / {analytics.total_trades} Fills)
          </div>
          <div className="text-2xl font-mono font-bold text-[#10B981] mt-1">
            {analytics.win_rate_pct !== null ? `${analytics.win_rate_pct.toFixed(1)}%` : 'Unavailable'}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1">
            Avg Win {analytics.avg_win_usd !== null ? formatUsd(analytics.avg_win_usd) : '—'} / Loss{' '}
            {analytics.avg_loss_usd !== null ? formatUsd(Math.abs(analytics.avg_loss_usd)) : '—'}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Profit Factor</div>
          <div className="text-2xl font-mono font-bold text-white mt-1">
            {analytics.profit_factor !== null ? analytics.profit_factor.toFixed(2) : 'Unavailable'}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1">Gross Profit / Gross Loss</div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-[#d4a5ac]">Sharpe / Sortino</div>
          <div className="text-2xl font-mono font-bold text-[#ff6375] mt-1">
            {analytics.sharpe_ratio !== null ? analytics.sharpe_ratio.toFixed(2) : '—'} /{' '}
            {analytics.sortino_ratio !== null ? analytics.sortino_ratio.toFixed(2) : '—'}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1">
            {analytics.metrics_note || 'Annualized Daily Return Window'}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Max Historical Drawdown</div>
          <div className="text-2xl font-mono font-bold text-amber-400 mt-1">
            {maxHistDd !== null && maxHistDd !== undefined
              ? (maxHistDd > 0.004 ? `-${maxHistDd.toFixed(2)}%` : '0.00%')
              : 'Unavailable'}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1">
            Current: {ddSummary.displayValue}
          </div>
        </div>
      </div>

      {/* Equity Curve Chart */}
      <div className="nq-panel rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-[#ff3555]/20 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[#ffe3e5]">Aggregate Paper Equity Curve (USD)</h3>
          <span className="text-xs font-mono text-[#ff6375]">
            Current: {formatUsd(state.risk.total_paper_equity_usd)}
          </span>
        </div>
        {normalizedCurve.length === 0 ? (
          <div className="p-8 text-center text-xs text-[#d4a5ac] bg-[#080305]">
            No timestamped equity points recorded yet.
          </div>
        ) : (
          <div ref={chartContainerRef} className="w-full bg-[#080305]" />
        )}
      </div>

      {/* Attribution Split: By Exchange & By Strategy */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="nq-panel rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white pb-3 border-b border-white/[0.08]">
            Net PnL Attribution by Exchange
          </h3>
          <div className="divide-y divide-white/[0.06] mt-2 text-xs font-mono">
            {(['binance', 'bybit', 'okx'] as ExchangeId[]).map((exId) => {
              const pnl = analytics.pnl_by_exchange[exId] || 0;
              const meta = EXCHANGE_META[exId];
              return (
                <div key={exId} className="py-3 flex items-center justify-between">
                  <span className={`font-semibold ${meta.accentText}`}>{meta.name} Paper Ledger</span>
                  <span className={pnl >= 0 ? 'text-[#10B981] font-semibold' : 'text-[#EF4444] font-semibold'}>
                    {formatSignedUsd(pnl)}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white pb-3 border-b border-white/[0.08]">
            Attribution by Quantitative Strategy
          </h3>
          <div className="overflow-x-auto mt-2">
            <table className="w-full text-left text-xs font-mono">
              <thead>
                <tr className="text-[11px] text-slate-400 border-b border-white/[0.06]">
                  <th className="py-2">Strategy</th>
                  <th className="py-2 text-right">Trades</th>
                  <th className="py-2 text-right">Win %</th>
                  <th className="py-2 text-right">Net PnL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.06]">
                {analytics.pnl_by_strategy.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="py-6 text-center text-slate-400">
                      No closed trades recorded per strategy yet.
                    </td>
                  </tr>
                ) : (
                  analytics.pnl_by_strategy.map((row) => (
                    <tr key={row.strategy}>
                      <td className="py-2.5 text-slate-200 truncate max-w-[160px]">{row.strategy}</td>
                      <td className="py-2.5 text-right text-slate-400">{row.trades}</td>
                      <td className="py-2.5 text-right text-slate-200">{row.win_rate.toFixed(1)}%</td>
                      <td className={`py-2.5 text-right font-semibold ${row.net_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                        {formatSignedUsd(row.net_pnl_usd)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};
