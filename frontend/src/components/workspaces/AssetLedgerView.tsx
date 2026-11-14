import React, { useCallback, useEffect, useState } from 'react';
import { BookOpen, RefreshCw, ShieldCheck } from 'lucide-react';
import { fetchVioletPnl, VioletPnlReport } from '../../services/violetResearchApi';

interface AssetLedgerViewProps {
  initData?: string;
}

export const AssetLedgerView: React.FC<AssetLedgerViewProps> = ({ initData }) => {
  const [report, setReport] = useState<VioletPnlReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [eventFilter, setEventFilter] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await fetchVioletPnl({}, initData);
      setReport(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Unable to load Asset Ledger');
    } finally {
      setLoading(false);
    }
  }, [initData]);

  useEffect(() => {
    load();
  }, [load]);

  const events = (report?.ledger ?? []).filter(
    (ev) => !eventFilter || ev.event_type.toLowerCase().includes(eventFilter.toLowerCase()) || ev.symbol.toLowerCase().includes(eventFilter.toLowerCase())
  );

  return (
    <div className="space-y-5">
      <div className="nq-panel rounded-xl p-4 sm:p-5 border border-[#ff3555]/30 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <BookOpen className="w-5 h-5 text-[#ff6375]" />
            <h2 className="font-display font-bold text-lg text-[#ffe3e5]">Asset Ledger &amp; Audit Journal</h2>
            <span className="px-2 py-0.5 rounded bg-[#10B981]/15 border border-[#10B981]/35 text-[#10B981] text-[10px] font-mono">
              APPEND-ONLY · DECIMAL ACCOUNTING
            </span>
          </div>
          <p className="text-xs font-mono text-[#c28e95] mt-1">
            Immutable event journal from capital allocation through fills, fees, funding, and realized closures
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          disabled={loading}
          className="px-3 py-1.5 rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/45 text-[#ffe3e5] text-xs font-mono inline-flex items-center gap-1.5"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          <span>Refresh Ledger</span>
        </button>
      </div>

      {error && (
        <div className="rounded-xl bg-[#EF4444]/10 border border-[#EF4444]/40 px-4 py-3 text-xs font-mono text-[#ffe3e5]">
          {error}
        </div>
      )}

      {report?.reconciliation && (
        <div className="nq-panel rounded-xl p-3.5 border border-[#10B981]/30 flex flex-wrap items-center justify-between gap-2 text-xs font-mono">
          <span className="inline-flex items-center gap-1.5 text-[#10B981]">
            <ShieldCheck className="w-4 h-4" />
            Deterministic Reconciliation: {report.reconciliation.balanced ? 'BALANCED' : 'DRIFT DETECTED'}
          </span>
          <span className="text-[#ffe3e5]">
            Expected {report.reconciliation.expected_equity_usd.toFixed(2)} USDT · Actual {report.reconciliation.actual_equity_usd.toFixed(2)} USDT · Drift {report.reconciliation.drift_usd.toFixed(4)} USDT
          </span>
        </div>
      )}

      <div className="nq-panel rounded-xl p-4 border border-[#ff3555]/20 space-y-3 overflow-x-auto">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-mono text-[#d4a5ac]">Immutable Ledger Events ({events.length})</span>
          <input
            type="text"
            placeholder="Filter by event type or symbol..."
            value={eventFilter}
            onChange={(e) => setEventFilter(e.target.value)}
            className="px-2.5 py-1.5 rounded bg-[#080305] border border-[#ff3555]/30 text-xs font-mono text-[#ffe3e5] w-64"
          />
        </div>
        <table className="w-full text-left text-xs font-mono">
          <thead>
            <tr className="border-b border-[#ff3555]/25 text-[#c28e95]">
              <th className="py-2 pr-3">Seq</th>
              <th className="py-2 pr-3">Timestamp (UTC)</th>
              <th className="py-2 pr-3">Event Type</th>
              <th className="py-2 pr-3">Asset</th>
              <th className="py-2 pr-3">Cap Band</th>
              <th className="py-2 pr-3">Strategy</th>
              <th className="py-2 pr-3">Amount (USDT)</th>
              <th className="py-2 pr-3">Fee (USDT)</th>
              <th className="py-2">Realized PnL</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 ? (
              <tr>
                <td colSpan={9} className="py-6 text-center text-[#a1737a]">
                  No ledger events recorded yet.
                </td>
              </tr>
            ) : (
              events.map((ev) => (
                <tr key={ev.event_id} className="border-b border-[#ff3555]/10 text-[#ffe3e5]">
                  <td className="py-2 pr-3 text-[#a1737a]">#{ev.seq}</td>
                  <td className="py-2 pr-3">{ev.timestamp_utc.slice(0, 19).replace('T', ' ')}</td>
                  <td className="py-2 pr-3 text-[#ff6375]">{ev.event_type}</td>
                  <td className="py-2 pr-3">{ev.symbol || ev.bot_id} <span className="text-[#a1737a] uppercase">{ev.exchange}</span></td>
                  <td className="py-2 pr-3 uppercase">{ev.market_cap_band}</td>
                  <td className="py-2 pr-3">{ev.strategy || '—'}</td>
                  <td className="py-2 pr-3">{Number(ev.amount_usd).toFixed(4)}</td>
                  <td className="py-2 pr-3">{Number(ev.fee_usd).toFixed(4)}</td>
                  <td className="py-2">{Number(ev.realized_pnl_usd).toFixed(4)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
};
