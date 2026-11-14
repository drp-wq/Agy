import React, { useEffect, useState } from 'react';
import { fetchUnifiedBrainStatus, fetchPersistenceHealth, type UnifiedBrainStatusPayload, type PersistenceHealthPayload } from '../../services/violetResearchApi';
import { ActiveCampaignTradesView } from './ActiveCampaignTradesView';

type Props = { initData?: string };
/** Keep independent engine diagnostics, but never display its legacy simulated custody as campaign money. */
export const ActiveCampaignBrainView: React.FC<Props> = ({ initData }) => {
  const [brain, setBrain] = useState<UnifiedBrainStatusPayload | null>(null);
  const [health, setHealth] = useState<PersistenceHealthPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      const [b, h] = await Promise.allSettled([fetchUnifiedBrainStatus(initData, controller.signal), fetchPersistenceHealth(initData, controller.signal)]);
      if (controller.signal.aborted) return;
      setBrain(b.status === 'fulfilled' ? b.value : null);
      setHealth(h.status === 'fulfilled' ? h.value : null);
      setError(b.status === 'rejected' ? 'Brain status unavailable' : null);
    };
    void load(); const id = window.setInterval(() => void load(), 30000);
    return () => { controller.abort(); window.clearInterval(id); };
  }, [initData]);
  const notes = brain?.last_report?.skipped_reasons ?? [];
  return <section className="space-y-5">
    <div className="nq-panel rounded-xl p-5 border border-[#ff3555]/30"><h2 className="text-xl font-bold text-[#ffe3e5]">AI Brain · Active 500 USDT Campaign</h2><p className="text-xs font-mono text-[#c28e95]">Paper-only monitoring · Legacy closed-loop simulated treasury excluded from account figures</p></div>
    <ActiveCampaignTradesView kind="positions" initData={initData}/>
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
      <div className="nq-panel rounded-xl p-4"><div className="text-xs text-[#c28e95]">Brain mode</div><div className="font-mono text-[#ffe3e5]">{brain?.mode ?? 'UNAVAILABLE'}</div></div>
      <div className="nq-panel rounded-xl p-4"><div className="text-xs text-[#c28e95]">Last cycle</div><div className="font-mono text-[#ffe3e5]">{brain?.last_report?.actions_executed?.length ?? '—'} actions</div></div>
      <div className="nq-panel rounded-xl p-4"><div className="text-xs text-[#c28e95]">Persistence</div><div className="font-mono text-[#ffe3e5]">{health?.status ?? 'UNAVAILABLE'}</div></div>
    </div>
    {(error || notes.length > 0) && <div role="alert" className="nq-panel rounded-xl p-4 border border-[#ffb16c]/40 text-sm text-[#ffb16c]"><strong>AI diagnostics — not verified as ready</strong><div className="mt-2 font-mono text-xs">{error ?? notes.join(' · ')}</div></div>}
  </section>;
};
