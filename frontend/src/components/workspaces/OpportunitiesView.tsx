import React, { useState } from 'react';
import { CheckCircle2, XCircle, Eye, LineChart } from 'lucide-react';
import { MarketOpportunity, MiniAppStateResponse } from '../../types/quant';
import { EXCHANGE_META, EmptyStatePanel, SectionHeader } from '../common/TerminalPrimitives';
import { formatPrice, formatUsd } from '../../utils/formatters';

interface OpportunitiesViewProps {
  state: MiniAppStateResponse;
  onInspectChart: (opp: MarketOpportunity) => void;
}

export const OpportunitiesView: React.FC<OpportunitiesViewProps> = ({ state, onInspectChart }) => {
  const [decisionFilter, setDecisionFilter] = useState<'ALL' | 'APPROVED' | 'WATCHLIST' | 'REJECTED'>('ALL');

  const items = state.opportunities.filter(
    (o) => decisionFilter === 'ALL' || o.estimates.screening_decision === decisionFilter
  );

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Trading Opportunities & Screening Audit"
        subtitle="Strategy projections, estimated execution costs, risk/reward filters, and explicit rejection reasons"
        actions={
          <div className="flex items-center p-1 rounded-lg bg-[#0B1120] border border-white/[0.08]">
            {(['ALL', 'APPROVED', 'WATCHLIST', 'REJECTED'] as const).map((status) => (
              <button
                key={status}
                onClick={() => setDecisionFilter(status)}
                className={`px-3 py-1.5 min-h-[36px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
                  decisionFilter === status
                    ? 'bg-[#00E5FF]/20 text-[#00E5FF]'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {status}
              </button>
            ))}
          </div>
        }
      />

      {items.length === 0 ? (
        <EmptyStatePanel
          title="No Opportunities Under Current Decision Filter"
          description="Switch the screening decision filter to review approved, watchlisted, or rejected signals."
          actionLabel="Show All Setups"
          onAction={() => setDecisionFilter('ALL')}
        />
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {items.map((opp) => {
            const { confirmed, estimates } = opp;
            const meta = EXCHANGE_META[opp.exchange];
            const isApproved = estimates.screening_decision === 'APPROVED';
            const isRejected = estimates.screening_decision === 'REJECTED';

            return (
              <div key={opp.id} className="nq-panel rounded-xl p-5 flex flex-col justify-between gap-4">
                <div>
                  {/* Header */}
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <div className="flex items-center gap-2 text-xs font-mono">
                        <span className={`font-semibold uppercase ${meta.accentText}`}>{opp.exchange}</span>
                        <span className="text-slate-500">·</span>
                        <span className="text-slate-300 uppercase">{opp.market_type}</span>
                      </div>
                      <h3 className="text-lg font-mono font-bold text-white mt-0.5">
                        {opp.symbol}{' '}
                        <span
                          className={`text-xs font-semibold ml-1 ${
                            estimates.direction === 'LONG'
                              ? 'text-[#10B981]'
                              : estimates.direction === 'SHORT'
                                ? 'text-[#EF4444]'
                                : 'text-slate-400'
                          }`}
                        >
                          {estimates.direction}
                        </span>
                      </h3>
                    </div>

                    <div
                      className={`flex items-center gap-1.5 text-xs font-mono font-semibold ${
                        isApproved ? 'text-[#10B981]' : isRejected ? 'text-[#EF4444]' : 'text-amber-400'
                      }`}
                    >
                      {isApproved && <CheckCircle2 className="w-4 h-4" />}
                      {isRejected && <XCircle className="w-4 h-4" />}
                      {!isApproved && !isRejected && <Eye className="w-4 h-4" />}
                      <span>{estimates.screening_decision}</span>
                    </div>
                  </div>

                  {/* Two-Section Hairline Split: Confirmed vs Estimated */}
                  <div className="mt-4 pt-3 border-t border-white/[0.08] grid grid-cols-2 gap-4 text-xs font-mono">
                    <div>
                      <div className="text-[11px] text-slate-400 font-sans mb-1.5">Confirmed Market Data</div>
                      <div className="space-y-1 text-slate-300">
                        <div className="flex justify-between">
                          <span className="text-slate-500">Last:</span>
                          <span className="text-white">{formatPrice(confirmed.last_price)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-500">Spread:</span>
                          <span>{confirmed.spread_bps.toFixed(2)} bps</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-500">24h Vol:</span>
                          <span>{formatUsd(confirmed.volume_24h_usd, true)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-500">Regime:</span>
                          <span className="truncate max-w-[110px]">{confirmed.regime}</span>
                        </div>
                      </div>
                    </div>

                    <div className="pl-4 border-l border-white/[0.08]">
                      <div className="text-[11px] text-[#8B5CF6] font-sans mb-1.5">Strategy Model Estimates</div>
                      <div className="space-y-1 text-slate-300">
                        <div className="flex justify-between">
                          <span className="text-slate-500">Entry:</span>
                          <span className="text-[#00E5FF]">{formatPrice(estimates.suggested_entry)}</span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-500">SL / TP:</span>
                          <span>
                            {estimates.suggested_sl > 0 ? formatPrice(estimates.suggested_sl) : '—'} /{' '}
                            {estimates.suggested_tp > 0 ? formatPrice(estimates.suggested_tp) : '—'}
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-500">R:R / Conf:</span>
                          <span>
                            {estimates.risk_reward_ratio.toFixed(2)} · {estimates.confidence_pct}%
                          </span>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-slate-500">Est Cost:</span>
                          <span>{(estimates.est_fees_bps + estimates.est_slippage_bps).toFixed(1)} bps</span>
                        </div>
                      </div>
                    </div>
                  </div>

                  {/* Rejection / Watchlist Notes */}
                  {estimates.rejection_reasons.length > 0 && (
                    <div className="mt-3 pt-3 border-t border-white/[0.06] text-xs">
                      <div className="text-[11px] text-slate-400 mb-1">Screening Gatekeeper Notes:</div>
                      <ul className="space-y-1 text-slate-300 font-mono text-[11px]">
                        {estimates.rejection_reasons.map((reason, idx) => (
                          <li key={idx} className={isRejected ? 'text-[#EF4444]' : 'text-amber-300'}>
                            • {reason}
                          </li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>

                {/* Action Footer */}
                <div className="pt-3 border-t border-white/[0.06] flex items-center justify-between text-xs">
                  <span className="font-mono text-slate-400 truncate">{estimates.strategy_name}</span>
                  <button
                    onClick={() => onInspectChart(opp)}
                    className="px-3 py-1.5 min-h-[36px] rounded-lg bg-[#00E5FF]/10 hover:bg-[#00E5FF]/20 border border-[#00E5FF]/30 text-[#00E5FF] font-medium flex items-center gap-1.5 whitespace-nowrap shrink-0 transition-colors"
                  >
                    <LineChart className="w-3.5 h-3.5" />
                    <span>Inspect on Chart</span>
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
