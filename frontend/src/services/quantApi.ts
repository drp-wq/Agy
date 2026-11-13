import {
  CandleBar,
  CandlesResponse,
  ExchangeId,
  MarketType,
  MiniAppHealthResponse,
  MiniAppStateResponse,
  Timeframe,
  LIVE_EXECUTION_PERMANENTLY_DISABLED,
  LiveExecutionBlockedError
} from '../types/quant';
import {
  buildDemoHealthResponse,
  buildDemoStateResponse,
  generateDemoCandles
} from './demoFixtures';

export class TelegramAuthError extends Error {
  public readonly statusCode: number;
  constructor(message: string, statusCode = 401) {
    super(message);
    this.name = 'TelegramAuthError';
    this.statusCode = statusCode;
  }
}

export class ApiFetchError extends Error {
  public readonly statusCode?: number;
  constructor(message: string, statusCode?: number) {
    super(message);
    this.name = 'ApiFetchError';
    this.statusCode = statusCode;
  }
}

function validateHealthPayload(json: unknown): MiniAppHealthResponse {
  if (!json || typeof json !== 'object') {
    throw new ApiFetchError('Invalid health payload: expected JSON object');
  }
  const obj = json as Record<string, unknown>;
  if (typeof obj.status !== 'string' || !obj.adapters || typeof obj.adapters !== 'object') {
    throw new ApiFetchError('Invalid health payload schema: missing status or adapters');
  }
  const ts = Number(obj.timestamp);
  if (!Number.isFinite(ts) || ts <= 0) {
    throw new ApiFetchError('Invalid health payload: invalid timestamp');
  }
  return json as MiniAppHealthResponse;
}

function validateStatePayload(json: unknown): MiniAppStateResponse {
  if (!json || typeof json !== 'object') {
    throw new ApiFetchError('Invalid state payload: expected JSON object');
  }
  const obj = json as Record<string, unknown>;
  if (!obj.exchanges || typeof obj.exchanges !== 'object' || !obj.risk || typeof obj.risk !== 'object') {
    throw new ApiFetchError('Invalid state payload schema: missing exchanges or risk');
  }
  const genAt = Number(obj.generated_at);
  if (!Number.isFinite(genAt) || genAt <= 0) {
    throw new ApiFetchError('Invalid state payload: invalid generated_at timestamp');
  }
  return {
    ...(json as MiniAppStateResponse),
    is_demo: Boolean(obj.is_demo)
  };
}

function normalizeCandles(raw: unknown): CandleBar[] {
  if (!Array.isArray(raw)) return [];
  const byTime = new Map<number, CandleBar>();
  for (const item of raw) {
    if (!item || typeof item !== 'object') continue;
    const c = item as Record<string, unknown>;
    const rawTime = c.time ?? (typeof c.timestamp === 'number' ? Math.floor(c.timestamp / 1000) : undefined);
    const time = Number(rawTime);
    const open = Number(c.open);
    const high = Number(c.high);
    const low = Number(c.low);
    const close = Number(c.close);
    const volume = Number(c.volume ?? 0);
    if (
      Number.isFinite(time) &&
      time > 0 &&
      Number.isFinite(open) &&
      Number.isFinite(high) &&
      Number.isFinite(low) &&
      Number.isFinite(close)
    ) {
      byTime.set(time, { time, open, high, low, close, volume: Number.isFinite(volume) ? volume : 0 });
    }
  }
  return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
}

export interface Freshness { status: 'fresh' | 'stale' | 'missing' | 'invalid'; timestamp: string | null; age_seconds: number | null }
export interface PaperDiagnostics {
  mode: 'PAPER_ONLY'; read_only: true; generated_at: string; pnl_semantics: string;
  exchanges: Record<string, { orders_by_status: Record<string, number>; rejections_by_reason: Record<string, number>;
    fills: number; fees_usd: number; realized_pnl_usd: number; unrealized_pnl_usd: number;
    account_freshness: Freshness; order_freshness: Freshness; fill_freshness: Freshness }>;
  system: { cycle: Freshness; observation: Freshness };
  screening: { cached_market_snapshots: number; active_signals: number; note: string };
  risk: { emergency_stop_active: boolean; live_execution_disabled: boolean };
}

