import React, { useCallback, useEffect, useState } from 'react';
import { BarChart3, Filter, RefreshCw, ShieldCheck, TrendingUp } from 'lucide-react';
import { fetchVioletPnl, VioletPnlFilters, VioletPnlReport } from '../../services/violetResearchApi';

interface PnlCommandCenterViewProps {
  initData?: string;
}

const fmtUsd = (v: number | undefined, d = 2) =>
  v === undefined || !Number.isFinite(v) ? '—' : `${v.toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })} USDT`;

const fmtSigned = (v: number | undefined, d = 4) => {
  if (v === undefined || !Number.isFinite(v)) return '—';
  const sign = v > 0 ? '+' : v < 0 ? '-' : '';
  return `${sign}${Math.abs(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d })}`;
};

function MiniLineChart({ points, valueKey, color }: { points: Array<Record<string, unknown>>; valueKey: string; color: string }) {
  if (!points || points.length === 0) {
    return <div className="h-24 flex items-center justify-center text-xs font-mono text-[#a1737a]">No data points yet</div>;
  }
  const vals = points.map((p) => Number(p[valueKey] ?? 0));
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const span = max - min || 1;
  const w = 320;
  const h = 80;
  const coords = vals.map((v, i) => {
    const x = vals.length === 1 ? w / 2 : (i / (vals.length - 1)) * (w - 8) + 4;
    const y = h - 8 - ((v - min) / span) * (h - 16);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  });
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="w-full h-24 overflow-visible">
      <polyline fill="none" stroke={color} strokeWidth="2.2" points={coords.join(' ')} />
    </svg>
  );
}

