import React, { useCallback, useEffect, useState } from 'react';
import { Brain, CheckCircle2, Clock, RefreshCw } from 'lucide-react';
import { fetchVioletPnl, VioletPnlReport } from '../../services/violetResearchApi';

interface StrategyAnalyticsViewProps {
  initData?: string;
}

export const StrategyAnalyticsView: React.FC<StrategyAnalyticsViewProps> = ({ initData }) => {
  const [report, setReport] = useState<VioletPnlReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchVioletPnl({}, initData);
      setReport(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load Strategy Analytics');
    } finally {
      setLoading(false);
    }
  }, [initData]);

  useEffect(() => {
    load();
  }, [load]);

  const strategies = report?.charts?.strategy_performance ?? [];
  const capPerf = report?.charts?.cap_category_performance ?? [];

  return (
    <div className="space-y-5">
      <div className="nq-panel rounded-xl p-4 sm:p-5 border border-[#ff3555]/30 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Brain className="w-5 h-5 text-[#ff6375]" />
            <h2 className="font-display font-bold text-lg text-[#ffe3e5]">Strategy Intelligence &amp; Validation</h2>
            <span className="px-2 py-0.5 rounded bg-[#ff3555]/15 border border-[#ff3555]/35 text-[#ffe3e5] text-[10px] font-mono">
              OOS VALIDATION GATE · NO AUTO-LEVERAGE
            </span>
          </div>
          <p className="text-xs font-mono text-[#c28e95] mt-1">
            Confidence, sample size, and out-of-sample paper validation across market-cap tiers and exchanges
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="px-3 py-1.5 rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/45 text-[#ffe3e5] text-xs font-mono inline-flex items-center gap-1.5"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>Refresh</span>
        </button>
      </div>

      {error && (
        <div className="rounded-xl bg-[#EF4444]/10 border border-[#EF4444]/40 px-4 py-3 text-xs font-mono text-[#ffe3e5]">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {strategies.length === 0 ? (
          <div className="col-span-full nq-panel rounded-xl p-6 text-center text-xs font-mono text-[#a1737a] border border-[#ff3555]/20">
            No completed strategy samples yet. Strategies require out-of-sample paper validation before promotion.
          </div>
        ) : (
          strategies.map((s) => (
            <div key={s.strategy} className="nq-panel rounded-xl p-4 border border-[#ff3555]/25 space-y-2 text-xs font-mono">
              <div className="flex items-center justify-between">
                <span className="font-bold text-[#ffe3e5] text-sm">{s.strategy}</span>
                {s.oos_validated ? (
                  <span className="inline-flex items-center gap-1 text-[#10B981] text-[10px]">
                    <CheckCircle2 className="w-3.5 h-3.5" /> OOS VALIDATED
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1 text-amber-300 text-[10px]">
                    <Clock className="w-3.5 h-3.5" /> PROBATION
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 gap-2 text-[#d4a5ac]">
                <div>Sample Size: <strong className="text-[#ffe3e5]">{s.sample_size}</strong></div>
                <div>Win Rate: <strong className="text-[#ffe3e5]">{s.win_rate_pct.toFixed(1)}%</strong></div>
                <div>Confidence: <strong className="text-[#ffe3e5] uppercase">{s.confidence}</strong></div>
                <div>Net PnL: <strong className={s.net_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}>{s.net_pnl_usd.toFixed(4)} USDT</strong></div>
              </div>
            </div>
          ))
        )}
      </div>

      <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20 space-y-2">
        <div className="text-xs font-mono text-[#d4a5ac]">Performance by Market-Cap Category</div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2.5 text-xs font-mono">
          {capPerf.map((c) => (
            <div key={c.category} className="rounded-lg bg-[#080305] border border-[#ff3555]/20 p-3 space-y-1">
              <div className="uppercase font-bold text-[#ffe3e5]">{c.category}</div>
              <div className="text-[#c28e95]">Trades: {c.trades}</div>
              <div className={c.net_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}>Net: {c.net_pnl_usd.toFixed(4)} USDT</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
};