class NeonQuantApiClient {
  private baseUrl: string;

  constructor() {
    const env = (import.meta as unknown as { env?: Record<string, string | boolean | undefined> }).env;
    const envBase = typeof env?.VITE_API_BASE_URL === 'string' ? env.VITE_API_BASE_URL : '';
    this.baseUrl = envBase.replace(/\/+$/, '');
  }

  private getHeaders(initData?: string): HeadersInit {
    const headers: Record<string, string> = {
      Accept: 'application/json'
    };
    const tgInitData = initData || window.Telegram?.WebApp?.initData || '';
    if (tgInitData) {
      headers['X-Telegram-Init-Data'] = tgInitData;
    }
    return headers;
  }

  /**
   * Demo fallback is strictly disabled in production builds.
   * Only allowed in local Vite dev previews when explicitly opted in.
   */
  private isDevDemoPreviewEnabled(): boolean {
    const env = (import.meta as unknown as { env?: { DEV?: boolean; VITE_ENABLE_DEMO_PREVIEW?: string } }).env;
    return Boolean(env?.DEV && env?.VITE_ENABLE_DEMO_PREVIEW === 'true');
  }

  /**
   * Hard read-only security guard: refuses any call attempting live execution.
   */
  public submitLiveOrder(): never {
    if (LIVE_EXECUTION_PERMANENTLY_DISABLED) {
      throw new LiveExecutionBlockedError('submitLiveOrder');
    }
    throw new LiveExecutionBlockedError('submitLiveOrder');
  }

  public async getDiagnostics(initData?: string, signal?: AbortSignal): Promise<PaperDiagnostics> {
    const res = await fetch(`${this.baseUrl}/api/miniapp/diagnostics`, {
      headers: this.getHeaders(initData), credentials: 'include', cache: 'no-store', signal
    });
    if (res.status === 401 || res.status === 403) throw new TelegramAuthError('Diagnostics authentication required', res.status);
    if (!res.ok) throw new ApiFetchError('Unable to load diagnostics', res.status);
    const data = await res.json();
    if (data?.mode !== 'PAPER_ONLY' || data?.read_only !== true || !data?.exchanges || !data?.system || !data?.screening || !data?.risk) {
      throw new ApiFetchError('Invalid paper diagnostics payload');
    }
    return data as PaperDiagnostics;
  }

  public async getHealth(
    initData?: string,
    signal?: AbortSignal
  ): Promise<{ data: MiniAppHealthResponse; isDemo: boolean }> {
    try {
      const res = await fetch(`${this.baseUrl}/api/miniapp/health`, {
        method: 'GET',
        headers: this.getHeaders(initData),
        credentials: 'same-origin',
        signal
      });

      if (res.status === 401 || res.status === 403) {
        throw new TelegramAuthError(
          'Telegram WebApp signature verification failed or owner not authorized.',
          res.status
        );
      }

      if (res.ok) {
        const json = await res.json();
        return { data: validateHealthPayload(json), isDemo: false };
      }

      throw new ApiFetchError(`Health endpoint returned HTTP ${res.status}`, res.status);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') throw err;
      if (err instanceof TelegramAuthError) throw err;
      if (this.isDevDemoPreviewEnabled()) {
        return { data: buildDemoHealthResponse(), isDemo: true };
      }
      throw err instanceof Error ? err : new ApiFetchError('Unable to reach /api/miniapp/health');
    }
  }