export const PnlCommandCenterView: React.FC<PnlCommandCenterViewProps> = ({ initData }) => {
  const [report, setReport] = useState<VioletPnlReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<VioletPnlFilters>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchVioletPnl(filters, initData);
      setReport(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load PnL Command Center');
    } finally {
      setLoading(false);
    }
  }, [filters, initData]);

  useEffect(() => {
    load();
  }, [load]);

  const ov = report?.overview;
  const charts = report?.charts;

  return (
    <div className="space-y-6">
      <div className="nq-panel rounded-xl p-4 sm:p-5 border border-[#ff3555]/30 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <TrendingUp className="w-5 h-5 text-[#ff6375]" />
            <h2 className="font-display font-bold text-lg text-[#ffe3e5]">PnL Command Center</h2>
            <span className="px-2 py-0.5 rounded bg-[#10B981]/15 border border-[#10B981]/35 text-[#10B981] text-[10px] font-mono">
              PAPER_ONLY · LIVE ACCOUNTS EXCLUDED
            </span>
          </div>
          <p className="text-xs font-mono text-[#c28e95] mt-1">
            Decimal-precision portfolio, asset, strategy, exchange &amp; cap-tier PnL attribution
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="px-3 py-1.5 rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/45 text-[#ffe3e5] text-xs font-mono inline-flex items-center gap-1.5"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>Refresh PnL</span>
        </button>
      </div>

      {error && (
        <div className="rounded-xl bg-[#EF4444]/10 border border-[#EF4444]/40 px-4 py-3 text-xs font-mono text-[#ffe3e5]">
          {error}
        </div>
      )}

      {/* Portfolio Overview KPIs */}
      {ov && (
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {[
            ['Starting Capital', fmtUsd(ov.starting_capital_usdt)],
            ['Current Equity', fmtUsd(ov.current_equity_usdt)],
            ['Available Cash', fmtUsd(ov.available_cash_usdt)],
            ['Reserved Margin', fmtUsd(ov.reserved_margin_usdt)],
            ['Realized PnL', `${fmtSigned(ov.realized_pnl_usdt)} USDT`],
            ['Unrealized PnL', `${fmtSigned(ov.unrealized_pnl_usdt)} USDT`],
            ['Net PnL (After Fees)', `${fmtSigned(ov.net_pnl_after_fees_usdt)} USDT`],
            ['ROI %', `${ov.roi_pct.toFixed(2)}%`],
            ['Daily / Weekly', `${ov.daily_return_pct.toFixed(2)}% / ${ov.weekly_return_pct.toFixed(2)}%`],
            ['Monthly / Lifetime', `${ov.monthly_return_pct.toFixed(2)}% / ${ov.lifetime_return_pct.toFixed(2)}%`],
            ['Max Drawdown', `${ov.max_drawdown_pct.toFixed(2)}%`],
            ['Win Rate / PF', `${ov.win_rate_pct.toFixed(1)}% · PF ${ov.profit_factor.toFixed(2)}`],
          ].map(([label, val]) => (
            <div key={label} className="nq-panel rounded-xl p-3 border border-[#ff3555]/20">
              <div className="text-[10px] font-mono uppercase text-[#c28e95]">{label}</div>
              <div className="text-sm font-mono font-bold text-[#ffe3e5] mt-1">{val}</div>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      <div className="nq-panel rounded-xl p-3.5 border border-[#ff3555]/20 flex flex-wrap items-center gap-2.5 text-xs font-mono">
        <Filter className="w-3.5 h-3.5 text-[#ff6375]" />
        <input
          type="text"
          placeholder="Asset (e.g. BTC)"
          value={filters.asset || ''}
          onChange={(e) => setFilters((f) => ({ ...f, asset: e.target.value }))}
          className="px-2.5 py-1.5 rounded bg-[#080305] border border-[#ff3555]/30 text-[#ffe3e5] w-32"
        />
        <select
          value={filters.exchange || ''}
          onChange={(e) => setFilters((f) => ({ ...f, exchange: e.target.value }))}
          className="px-2.5 py-1.5 rounded bg-[#080305] border border-[#ff3555]/30 text-[#ffe3e5]"
        >
          <option value="">All Exchanges</option>
          <option value="binance">Binance</option>
          <option value="bybit">Bybit</option>
          <option value="okx">OKX</option>
          <option value="gateio">Gate.io</option>
        </select>
        <select
          value={filters.cap_band || ''}
          onChange={(e) => setFilters((f) => ({ ...f, cap_band: e.target.value }))}
          className="px-2.5 py-1.5 rounded bg-[#080305] border border-[#ff3555]/30 text-[#ffe3e5]"
        >
          <option value="">All Cap Bands</option>
          <option value="micro">Micro</option>
          <option value="low">Low</option>
          <option value="mid">Mid</option>
          <option value="large">Large</option>
          <option value="mega">Mega</option>
        </select>
        <select
          value={filters.status || ''}
          onChange={(e) => setFilters((f) => ({ ...f, status: e.target.value }))}
          className="px-2.5 py-1.5 rounded bg-[#080305] border border-[#ff3555]/30 text-[#ffe3e5]"
        >
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="closed">Closed</option>
          <option value="rejected">Rejected</option>
        </select>
        <input
          type="date"
          value={filters.start_date || ''}
          onChange={(e) => setFilters((f) => ({ ...f, start_date: e.target.value }))}
          className="px-2 py-1.5 rounded bg-[#080305] border border-[#ff3555]/30 text-[#ffe3e5]"
        />
        <input
          type="date"
          value={filters.end_date || ''}
          onChange={(e) => setFilters((f) => ({ ...f, end_date: e.target.value }))}
          className="px-2 py-1.5 rounded bg-[#080305] border border-[#ff3555]/30 text-[#ffe3e5]"
        />
      </div>

      {/* 10 Interactive Visualizations Grid */}
      {charts && (
        <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20">
            <div className="text-xs font-mono text-[#d4a5ac] mb-2">1. Portfolio Equity Curve</div>
            <MiniLineChart points={charts.equity_curve} valueKey="equity_usd" color="#10B981" />
          </div>
          <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20">
            <div className="text-xs font-mono text-[#d4a5ac] mb-2">2. Cumulative Realized PnL</div>
            <MiniLineChart points={charts.cumulative_realized_pnl} valueKey="cumulative_pnl_usd" color="#ff6375" />
          </div>
          <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20">
            <div className="text-xs font-mono text-[#d4a5ac] mb-2">3. Drawdown Chart (%)</div>
            <MiniLineChart points={charts.drawdown_series} valueKey="drawdown_pct" color="#ffb16c" />
          </div>

          <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20 space-y-2">
            <div className="text-xs font-mono text-[#d4a5ac]">4. Daily PnL Histogram</div>
            {charts.daily_pnl_histogram.length === 0 ? (
              <div className="text-xs font-mono text-[#a1737a]">No closed trades yet</div>
            ) : (
              charts.daily_pnl_histogram.slice(-7).map((d) => (
                <div key={d.date} className="flex justify-between text-xs font-mono">
                  <span className="text-[#c28e95]">{d.date} ({d.trades})</span>
                  <span className={d.realized_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}>{fmtSigned(d.realized_pnl_usd)}</span>
                </div>
              ))
            )}
          </div>

          <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20 space-y-2">
            <div className="text-xs font-mono text-[#d4a5ac]">5. Exchange Profitability Comparison</div>
            {charts.exchange_comparison.length === 0 ? (
              <div className="text-xs font-mono text-[#a1737a]">Awaiting fills</div>
            ) : (
              charts.exchange_comparison.map((ex) => (
                <div key={ex.exchange} className="flex justify-between text-xs font-mono">
                  <span className="uppercase text-[#ffe3e5]">{ex.exchange} ({ex.trades})</span>
                  <span className={ex.net_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}>{fmtSigned(ex.net_pnl_usd)} USDT</span>
                </div>
              ))
            )}
          </div>

          <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20 space-y-2">
            <div className="text-xs font-mono text-[#d4a5ac]">6. Market-Cap Category Performance</div>
            {charts.cap_category_performance.length === 0 ? (
              <div className="text-xs font-mono text-[#a1737a]">Awaiting fills</div>
            ) : (
              charts.cap_category_performance.map((c) => (
                <div key={c.category} className="flex justify-between text-xs font-mono">
                  <span className="uppercase text-[#ffe3e5]">{c.category} ({c.trades})</span>
                  <span className={c.net_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}>{fmtSigned(c.net_pnl_usd)} USDT</span>
                </div>
              ))
            )}
          </div>

          <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20 space-y-2">
            <div className="text-xs font-mono text-[#d4a5ac]">7. Strategy Performance Comparison</div>
            {charts.strategy_performance.length === 0 ? (
              <div className="text-xs font-mono text-[#a1737a]">No strategy fills yet</div>
            ) : (
              charts.strategy_performance.map((s) => (
                <div key={s.strategy} className="flex justify-between text-xs font-mono">
                  <span className="text-[#ffe3e5]">{s.strategy} ({s.trades})</span>
                  <span className={s.net_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}>{fmtSigned(s.net_pnl_usd)} USDT</span>
                </div>
              ))
            )}
          </div>

          <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20 space-y-2">
            <div className="text-xs font-mono text-[#d4a5ac]">8. Asset Profitability Leaderboard</div>
            {charts.asset_leaderboard.slice(0, 5).map((a) => (
              <div key={`${a.exchange}:${a.asset}`} className="flex justify-between text-xs font-mono">
                <span className="text-[#ffe3e5]">{a.asset} ({a.exchange})</span>
                <span className={a.net_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}>{fmtSigned(a.net_pnl_usd)}</span>
              </div>
            ))}
          </div>

          <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20 space-y-1.5 text-xs font-mono">
            <div className="text-[#d4a5ac] mb-2">9. Fees &amp; Funding Breakdown</div>
            <div className="flex justify-between"><span>Trading Fees:</span><span>{charts.fees_funding_breakdown.trading_fees_usd.toFixed(4)} USDT</span></div>
            <div className="flex justify-between"><span>Funding Costs:</span><span>{charts.fees_funding_breakdown.funding_usd.toFixed(4)} USDT</span></div>
            <div className="flex justify-between font-bold text-[#ffe3e5] border-t border-[#ff3555]/20 pt-1">
              <span>Total Costs:</span><span>{charts.fees_funding_breakdown.total_costs_usd.toFixed(4)} USDT</span>
            </div>
          </div>
        </div>
      )}

      {/* Asset-Level Tracking Table */}
      <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/25 overflow-x-auto">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-[#ffe3e5]">
            <BarChart3 className="w-4 h-4 text-[#ff6375]" />
            <span>Asset-Level Tracking ({report?.assets.length ?? 0})</span>
          </div>
          {report?.reconciliation && (
            <span className="inline-flex items-center gap-1 text-[11px] font-mono text-[#10B981]">
              <ShieldCheck className="w-3.5 h-3.5" />
              Reconciled (Drift: {report.reconciliation.drift_usd.toFixed(4)} USDT)
            </span>
          )}
        </div>
        <table className="w-full text-left text-xs font-mono">
          <thead>
            <tr className="border-b border-[#ff3555]/25 text-[#c28e95]">
              <th className="py-2 pr-3">Asset</th>
              <th className="py-2 pr-3">Cap Band</th>
              <th className="py-2 pr-3">Strategy</th>
              <th className="py-2 pr-3">Entry / Qty</th>
              <th className="py-2 pr-3">Mark</th>
              <th className="py-2 pr-3">Exposure</th>
              <th className="py-2 pr-3">Realized</th>
              <th className="py-2 pr-3">Unrealized</th>
              <th className="py-2 pr-3">Fees</th>
              <th className="py-2 pr-3">Net PnL</th>
              <th className="py-2 pr-3">ROI</th>
              <th className="py-2">Status</th>
            </tr>
          </thead>
          <tbody>
            {(report?.assets ?? []).slice(0, 50).map((a) => (
              <tr key={`${a.exchange}:${a.asset}`} className="border-b border-[#ff3555]/10 text-[#ffe3e5]">
                <td className="py-2 pr-3">{a.asset} <span className="text-[#a1737a] uppercase">{a.exchange}</span></td>
                <td className="py-2 pr-3 uppercase">{a.market_cap_band}</td>
                <td className="py-2 pr-3">{a.strategy}</td>
                <td className="py-2 pr-3">{a.entry_price ? `${a.entry_price.toFixed(4)} × ${a.qty}` : '—'}</td>
                <td className="py-2 pr-3">{a.mark_price ? a.mark_price.toFixed(4) : '—'}</td>
                <td className="py-2 pr-3">{a.notional_usd.toFixed(2)}</td>
                <td className="py-2 pr-3">{fmtSigned(a.realized_pnl_usd)}</td>
                <td className="py-2 pr-3">{fmtSigned(a.unrealized_pnl_usd)}</td>
                <td className="py-2 pr-3">{a.fees_usd.toFixed(4)}</td>
                <td className={`py-2 pr-3 font-bold ${a.net_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>{fmtSigned(a.net_pnl_usd)}</td>
                <td className="py-2 pr-3">{a.roi_pct.toFixed(2)}%</td>
                <td className="py-2 uppercase text-[10px]">{a.status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};
