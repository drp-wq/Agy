import { ApiFetchError, TelegramAuthError } from './quantApi';

export interface SolanaCostAssumptions {
  network_fee_lamports?: number;
  priority_fee_lamports?: number;
  jito_tip_lamports?: number;
  slippage_bps_per_leg?: number;
  extra_buffer_bps?: number;
}

export interface SolanaRouteObservation {
  dataset: string;
  observations: number | null;
  stale: boolean;
  latest_age_seconds?: number | null;
  mean_gross_lamports?: number | null;
  mean_conservative_net_lamports: number | null;
  median_conservative_net_lamports?: number | null;
  positive_conservative_quotes?: number | null;
  cost_assumptions?: SolanaCostAssumptions | null;
  warning?: string | null;
}

export interface SolanaFundingObservation {
  dataset: string;
  gross_funding_usd: number | null;
  fees_usd: number | null;
  net_pnl_usd: number | null;
  hours: number | null;
  observations: number | null;
  coverage_pct: number | null;
  assumption: string | null;
  warning: string | null;
}

export interface SolanaSummaryPayload {
  status: 'healthy' | 'unavailable' | 'safety_check_failed' | string;
  mode: string;
  live_orders_enabled: false;
  verdict: 'NOT PROVEN PROFITABLE' | 'NOT PROVEN' | 'UNKNOWN' | string;
  observations: number | null;
  routes: SolanaRouteObservation[];
  funding?: SolanaFundingObservation[];
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

function numOrNull(val: unknown): number | null {
  return typeof val === 'number' && Number.isFinite(val) ? val : null;
}

function normalizeStringArray(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((item): item is string => typeof item === 'string' && item.trim().length > 0);
}

function normalizeCostAssumptions(raw: unknown): SolanaCostAssumptions | null {
  if (!raw || typeof raw !== 'object') return null;
  const obj = raw as Record<string, unknown>;
  const out: SolanaCostAssumptions = {};
  const keys: Array<keyof SolanaCostAssumptions> = [
    'network_fee_lamports',
    'priority_fee_lamports',
    'jito_tip_lamports',
    'slippage_bps_per_leg',
    'extra_buffer_bps'
  ];
  let hasAny = false;
  for (const k of keys) {
    const val = numOrNull(obj[k]);
    if (val !== null) {
      out[k] = val;
      hasAny = true;
    }
  }
  return hasAny ? out : null;
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
      observations: numOrNull(r.observations),
      stale: r.stale === true,
      latest_age_seconds: numOrNull(r.latest_age_seconds),
      mean_gross_lamports: numOrNull(r.mean_gross_lamports),
      mean_conservative_net_lamports: numOrNull(r.mean_conservative_net_lamports),
      median_conservative_net_lamports: numOrNull(r.median_conservative_net_lamports),
      positive_conservative_quotes: numOrNull(r.positive_conservative_quotes),
      cost_assumptions: normalizeCostAssumptions(r.cost_assumptions),
      warning: typeof r.warning === 'string' ? r.warning : null
    });
  }

  const rawFunding = Array.isArray(obj.funding) ? obj.funding : [];
  const funding: SolanaFundingObservation[] = [];
  for (const item of rawFunding) {
    if (!item || typeof item !== 'object') continue;
    const f = item as Record<string, unknown>;
    if (typeof f.dataset !== 'string') continue;
    funding.push({
      dataset: f.dataset,
      gross_funding_usd: numOrNull(f.gross_funding_usd),
      fees_usd: numOrNull(f.fees_usd),
      net_pnl_usd: numOrNull(f.net_pnl_usd),
      hours: numOrNull(f.hours),
      observations: numOrNull(f.observations),
      coverage_pct: numOrNull(f.coverage_pct),
      assumption: typeof f.assumption === 'string' ? f.assumption : null,
      warning: typeof f.warning === 'string' ? f.warning : null
    });
  }

  return {
    status: obj.status,
    mode: obj.mode,
    live_orders_enabled: false,
    verdict: typeof obj.verdict === 'string' ? obj.verdict : 'UNKNOWN',
    observations: numOrNull(obj.observations),
    routes,
    funding,
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
      cycles: numOrNull(violetObj.cycles),
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

export interface ClosedLoopTransferItem {
  transfer_id: string;
  idempotency_key: string;
  source_venue: string;
  destination_venue: string;
  destination_address: string;
  asset: string;
  amount_usd: string;
  fee_usd: string;
  net_amount_usd: string;
  state: string;
  reason: string;
  requested_by: string;
  created_at_utc: string;
  settled_at_utc?: string | null;
}

export interface UnifiedAccountPayload {
  mode: string;
  live_execution_enabled: false;
  active_cex_venues: string[];
  active_dex_venues: string[];
  hub_venue: string;
  balances_usd: Record<string, string>;
  allowlist: Record<string, string>;
  invariant: {
    conservation_ok: boolean;
    initial_equity_usd: string;
    current_venue_total_usd: string;
    in_flight_usd: string;
    cumulative_fees_usd: string;
    drift_usd: string;
  };
  risk_gate: {
    emergency_stop_active: boolean;
    circuit_breaker_tripped: boolean;
  };
  brain_sync?: {
    agents: string[];
    shared_skills: string[];
    governance_contract: string;
    paper_only: boolean;
  };
  transfer_count: number;
  transfers: ClosedLoopTransferItem[];
}

export async function fetchUnifiedAccount(initData?: string, signal?: AbortSignal): Promise<UnifiedAccountPayload> {
  const res = await fetch(`${getBaseUrl()}/api/violet/unified-account`, {
    method: 'GET',
    headers: buildAuthHeaders(initData),
    credentials: 'same-origin',
    cache: 'no-store',
    signal
  });
  if (res.status === 401 || res.status === 403) {
    throw new TelegramAuthError('Authentication required to read Unified Account telemetry.', res.status);
  }
  if (!res.ok) {
    throw new ApiFetchError(`Unified Account endpoint returned HTTP ${res.status}`, res.status);
  }
  const json = (await res.json()) as UnifiedAccountPayload;
  if (json.live_execution_enabled !== false) {
    throw new ApiFetchError('Safety policy violation: live_execution_enabled must be false');
  }
  return json;
}

export async function simulateClosedLoopTransfer(
  params: {
    source_venue: string;
    destination_venue: string;
    amount_usd: string;
    idempotency_key: string;
    reason?: string;
  },
  initData?: string
): Promise<{ summary: UnifiedAccountPayload; transfer: ClosedLoopTransferItem }> {
  const res = await fetch(`${getBaseUrl()}/api/violet/transfers/simulate`, {
    method: 'POST',
    headers: {
      ...buildAuthHeaders(initData),
      'Content-Type': 'application/json'
    },
    credentials: 'same-origin',
    cache: 'no-store',
    body: JSON.stringify(params)
  });
  if (res.status === 401 || res.status === 403) {
    throw new TelegramAuthError('Blocked or unauthorized to simulate closed-loop transfer.', res.status);
  }
  if (!res.ok) {
    const errText = await res.text();
    throw new ApiFetchError(`Closed-loop simulation failed (${res.status}): ${errText}`, res.status);
  }
  return (await res.json()) as { summary: UnifiedAccountPayload; transfer: ClosedLoopTransferItem };
}
