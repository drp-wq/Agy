import React, { useState } from 'react';
import { ShieldAlert, CheckCircle2 } from 'lucide-react';
import {
  ExchangeId,
  FuturesPosition,
  MarketType,
  MiniAppStateResponse,
  Timeframe
} from '../../types/quant';
import { EXCHANGE_META, EmptyStatePanel, SectionHeader } from '../common/TerminalPrimitives';
import { ProMarketChart } from '../charts/ProMarketChart';
import {
  formatAgeSeconds,
  formatPrice,
  formatSignedPct,
  formatSignedUsd,
  formatTimeShort,
  formatUsd
} from '../../utils/formatters';
import { formatExchangeRealizedPnl } from '../../utils/telemetryMetrics';

interface ExchangeWorkspaceViewProps {
  exchange: ExchangeId;
  state: MiniAppStateResponse;
  initData?: string;
  onInspectPosition: (pos: FuturesPosition) => void;
}

type WorkspaceTab = 'spot' | 'futures' | 'orders' | 'chart';

export const ExchangeWorkspaceView: React.FC<ExchangeWorkspaceViewProps> = ({
  exchange,
  state,
  initData,
  onInspectPosition
}) => {
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('futures');
  const [chartMarket, setChartMarket] = useState<MarketType>('futures');
  const [chartSymbol, setChartSymbol] = useState<string>('BTC/USDT');
  const [chartTf, setChartTf] = useState<Timeframe>('15m');

  const ex = state.exchanges[exchange];
  const meta = EXCHANGE_META[exchange];

  const spotHoldings = state.spot_holdings.filter((s) => s.exchange === exchange);
  const futuresPositions = state.futures_positions.filter(
    (p) => p.exchange === exchange && p.status === 'open'
  );
  const exchangeOrders = state.orders.filter((o) => o.exchange === exchange);

  return (
    <div className="space-y-6">
      <SectionHeader
        title={`${meta.name} Dedicated Workspace`}
        subtitle={`${ex.exchange_meta.account_type} · ${ex.exchange_meta.fee_tier} · Synced ${formatAgeSeconds(ex.last_sync_ts)}`}
        actions={
          <div className="flex flex-wrap items-center gap-3 text-xs font-mono">
            <span className={ex.public_feed_ok ? 'text-[#10B981]' : 'text-[#EF4444]'}>
              Public Market: {ex.public_feed_ok ? 'Connected' : 'Offline'}
            </span>
            <span className="text-slate-600">|</span>
            <span className={ex.private_auth_ok ? 'text-[#10B981]' : 'text-amber-400'}>
              Private Account: {ex.private_auth_ok ? 'Authenticated (Read-Only)' : 'Unauthenticated'}
            </span>
          </div>
        }
      />

      {/* Explicit Distinction Banner: Public Market vs Private Account Auth */}
      {!ex.private_auth_ok && (
        <div className="rounded-lg px-4 py-2.5 bg-amber-500/10 border border-amber-500/30 text-xs flex items-center gap-2.5 text-amber-200">
          <ShieldAlert className="w-4 h-4 text-amber-400 shrink-0" />
          <span>
            <strong>Private Account Read-Only Sync Unavailable:</strong>{' '}
            {ex.private_auth_error || 'Public market feeds are active, but private balances require authenticated VPS API keys.'}{' '}
            Displaying isolated <strong>PAPER</strong> ledger below.
          </span>
        </div>
      )}

      {/* Isolated authenticated account cache: quantities are not USD valuations. */}
      <div className="nq-panel rounded-xl p-4 space-y-3 border border-white/[0.08]">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="text-sm font-semibold text-white">Live Account · Read-Only Snapshot</h3>
          <span className="text-xs text-slate-400 font-mono">
            {ex.live_readonly_snapshot?.status === 'ok' && ex.live_readonly_snapshot.timestamp
              ? `Last synced ${formatAgeSeconds(ex.live_readonly_snapshot.timestamp)}`
              : ex.live_readonly_snapshot?.status === 'error' ? 'Sync failed' : 'Unavailable'}
          </span>
        </div>
        {ex.live_readonly_snapshot?.status === 'ok' ? (
          <>
            <div className="text-xs text-slate-400">Actual asset quantities (not USD equity). Paper holdings remain separate below.</div>
            <div className="flex flex-wrap gap-2">
              {Object.entries(ex.live_readonly_snapshot.balances).length ? Object.entries(ex.live_readonly_snapshot.balances).map(([asset, amount]) => (
                <span key={asset} className="px-3 py-2 rounded-lg bg-white/[0.04] text-xs font-mono text-slate-200">{asset}: {Number(amount).toLocaleString(undefined, { maximumFractionDigits: 8 })}</span>
              )) : <span className="text-xs text-slate-400">No nonzero balances reported</span>}
            </div>
            <div className="text-xs text-slate-400">Live derivative positions: {ex.live_readonly_snapshot.positions.length}</div>
            {ex.live_readonly_snapshot.positions.map((position, i) => (
              <div key={`${position.symbol}-${i}`} className="flex flex-wrap gap-4 text-xs font-mono text-slate-200 border-t border-white/[0.06] pt-2">
                <span>{position.symbol}</span><span>{position.side}</span><span>Contracts: {position.contracts}</span>
                <span>Entry: {position.entry_price}</span><span>Unrealized PnL: {position.unrealized_pnl}</span>
              </div>
            ))}
          </>
        ) : <p className="text-xs text-amber-200">No verified live account snapshot available. Check API permissions and the latest sync status. No balances are inferred.</p>}
      </div>

      {/* Account Metrics Strip */}
      {(() => {
        const realizedFmt = formatExchangeRealizedPnl(ex.realized_pnl_24h, ex.lifetime_realized_pnl);
        const marginPct =
          exchange === 'bybit'
            ? (ex.exchange_meta.im_rate_pct !== null && ex.exchange_meta.im_rate_pct !== undefined
                ? `${ex.exchange_meta.im_rate_pct}% / ${ex.exchange_meta.mm_rate_pct ?? 0}%`
                : null)
            : (ex.exchange_meta.margin_ratio_pct !== null && ex.exchange_meta.margin_ratio_pct !== undefined
                ? `${ex.exchange_meta.margin_ratio_pct}%`
                : null);
        return (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
            <div className="nq-panel rounded-xl p-4">
              <div className="text-xs text-slate-400">Paper Equity</div>
              <div className="text-xl font-mono font-bold text-white mt-1">{formatUsd(ex.paper_equity_usd)}</div>
              <div className="text-[11px] font-mono text-slate-400 mt-1">
                Live Read-Only: {ex.live_readonly_equity_usd !== null ? formatUsd(ex.live_readonly_equity_usd) : (ex.private_auth_ok ? 'Synced (Multi-Asset)' : 'Unauthenticated')}
              </div>
            </div>

            <div className="nq-panel rounded-xl p-4">
              <div className="text-xs text-slate-400">Free Collateral</div>
              <div className="text-xl font-mono font-bold text-[#00E5FF] mt-1">{formatUsd(ex.available_margin_usd)}</div>
              <div className="text-[11px] font-mono text-slate-400 mt-1">Available Paper Capital</div>
            </div>

            <div className="nq-panel rounded-xl p-4">
              <div className="text-xs text-slate-400">Reserved Margin / Notional</div>
              <div className="text-xl font-mono font-bold text-white mt-1">{formatUsd(ex.locked_margin_usd)}</div>
              <div className="text-[11px] font-mono text-slate-400 mt-1">
                Notional: {formatUsd(ex.notional_exposure_usd ?? ex.locked_margin_usd)}
              </div>
            </div>

            <div className="nq-panel rounded-xl p-4">
              <div className="text-xs text-slate-400">Open / Realized PnL</div>
              <div className={`text-xl font-mono font-bold mt-1 ${ex.unrealized_pnl >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                {formatSignedUsd(ex.unrealized_pnl)}
              </div>
              <div className="text-[11px] font-mono text-slate-400 mt-1">
                {realizedFmt.caption}: {realizedFmt.text}
              </div>
            </div>

            <div className="nq-panel rounded-xl p-4 sm:col-span-2 lg:col-span-1">
              <div className="text-xs text-slate-400">
                {exchange === 'bybit' ? 'UTA IM / MM Rate' : exchange === 'okx' ? 'OKX Margin Ratio' : 'Margin Ratio'}
              </div>
              <div className="text-xl font-mono font-bold text-white mt-1">
                {marginPct ?? 'Paper Isolated'}
              </div>
              <div className="text-[11px] font-mono text-slate-400 mt-1 flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3 text-[#10B981]" />
                <span>{marginPct ? 'Exchange Reported' : 'Simulated Margin Mode'}</span>
              </div>
            </div>
          </div>
        );
      })()}

      {/* Sub-Tabs */}
      <div className="flex items-center gap-1 p-1 rounded-lg bg-[#0B1120] border border-white/[0.08] overflow-x-auto no-scrollbar">
        {[
          { id: 'futures', label: `Futures Positions (${futuresPositions.length})` },
          { id: 'spot', label: `Spot Portfolio (${spotHoldings.length})` },
          { id: 'orders', label: `Orders & Fills (${exchangeOrders.length})` },
          { id: 'chart', label: `${meta.name} Market Chart` }
        ].map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id as WorkspaceTab)}
            className={`px-3.5 py-2 min-h-[38px] rounded-md text-xs font-medium whitespace-nowrap shrink-0 transition-colors ${
              activeTab === tab.id
                ? 'bg-[#00E5FF]/20 text-[#00E5FF]'
                : 'text-slate-400 hover:text-white'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab Content */}
      {activeTab === 'futures' && (
        futuresPositions.length === 0 ? (
          <EmptyStatePanel
            title={`No Open ${meta.name} Futures Positions`}
            description="Paper Futures positions opened by active strategies on this exchange will appear here."
          />
        ) : (
          <div className="nq-panel rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs font-mono">
                <thead>
                  <tr className="border-b border-white/[0.08] bg-[#0B1120] text-[11px] text-slate-400">
                    <th className="py-3 px-4">Contract / Mode</th>
                    <th className="py-3 px-3">Side / Lev</th>
                    <th className="py-3 px-3 text-right">Qty / Notional</th>
                    <th className="py-3 px-3 text-right">Entry / Mark</th>
                    <th className="py-3 px-3 text-right">Liq. Price</th>
                    <th className="py-3 px-3 text-right">Unrealized PnL</th>
                    <th className="py-3 px-4 text-right">SL / TP</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {futuresPositions.map((pos) => (
                    <tr
                      key={pos.id}
                      onClick={() => onInspectPosition(pos)}
                      className="hover:bg-white/[0.03] cursor-pointer transition-colors"
                    >
                      <td className="py-3 px-4">
                        <div className="font-semibold text-white">{pos.instrument_id || pos.symbol}</div>
                        <div className="text-[10px] text-slate-400 uppercase">
                          {pos.account_mode} · {pos.margin_mode}
                        </div>
                      </td>
                      <td className="py-3 px-3">
                        <span className={pos.side === 'LONG' ? 'text-[#10B981] font-semibold' : 'text-[#EF4444] font-semibold'}>
                          {pos.side}
                        </span>{' '}
                        <span className="text-slate-300">{pos.leverage}x</span>
                      </td>
                      <td className="py-3 px-3 text-right">
                        <div className="text-white">{pos.quantity}</div>
                        <div className="text-[10px] text-slate-400">{formatUsd(pos.notional_usd)}</div>
                      </td>
                      <td className="py-3 px-3 text-right">
                        <div className="text-slate-300">{formatPrice(pos.entry_price)}</div>
                        <div className="text-white">{formatPrice(pos.mark_price)}</div>
                      </td>
                      <td className="py-3 px-3 text-right">
                        <div className="text-amber-400">{formatPrice(pos.liquidation_price)}</div>
                        <div className="text-[10px] text-slate-500">
                          {pos.liquidation_distance_pct ? `${pos.liquidation_distance_pct.toFixed(1)}% dist` : '—'}
                        </div>
                      </td>
                      <td className="py-3 px-3 text-right">
                        <div className={`font-semibold ${pos.unrealized_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                          {formatSignedUsd(pos.unrealized_pnl_usd)}
                        </div>
                        <div className={`text-[10px] ${pos.unrealized_pnl_pct >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}`}>
                          {formatSignedPct(pos.unrealized_pnl_pct)}
                        </div>
                      </td>
                      <td className="py-3 px-4 text-right text-slate-300">
                        <div>SL {formatPrice(pos.stop_loss)}</div>
                        <div className="text-[#10B981]">TP {formatPrice(pos.take_profit)}</div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      )}

      {activeTab === 'spot' && (
        spotHoldings.length === 0 ? (
          <EmptyStatePanel
            title={`No ${meta.name} Spot Balances`}
            description="Spot asset inventories are tracked separately from leveraged Futures positions."
          />
        ) : (
          <div className="nq-panel rounded-xl overflow-hidden">
            <div className="px-4 py-2.5 border-b border-white/[0.08] text-xs text-slate-400">
              Spot Asset Inventory · Unleveraged Balances (Separate from Futures Margin)
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs font-mono">
                <thead>
                  <tr className="border-b border-white/[0.08] bg-[#0B1120] text-[11px] text-slate-400">
                    <th className="py-3 px-4">Asset / Mode</th>
                    <th className="py-3 px-3 text-right">Available</th>
                    <th className="py-3 px-3 text-right">Locked</th>
                    <th className="py-3 px-3 text-right">Total Balance</th>
                    <th className="py-3 px-3 text-right">Avg Cost / Mark</th>
                    <th className="py-3 px-3 text-right">Valuation (USD)</th>
                    <th className="py-3 px-4 text-right">Unrealized PnL</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {spotHoldings.map((h) => (
                    <tr key={h.id} className="hover:bg-white/[0.02]">
                      <td className="py-3 px-4">
                        <div className="font-semibold text-white">{h.asset}</div>
                        <div className="text-[10px] text-slate-400 uppercase">{h.account_mode} Spot</div>
                      </td>
                      <td className="py-3 px-3 text-right text-slate-200">{h.available}</td>
                      <td className="py-3 px-3 text-right text-slate-400">{h.locked}</td>
                      <td className="py-3 px-3 text-right text-white font-semibold">{h.total}</td>
                      <td className="py-3 px-3 text-right text-slate-300">
                        {formatPrice(h.avg_entry_price)} / {formatPrice(h.mark_price)}
                      </td>
                      <td className="py-3 px-3 text-right text-white">{formatUsd(h.valuation_usd)}</td>
                      <td className="py-3 px-4 text-right">
                        {h.unrealized_pnl_usd !== null ? (
                          <span className={h.unrealized_pnl_usd >= 0 ? 'text-[#10B981]' : 'text-[#EF4444]'}>
                            {formatSignedUsd(h.unrealized_pnl_usd)} ({formatSignedPct(h.unrealized_pnl_pct)})
                          </span>
                        ) : (
                          '—'
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      )}

      {activeTab === 'orders' && (
        exchangeOrders.length === 0 ? (
          <EmptyStatePanel
            title={`No ${meta.name} Paper Orders`}
            description="Active orders, fills, and rejected paper signals for this exchange will appear here."
          />
        ) : (
          <div className="nq-panel rounded-xl overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left border-collapse text-xs font-mono">
                <thead>
                  <tr className="border-b border-white/[0.08] bg-[#0B1120] text-[11px] text-slate-400">
                    <th className="py-3 px-4">Time / Pair</th>
                    <th className="py-3 px-3">Type / Side</th>
                    <th className="py-3 px-3">Status</th>
                    <th className="py-3 px-3 text-right">Order / Fill Price</th>
                    <th className="py-3 px-3 text-right">Qty / Filled</th>
                    <th className="py-3 px-3 text-right">Fee / Slip</th>
                    <th className="py-3 px-4">Strategy</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.06]">
                  {exchangeOrders.map((ord) => (
                    <tr key={ord.id} className="hover:bg-white/[0.02]">
                      <td className="py-3 px-4">
                        <div className="text-white font-semibold">{ord.symbol}</div>
                        <div className="text-[10px] text-slate-400">{formatTimeShort(ord.created_at)}</div>
                      </td>
                      <td className="py-3 px-3">
                        <span className={ord.side === 'BUY' ? 'text-[#10B981]' : 'text-[#EF4444]'}>{ord.side}</span>{' '}
                        <span className="text-slate-300">{ord.order_type}</span>
                      </td>
                      <td className="py-3 px-3">
                        <span
                          className={
                            ord.status === 'FILLED'
                              ? 'text-[#10B981]'
                              : ord.status === 'OPEN'
                              ? 'text-[#00E5FF]'
                              : ord.status === 'REJECTED'
                              ? 'text-[#EF4444]'
                              : 'text-slate-400'
                          }
                        >
                          {ord.status}
                        </span>
                      </td>
                      <td className="py-3 px-3 text-right">
                        {formatPrice(ord.price)} / {formatPrice(ord.avg_fill_price)}
                      </td>
                      <td className="py-3 px-3 text-right">
                        {ord.quantity} / {ord.filled_quantity}
                      </td>
                      <td className="py-3 px-3 text-right text-slate-300">
                        {formatUsd(ord.fee_usd)} · {ord.slippage_bps.toFixed(1)} bps
                      </td>
                      <td className="py-3 px-4 text-slate-400 truncate max-w-[160px]">{ord.strategy_name}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )
      )}

      {activeTab === 'chart' && (
        <ProMarketChart
          exchange={exchange}
          marketType={chartMarket}
          symbol={chartSymbol}
          timeframe={chartTf}
          onMarketTypeChange={setChartMarket}
          onSymbolChange={setChartSymbol}
          onTimeframeChange={setChartTf}
          activePosition={futuresPositions.find((p) => p.symbol === chartSymbol) || null}
          initData={initData}
        />
      )}
    </div>
  );
};
