import { CandleBar } from '../types/quant';

export const CHART_REFRESH_MS = 6000;
export const CHART_MAX_BACKOFF_MS = 30000;
export const CHART_STALE_SEC = 20;
export const MAX_CHART_CANDLES = 240;

/**
 * Merge incoming OHLCV bars into existing bars, deduplicating by Unix timestamp
 * and keeping strictly ascending chronological order.
 */
export function mergeCandlesIncremental(
  existing: CandleBar[],
  incoming: CandleBar[],
  incremental: boolean,
  maxBars = MAX_CHART_CANDLES
): CandleBar[] {
  const merged = new Map<number, CandleBar>();
  if (incremental) {
    for (const bar of existing) {
      if (Number.isFinite(bar.time) && bar.time > 0) {
        merged.set(bar.time, bar);
      }
    }
  }
  for (const bar of incoming) {
    if (
      Number.isFinite(bar.time) &&
      bar.time > 0 &&
      Number.isFinite(bar.open) &&
      Number.isFinite(bar.high) &&
      Number.isFinite(bar.low) &&
      Number.isFinite(bar.close)
    ) {
      merged.set(bar.time, bar);
    }
  }
  return Array.from(merged.values())
    .sort((a, b) => a.time - b.time)
    .slice(-Math.max(1, maxBars));
}

export function computeNextBackoffMs(
  currentBackoffMs: number,
  succeeded: boolean,
  baseMs = CHART_REFRESH_MS,
  maxMs = CHART_MAX_BACKOFF_MS
): number {
  if (succeeded) return baseMs;
  const base = Number.isFinite(currentBackoffMs) && currentBackoffMs > 0 ? currentBackoffMs : baseMs;
  return Math.min(base * 2, maxMs);
}

export function evaluateFeedFreshness(params: {
  lastUpdatedMs: number;
  nowMs: number;
  exchangeHealthy: boolean;
  errorMsg: string | null;
  staleThresholdSec?: number;
}): { ageSec: number | null; isStale: boolean } {
  const threshold = params.staleThresholdSec ?? CHART_STALE_SEC;
  const ageSec =
    params.lastUpdatedMs > 0
      ? Math.max(0, Math.floor((params.nowMs - params.lastUpdatedMs) / 1000))
      : null;
  const isStale =
    Boolean(params.errorMsg) ||
    !params.exchangeHealthy ||
    (ageSec !== null && ageSec > threshold);
  return { ageSec, isStale };
}
