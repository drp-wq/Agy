import React from 'react';
import { AlertTriangle, Lock, RefreshCw, ShieldAlert, Inbox } from 'lucide-react';
import { ExchangeId } from '../../types/quant';

export const EXCHANGE_META: Record<
  ExchangeId,
  { name: string; accentText: string; accentBorder: string; accentBg: string }
> = {
  binance: {
    name: 'Binance',
    accentText: 'text-[#F0B90B]',
    accentBorder: 'border-[#F0B90B]/30',
    accentBg: 'bg-[#F0B90B]/10'
  },
  bybit: {
    name: 'Bybit',
    accentText: 'text-[#00E5FF]',
    accentBorder: 'border-[#00E5FF]/30',
    accentBg: 'bg-[#00E5FF]/10'
  },
  okx: {
    name: 'OKX',
    accentText: 'text-[#8B5CF6]',
    accentBorder: 'border-[#8B5CF6]/30',
    accentBg: 'bg-[#8B5CF6]/10'
  }
};

export const SectionHeader: React.FC<{
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}> = ({ title, subtitle, actions }) => (
  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 pb-4 border-b border-white/[0.08]">
    <div>
      <h2 className="font-display text-xl font-semibold tracking-tight text-white">{title}</h2>
      {subtitle && <p className="text-xs text-slate-400 mt-0.5">{subtitle}</p>}
    </div>
    {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
  </div>
);

export const EmptyStatePanel: React.FC<{
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
}> = ({ title, description, actionLabel, onAction }) => (
  <div className="nq-panel rounded-xl p-8 text-center flex flex-col items-center justify-center border border-white/[0.08]">
    <Inbox className="w-8 h-8 text-slate-500 mb-3" />
    <h3 className="text-sm font-semibold text-slate-200">{title}</h3>
    <p className="text-xs text-slate-400 mt-1 max-w-md">{description}</p>
    {actionLabel && onAction && (
      <button
        onClick={onAction}
        className="mt-4 px-4 py-2 min-h-[40px] rounded-lg bg-[#00E5FF]/15 hover:bg-[#00E5FF]/25 border border-[#00E5FF]/35 text-[#00E5FF] text-xs font-medium transition-colors"
      >
        {actionLabel}
      </button>
    )}
  </div>
);

export const LoadingTerminalSkeleton: React.FC = () => (
  <div className="space-y-6 animate-pulse" aria-label="Loading terminal telemetry">
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="nq-panel rounded-xl p-4 h-24 bg-[#0B1120]/60" />
      ))}
    </div>
    <div className="nq-panel rounded-xl h-[380px] bg-[#0B1120]/60" />
    <div className="nq-panel rounded-xl h-56 bg-[#0B1120]/60" />
  </div>
);

export const AuthErrorBanner: React.FC<{
  message: string;
  onRetry: () => void;
}> = ({ message, onRetry }) => (
  <div className="nq-panel rounded-xl p-6 border border-[#EF4444]/40 bg-[#EF4444]/5 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
    <div className="flex items-start gap-3">
      <ShieldAlert className="w-5 h-5 text-[#EF4444] shrink-0 mt-0.5" />
      <div>
        <h3 className="text-sm font-semibold text-[#EF4444]">Telegram Authentication Required</h3>
        <p className="text-xs text-slate-300 mt-1">{message}</p>
        <p className="text-[11px] text-slate-400 mt-1 font-mono">
          Verify HMAC-SHA256 initData signature via authorized Telegram owner session.
        </p>
      </div>
    </div>
    <button
      onClick={onRetry}
      className="px-4 py-2 min-h-[44px] rounded-lg bg-[#EF4444]/15 hover:bg-[#EF4444]/25 border border-[#EF4444]/40 text-white text-xs font-medium flex items-center gap-2 shrink-0 transition-colors"
    >
      <RefreshCw className="w-3.5 h-3.5" />
      <span>Retry Verification</span>
    </button>
  </div>
);

export const StaleWarningStrip: React.FC<{
  ageSec: number;
  isDemo: boolean;
  onRefresh: () => void;
}> = ({ ageSec, isDemo, onRefresh }) => {
  if (!isDemo && ageSec < 30) return null;

  return (
    <div
      className={`rounded-lg px-4 py-2.5 text-xs flex flex-wrap items-center justify-between gap-2 border ${
        isDemo
          ? 'bg-[#8B5CF6]/10 border-[#8B5CF6]/30 text-slate-200'
          : 'bg-[#F59E0B]/10 border-[#F59E0B]/35 text-amber-200'
      }`}
    >
      <div className="flex items-center gap-2">
        <AlertTriangle
          className={`w-4 h-4 shrink-0 ${isDemo ? 'text-[#8B5CF6]' : 'text-[#F59E0B]'}`}
        />
        {isDemo ? (
          <span>
            <strong className="text-white font-semibold">DEMO PREVIEW MODE</strong> · FastAPI backend unreachable in browser sandbox. Displaying isolated synthetic preview data and deterministic candles.
          </span>
        ) : (
          <span>
            <strong className="text-amber-300 font-semibold">Stale Telemetry Warning</strong> · Last snapshot updated {ageSec}s ago. Market prices may be delayed.
          </span>
        )}
      </div>
      <button
        onClick={onRefresh}
        className="text-xs font-medium text-[#00E5FF] hover:underline flex items-center gap-1 whitespace-nowrap shrink-0"
      >
        <RefreshCw className="w-3 h-3" />
        <span>Sync Now</span>
      </button>
    </div>
  );
};

export const ReadOnlySecurityFooterNotice: React.FC = () => (
  <div className="flex flex-wrap items-center justify-between gap-3 pt-4 border-t border-white/[0.06] text-[11px] text-slate-500">
    <div className="flex items-center gap-1.5">
      <Lock className="w-3.5 h-3.5 text-[#10B981]" />
      <span>Paper Trading Only · Live Execution Permanently Disabled</span>
    </div>
    <span className="font-mono">NEON QUANT V3 · Read-Only Telemetry</span>
  </div>
);
