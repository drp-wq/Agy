import React, { useMemo, useState } from 'react';
import {
  CheckCircle2,
  AlertTriangle,
  Lock,
  Landmark,
  FileCheck2,
  Users,
  Wallet
} from 'lucide-react';
import { ExchangeId, MiniAppStateResponse } from '../../types/quant';
import { EmptyStatePanel, EXCHANGE_META, SectionHeader } from '../common/TerminalPrimitives';

interface Props {
  state: MiniAppStateResponse;
}

function fmtDec(val: string | number | undefined | null, digits = 2): string {
  if (val === undefined || val === null || val === '') return '0.00';
  const num = Number(val);
  if (!Number.isFinite(num)) return String(val);
  return num.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits
  });
}

function fmtPct(val: string | number | undefined | null): string {
  if (val === undefined || val === null || val === '') return '0.00%';
  const num = Number(val);
  if (!Number.isFinite(num)) return String(val);
  return `${(num * 100).toFixed(2)}%`;
}

export const TreasuryAuditView: React.FC<Props> = ({ state }) => {
  const [exchangeFilter, setExchangeFilter] = useState<'all' | ExchangeId>('all');
  const [statusFilter, setStatusFilter] = useState<'all' | 'active' | 'idle'>('all');

  const treasury = state.unified_treasury;
  const recon = state.treasury_reconciliation;
  const pnlAudit = state.paper_pnl_audit;
  const slots = treasury?.slots || [];

  const filteredSlots = useMemo(() => {
    return slots.filter((slot) => {
      if (exchangeFilter !== 'all' && slot.exchange !== exchangeFilter) return false;
      if (statusFilter !== 'all' && slot.status.toLowerCase() !== statusFilter) return false;
      return true;
    });
  }, [slots, exchangeFilter, statusFilter]);

  if (!treasury || !treasury.enabled) {
    return (
      <div className="space-y-6">
        <SectionHeader
          title="18-Agent Treasury & Paper PnL Audit"
          subtitle="Single master pool paper accounting and reconciliation verification"
        />
        <EmptyStatePanel
          title="Unified Treasury Disabled"
          description="The 18-agent unified treasury feature flag is currently disabled on the backend engine."
        />
      </div>
    );
  }

  const masterPool = recon?.master_pool;
  const usdVal = recon?.usd_valuation;
  const unexplainedNum = pnlAudit ? Number(pnlAudit.unexplained_usd) : 0;
  const hasUnexplained = Number.isFinite(unexplainedNum) && Math.abs(unexplainedNum) > 0.01;

  return (
    <div className="space-y-6">
      <SectionHeader
        title="18-Agent Treasury & Paper PnL Audit"
        subtitle="Read-only paper accounting ledger, master pool conservation, and realized PnL attribution audit"
        actions={
          <span className="px-2.5 py-1 rounded-md bg-[#10B981]/15 border border-[#10B981]/30 text-[#10B981] text-[11px] font-mono inline-flex items-center gap-1.5">
            <Lock className="w-3 h-3" />
            <span>PAPER ACCOUNTING ONLY</span>
          </span>
        }
      />

      {/* Master Pool & Conservation KPIs */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 sm:gap-4">
        <div className="nq-panel rounded-xl p-4 space-y-1">
          <div className="text-[11px] text-slate-400 flex items-center justify-between">
            <span>Master Pool Total</span>
            <Landmark className="w-3.5 h-3.5 text-[#00E5FF]" />
          </div>
          <div className="text-lg sm:text-xl font-mono font-semibold text-white">
            {fmtDec(masterPool?.total_capital)} {masterPool?.asset || 'USDT'}
          </div>
          <div className="text-[11px] font-mono text-slate-400">
            {usdVal ? `≈ $${fmtDec(usdVal.total_usd)} (${usdVal.fx_pair})` : 'Paper Pool'}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4 space-y-1">
          <div className="text-[11px] text-slate-400">Free / Reserved Capital</div>
          <div className="text-lg sm:text-xl font-mono font-semibold text-[#10B981]">
            {fmtDec(masterPool?.free_capital)}
          </div>
          <div className="text-[11px] font-mono text-slate-400">
            Reserved: {fmtDec(masterPool?.reserved_capital)} · Slots: {fmtDec(masterPool?.slot_allocated_sum)}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4 space-y-1">
          <div className="text-[11px] text-slate-400 flex items-center justify-between">
            <span>18-Agent Slots</span>
            <Users className="w-3.5 h-3.5 text-[#8B5CF6]" />
          </div>
          <div className="text-lg sm:text-xl font-mono font-semibold text-white">
            {treasury.configured_slots ?? slots.length} / {treasury.max_agent_slots ?? 18}
          </div>
          <div className="text-[11px] font-mono text-slate-400">
            Active: {treasury.active_slots ?? 0} · Custody: {treasury.custody_model || 'paper_pool'}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4 space-y-1">
          <div className="text-[11px] text-slate-400">Ledger Conservation</div>
          <div className="flex items-center gap-2 pt-0.5">
            {recon?.balanced && recon?.pool_conservation_ok ? (
              <span className="inline-flex items-center gap-1.5 text-sm font-mono font-semibold text-[#10B981]">
                <CheckCircle2 className="w-4 h-4" />
                BALANCED
              </span>
            ) : (
              <span className="inline-flex items-center gap-1.5 text-sm font-mono font-semibold text-amber-400">
                <AlertTriangle className="w-4 h-4" />
                CHECK AUDIT
              </span>
            )}
          </div>
          <div className="text-[11px] font-mono text-slate-400">
            Journal entries: {recon?.journal_entries_checked ?? 0} · Imbalances: {recon?.journal_imbalances?.length ?? 0}
          </div>
        </div>
      </div>

      {/* Paper PnL Reconciliation Audit */}
      <div className="nq-panel rounded-xl p-4 sm:p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-3 border-b border-white/[0.08]">
          <div className="flex items-center gap-2">
            <FileCheck2 className="w-4 h-4 text-[#00E5FF]" />
            <h3 className="text-sm font-display font-semibold text-white">
              Paper PnL Reconciliation Audit
            </h3>
          </div>
          {pnlAudit && (
            <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono">
              <span
                className={`px-2 py-0.5 rounded border ${
                  pnlAudit.recorded_evidence_coverage_ok
                    ? 'bg-[#10B981]/15 border-[#10B981]/30 text-[#10B981]'
                    : 'bg-[#EF4444]/15 border-[#EF4444]/30 text-[#EF4444]'
                }`}
              >
                Evidence Coverage: {pnlAudit.recorded_evidence_coverage_ok ? 'OK' : 'GAP'}
              </span>
              <span
                className={`px-2 py-0.5 rounded border ${
                  pnlAudit.historical_completeness_certified
                    ? 'bg-[#10B981]/15 border-[#10B981]/30 text-[#10B981]'
                    : 'bg-amber-500/15 border-amber-500/30 text-amber-300'
                }`}
              >
                Historical Certified: {pnlAudit.historical_completeness_certified ? 'YES' : 'NO'}
              </span>
            </div>
          )}
        </div>

        {pnlAudit ? (
          <div className="space-y-4">
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3 text-xs font-mono">
              <div className="bg-[#05080F] border border-white/[0.06] rounded-lg p-3">
                <div className="text-[11px] text-slate-400">Account Realized</div>
                <div className="text-sm font-semibold text-white mt-1">
                  ${fmtDec(pnlAudit.account_realized_usd, 4)}
                </div>
              </div>
              <div className="bg-[#05080F] border border-white/[0.06] rounded-lg p-3">
                <div className="text-[11px] text-slate-400">Attributed Realized</div>
                <div className="text-sm font-semibold text-[#00E5FF] mt-1">
                  ${fmtDec(pnlAudit.attributed_realized_usd, 4)}
                </div>
              </div>
              <div className="bg-[#05080F] border border-white/[0.06] rounded-lg p-3">
                <div className="text-[11px] text-slate-400">Unexplained Delta</div>
                <div
                  className={`text-sm font-semibold mt-1 ${
                    hasUnexplained ? 'text-amber-400' : 'text-[#10B981]'
                  }`}
                >
                  ${fmtDec(pnlAudit.unexplained_usd, 4)}
                </div>
              </div>
              <div className="bg-[#05080F] border border-white/[0.06] rounded-lg p-3">
                <div className="text-[11px] text-slate-400">Paper Fills / Fees</div>
                <div className="text-sm font-semibold text-slate-200 mt-1">
                  {pnlAudit.paper_fills} / {pnlAudit.fee_attributions}
                </div>
              </div>
              <div className="bg-[#05080F] border border-white/[0.06] rounded-lg p-3">
                <div className="text-[11px] text-slate-400">Closures / Attrib</div>
                <div className="text-sm font-semibold text-slate-200 mt-1">
                  {pnlAudit.recorded_closures} / {pnlAudit.closure_attributions}
                </div>
              </div>
              <div className="bg-[#05080F] border border-white/[0.06] rounded-lg p-3">
                <div className="text-[11px] text-slate-400">Funding / Attrib</div>
                <div className="text-sm font-semibold text-slate-200 mt-1">
                  {pnlAudit.funding_settlements} / {pnlAudit.funding_attributions}
                </div>
              </div>
            </div>

            {pnlAudit.reason && (
              <div className="text-xs text-slate-300 bg-[#05080F] border border-white/[0.06] rounded-lg px-3 py-2 flex items-start gap-2">
                <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
                <span>{pnlAudit.reason}</span>
              </div>
            )}
          </div>
        ) : (
          <div className="text-xs text-slate-400">Paper PnL audit payload unavailable.</div>
        )}
      </div>

      {/* 18-Agent Slots */}
      <div className="nq-panel rounded-xl p-4 sm:p-5 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-display font-semibold text-white">
              Agent Allocation Slots ({filteredSlots.length})
            </h3>
            <p className="text-[11px] text-slate-400">
              Paper sub-accounts with per-agent risk caps and single-pool accounting
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {(['all', 'binance', 'bybit', 'okx'] as const).map((ex) => (
              <button
                key={ex}
                onClick={() => setExchangeFilter(ex)}
                className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium border transition-colors ${
                  exchangeFilter === ex
                    ? 'bg-[#00E5FF]/15 border-[#00E5FF]/40 text-[#00E5FF]'
                    : 'bg-[#05080F] border-white/[0.08] text-slate-400 hover:text-white'
                }`}
              >
                {ex === 'all' ? 'All Exchanges' : ex.toUpperCase()}
              </button>
            ))}
            {(['all', 'active', 'idle'] as const).map((st) => (
              <button
                key={st}
                onClick={() => setStatusFilter(st)}
                className={`px-2.5 py-1.5 rounded-lg text-[11px] font-medium border transition-colors ${
                  statusFilter === st
                    ? 'bg-[#8B5CF6]/20 border-[#8B5CF6]/40 text-[#C4B5FD]'
                    : 'bg-[#05080F] border-white/[0.08] text-slate-400 hover:text-white'
                }`}
              >
                {st.toUpperCase()}
              </button>
            ))}
          </div>
        </div>

        {/* Desktop Table */}
        <div className="hidden md:block overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead>
              <tr className="border-b border-white/[0.08] text-[11px] text-slate-400 font-mono">
                <th className="py-2 pr-3">#</th>
                <th className="py-2 pr-3">Agent</th>
                <th className="py-2 pr-3">Exchange</th>
                <th className="py-2 pr-3">Strategy</th>
                <th className="py-2 pr-3 text-right">Budget</th>
                <th className="py-2 pr-3 text-right">Reserved</th>
                <th className="py-2 pr-3 text-right">Available</th>
                <th className="py-2 pr-3 text-right">Max Lev</th>
                <th className="py-2 pr-3 text-right">Daily Loss Cap</th>
                <th className="py-2 text-right">Status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.05] font-mono">
              {filteredSlots.map((slot) => {
                const exMeta = EXCHANGE_META[slot.exchange as ExchangeId];
                return (
                  <tr key={slot.agent_id} className="hover:bg-white/[0.02]">
                    <td className="py-2.5 pr-3 text-slate-500">{slot.slot_index}</td>
                    <td className="py-2.5 pr-3">
                      <div className="text-white font-medium">{slot.agent_id}</div>
                      <div className="text-[10px] text-slate-500 font-sans truncate max-w-[200px]">
                        {slot.allowed_symbols.join(', ')}
                      </div>
                    </td>
                    <td className="py-2.5 pr-3">
                      <span className={exMeta ? exMeta.accentText : 'text-slate-300'}>
                        {slot.exchange.toUpperCase()}
                      </span>
                    </td>
                    <td className="py-2.5 pr-3 text-slate-300">{slot.strategy}</td>
                    <td className="py-2.5 pr-3 text-right text-white">{fmtDec(slot.capital_budget)}</td>
                    <td className="py-2.5 pr-3 text-right text-amber-300">{fmtDec(slot.reserved_capital)}</td>
                    <td className="py-2.5 pr-3 text-right text-[#10B981]">{fmtDec(slot.available_capital)}</td>
                    <td className="py-2.5 pr-3 text-right text-slate-300">{Number(slot.max_leverage).toFixed(1)}x</td>
                    <td className="py-2.5 pr-3 text-right text-slate-300">{fmtPct(slot.daily_loss_limit_pct)}</td>
                    <td className="py-2.5 text-right">
                      <span
                        className={`px-2 py-0.5 rounded text-[10px] ${
                          slot.status === 'active'
                            ? 'bg-[#10B981]/15 text-[#10B981]'
                            : 'bg-slate-800 text-slate-400'
                        }`}
                      >
                        {slot.status.toUpperCase()}
                      </span>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Mobile Cards */}
        <div className="md:hidden grid grid-cols-1 gap-2.5">
          {filteredSlots.map((slot) => (
            <div
              key={slot.agent_id}
              className="bg-[#05080F] border border-white/[0.08] rounded-xl p-3 space-y-2 text-xs font-mono"
            >
              <div className="flex items-center justify-between">
                <span className="text-white font-semibold">
                  #{slot.slot_index} · {slot.agent_id}
                </span>
                <span
                  className={`px-2 py-0.5 rounded text-[10px] ${
                    slot.status === 'active'
                      ? 'bg-[#10B981]/15 text-[#10B981]'
                      : 'bg-slate-800 text-slate-400'
                  }`}
                >
                  {slot.status.toUpperCase()}
                </span>
              </div>
              <div className="text-[11px] text-slate-400">
                {slot.exchange.toUpperCase()} · {slot.strategy}
              </div>
              <div className="grid grid-cols-3 gap-2 pt-1 border-t border-white/[0.06] text-[11px]">
                <div>
                  <div className="text-slate-500">Budget</div>
                  <div className="text-white">{fmtDec(slot.capital_budget)}</div>
                </div>
                <div>
                  <div className="text-slate-500">Reserved</div>
                  <div className="text-amber-300">{fmtDec(slot.reserved_capital)}</div>
                </div>
                <div>
                  <div className="text-slate-500">Avail</div>
                  <div className="text-[#10B981]">{fmtDec(slot.available_capital)}</div>
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Read-Only Exchange Custody Snapshots */}
      {recon?.exchange_readonly_balances && (
        <div className="nq-panel rounded-xl p-4 sm:p-5 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Wallet className="w-4 h-4 text-[#00E5FF]" />
              <h3 className="text-sm font-display font-semibold text-white">
                Exchange Read-Only Balance Snapshots
              </h3>
            </div>
            <span className="text-[10px] font-mono text-slate-400">
              READ-ONLY OBSERVATION · NOT TRADING CASH
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {Object.entries(recon.exchange_readonly_balances).map(([exId, snap]) => {
              const balances = Object.entries(snap.balances || {}).filter(([, amt]) => Number(amt) > 0);
              return (
                <div
                  key={exId}
                  className="bg-[#05080F] border border-white/[0.08] rounded-xl p-3 space-y-2"
                >
                  <div className="flex items-center justify-between text-xs font-mono">
                    <span className="font-semibold text-white">{exId.toUpperCase()}</span>
                    <span
                      className={
                        snap.status === 'ok' ? 'text-[#10B981]' : 'text-amber-400'
                      }
                    >
                      {snap.status.toUpperCase()}
                    </span>
                  </div>
                  {balances.length === 0 ? (
                    <div className="text-[11px] text-slate-500 font-mono">
                      {snap.error_type ? `Error: ${snap.error_type}` : 'No non-zero balances'}
                    </div>
                  ) : (
                    <div className="max-h-32 overflow-y-auto space-y-1 text-[11px] font-mono">
                      {balances.slice(0, 8).map(([asset, amt]) => (
                        <div key={asset} className="flex justify-between text-slate-300">
                          <span>{asset}</span>
                          <span>{Number(amt).toFixed(6)}</span>
                        </div>
                      ))}
                      {balances.length > 8 && (
                        <div className="text-[10px] text-slate-500">
                          +{balances.length - 8} more assets
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};
