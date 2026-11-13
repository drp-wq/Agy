import React from 'react';
import { ShieldAlert, AlertTriangle, CheckCircle2 } from 'lucide-react';
import { ExchangeId, MiniAppHealthResponse, MiniAppStateResponse } from '../../types/quant';
import { EXCHANGE_META, SectionHeader } from '../common/TerminalPrimitives';
import { formatAgeSeconds, formatSignedUsd, formatUsd } from '../../utils/formatters';

interface RiskCommandViewProps {
  state: MiniAppStateResponse;
  health: MiniAppHealthResponse | null;
}

export const RiskCommandView: React.FC<RiskCommandViewProps> = ({ state, health }) => {
  const { risk } = state;

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Risk Command Center"
        subtitle="Cross-exchange margin exposure, drawdown limits, circuit-breaker state, and API authentication matrix"
        actions={
          <div
            className={`text-xs font-mono font-semibold flex items-center gap-1.5 ${
              risk.emergency_stop_active ? 'text-[#EF4444]' : 'text-[#10B981]'
            }`}
          >
            {risk.emergency_stop_active ? <ShieldAlert className="w-4 h-4" /> : <CheckCircle2 className="w-4 h-4" />}
            <span>
              {risk.emergency_stop_active ? 'EMERGENCY STOP ENGAGED' : `CIRCUIT BREAKER: ${risk.circuit_breaker_state}`}
            </span>
          </div>
        }
      />

      {/* Core Risk Gauges */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Daily Realized / Unrealized</div>
          <div
            className={`text-xl font-mono font-bold mt-1 ${
              risk.daily_realized_pnl_usd + risk.daily_unrealized_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'
            }`}
          >
            {formatSignedUsd(risk.daily_realized_pnl_usd + risk.daily_unrealized_pnl_usd)}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1">
            Realized {formatSignedUsd(risk.daily_realized_pnl_usd)}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Drawdown vs Limit</div>
          <div className="text-xl font-mono font-bold text-white mt-1">
            -{risk.current_drawdown_pct.toFixed(2)}% / -{risk.drawdown_limit_pct.toFixed(1)}%
          </div>
          <div className="w-full h-1.5 bg-white/10 rounded-full mt-2 overflow-hidden">
            <div
              className="h-full bg-[#00E5FF] rounded-full"
              style={{ width: `${Math.min(100, (risk.current_drawdown_pct / risk.drawdown_limit_pct) * 100)}%` }}
            />
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Gross Margin / Net Delta</div>
          <div className="text-xl font-mono font-bold text-[#00E5FF] mt-1">
            {formatUsd(risk.gross_margin_exposure_usd)}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1">
            Net Delta: {formatUsd(risk.net_delta_exposure_usd)}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Risk Capital Allocated</div>
          <div className="text-xl font-mono font-bold text-white mt-1">
            {risk.risk_allocation_pct.toFixed(2)}%
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1">Paper Equity Reserve Active</div>
        </div>
      </div>

      {/* Equity By Exchange + Asset Concentration */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="nq-panel rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white pb-3 border-b border-white/[0.08]">
            Portfolio Equity & Auth Matrix by Exchange
          </h3>
          <div className="divide-y divide-white/[0.06] mt-2 text-xs font-mono">
            {(['binance', 'bybit', 'okx'] as ExchangeId[]).map((exId) => {
              const ex = state.exchanges[exId];
              const adapter = health?.adapters[exId];
              const meta = EXCHANGE_META[exId];
              return (
                <div key={exId} className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                  <div>
                    <div className={`font-semibold ${meta.accentText}`}>{meta.name}</div>
                    <div className="text-[11px] text-slate-400 mt-0.5">
                      Public Feed: {ex.public_feed_ok ? 'OK' : 'Offline'} · Private Auth:{' '}
                      <span className={ex.private_auth_ok ? 'text-[#10B981]' : 'text-amber-400'}>
                        {ex.private_auth_ok ? 'Verified' : 'Unauthenticated'}
                      </span>
                    </div>
                    {adapter && !adapter.private_auth_ok && (
                      <div className="text-[10px] text-slate-500 mt-0.5">{adapter.private_auth_note}</div>
                    )}
                  </div>
                  <div className="text-right">
                    <div className="text-white font-semibold">{formatUsd(ex.paper_equity_usd)}</div>
                    <div className="text-[10px] text-slate-400">
                      Sync {formatAgeSeconds(ex.last_sync_ts)}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white pb-3 border-b border-white/[0.08]">
            Concentration by Underlying Asset
          </h3>
          <div className="space-y-3 mt-3 text-xs font-mono">
            {risk.concentration_by_asset.map((item) => (
              <div key={item.asset}>
                <div className="flex justify-between text-slate-200 mb-1">
                  <span className="font-semibold text-white">{item.asset}</span>
                  <span>
                    {formatUsd(item.exposure_usd)} ({item.share_pct.toFixed(1)}%)
                  </span>
                </div>
                <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-gradient-to-r from-[#00E5FF] to-[#8B5CF6] rounded-full"
                    style={{ width: `${Math.min(100, item.share_pct)}%` }}
                  />
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Stale-Data Warnings & Reconciliation Errors */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="nq-panel rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white pb-3 border-b border-white/[0.08]">
            Stale-Data & Feed Integrity Warnings
          </h3>
          {risk.stale_data_warnings.length === 0 ? (
            <p className="text-xs text-[#10B981] py-4 font-mono">All feeds within freshness thresholds.</p>
          ) : (
            <div className="divide-y divide-white/[0.06] mt-2 text-xs">
              {risk.stale_data_warnings.map((w, idx) => (
                <div key={idx} className="py-3 flex items-start gap-2.5">
                  <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                  <div>
                    <div className="font-semibold text-amber-300">{w.component}</div>
                    <div className="text-slate-300 mt-0.5">{w.message}</div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="nq-panel rounded-xl p-4">
          <h3 className="text-sm font-semibold text-white pb-3 border-b border-white/[0.08]">
            Ledger Reconciliation Audit
          </h3>
          {risk.reconciliation_errors.length === 0 ? (
            <div className="py-4 flex items-center gap-2 text-xs font-mono text-[#10B981]">
              <CheckCircle2 className="w-4 h-4" />
              <span>Zero paper-ledger reconciliation discrepancies detected.</span>
            </div>
          ) : (
            <div className="divide-y divide-white/[0.06] mt-2 text-xs">
              {risk.reconciliation_errors.map((err) => (
                <div key={err.id} className="py-3">
                  <div className="font-semibold text-[#EF4444] uppercase">
                    {err.exchange} · {err.severity}
                  </div>
                  <div className="text-slate-300 mt-0.5">{err.detail}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
