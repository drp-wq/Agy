import React, { useEffect, useState } from 'react';
import { ShieldCheck } from 'lucide-react';
import type { ExchangeId, FuturesPosition, MarketType, MiniAppStateResponse, NavSectionId, Timeframe } from '../../types/quant';
import { SectionHeader } from '../common/TerminalPrimitives';
import { ProMarketChart } from '../charts/ProMarketChart';
import { VioletCampaignPanel } from './VioletCampaignPanel';
import { ActiveCampaignTradesView } from './ActiveCampaignTradesView';
import { fetchVioletResearch, type VioletResearchPayload } from '../../services/violetResearchApi';

type Props = { state: MiniAppStateResponse; initData?: string; onNavigate: (section: NavSectionId) => void; onInspectPosition: (pos: FuturesPosition) => void };

/** The overview is intentionally scoped to one 500 USDT campaign. Scanner quotes are research, never account equity. */
export const GlobalOverviewView: React.FC<Props> = ({ state, initData, onNavigate }) => {
  const [research, setResearch] = useState<VioletResearchPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [chartExchange, setChartExchange] = useState<ExchangeId>('binance');
  const [chartMarket, setChartMarket] = useState<MarketType>('spot');
  const [chartSymbol, setChartSymbol] = useState('BTC/USDT');
  const [chartTf, setChartTf] = useState<Timeframe>('15m');
  useEffect(() => {
    const controller = new AbortController();
    const load = () => fetchVioletResearch(initData, controller.signal).then(data => { if (!controller.signal.aborted) { setResearch(data); setError(null); } }).catch(err => { if (!controller.signal.aborted) { setResearch(null); setError(err instanceof Error ? err.message : 'Campaign telemetry unavailable'); } });
    void load(); const id = window.setInterval(() => void load(), 30000);
    return () => { controller.abort(); window.clearInterval(id); };
  }, [initData]);
  return <div className="space-y-6">
    <SectionHeader title="VIOLET · Active 500 USDT Paper Account" subtitle="Only the current campaign ledger contributes to account balances, positions and P&L" actions={<div className="flex items-center gap-2 text-xs font-mono text-[#10B981]"><ShieldCheck className="w-4 h-4"/> PAPER ONLY</div>}/>
    <VioletCampaignPanel research={research} error={error}/>
    <div className="flex flex-wrap gap-2 text-xs font-mono">
      <button className="rounded border border-[#ff3555]/40 px-3 py-2 text-[#ffe3e5]" onClick={() => onNavigate('positions')}>Campaign Positions</button>
      <button className="rounded border border-[#ff3555]/40 px-3 py-2 text-[#ffe3e5]" onClick={() => onNavigate('orders')}>Campaign Orders</button>
      <button className="rounded border border-[#ff3555]/40 px-3 py-2 text-[#ffe3e5]" onClick={() => onNavigate('pnl')}>Campaign P&L</button>
    </div>
    <ActiveCampaignTradesView kind="positions" initData={initData}/>
    <ProMarketChart exchange={chartExchange} marketType={chartMarket} symbol={chartSymbol} timeframe={chartTf} onExchangeChange={setChartExchange} onMarketTypeChange={setChartMarket} onSymbolChange={setChartSymbol} onTimeframeChange={setChartTf} activePosition={null} initData={initData}/>
    <div className="nq-panel rounded-xl p-4 text-xs text-[#d4a5ac]">Market chart uses public quotes for research only. It does not represent holdings or active trades. <span className="text-[#ff6375]">Legacy preview balances and separate simulator treasuries are excluded.</span> Scanner feed: {state.generated_at ? 'available for research' : 'unavailable'}.</div>
  </div>;
};
