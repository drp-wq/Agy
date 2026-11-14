import React from 'react';
import { Activity, AlertTriangle, Ban, CheckCircle2, Clock, Lock, ShieldCheck } from 'lucide-react';
import { VioletResearchPayload, VioletSleeveBalance } from '../../services/violetResearchApi';

interface VioletCampaignPanelProps {
  research: VioletResearchPayload | null;
  error?: string | null;
}

function fmtUsdt(val: number | null | undefined, digits = 2): string {
  if (val === null || val === undefined || !Number.isFinite(val)) return 'Unavailable';
  return `${val.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })} USDT`;
}

function fmtSignedUsdt(val: number | null | undefined, digits = 2): string {
  if (val === null || val === undefined || !Number.isFinite(val)) return 'Unavailable';
  const sign = val > 0 ? '+' : val < 0 ? '-' : '';
  return `${sign}${Math.abs(val).toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })} USDT`;
}

const BAND_ORDER = ['micro', 'low', 'mid', 'large', 'mega'];

export const VioletCampaignPanel: React.FC<VioletCampaignPanelProps> = ({ research, error }) => {
  const violet = research?.violet ?? null;
  const budget = violet?.budget ?? null;
  const riskBudget = violet?.risk_budget ?? null;
  const gate = violet?.entry_gate ?? null;
  const campaign = violet?.campaign ?? null;
  const campaignId = violet?.campaign_id || (campaign?.campaign_id as string | undefined) || null;
  const cycles = violet?.cycles ?? (campaign?.cycles_observed as number | undefined) ?? null;
  const heartbeatAge = violet?.heartbeat_age_sec ?? null;
  const workerHealth = violet?.worker_health ?? null;

  const isStale =
    gate?.status === 'STALE' ||
    Boolean(workerHealth?.toLowerCase().includes('stale')) ||
    (heartbeatAge !== null && heartbeatAge > 210);
  const isBlocked = gate?.status === 'BLOCKED';
  const isExpired = gate?.status === 'EXPIRED' || campaign?.status === 'completed' || campaign?.status === 'protective_exits_only';

  const statusBadge = isStale
    ? { label: 'STALE', cls: 'bg-[#ffb16c]/15 border-[#ffb16c]/45 text-[#ffb16c]', icon: Clock }
    : isBlocked
      ? { label: 'BLOCKED', cls: 'bg-[#EF4444]/15 border-[#EF4444]/45 text-[#EF4444]', icon: Ban }
      : isExpired
        ? { label: String(campaign?.status || 'EXPIRED').toUpperCase(), cls: 'bg-amber-500/15 border-amber-500/40 text-amber-300', icon: AlertTriangle }
        : gate?.status === 'INITIALIZING'
          ? { label: 'INITIALIZING', cls: 'bg-[#ff3555]/15 border-[#ff3555]/40 text-[#ffe3e5]', icon: Clock }
          : { label: gate?.status || 'ACTIVE', cls: 'bg-[#10B981]/15 border-[#10B981]/40 text-[#10B981]', icon: CheckCircle2 };

  const StatusIcon = statusBadge.icon;

  const sleeves: VioletSleeveBalance[] = React.useMemo(() => {
    const raw = violet?.sleeves ?? [];
    return [...raw].sort((a, b) => BAND_ORDER.indexOf(a.band) - BAND_ORDER.indexOf(b.band));
  }, [violet?.sleeves]);

  const remainingHours =
    typeof campaign?.remaining_hours === 'number'
      ? campaign.remaining_hours
      : typeof campaign?.remaining_seconds === 'number'
        ? Math.max(0, campaign.remaining_seconds / 3600)
        : null;

  return (
    <section
      aria-label="VIOLET 500 USDT All-Cap Paper Campaign"
      className="nq-panel rounded-xl p-4 sm:p-5 space-y-4 border border-[#ff3555]/35"
    >
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 pb-3 border-b border-[#ff3555]/20">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Activity className="w-4 h-4 text-[#ff6375]" />
            <h3 className="font-display font-semibold text-sm sm:text-base text-[#ffe3e5]">
              VIOLET 500 USDT All-Cap Spot Campaign
            </h3>
            {campaignId && (
              <span className="px-2 py-0.5 rounded bg-[#080305] border border-[#ff3555]/35 text-[11px] font-mono text-[#ff6375]">
                {campaignId}
              </span>
            )}
          </div>
          <p className="text-[11px] font-mono text-[#c28e95]">
            Independent 72h PAPER_ONLY ledger · 5 market-cap sleeves × 100.00 USDT · Isolated from Closed-Loop Router, 18-Agent Treasury, and Historical Archive
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <span className="px-2 py-1 rounded bg-[#10B981]/10 border border-[#10B981]/30 text-[#10B981] text-[11px] font-mono inline-flex items-center gap-1">
            <Lock className="w-3 h-3" />
            <span>0x LEVERAGE · PAPER ONLY</span>
          </span>
          <span className={`px-2.5 py-1 rounded border text-[11px] font-mono inline-flex items-center gap-1.5 ${statusBadge.cls}`}>
            <StatusIcon className="w-3.5 h-3.5" />
            <span>ENTRY GATE: {statusBadge.label}</span>
          </span>
        </div>
      </div>

      {error && !violet && (
        <div className="rounded-lg bg-[#ffb16c]/10 border border-[#ffb16c]/35 px-3 py-2 text-xs font-mono text-[#ffd7b3]">
          VIOLET campaign telemetry unavailable: {error}
        </div>
      )}

      {!violet && !error && (
        <div className="text-xs font-mono text-[#c28e95]">Loading VIOLET 500 USDT campaign state…</div>
      )}

      {violet && (
        <>
          {/* Honest Blocked / Stale Notice */}
          {(isStale || isBlocked) && (
            <div
              role="alert"
              className="rounded-lg bg-[#EF4444]/10 border border-[#EF4444]/40 px-3.5 py-2.5 text-xs font-mono text-[#ffe3e5] space-y-1"
            >
              <div className="font-semibold text-[#ff6375] flex items-center gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5" />
                <span>
                  {isStale
                    ? 'STALE WORKER TELEMETRY — Entries fail closed until fresh cycle completes'
                    : 'ENTRIES BLOCKED BY FAIL-CLOSED SAFETY / MIN-NOTIONAL GATE'}
                </span>
              </div>
              {gate && gate.reasons.length > 0 && (
                <div className="text-[11px] text-[#f3c6cc]">
                  Gate reasons: {gate.reasons.join(', ')}
                  {gate.min_notional_blocked_count > 0
                    ? ` · Min-notional blocks: ${gate.min_notional_blocked_count}`
                    : ''}
                  {gate.stale_blocked_count > 0
                    ? ` · Stale-feed blocks: ${gate.stale_blocked_count}`
                    : ''}
                </div>
              )}
            </div>
          )}

          {gate?.partial_feed_degradation && !isStale && !isBlocked && (
            <div role="status" className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3.5 py-2.5 text-xs font-mono text-amber-200">
              PARTIAL FEED DEGRADATION · {gate.stale_blocked_count} stale-data candidate rejection(s). Affected candidates remain rejected; the global entry gate is not blocked.
            </div>
          )}

          {/* Top 4 KPIs: 500 Budget | Cycle & Window | Current P&L vs Historical P&L | Risk Budget */}
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
            <div className="rounded-xl bg-[#080305] border border-[#ff3555]/25 p-3.5 space-y-1">
              <div className="text-[11px] font-mono text-[#b8868e]">Active Paper Budget</div>
              <div className="text-xl font-mono font-bold text-[#ffe3e5]">
                {budget ? fmtUsdt(budget.total_initial_usdt) : 'Unavailable'}
              </div>
              <div className="text-[11px] font-mono text-[#d4a5ac]">
                Equity {budget ? fmtUsdt(budget.current_equity_usdt) : '—'} · Free {budget ? fmtUsdt(budget.free_cash_usdt) : '—'}
              </div>
              <div className="text-[10px] font-mono text-[#a1737a]">
                {budget ? `${budget.sleeve_count} sleeves × ${fmtUsdt(budget.sleeve_initial_usdt)} · Reserved ${fmtUsdt(budget.reserved_margin_usdt)}` : '5 sleeves'}
              </div>
            </div>

            <div className="rounded-xl bg-[#080305] border border-[#ff3555]/25 p-3.5 space-y-1">
              <div className="text-[11px] font-mono text-[#b8868e]">Active Campaign Cycle</div>
              <div className="text-xl font-mono font-bold text-[#ff6375]">
                Cycle #{cycles ?? '—'}
              </div>
              <div className="text-[11px] font-mono text-[#d4a5ac]">
                Window: {String(campaign?.status || 'active').toUpperCase()}
                {remainingHours !== null ? ` · ${remainingHours.toFixed(1)}h left` : ''}
              </div>
              <div className="text-[10px] font-mono text-[#a1737a]">
                Worker: {(workerHealth || 'unknown').toUpperCase()}
                {heartbeatAge !== null ? ` · Heartbeat ${Math.round(heartbeatAge)}s ago` : ''}
              </div>
            </div>

            <div className="rounded-xl bg-[#080305] border border-[#ff3555]/25 p-3.5 space-y-1">
              <div className="text-[11px] font-mono text-[#b8868e]">Current Campaign P&amp;L</div>
              <div
                className={`text-xl font-mono font-bold ${
                  (budget?.current_total_pnl_usdt ?? 0) >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'
                }`}
              >
                {budget ? fmtSignedUsdt(budget.current_total_pnl_usdt) : 'Unavailable'}
              </div>
              <div className="text-[11px] font-mono text-[#d4a5ac]">
                Realized {budget ? fmtSignedUsdt(budget.current_realized_pnl_usdt) : '—'} · Open {budget ? fmtSignedUsdt(budget.current_unrealized_pnl_usdt) : '—'}
              </div>
              <div className="text-[10px] font-mono text-[#a1737a]">
                Fills {Number(campaign?.public_paper_fills_during_run ?? 0)} · Open Pos {Number(campaign?.open_positions ?? 0)}
              </div>
            </div>

            <div className="rounded-xl bg-[#080305] border border-[#ff3555]/25 p-3.5 space-y-1">
              <div className="text-[11px] font-mono text-[#b8868e] flex items-center justify-between">
                <span>Risk Budget (0x Leverage)</span>
                <ShieldCheck className="w-3.5 h-3.5 text-[#10B981]" />
              </div>
              <div className="text-xl font-mono font-bold text-[#ffe3e5]">
                {riskBudget
                  ? `${riskBudget.leverage}x Lev · ${(riskBudget.max_position_pct_per_sleeve * 100).toFixed(0)}% Cap`
                  : '0x Lev'}
              </div>
              <div className="text-[11px] font-mono text-[#d4a5ac]">
                Min Order {riskBudget ? fmtUsdt(riskBudget.min_order_notional_usd) : '—'} · Sleeve Daily {( (riskBudget?.daily_loss_limit_pct_per_sleeve ?? 0.01) * 100).toFixed(1)}%
              </div>
              <div className="text-[10px] font-mono text-[#a1737a]">
                Portfolio Daily {((riskBudget?.portfolio_daily_loss_limit_pct ?? 0.03) * 100).toFixed(1)}% · Max DD {((riskBudget?.portfolio_max_drawdown_pct ?? 0.1) * 100).toFixed(0)}%
              </div>
            </div>
          </div>

          {/* 5 Market-Cap Sleeves (100 USDT each) */}
          <div className="space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h4 className="text-xs font-mono uppercase tracking-wider text-[#d4a5ac]">
                Market-Cap Sleeves (5 × 100.00 USDT = 500.00 USDT Total)
              </h4>
              <span className="text-[10px] font-mono text-[#a1737a]">
                Spot only · Max 20.00 USDT/position · Blocked below exchange min notional
              </span>
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2.5">
              {sleeves.length === 0 ? (
                <div className="col-span-full text-xs font-mono text-[#c28e95]">
                  Sleeve balances unavailable.
                </div>
              ) : (
                sleeves.map((s) => (
                  <div
                    key={s.bot_id}
                    className="rounded-xl bg-[#080305] border border-[#ff3555]/20 p-3 space-y-1.5 text-xs font-mono"
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-bold uppercase text-[#ffe3e5]">{s.band}</span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] ${
                          s.daily_loss_tripped
                            ? 'bg-[#EF4444]/15 text-[#EF4444]'
                            : 'bg-[#10B981]/15 text-[#10B981]'
                        }`}
                      >
                        {s.daily_loss_tripped ? 'TRIPPED' : 'ARMED'}
                      </span>
                    </div>
                    <div className="text-sm font-bold text-[#ffe3e5]">{fmtUsdt(s.equity_usdt)}</div>
                    <div className="text-[10px] text-[#c28e95]">
                      Init {s.initial_usdt.toFixed(0)} · Free {s.free_usdt.toFixed(2)} · Res {s.reserved_usdt.toFixed(2)}
                    </div>
                    <div className="text-[10px] text-[#d4a5ac]">
                      P&amp;L {fmtSignedUsdt(s.realized_pnl_usdt + s.unrealized_pnl_usdt)} · Pos {s.open_positions}
                    </div>
                    <div className="text-[10px] text-[#a1737a]">
                      Risk {(s.max_risk_per_trade_pct * 100).toFixed(2)}% ({s.max_risk_per_trade_usdt.toFixed(2)} USDT)
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Global Market Cap Separation (Low / Mid / High Spot) */}
          {violet.market_cap_tiers && Object.keys(violet.market_cap_tiers).length > 0 && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h4 className="text-xs font-mono uppercase tracking-wider text-[#d4a5ac]">
                  Global Market Cap Separation (Micro · Low · Mid · High/Large · Mega Spot)
                </h4>
                <span className="text-[10px] font-mono text-[#a1737a]">
                  Live public spot discovery grouped by market-cap tier
                </span>
              </div>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-2.5">
                {BAND_ORDER.map((band) => {
                  const tier = violet.market_cap_tiers?.[band];
                  if (!tier) return null;
                  return (
                    <div
                      key={band}
                      className="rounded-xl bg-[#080305] border border-[#ff3555]/20 p-3 space-y-2 text-xs font-mono"
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className="font-bold text-[#ffe3e5] text-[11px]">{tier.label}</span>
                      </div>
                      <div className="text-[10px] text-[#c28e95]">
                        Discovered {tier.discovered_count} · Scanned {tier.scanned_count} · Passed {tier.passed_count}
                      </div>
                      <div className="space-y-1">
                        {tier.top_symbols.length === 0 ? (
                          <div className="text-[10px] text-[#a1737a]">No symbols in tier</div>
                        ) : (
                          tier.top_symbols.slice(0, 4).map((sym) => (
                            <div
                              key={`${sym.exchange}:${sym.symbol}`}
                              className="flex items-center justify-between text-[10px] text-[#d4a5ac] border-t border-[#ff3555]/10 pt-1"
                            >
                              <span className="text-[#ffe3e5] truncate">{sym.symbol}</span>
                              <span className="text-[#a1737a] uppercase">{sym.exchange}</span>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}


        </>
      )}
    </section>
  );
};
