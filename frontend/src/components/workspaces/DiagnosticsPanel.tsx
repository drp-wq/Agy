import React, { useEffect, useState } from 'react';
import { quantApi, PaperDiagnostics, Freshness } from '../../services/quantApi';

export function freshnessLabel(value: Freshness): string {
  return `${value.status}${value.age_seconds === null ? '' : ` · ${value.age_seconds}s old`}`;
}

export function DiagnosticsContent({ data, section }: { data: PaperDiagnostics; section: string }) {
  return <section aria-label="Paper diagnostics" className="nq-panel rounded-xl p-4 mb-6 min-w-0 space-y-3">
    <h2 className="text-sm font-semibold text-[#ffe3e5]">PAPER_ONLY · Read-only diagnostics</h2>
    <p className="text-xs text-[#d4a5ac]">All-time ledger · Updated {new Date(data.generated_at).toLocaleString()}</p>
    {section === 'system' && <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-sm">
      <p>Engine cycle: {freshnessLabel(data.system.cycle)}</p>
      <div>
        <p>Validation observation: {freshnessLabel(data.system.observation)}</p>
        <p>Observed ledger: {data.system.observation.ledger_status || 'unknown'}</p>
        {data.system.observation.window_complete && <p>Observation window complete</p>}
      </div>
    </div>}
    {['orders', 'analytics', 'risk'].includes(section) && <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
      {Object.entries(data.exchanges).map(([exchange, metrics]) => <article key={exchange} className="rounded-lg border border-[#ff3555]/25 bg-[#080305]/70 p-3 min-w-0 text-xs space-y-2">
        <h3 className="uppercase font-semibold text-[#ff6375]">{exchange}</h3>
        {section === 'analytics' ? <>
          <p>Realized net PnL: ${metrics.realized_pnl_usd.toFixed(2)}</p>
          <p>Unrealized PnL: ${metrics.unrealized_pnl_usd.toFixed(2)}</p>
          <p>Execution fees: ${metrics.fees_usd.toFixed(4)} · Fills: {metrics.fills}</p>
          <p>Account: {freshnessLabel(metrics.account_freshness)}</p>
        </> : <>
          {section === 'orders' && <p className="break-words">Orders: {Object.entries(metrics.orders_by_status).map(([status, count]) => `${status}: ${count}`).join(' · ') || 'None'}</p>}
          <p className="font-semibold">Order rejections by reason</p>
          {Object.entries(metrics.rejections_by_reason).map(([reason, count]) => <p className="break-words" key={reason}>{reason}: {count}</p>)}
          {!Object.keys(metrics.rejections_by_reason).length && <p>No recorded rejections</p>}
        </>}
        <p className="text-slate-400">Last fill: {freshnessLabel(metrics.fill_freshness)}</p>
        <p className="text-slate-400">Last order: {freshnessLabel(metrics.order_freshness)}</p>
      </article>)}
    </div>}
    {section === 'analytics' && <p className="text-xs text-slate-400">{data.pnl_semantics}</p>}
    {['scanner', 'opportunities'].includes(section) && <div className="text-sm space-y-2">
      <p>Cached market snapshots: {data.screening.cached_market_snapshots} · Active signals: {data.screening.active_signals}</p>
      <p>Engine cycle: {freshnessLabel(data.system.cycle)}</p>
      <p className="text-xs text-slate-400">{data.screening.note}</p>
    </div>}
    {section === 'risk' && <p className="text-sm">Emergency stop: {data.risk.emergency_stop_active ? 'ACTIVE' : 'inactive'} · Live execution: {data.risk.live_execution_disabled ? 'disabled' : 'UNSAFE'}</p>}
  </section>;
}

export function DiagnosticsPanel({ section, initData }: { section: string; initData?: string }) {
  const [data, setData] = useState<PaperDiagnostics | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let controller: AbortController | undefined;
    const refresh = async () => {
      controller?.abort();
      controller = new AbortController();
      const current = controller;
      try {
        const result = await quantApi.getDiagnostics(initData, current.signal);
        if (!current.signal.aborted) { setData(result); setError(null); }
      } catch (err) {
        if (!current.signal.aborted) setError(err instanceof Error ? err.message : 'Diagnostics unavailable');
      }
    };
    void refresh();
    const timer = setInterval(() => { if (document.visibilityState === 'visible') void refresh(); }, 15000);
    return () => { clearInterval(timer); controller?.abort(); };
  }, [initData]);
  if (error) return <p role="alert" className="nq-panel p-4 mb-6 text-amber-300">Diagnostics unavailable: {error}</p>;
  if (!data) return <p role="status" className="p-4 text-slate-400">Loading paper diagnostics…</p>;
  return <DiagnosticsContent data={data} section={section} />;
}
