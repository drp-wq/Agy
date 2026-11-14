import { ApiFetchError, TelegramAuthError } from './quantApi';

export interface SolanaRouteObservation {
  dataset: string;
  observations: number | null;
  stale: boolean;
  mean_conservative_net_lamports: number | null;
}

export interface SolanaSummaryPayload {
  status: 'healthy' | 'unavailable' | 'safety_check_failed' | string;
  mode: string;
  live_orders_enabled: false;
  verdict: 'NOT PROVEN PROFITABLE' | 'NOT PROVEN' | 'UNKNOWN' | string;
  observations: number | null;
  routes: SolanaRouteObservation[];
  blockers: string[];
  alerts: string[];
}

export interface VioletCampaignTelemetry {
  campaign_id?: string;
  status?: string;
  started_at?: string;
  ends_at?: string;
  elapsed_hours?: number;
  remaining_hours?: number;
  cycles?: number;
  [key: string]: unknown;
}

export interface VioletResearchPayload {
  mode: string;
  live_execution_enabled: false;
  violet: {
    campaign: VioletCampaignTelemetry | null;
    worker_health: string | null;
    cycles: number | null;
    source: string;
  };
  solana: SolanaSummaryPayload;
  accounting: string;
}

function getBaseUrl(): string {
  const env = (import.meta as unknown as { env?: Record<string, string | boolean | undefined> }).env;
  const envBase = typeof env?.VITE_API_BASE_URL === 'string' ? env.VITE_API_BASE_URL : '';
  return envBase.replace(/\/+$/, '');
}

function buildAuthHeaders(initData?: string): HeadersInit {
  const headers: Record<string, string> = {
    Accept: 'application/json'
  };
  const tgInitData = initData || window.Telegram?.WebApp?.initData || '';
  if (tgInitData) {
    headers['X-Telegram-Init-Data'] = tgInitData;
  }
  return headers;
}

function normalizeStringArray(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

function validateSolanaSummary(json: unknown): SolanaSummaryPayload {
  if (!json || typeof json !== 'object') {
    throw new ApiFetchError('Invalid Solana summary payload: expected JSON object');
  }
  const obj = json as Record<string, unknown>;
  if (typeof obj.status !== 'string' || typeof obj.mode !== 'string') {
    throw new ApiFetchError('Invalid Solana summary payload: missing status or mode');
  }
  if (obj.live_orders_enabled !== false) {
    throw new ApiFetchError('Safety policy violation: live_orders_enabled must be false');
  }

  const rawRoutes = Array.isArray(obj.routes) ? obj.routes : [];
  const routes: SolanaRouteObservation[] = [];
  for (const item of rawRoutes) {
    if (!item || typeof item !== 'object') continue;
    const r = item as Record<string, unknown>;
    if (typeof r.dataset !== 'string') continue;
    routes.push({
      dataset: r.dataset,
      observations: typeof r.observations === 'number' && Number.isFinite(r.observations) ? r.observations : null,
      stale: r.stale === true,
      mean_conservative_net_lamports:
        typeof r.mean_conservative_net_lamports === 'number' && Number.isFinite(r.mean_conservative_net_lamports)
          ? r.mean_conservative_net_lamports
          : null
    });
  }

  return {
    status: obj.status,
    mode: obj.mode,
    live_orders_enabled: false,
    verdict: typeof obj.verdict === 'string' ? obj.verdict : 'UNKNOWN',
    observations: typeof obj.observations === 'number' && Number.isFinite(obj.observations) ? obj.observations : null,
    routes,
    blockers: normalizeStringArray(obj.blockers),
    alerts: normalizeStringArray(obj.alerts)
  };
}

function validateVioletResearch(json: unknown): VioletResearchPayload {
  if (!json || typeof json !== 'object') {
    throw new ApiFetchError('Invalid VIOLET research payload: expected JSON object');
  }
  const obj = json as Record<string, unknown>;
  if (typeof obj.mode !== 'string' || typeof obj.accounting !== 'string') {
    throw new ApiFetchError('Invalid VIOLET research payload: missing mode or accounting');
  }
  if (obj.live_execution_enabled !== false) {
    throw new ApiFetchError('Safety policy violation: live_execution_enabled must be false');
  }
  if (!obj.violet || typeof obj.violet !== 'object') {
    throw new ApiFetchError('Invalid VIOLET research payload: missing violet block');
  }
  const violetObj = obj.violet as Record<string, unknown>;
  const campaign =
    violetObj.campaign && typeof violetObj.campaign === 'object'
      ? (violetObj.campaign as VioletCampaignTelemetry)
      : null;

  return {
    mode: obj.mode,
    live_execution_enabled: false,
    violet: {
      campaign,
      worker_health: typeof violetObj.worker_health === 'string' ? violetObj.worker_health : null,
      cycles: typeof violetObj.cycles === 'number' && Number.isFinite(violetObj.cycles) ? violetObj.cycles : null,
      source: typeof violetObj.source === 'string' ? violetObj.source : 'isolated_violet_paper_ledger'
    },
    solana: validateSolanaSummary(obj.solana),
    accounting: obj.accounting
  };
}

export async function fetchVioletResearch(initData?: string, signal?: AbortSignal): Promise<VioletResearchPayload> {
  const res = await fetch(`${getBaseUrl()}/api/violet/research`, {
    method: 'GET',
    headers: buildAuthHeaders(initData),
    credentials: 'same-origin',
    cache: 'no-store',
    signal
  });
  if (res.status === 401 || res.status === 403) {
    throw new TelegramAuthError('Authentication required to read VIOLET research telemetry.', res.status);
  }
  if (!res.ok) {
    throw new ApiFetchError(`VIOLET research endpoint returned HTTP ${res.status}`, res.status);
  }
  const json = await res.json();
  return validateVioletResearch(json);
}

export async function fetchVioletSolana(initData?: string, signal?: AbortSignal): Promise<SolanaSummaryPayload> {
  const res = await fetch(`${getBaseUrl()}/api/violet/solana`, {
    method: 'GET',
    headers: buildAuthHeaders(initData),
    credentials: 'same-origin',
    cache: 'no-store',
    signal
  });
  if (res.status === 401 || res.status === 403) {
    throw new TelegramAuthError('Authentication required to read Solana research telemetry.', res.status);
  }
  if (!res.ok) {
    throw new ApiFetchError(`Solana research endpoint returned HTTP ${res.status}`, res.status);
  }
  const json = await res.json();
  return validateSolanaSummary(json);
}
