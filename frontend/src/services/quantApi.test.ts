import { describe, it, expect, vi, afterEach } from 'vitest';
import { quantApi, TelegramAuthError, ApiFetchError } from './quantApi.ts';
import {
  LIVE_EXECUTION_PERMANENTLY_DISABLED,
  LiveExecutionBlockedError
} from '../types/quant.ts';

describe('quantApi security, incremental candles, cancellation, and error handling', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('enforces LIVE_EXECUTION_PERMANENTLY_DISABLED and blocks live order submission', () => {
    expect(LIVE_EXECUTION_PERMANENTLY_DISABLED).toBe(true);
    expect(() => quantApi.submitLiveOrder()).toThrow(LiveExecutionBlockedError);
  });

  it('passes since & limit query params for incremental candle refresh and normalizes timestamps', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        exchange: 'okx',
        symbol: 'BTC/USDT',
        timeframe: '15m',
        source: 'exchange_rest',
        incremental: true,
        stale: false,
        exchange_healthy: true,
        fetched_at: 1700001900,
        candles: [
          { timestamp: 1700001800000, open: 90100, high: 90200, low: 90050, close: 90150, volume: 12 },
          { timestamp: 1700000900000, open: 90000, high: 90150, low: 89950, close: 90100, volume: 10 },
          { timestamp: 1700001800000, open: 90100, high: 90250, low: 90050, close: 90220, volume: 15 }
        ]
      })
    });
    vi.stubGlobal('fetch', fetchMock);

    const res = await quantApi.getCandles('okx', 'futures', 'BTC/USDT', '15m', undefined, undefined, {
      sinceSec: 1700000900,
      limit: 10
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [calledUrl] = fetchMock.mock.calls[0];
    expect(calledUrl).toBe('/api/miniapp/candles/okx/perp/BTC/USDT/15m?since=1700000900&limit=10');
    expect(res.source).toBe('fast_api');
    expect(res.backend_source).toBe('exchange_rest');
    expect(res.incremental).toBe(true);
    expect(res.candles.map((c) => c.time)).toEqual([1700000900, 1700001800]);
    expect(res.candles[1].close).toBe(90220);
  });

  it('propagates AbortError when request is cancelled on market switch', async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockImplementation((_url: string, init?: RequestInit) => {
      return new Promise((_resolve, reject) => {
        init?.signal?.addEventListener('abort', () => {
          reject(new DOMException('The operation was aborted', 'AbortError'));
        });
      });
    });
    vi.stubGlobal('fetch', fetchMock);

    const promise = quantApi.getCandles('binance', 'spot', 'ETH/USDT', '5m', undefined, controller.signal);
    controller.abort();
    await expect(promise).rejects.toMatchObject({ name: 'AbortError' });
  });

  it('surfaces HTTP 401 as TelegramAuthError and HTTP 429/503/504 as ApiFetchError without falling back to demo', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce({
        ok: false,
        status: 401,
        json: async () => ({ detail: 'Unauthorized' })
      })
    );
    await expect(quantApi.getCandles('bybit', 'spot', 'SOL/USDT', '15m')).rejects.toBeInstanceOf(
      TelegramAuthError
    );

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce({
        ok: false,
        status: 429,
        json: async () => ({ detail: { message: 'BYBIT rate limit exceeded' } })
      })
    );
    await expect(quantApi.getCandles('bybit', 'spot', 'SOL/USDT', '15m')).rejects.toMatchObject({
      name: 'ApiFetchError',
      statusCode: 429,
      message: 'BYBIT rate limit exceeded'
    });
  });

  it('keeps paper equity strictly separated from live read-only equity when validating state payload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          mode: 'PAPER_ONLY',
          live_execution_permanently_disabled: true,
          is_demo: false,
          generated_at: 1700000000,
          exchanges: {
            binance: { private_auth_ok: true, paper_equity_usd: 4000, live_readonly_equity_usd: null },
            bybit: { private_auth_ok: true, paper_equity_usd: 5000, live_readonly_equity_usd: null },
            okx: { private_auth_ok: true, paper_equity_usd: 4916.62, live_readonly_equity_usd: null }
          },
          risk: {
            total_paper_equity_usd: 13916.62,
            total_live_readonly_equity_usd: null
          }
        })
      })
    );

    const stateRes = await quantApi.getState();
    expect(stateRes.isDemo).toBe(false);
    expect(stateRes.data.risk.total_paper_equity_usd).toBe(13916.62);
    expect(stateRes.data.risk.total_live_readonly_equity_usd).toBeNull();
  });
});

describe('paper diagnostics API', () => {
  afterEach(() => vi.restoreAllMocks());
  it('uses authenticated read-only GET with no caching', async () => {
    const data = { mode: 'PAPER_ONLY', read_only: true, exchanges: {}, system: {}, screening: {}, risk: {} };
    const request = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => data });
    vi.stubGlobal('fetch', request);
    expect(await quantApi.getDiagnostics('signed-owner-data')).toEqual(data);
    expect(request).toHaveBeenCalledWith('/api/miniapp/diagnostics', expect.objectContaining({
      credentials: 'include', cache: 'no-store', headers: expect.objectContaining({ 'X-Telegram-Init-Data': 'signed-owner-data' })
    }));
  });
  it('fails closed on auth errors and non-paper responses', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    await expect(quantApi.getDiagnostics()).rejects.toBeInstanceOf(TelegramAuthError);
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ mode: 'LIVE' }) }));
    await expect(quantApi.getDiagnostics()).rejects.toBeInstanceOf(ApiFetchError);
  });
});
