import React, { useCallback, useEffect, useState } from 'react';
import { fetchVioletPnl, type VioletPnlReport } from '../../services/violetResearchApi';
import { TradeSymbolLink } from '../charts/TradeSymbolLink';
import type { ExchangeId } from '../../types/quant';

type Props = { kind: 'positions' | 'orders' | 'exchange'; exchange?: ExchangeId; initData?: string };

/** Active campaign ledger only. Never mixes historical or legacy simulator balances into trading views. */
export const ActiveCampaignTradesView: React.FC<Props> = ({ kind, exchange, initData }) => {
  const [report, setReport] = useState<VioletPnlReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    try { setReport(await fetchVioletPnl({}, initData)); setError(null); }
    catch (err) { setReport(null); setError(err instanceof Error ? err.message : 'Campaign data unavailable'); }
  }, [initData]);
  useEffect(() => { void refresh(); const id = window.setInterval(() => void refresh(), 30000); return () => window.clearInterval(id); }, [refresh]);
  const assets = (report?.assets ?? []).filter((a) => (!exchange || a.exchange.toLowerCase() === exchange) && (kind !== 'positions' || a.status.toLowerCase() === 'open'));
  const events = (report?.ledger ?? []).filter((e) => (!exchange || e.exchange.toLowerCase() === exchange) && /ORDER|FILL|TRADE/.test(e.event_type.toUpperCase()));
  return <section className="space-y-4" aria-label="Active 500 USDT paper campaign trades">
    <div className="nq-panel rounded-xl border border-[#ff3555]/35 p-4 flex flex-wrap justify-between gap-3">
      <div><h2 className="text-lg font-bold text-[#ffe3e5]">{kind === 'positions' ? 'Active Campaign Positions' : kind === 'orders' ? 'Active Campaign Orders & Fills' : `${exchange?.toUpperCase()} · Active Campaign`}</h2>
        <p className="text-xs font-mono text-[#d4a5ac]">500 USDT PAPER ONLY · Current campaign ledger · Archived and legacy assets excluded</p></div>
      <button type="button" onClick={() => void refresh()} className="rounded-lg border border-[#ff3555]/40 px-3 py-2 text-xs text-[#ffe3e5]">Refresh</button>
    </div>
    {error && <p role="alert" className="text-[#ffb16c] text-sm">Unable to verify current campaign: {error}</p>}
    {!error && !report && <p className="text-[#d4a5ac] text-sm">Loading verified active campaign ledger…</p>}
    {report && <div className="grid grid-cols-2 md:grid-cols-4 gap-3">{[
      ['Starting capital', report.overview.starting_capital_usdt],['Current equity',report.overview.current_equity_usdt],
      ['Available cash',report.overview.available_cash_usdt],['Open positions',report.overview.active_positions]
    ].map(([label,value])=><div key={label} className="nq-panel rounded-xl p-3"><div className="text-xs text-[#c28e95]">{label}</div><div className="font-mono text-lg text-[#ffe3e5]">{typeof value==='number'?value.toFixed(label==='Open positions'?0:2):'—'}{label==='Open positions'?'':' USDT'}</div></div>)}</div>}
    {report && (kind === 'orders' ? <div className="nq-panel rounded-xl p-4 overflow-x-auto"><h3 className="text-sm text-[#ffe3e5] mb-3">Current Campaign Order & Fill Events</h3>{events.length===0?<p className="text-[#c28e95] text-sm">No orders or fills in the active campaign.</p>:<table className="w-full text-xs font-mono"><thead><tr className="text-[#c28e95] text-left"><th>Time</th><th>Event</th><th>Symbol</th><th>Amount</th></tr></thead><tbody>{events.map(e=><tr key={e.event_id} className="border-t border-[#ff3555]/15"><td className="py-2">{e.timestamp_utc}</td><td>{e.event_type}</td><td>{e.symbol && <TradeSymbolLink exchange={e.exchange as ExchangeId} marketType="spot" symbol={e.symbol}/>}</td><td>{e.amount_usd} USDT</td></tr>)}</tbody></table>}</div>
      : <div className="nq-panel rounded-xl p-4 overflow-x-auto"><h3 className="text-sm text-[#ffe3e5] mb-3">{kind==='positions'?'Current Open Positions':'Campaign Asset Positions'}</h3>{assets.length===0?<p className="text-[#c28e95] text-sm">No {kind==='positions'?'open positions':'campaign positions'} on this account.</p>:<table className="w-full text-xs font-mono"><thead><tr className="text-[#c28e95] text-left"><th>Exchange</th><th>Symbol</th><th>Status</th><th>Quantity</th><th>Value</th><th>Unrealized P&L</th></tr></thead><tbody>{assets.map((a,i)=><tr key={`${a.exchange}:${a.asset}:${i}`} className="border-t border-[#ff3555]/15"><td className="py-2">{a.exchange}</td><td><TradeSymbolLink exchange={a.exchange as ExchangeId} marketType="spot" symbol={a.asset.includes('/')?a.asset:`${a.asset}/USDT`}/></td><td>{a.status}</td><td>{a.qty}</td><td>{a.notional_usd.toFixed(2)}</td><td>{a.unrealized_pnl_usd.toFixed(2)}</td></tr>)}</tbody></table>}</div>)}
  </section>;
};