  public async getState(
    initData?: string,
    signal?: AbortSignal
  ): Promise<{ data: MiniAppStateResponse; isDemo: boolean }> {
    try {
      const res = await fetch(`${this.baseUrl}/api/miniapp/state`, {
        method: 'GET',
        headers: this.getHeaders(initData),
        credentials: 'same-origin',
        signal
      });

      if (res.status === 401 || res.status === 403) {
        throw new TelegramAuthError(
          'Unauthorized: Valid Telegram signed initData from authorized owner required.',
          res.status
        );
      }

      if (res.ok) {
        const json = await res.json();
        const validated = validateStatePayload(json);
        return { data: validated, isDemo: validated.is_demo };
      }

      throw new ApiFetchError(`State endpoint returned HTTP ${res.status}`, res.status);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') throw err;
      if (err instanceof TelegramAuthError) throw err;
      if (this.isDevDemoPreviewEnabled()) {
        return { data: buildDemoStateResponse(), isDemo: true };
      }
      throw err instanceof Error ? err : new ApiFetchError('Unable to reach /api/miniapp/state');
    }
  }

  public async getCandles(
    exchange: ExchangeId,
    marketType: MarketType,
    symbol: string,
    timeframe: Timeframe,
    initData?: string,
    signal?: AbortSignal,
    options?: { sinceSec?: number; limit?: number }
  ): Promise<CandlesResponse> {
    const [base, quote] = symbol.split('/');
    const apiMarketType = marketType === 'futures' ? 'perp' : marketType;
    const qs = new URLSearchParams();
    if (options?.sinceSec && options.sinceSec > 0) {
      qs.set('since', String(options.sinceSec));
    }
    if (options?.limit && options.limit > 0) {
      qs.set('limit', String(options.limit));
    }
    const queryStr = qs.toString() ? `?${qs.toString()}` : '';
    const endpoint = `${this.baseUrl}/api/miniapp/candles/${exchange}/${apiMarketType}/${base}/${quote}/${timeframe}${queryStr}`;

    try {
      const res = await fetch(endpoint, {
        method: 'GET',
        headers: this.getHeaders(initData),
        credentials: 'same-origin',
        signal
      });

      if (res.status === 401 || res.status === 403) {
        throw new TelegramAuthError('Unauthorized candle request.', res.status);
      }

      if (res.ok) {
        const json = await res.json();
        const rawCandles = Array.isArray(json) ? json : json.candles || [];
        return {
          exchange,
          market_type: marketType,
          symbol,
          timeframe,
          candles: normalizeCandles(rawCandles),
          source: 'fast_api',
          backend_source: typeof json?.source === 'string' ? json.source : 'exchange_rest',
          stale: Boolean(json?.stale),
          exchange_healthy: json?.exchange_healthy !== false,
          incremental: Boolean(json?.incremental),
          fetched_at: typeof json?.fetched_at === 'number' ? json.fetched_at : Math.floor(Date.now() / 1000)
        };
      }

      let detailMsg = `Candles endpoint returned HTTP ${res.status}`;
      try {
        const errJson = await res.json();
        if (errJson?.detail?.message) detailMsg = String(errJson.detail.message);
        else if (typeof errJson?.detail === 'string') detailMsg = errJson.detail;
      } catch {
        // ignore JSON parse error
      }
      throw new ApiFetchError(detailMsg, res.status);
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') throw err;
      if (err instanceof TelegramAuthError) throw err;
      if (this.isDevDemoPreviewEnabled()) {
        return {
          exchange,
          market_type: marketType,
          symbol,
          timeframe,
          candles: generateDemoCandles(exchange, marketType, symbol, timeframe),
          source: 'demo_deterministic',
          backend_source: 'demo_deterministic',
          stale: false,
          exchange_healthy: true,
          fetched_at: Math.floor(Date.now() / 1000)
        };
      }
      throw err instanceof Error ? err : new ApiFetchError('Unable to fetch OHLCV candles');
    }
  }
}

export const quantApi = new NeonQuantApiClient();
