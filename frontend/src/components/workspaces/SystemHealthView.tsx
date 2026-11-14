import React, { useState } from 'react';
import { Server, ShieldCheck, RefreshCw, Terminal, CheckCircle2, AlertTriangle } from 'lucide-react';
import { ExchangeId, MiniAppHealthResponse, LIVE_EXECUTION_PERMANENTLY_DISABLED } from '../../types/quant';
import { EXCHANGE_META, SectionHeader } from '../common/TerminalPrimitives';
import { formatAgeSeconds, formatDuration } from '../../utils/formatters';

interface SystemHealthViewProps {
  health: MiniAppHealthResponse | null;
  isDemo: boolean;
  onRefresh: () => void;
}

export const SystemHealthView: React.FC<SystemHealthViewProps> = ({
  health,
  isDemo,
  onRefresh
}) => {
  const [showIntegrationSpec, setShowIntegrationSpec] = useState(false);

  return (
    <div className="space-y-6">
      <SectionHeader
        title="System Health & Security Architecture"
        subtitle="Ubuntu 24.04 VPS FastAPI backend, Caddy TLS proxy, exchange adapter status, and read-only enforcement"
        actions={
          <button
            type="button"
            onClick={onRefresh}
            className="px-3.5 py-2 min-h-[38px] rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/45 text-[#ffe3e5] text-xs font-medium flex items-center gap-1.5 whitespace-nowrap shrink-0"
          >
            <RefreshCw className="w-3.5 h-3.5 text-[#ff6375]" />
            <span>Probe /health</span>
          </button>
        }
      />

      {/* Infrastructure Summary Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Backend Service</div>
          <div className="text-base font-mono font-bold text-white mt-1 truncate">
            {health?.service || 'multi-exchange-paper'}
          </div>
          <div className="text-[11px] font-mono text-[#10B981] mt-1">
            {isDemo ? 'DEMO PREVIEW' : `v${health?.version || '3.0.0'}`}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Paper Trading Engine</div>
          <div className="text-base font-mono font-bold text-[#10B981] mt-1">
            {health?.paper_engine_running ? 'RUNNING' : 'STOPPED'}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1">
            Uptime: {health ? formatDuration(health.uptime_seconds) : '—'}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Live Execution Policy</div>
          <div className="text-base font-mono font-bold text-[#10B981] mt-1 flex items-center gap-1.5">
            <ShieldCheck className="w-4 h-4" />
            <span>PERMANENTLY DISABLED</span>
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1">
            Flag: {String(health?.live_execution_permanently_disabled ?? LIVE_EXECUTION_PERMANENTLY_DISABLED)}
          </div>
        </div>

        <div className="nq-panel rounded-xl p-4">
          <div className="text-xs text-slate-400">Ingress & TLS Proxy</div>
          <div className="text-base font-mono font-bold text-white mt-1 truncate">
            {health?.caddy_tls || 'Caddy HTTPS'}
          </div>
          <div className="text-[11px] font-mono text-slate-400 mt-1 truncate">
            {health?.vps_host || 'Ubuntu 24.04'}
          </div>
        </div>
      </div>

      {/* Exchange Adapter Connectivity Matrix */}
      <div className="nq-panel rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-[#ff3555]/20 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[#ffe3e5] flex items-center gap-2">
            <Server className="w-4 h-4 text-[#ff6375]" />
            <span>Exchange Adapter Connectivity (Public vs Private Boundary)</span>
          </h3>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse text-xs font-mono">
            <thead>
              <tr className="border-b border-[#ff3555]/20 bg-[#120508] text-[11px] text-[#d4a5ac]">
                <th className="py-3 px-4">Exchange</th>
                <th className="py-3 px-3">Public WS</th>
                <th className="py-3 px-3">Public REST</th>
                <th className="py-3 px-3">Private Account Auth</th>
                <th className="py-3 px-3 text-right">Latency</th>
                <th className="py-3 px-4 text-right">Last Tick</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.06]">
              {(['binance', 'bybit', 'okx'] as ExchangeId[]).map((exId) => {
                const adapter = health?.adapters[exId];
                const meta = EXCHANGE_META[exId];
                return (
                  <tr key={exId} className="hover:bg-white/[0.02]">
                    <td className={`py-3 px-4 font-semibold ${meta.accentText}`}>{meta.name}</td>
                    <td className="py-3 px-3">
                      <span
                        className={
                          adapter?.public_ws_ok
                            ? 'text-[#10B981]'
                            : adapter?.public_rest_ok
                              ? 'text-amber-300'
                              : 'text-[#EF4444]'
                        }
                      >
                        {adapter?.public_ws_ok
                          ? 'Connected'
                          : adapter?.public_rest_ok
                            ? 'REST Polling'
                            : 'Offline'}
                      </span>
                    </td>
                    <td className="py-3 px-3">
                      <span className={adapter?.public_rest_ok ? 'text-[#10B981]' : 'text-[#EF4444]'}>
                        {adapter?.public_rest_ok ? 'Nominal' : 'Error'}
                      </span>
                    </td>
                    <td className="py-3 px-3">
                      <div className="flex items-center gap-1.5">
                        {adapter?.private_auth_ok ? (
                          <CheckCircle2 className="w-3.5 h-3.5 text-[#10B981]" />
                        ) : (
                          <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />
                        )}
                        <span className={adapter?.private_auth_ok ? 'text-[#10B981]' : 'text-amber-300'}>
                          {adapter?.private_auth_ok ? 'Authenticated (Read-Only)' : 'Unauthenticated'}
                        </span>
                      </div>
                      {adapter?.private_auth_note && (
                        <div className="text-[10px] text-slate-500 mt-0.5">{adapter.private_auth_note}</div>
                      )}
                    </td>
                    <td className="py-3 px-3 text-right text-slate-300">
                      {adapter?.latency_ms !== null && adapter?.latency_ms !== undefined
                        ? `${adapter.latency_ms} ms`
                        : '—'}
                    </td>
                    <td className="py-3 px-4 text-right text-slate-400">
                      {adapter ? formatAgeSeconds(adapter.last_tick_ts) : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Backend Integration & VPS Deployment Spec Toggle */}
      <div className="nq-panel rounded-xl p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <Terminal className="w-4 h-4 text-[#ff6375]" />
            <div>
              <h3 className="text-sm font-semibold text-[#ffe3e5]">
                FastAPI Integration Contract & VPS Deployment Spec
              </h3>
              <p className="text-xs text-[#d4a5ac]">
                Existing + proposed authenticated read-only endpoints, Caddy config, and rollback procedure.
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => setShowIntegrationSpec(!showIntegrationSpec)}
            className="px-3.5 py-2 min-h-[36px] rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/40 text-[#ffe3e5] text-xs font-medium whitespace-nowrap shrink-0"
          >
            {showIntegrationSpec ? 'Hide Integration Spec' : 'View Integration Spec'}
          </button>
        </div>

        {showIntegrationSpec && (
          <div className="pt-3 border-t border-[#ff3555]/20 space-y-4 text-xs font-mono text-[#f3c6cc]">
            <div>
              <div className="text-[#ff6375] font-semibold mb-1">1. Existing & Proposed Read-Only Endpoints (FastAPI)</div>
              <pre className="p-3 rounded-lg bg-[#080305] border border-[#ff3555]/20 overflow-x-auto text-[11px] leading-relaxed">
{`# Existing Verified Read-Only Endpoints:
GET /api/miniapp/state
GET /api/miniapp/health
GET /api/miniapp/candles/{exchange}/{market_type}/{base}/{quote}/{timeframe}
GET /api/violet/research
GET /api/violet/solana

# Proposed New Authenticated Read-Only Endpoints (Additive Only):
GET /api/miniapp/orders?exchange=all&status=all&limit=100
GET /api/miniapp/positions/history?exchange=all&days=30
GET /api/miniapp/scanner/arbitrage
GET /api/miniapp/alerts?category=all&limit=50
# Security: All routes enforce Depends(verify_telegram_init_data) + owner whitelist`}
              </pre>
            </div>

            <div>
              <div className="text-[#ff6375] font-semibold mb-1">2. Safe Ubuntu 24.04 VPS Build & Rollback Procedure</div>
              <pre className="p-3 rounded-lg bg-[#080305] border border-[#ff3555]/20 overflow-x-auto text-[11px] leading-relaxed">
{`# Backup existing miniapp static bundle before deploying V3:
cp -a /root/multi-exchange-paper/miniapp_dist /root/multi-exchange-paper/miniapp_dist.bak.$(date +%s)

# Build V3 SPA:
npm ci && npm run build

# Serve dist/ under /miniapp behind existing Caddy HTTPS reverse proxy
# Rollback if needed:
rm -rf /root/multi-exchange-paper/miniapp_dist && mv /root/multi-exchange-paper/miniapp_dist.bak.* /root/multi-exchange-paper/miniapp_dist`}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
