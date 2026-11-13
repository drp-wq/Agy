import { describe, it, expect } from 'vitest';
import {
  CHART_MAX_BACKOFF_MS,
  CHART_REFRESH_MS,
  CHART_STALE_SEC,
  computeNextBackoffMs,
  evaluateFeedFreshness,
  mergeCandlesIncremental
} from './chartRefresh.ts';
import { formatAgeSeconds, formatPrice, formatSignedPct, formatSignedUsd, formatUsd } from './formatters.ts';
import { CandleBar } from '../types/quant.ts';

describe('chartRefresh & data freshness', () => {
  it('merges incremental candles, deduplicates timestamps, filters invalid bars, and sorts ascending', () => {
    const existing: CandleBar[] = [
      { time: 1700000000, open: 90000, high: 90100, low: 89900, close: 90050, volume: 10 },
      { time: 1700000900, open: 90050, high: 90200, low: 90000, close: 90150, volume: 12 }
    ];
    const incoming: CandleBar[] = [
      { time: 1700001800, open: 90150, high: 90300, low: 90100, close: 90280, volume: 20 },
      { time: 1700000900, open: 90050, high: 90250, low: 90000, close: 90190, volume: 15 }, // updated bar
      { time: -1, open: 100, high: 100, low: 100, close: 100, volume: 1 }, // invalid timestamp
      { time: 1700002700, open: Number.NaN, high: 100, low: 100, close: 100, volume: 1 } // invalid OHLC
    ];

    const merged = mergeCandlesIncremental(existing, incoming, true, 10);
    expect(merged.map((c) => c.time)).toEqual([1700000000, 1700000900, 1700001800]);
    expect(merged[1].close).toBe(90190);
    expect(merged[2].close).toBe(90280);

    // Non-incremental full replace drops old bars not in incoming
    const replaced = mergeCandlesIncremental(existing, [incoming[0]], false, 10);
    expect(replaced.map((c) => c.time)).toEqual([1700001800]);
  });

  it('bounds exponential backoff on error and resets on success', () => {
    let backoff = CHART_REFRESH_MS;
    backoff = computeNextBackoffMs(backoff, false);
    expect(backoff).toBe(12000);
    backoff = computeNextBackoffMs(backoff, false);
    expect(backoff).toBe(24000);
    backoff = computeNextBackoffMs(backoff, false);
    expect(backoff).toBe(CHART_MAX_BACKOFF_MS);
    backoff = computeNextBackoffMs(backoff, true);
    expect(backoff).toBe(CHART_REFRESH_MS);
  });

  it('detects stale feeds from age threshold, error, or unhealthy exchange', () => {
    const now = 1_700_000_000_000;
    expect(
      evaluateFeedFreshness({
        lastUpdatedMs: now - 5_000,
        nowMs: now,
        exchangeHealthy: true,
        errorMsg: null
      })
    ).toEqual({ ageSec: 5, isStale: false });

    expect(
      evaluateFeedFreshness({
        lastUpdatedMs: now - (CHART_STALE_SEC + 2) * 1000,
        nowMs: now,
        exchangeHealthy: true,
        errorMsg: null
      }).isStale
    ).toBe(true);

    expect(
      evaluateFeedFreshness({
        lastUpdatedMs: now - 2_000,
        nowMs: now,
        exchangeHealthy: false,
        errorMsg: null
      }).isStale
    ).toBe(true);

    expect(
      evaluateFeedFreshness({
        lastUpdatedMs: now - 2_000,
        nowMs: now,
        exchangeHealthy: true,
        errorMsg: 'HTTP 503'
      }).isStale
    ).toBe(true);
  });

  it('formats metrics and data age cleanly without NaN/undefined artifacts', () => {
    expect(formatUsd(13916.62)).toBe('$13,916.62');
    expect(formatUsd(null)).toBe('—');
    expect(formatSignedUsd(4.08)).toBe('+$4.08');
    expect(formatSignedUsd(-87.45)).toBe('-$87.45');
    expect(formatSignedPct(-0.6)).toBe('-0.60%');
    expect(formatPrice(90123.456)).toBe('90,123.46');
    expect(formatPrice(0.451234)).toBe('0.4512');
    const nowSec = Math.floor(Date.now() / 1000);
    expect(formatAgeSeconds(nowSec - 12)).toMatch(/^\d+s ago$/);
    expect(formatAgeSeconds(null)).toBe('Unknown');
  });
});
