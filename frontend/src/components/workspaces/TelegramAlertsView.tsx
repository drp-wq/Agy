import React, { useState } from 'react';
import { Bell, Search, CheckCircle2, ShieldCheck } from 'lucide-react';
import { MiniAppStateResponse } from '../../types/quant';
import { TelegramContextState } from '../../hooks/useTelegramWebApp';
import { EXCHANGE_META, EmptyStatePanel, SectionHeader } from '../common/TerminalPrimitives';
import { formatAgeSeconds } from '../../utils/formatters';

interface TelegramAlertsViewProps {
  state: MiniAppStateResponse;
  telegram: TelegramContextState;
}

export const TelegramAlertsView: React.FC<TelegramAlertsViewProps> = ({ state, telegram }) => {
  const [categoryFilter, setCategoryFilter] = useState<'ALL' | 'SIGNAL' | 'POSITION' | 'RISK' | 'SYSTEM'>('ALL');
  const [query, setQuery] = useState('');

  const filtered = state.alerts.filter((a) => {
    const matchCat = categoryFilter === 'ALL' || a.category === categoryFilter;
    const matchQ =
      a.title.toLowerCase().includes(query.toLowerCase()) ||
      a.message.toLowerCase().includes(query.toLowerCase()) ||
      (a.symbol && a.symbol.toLowerCase().includes(query.toLowerCase()));
    return matchCat && matchQ;
  });

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Telegram Bot Alerts & Mini App Session"
        subtitle="Verified owner telemetry stream and HMAC-SHA256 initData authentication context"
        actions={
          <div className="flex items-center gap-2 text-xs font-mono">
            <ShieldCheck className={`w-4 h-4 ${state.telegram_owner.authenticated ? 'text-[#10B981]' : 'text-amber-400'}`} />
            <span className={state.telegram_owner.authenticated ? 'text-[#10B981]' : 'text-amber-300'}>
              {state.telegram_owner.authenticated ? 'Owner Verified' : 'Preview Session'}
            </span>
          </div>
        }
      />

      {/* Telegram Mini App SDK Context Card */}
      <div className="nq-panel rounded-xl p-4 grid grid-cols-1 md:grid-cols-3 gap-4 text-xs font-mono">
        <div>
          <div className="text-slate-400 font-sans">Telegram WebApp Container</div>
          <div className="text-white font-semibold mt-1">
            {telegram.isTelegramEnv ? `Active (${telegram.platform})` : 'Standalone Browser'}
          </div>
          <div className="text-[11px] text-slate-400 mt-0.5">
            Safe Area: Top {telegram.safeTop}px · Bottom {telegram.safeBottom}px
          </div>
        </div>
        <div>
          <div className="text-slate-400 font-sans">Signed initData Verification</div>
          <div className="text-white font-semibold mt-1">
            {state.telegram_owner.auth_method.replace(/_/g, ' ').toUpperCase()}
          </div>
          <div className="text-[11px] text-slate-400 mt-0.5">
            User: {telegram.user?.username ? `@${telegram.user.username}` : state.telegram_owner.username || 'Not linked'}
          </div>
        </div>
        <div>
          <div className="text-slate-400 font-sans">Alert Delivery Pipeline</div>
          <div className="text-[#10B981] font-semibold mt-1 flex items-center gap-1.5">
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span>Read-Only Notification Stream</span>
          </div>
          <div className="text-[11px] text-slate-400 mt-0.5">Paper-Tagged Signals Only</div>
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center p-1 rounded-lg bg-[#120508] border border-[#ff3555]/25 overflow-x-auto no-scrollbar">
          {(['ALL', 'SIGNAL', 'POSITION', 'RISK', 'SYSTEM'] as const).map((cat) => (
            <button
              key={cat}
              type="button"
              onClick={() => setCategoryFilter(cat)}
              className={`px-3 py-1.5 min-h-[34px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 ${
                categoryFilter === cat ? 'bg-[#ff3555]/25 text-[#ffe3e5]' : 'text-[#d4a5ac] hover:text-[#ffe3e5]'
              }`}
            >
              {cat}
            </button>
          ))}
        </div>

        <div className="relative w-full sm:w-64">
          <Search className="w-4 h-4 text-[#c28e95] absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search alerts..."
            className="w-full pl-9 pr-3 py-2 min-h-[38px] rounded-lg bg-[#120508] border border-[#ff3555]/25 text-xs text-[#ffe3e5] placeholder-[#9e6d74] focus:outline-none focus:border-[#ff6375]"
          />
        </div>
      </div>

      {/* Alerts List */}
      {filtered.length === 0 ? (
        <EmptyStatePanel
          title="No Telegram Alerts Match Filter"
          description="Clear your category filter or search query to review recent alerts."
          actionLabel="Show All Alerts"
          onAction={() => {
            setCategoryFilter('ALL');
            setQuery('');
          }}
        />
      ) : (
        <div className="nq-panel rounded-xl divide-y divide-white/[0.06]">
          {filtered.map((alert) => (
            <div key={alert.id} className="p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="space-y-1">
                <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
                  <Bell
                    className={`w-3.5 h-3.5 ${
                      alert.severity === 'critical'
                        ? 'text-[#EF4444]'
                        : alert.severity === 'warning'
                        ? 'text-amber-400'
                        : alert.severity === 'success'
                        ? 'text-[#10B981]'
                        : 'text-[#ff6375]'
                    }`}
                  />
                  <span className="font-semibold text-white font-sans text-sm">{alert.title}</span>
                  <span className="text-slate-500">·</span>
                  <span className="text-slate-400">{alert.category}</span>
                  {alert.exchange && (
                    <>
                      <span className="text-slate-500">·</span>
                      <span className={`uppercase ${EXCHANGE_META[alert.exchange].accentText}`}>
                        {alert.exchange}
                      </span>
                    </>
                  )}
                  {alert.symbol && (
                    <>
                      <span className="text-slate-500">·</span>
                      <span className="text-slate-300">{alert.symbol}</span>
                    </>
                  )}
                </div>
                <p className="text-xs text-slate-300 leading-relaxed">{alert.message}</p>
              </div>

              <div className="text-left sm:text-right font-mono text-[11px] text-slate-400 shrink-0">
                <div>{formatAgeSeconds(alert.timestamp)}</div>
                <div className="text-[#10B981] mt-0.5">
                  {alert.delivered_to_bot ? 'Bot Dispatched' : 'Pending'} · {alert.account_mode.toUpperCase()}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
