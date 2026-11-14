import React, { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle,
  ArrowLeftRight,
  Clock,
  Lock,
  RefreshCw,
  ShieldAlert
} from 'lucide-react';
import {
  AuthErrorBanner,
  EXCHANGE_META,
  LoadingTerminalSkeleton,
  SectionHeader
} from '../common/TerminalPrimitives';
import { TelegramAuthError } from '../../services/quantApi';
import {
  ArbitrageBookEntry,
  ArbitrageComparisonEntry,
  ArbitrageResearchPayload,
  ArbitrageSymbol,
  fetchArbitrageResearch
} from '../../services/violetResearchApi';
import { ExchangeId } from '../../types/quant';

const SYMBOLS: ArbitrageSymbol[] = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT'];

const BLOCKER_LABELS: Record<string, string> = {
  fees_unverified: 'Fees Unverified',
  slippage_unverified: 'Slippage Unverified',
  depth_unverified: 'Depth Unverified',
  inventory_not_verified: 'Inventory Not Verified',
  no_order_routing: 'No Order Routing',
  stale_or_untimestamped_quote: 'Stale / Untimestamped Quote',
  partial_exchange_unavailability: 'Partial Exchange Outage'
};

function formatPrice(val: number | undefined): string {
  if (typeof val !== 'number' || !Number.isFinite(val)) return '—';
  return val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

function formatUsdCompact(val: number | undefined): string {
  if (typeof val !== 'number' || !Number.isFinite(val)) return '—';
  return `$${val.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

function freshnessBadge(book: ArbitrageBookEntry): { label: string; tone: string } {
  if (book.status !== 'ok') {
    return {
      label: 'UNAVAILABLE',
      tone: 'bg-[#EF4444]/15 border-[#EF4444]/35 text-[#ff6375]'
    };
  }
  if (book.freshness === 'fresh') {
    return {
      label: typeof book.age_seconds === 'number' ? `FRESH · ${book.age_seconds.toFixed(1)}s` : 'FRESH',
      tone: 'bg-[#10B981]/15 border-[#10B981]/35 text-[#10B981]'
    };
  }
  if (book.freshness === 'stale') {
    return {
      label: typeof book.age_seconds === 'number' ? `STALE · ${book.age_seconds.toFixed(1)}s` : 'STALE',
      tone: 'bg-[#ffb16c]/15 border-[#ffb16c]/35 text-[#ffb16c]'
    };
  }
  return {
    label: 'TIMESTAMP UNVERIFIED',
    tone: 'bg-[#ffb16c]/15 border-[#ffb16c]/35 text-[#ffb16c]'
  };
}

export interface ArbitrageResearchContentProps {
  symbol: ArbitrageSymbol;
  data: ArbitrageResearchPayload | null;
  loading: boolean;
  refreshing: boolean;
  authError: string | null;
  fetchError: string | null;
  onSymbolChange: (symbol: ArbitrageSymbol) => void;
  onRefresh: () => void;
}

export const ArbitrageResearchContent: React.FC<ArbitrageResearchContentProps> = ({
  symbol,
  data,
  loading,
  refreshing,
  authError,
  fetchError,
  onSymbolChange,
  onRefresh
}) => {
  const books = data?.books ?? [];
  const comparisons: ArbitrageComparisonEntry[] = data?.comparisons ?? [];
  const topBlockers = data?.blockers ?? [
    'fees_unverified',
    'slippage_unverified',
    'depth_unverified',
    'inventory_not_verified',
    'no_order_routing'
  ];

  return (
    <section className="space-y-6 text-[#ffe3e5]" aria-label="Cross-Exchange Arbitrage Lab">
      <SectionHeader
        title="Cross-Exchange Arbitrage Lab"
        subtitle="Parallel public order-book telemetry across Binance, Bybit, and OKX · Read-only research without routing or credentials"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span className="px-2.5 py-1 rounded-md bg-[#10B981]/15 border border-[#10B981]/35 text-[#10B981] text-[11px] font-mono inline-flex items-center gap-1.5">
              <Lock className="w-3 h-3" />
              <span>READ-ONLY RESEARCH</span>
            </span>
            <select
              aria-label="Market symbol"
              value={symbol}
              onChange={(e) => onSymbolChange(e.target.value as ArbitrageSymbol)}
              className="rounded-lg bg-[#120508] border border-[#ff3555]/35 px-3 py-2 min-h-[38px] text-xs font-mono text-[#ffe3e5]"
            >
              {SYMBOLS.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onRefresh}
              disabled={refreshing}
              className="px-3.5 py-2 min-h-[38px] rounded-lg bg-[#ff3555]/15 hover:bg-[#ff3555]/25 border border-[#ff3555]/45 text-[#ffe3e5] text-xs font-medium flex items-center gap-1.5 whitespace-nowrap shrink-0 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 text-[#ff6375] ${refreshing ? 'animate-spin' : ''}`} />
              <span>Refresh Books</span>
            </button>
          </div>
        }
      />

      {/* Reference Research Guardrails Banner */}
      <div className="nq-panel rounded-xl p-4 space-y-2 border border-[#ff3555]/30">
        <div className="flex flex-wrap items-center justify-between gap-2 text-xs font-mono">
          <div className="flex items-center gap-2 text-[#ff6375] font-semibold">
            <ArrowLeftRight className="w-4 h-4" />
            <span>VIOLET LAB // GROSS VS NET VERIFICATION BOUNDARY</span>
          </div>
          {data && (
            <span className="text-[11px] text-[#c28e95] flex items-center gap-1.5">
              <Clock className="w-3.5 h-3.5" />
              <span>
                {data.timestamp_utc.replace('T', ' ').slice(0, 19)} UTC ·{' '}
                {data.cached ? `Cached (${data.cache_age_seconds ?? 0}s)` : 'Live Public Snapshot'}
              </span>
            </span>
          )}
        </div>
        <div className="text-xs font-mono text-[#ffd7b3]">
          Reference Net Threshold: {data?.threshold_pct ?? 0.35}% · Reference Allocation Cap:{' '}
          {data?.max_allocation_pct ?? 5}% · Reference Daily Drawdown Limit: {data?.daily_drawdown_limit_pct ?? 2}% ·
          NOT LIVE-ENFORCED
        </div>
        <div className="flex flex-wrap gap-1.5 pt-1">
          {topBlockers.map((b) => (
            <span
              key={b}
              className="px-2 py-0.5 rounded bg-[#ff3555]/10 border border-[#ff3555]/30 text-[10px] font-mono text-[#ff9eab]"
            >
              BLOCKER: {BLOCKER_LABELS[b] ?? b}
            </span>
          ))}
        </div>
      </div>

      {authError && <AuthErrorBanner message={authError} onRetry={onRefresh} />}

      {fetchError && !authError && !data && (
        <div role="alert" className="nq-panel rounded-xl p-6 border border-[#EF4444]/45 space-y-3 text-xs">
          <div className="text-sm font-semibold text-[#ff6375] flex items-center gap-2">
            <ShieldAlert className="w-4 h-4" />
            <span>Arbitrage Research Telemetry Unavailable</span>
          </div>
          <p className="text-[#f3c6cc]">{fetchError}</p>
          <button
            type="button"
            onClick={onRefresh}
            className="px-4 py-2 min-h-[38px] rounded-lg bg-[#ff3555]/20 border border-[#ff3555]/45 text-[#ffe3e5] font-medium"
          >
            Retry Order Books
          </button>
        </div>
      )}

      {fetchError && !authError && data && (
        <div role="alert" className="px-4 py-2.5 rounded-xl bg-[#ffb16c]/10 border border-[#ffb16c]/35 text-xs text-[#ffd7b3] flex items-center justify-between gap-3">
          <span>Order-book refresh failed: {fetchError}. Showing previous snapshot.</span>
          <button
            type="button"
            onClick={onRefresh}
            className="px-2.5 py-1 rounded bg-[#ffb16c]/20 hover:bg-[#ffb16c]/30 text-[#ffe3e5] whitespace-nowrap shrink-0"
          >
            Retry
          </button>
        </div>
      )}

      {loading && !data && !authError && !fetchError && <LoadingTerminalSkeleton />}

      {data && !authError && (
        <>
          {/* Per-Exchange Public Order Books */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {books.map((b) => {
              const exMeta = EXCHANGE_META[b.exchange as ExchangeId];
              const badge = freshnessBadge(b);
              return (
                <article key={b.exchange} className="nq-panel rounded-xl p-4 space-y-3">
                  <div className="flex items-center justify-between gap-2">
                    <h3 className={`font-display font-bold text-base uppercase ${exMeta ? exMeta.accentText : 'text-white'}`}>
                      {exMeta ? exMeta.name : b.exchange}
                    </h3>
                    <span className={`px-2 py-0.5 rounded border text-[10px] font-mono ${badge.tone}`}>
                      {badge.label}
                    </span>
                  </div>

                  {b.status === 'ok' ? (
                    <div className="space-y-2 text-xs font-mono">
                      <div className="grid grid-cols-2 gap-2">
                        <div className="rounded-lg bg-[#080305] border border-[#ff3555]/15 p-2.5">
                          <div className="text-[10px] text-[#b8868e]">Best Ask (Buy)</div>
                          <div className="text-sm font-bold text-[#ffe3e5] mt-0.5">{formatPrice(b.ask)}</div>
                          <div className="text-[10px] text-[#c28e95] mt-0.5">
                            Size {b.ask_size ?? '—'} ({formatUsdCompact(b.top_ask_notional_usd)})
                          </div>
                        </div>
                        <div className="rounded-lg bg-[#080305] border border-[#ff3555]/15 p-2.5">
                          <div className="text-[10px] text-[#b8868e]">Best Bid (Sell)</div>
                          <div className="text-sm font-bold text-[#10B981] mt-0.5">{formatPrice(b.bid)}</div>
                          <div className="text-[10px] text-[#c28e95] mt-0.5">
                            Size {b.bid_size ?? '—'} ({formatUsdCompact(b.top_bid_notional_usd)})
                          </div>
                        </div>
                      </div>
                      <div className="text-[10px] text-[#a1737a] flex items-center justify-between">
                        <span>Top-{b.depth_levels ?? 5} snapshot</span>
                        <span>
                          {b.book_timestamp_utc
                            ? `${b.book_timestamp_utc.replace('T', ' ').slice(11, 19)} UTC`
                            : 'No source ts'}
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div className="rounded-lg bg-[#080305] border border-[#EF4444]/30 p-3 text-xs font-mono text-[#ff9eab] flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4 shrink-0 text-[#EF4444]" />
                      <span>Public feed unavailable: {b.error_type || 'Unavailable'}</span>
                    </div>
                  )}
                </article>
              );
            })}
          </div>

          {/* Cross-Exchange Comparisons */}
          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="font-display font-semibold text-sm text-[#ffe3e5]">
                Cross-Exchange Comparative Gross Spreads ({comparisons.length})
              </h3>
              <span className="text-[11px] font-mono text-[#ffb16c]">
                Fee Status: {data.fee_status} · Slippage: {data.slippage_status} · Inventory: {data.inventory_status}
              </span>
            </div>

            {comparisons.length === 0 ? (
              <div className="nq-panel rounded-xl p-6 text-center text-xs font-mono text-[#c28e95]">
                Fewer than two public order books returned valid quotes; cross-exchange comparisons unavailable.
              </div>
            ) : (
              <div className="grid gap-3 md:grid-cols-2">
                {comparisons.map((c) => {
                  const grossBps =
                    typeof c.gross_spread_bps === 'number'
                      ? c.gross_spread_bps
                      : Math.round(c.gross_spread_pct * 10000) / 100;
                  const pairBlockers = c.blockers ?? [
                    'fees_unverified',
                    'slippage_unverified',
                    'depth_unverified',
                    'inventory_not_verified',
                    'no_order_routing'
                  ];
                  return (
                    <article
                      key={`${c.buy_exchange}-${c.sell_exchange}`}
                      className="nq-panel rounded-xl p-4 space-y-2.5"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <h4 className="font-display font-semibold text-sm uppercase text-[#ffe3e5]">
                          BUY {c.buy_exchange} → SELL {c.sell_exchange}
                        </h4>
                        <span className="px-2 py-0.5 rounded bg-[#ffb16c]/15 border border-[#ffb16c]/35 text-[#ffb16c] text-[10px] font-mono">
                          NOT EXECUTABLE
                        </span>
                      </div>

                      <div className="grid grid-cols-2 gap-3 text-xs font-mono">
                        <div className="rounded-lg bg-[#080305] border border-[#ff3555]/15 p-2.5">
                          <div className="text-[10px] text-[#b8868e]">Gross Spread</div>
                          <div
                            className={`text-sm font-bold mt-0.5 ${
                              c.gross_spread_pct > 0 ? 'text-[#10B981]' : 'text-[#d4a5ac]'
                            }`}
                          >
                            {c.gross_spread_pct.toFixed(4)}% ({grossBps.toFixed(1)} bps)
                          </div>
                          <div className="text-[10px] text-[#a1737a] mt-0.5">
                            Ask {formatPrice(c.ask)} / Bid {formatPrice(c.bid)}
                          </div>
                        </div>

                        <div className="rounded-lg bg-[#080305] border border-[#ffb16c]/25 p-2.5">
                          <div className="text-[10px] text-[#b8868e]">Net Spread</div>
                          <div className="text-sm font-bold text-[#ffb16c] mt-0.5">
                            {c.net_spread_pct !== null ? `${c.net_spread_pct.toFixed(4)}%` : 'UNVERIFIED'}
                          </div>
                          <div className="text-[10px] text-[#a1737a] mt-0.5">
                            {typeof c.quote_age_diff_seconds === 'number'
                              ? `Quote skew ${c.quote_age_diff_seconds.toFixed(1)}s`
                              : 'Quote skew unverified'}
                          </div>
                        </div>
                      </div>

                      <div className="flex flex-wrap gap-1.5">
                        {pairBlockers.map((blk) => (
                          <span
                            key={blk}
                            className="px-2 py-0.5 rounded bg-[#120508] border border-[#ff3555]/25 text-[10px] font-mono text-[#d4a5ac]"
                          >
                            {BLOCKER_LABELS[blk] ?? blk}
                          </span>
                        ))}
                      </div>
                    </article>
                  );
                })}
              </div>
            )}
          </div>

          <p className="text-xs font-mono text-[#c28e95]">
            {data.note ||
              'Paper research only. Simultaneous order execution, pre-funded inventory, partial-fill hedging, WebSocket latency, and asset transfers are not implemented here.'}
          </p>
        </>
      )}
    </section>
  );
};

export default function ArbitrageResearchView({ initData }: { initData?: string }) {
  const [symbol, setSymbol] = useState<ArbitrageSymbol>('BTC/USDT');
  const [data, setData] = useState<ArbitrageResearchPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  const handleRefresh = useCallback(() => {
    setRefreshTick((prev) => prev + 1);
  }, []);

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();

    const load = async () => {
      setRefreshing(true);
      setAuthError(null);
      setFetchError(null);
      try {
        const result = await fetchArbitrageResearch(symbol, initData, controller.signal);
        if (!cancelled) {
          setData(result);
        }
      } catch (err) {
        if (err instanceof DOMException && err.name === 'AbortError') return;
        if (!cancelled) {
          if (err instanceof TelegramAuthError) {
            setAuthError(err.message);
          } else {
            setFetchError(err instanceof Error ? err.message : 'Arbitrage research unavailable');
          }
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    };

    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [symbol, refreshTick, initData]);

  return (
    <ArbitrageResearchContent
      symbol={symbol}
      data={data}
      loading={loading}
      refreshing={refreshing}
      authError={authError}
      fetchError={fetchError}
      onSymbolChange={setSymbol}
      onRefresh={handleRefresh}
    />
  );
}

export { ArbitrageResearchView };
