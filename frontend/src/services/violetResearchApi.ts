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
  remaining_seconds?: number;
  cycles_observed?: number;
  initial_budget_usd?: number;
  sleeve_capital_usd?: number;
  public_paper_fills_during_run?: number;
  paper_orders_during_run?: number;
  open_positions?: number;
  cycles?: number;
  [key: string]: unknown;
}

export interface VioletBudgetSummary {
  total_initial_usdt: number;
  sleeve_initial_usdt: number;
  sleeve_count: number;
  current_equity_usdt: number;
  current_cash_usdt: number;
  reserved_margin_usdt: number;
  free_cash_usdt: number;
  current_realized_pnl_usdt: number;
  current_unrealized_pnl_usdt: number;
  current_total_pnl_usdt: number;
  leverage: number;
  borrowed_leverage: number;
  separate_from?: string[];
}

export interface VioletSleeveBalance {
  band: string;
  bot_id: string;
  initial_usdt: number;
  cash_usdt: number;
  reserved_usdt: number;
  free_usdt: number;
  realized_pnl_usdt: number;
  unrealized_pnl_usdt: number;
  equity_usdt: number;
  open_positions: number;
  max_risk_per_trade_pct: number;
  max_risk_per_trade_usdt: number;
  max_position_usdt: number;
  daily_loss_tripped: boolean;
}

export interface VioletRiskBudget {
  leverage: number;
  max_leverage_ratio: number;
  min_order_notional_usd: number;
  max_position_pct_per_sleeve: number;
  max_simultaneous_positions_per_sleeve: number;
  daily_loss_limit_pct_per_sleeve: number;
  portfolio_daily_loss_limit_pct: number;
  portfolio_max_drawdown_pct: number;
  portfolio_max_exposure_pct: number;
  per_symbol_max_exposure_pct: number;
  band_risk_pct?: Record<string, number>;
}

export interface VioletEntryGate {
  status: 'OPEN' | 'BLOCKED' | 'STALE' | 'EXPIRED' | 'INITIALIZING' | string;
  reasons: string[];
  min_notional_blocked_count: number;
  stale_blocked_count: number;
  tripped_sleeves: string[];
  entries_allowed: boolean;
}

export interface VioletHistoricalArchive {
  status: string;
  archive_dir?: string;
  started_at?: string | null;
  ends_at?: string | null;
  cycles_observed?: number | null;
  initial_budget_usdt?: number | null;
  cash_usdt?: number | null;
  realized_pnl_usdt?: number | null;
  sleeve_count?: number;
  orders?: number;
  fills?: number;
  open_positions?: number;
  separate_from_active_campaign?: boolean;
}

export interface VioletMarketCapTierSymbol {
  symbol: string;
  exchange: string;
  last_price: number;
  volume_24h_usd: number;
  market_cap_usd: number;
}

export interface VioletMarketCapTier {
  band: string;
  label: string;
  discovered_count: number;
  scanned_count: number;
  passed_count: number;
  top_symbols: VioletMarketCapTierSymbol[];
}

export interface VioletRejectionItem {
  symbol?: string;
  band?: string;
  exchange?: string;
  reason?: string;
  reasons?: string[];
  notional_usd?: number;
  min_order_notional_usd?: number;
}

export interface VioletResearchPayload {
  mode: string;
  live_execution_enabled: false;
  violet: {
    campaign: VioletCampaignTelemetry | null;
    worker_health: string | null;
    cycles: number | null;
    source: string;
    campaign_id?: string | null;
    budget?: VioletBudgetSummary | null;
    sleeves?: VioletSleeveBalance[];
    risk_budget?: VioletRiskBudget | null;
    entry_gate?: VioletEntryGate | null;
    historical_archive?: VioletHistoricalArchive | null;
    market_cap_tiers?: Record<string, VioletMarketCapTier> | null;
    heartbeat_age_sec?: number | null;
    rejections?: VioletRejectionItem[];
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
      source: typeof violetObj.source === 'string' ? violetObj.source : 'isolated_violet_paper_ledger',
      campaign_id: typeof violetObj.campaign_id === 'string' ? violetObj.campaign_id : null,
      budget:
        violetObj.budget && typeof violetObj.budget === 'object'
          ? (violetObj.budget as VioletBudgetSummary)
          : null,
      sleeves: Array.isArray(violetObj.sleeves) ? (violetObj.sleeves as VioletSleeveBalance[]) : undefined,
      risk_budget:
        violetObj.risk_budget && typeof violetObj.risk_budget === 'object'
          ? (violetObj.risk_budget as VioletRiskBudget)
          : null,
      entry_gate:
        violetObj.entry_gate && typeof violetObj.entry_gate === 'object'
          ? (violetObj.entry_gate as VioletEntryGate)
          : null,
      historical_archive:
        violetObj.historical_archive && typeof violetObj.historical_archive === 'object'
          ? (violetObj.historical_archive as VioletHistoricalArchive)
          : null,
      market_cap_tiers:
        violetObj.market_cap_tiers && typeof violetObj.market_cap_tiers === 'object'
          ? (violetObj.market_cap_tiers as Record<string, VioletMarketCapTier>)
          : null,
      heartbeat_age_sec: numOrNull(violetObj.heartbeat_age_sec),
      rejections: Array.isArray(violetObj.rejections)
        ? (violetObj.rejections as VioletRejectionItem[])
        : undefined
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

export interface UnifiedBrainReport {
  timestamp_utc: string;
  mode: string;
  live_execution_enabled: false;
  halted_by_risk_guardian: boolean;
  actions_executed: ClosedLoopTransferItem[];
  skipped_reasons: string[];
  invariant: Record<string, unknown>;
}

export interface UnifiedBrainStatusPayload {
  mode: string;
  live_execution_enabled: false;
  agents: string[];
  shared_skill: string;
  risk_gate: {
    emergency_stop_active: boolean;
    circuit_breaker_tripped: boolean;
  };
  invariant: UnifiedAccountPayload['invariant'];
  last_report: UnifiedBrainReport | null;
}

export interface PersistenceHealthPayload {
  status: 'ok' | 'unavailable' | string;
  http_status?: number;
  live_execution_enabled: false;
  live_execution_permanently_disabled: true;
  sqlite: {
    status: string;
    integrity?: string;
    reason?: string;
    reconciliation?: { balanced?: boolean; drift_usd?: string };
    invariant?: { conservation_ok?: boolean };
  };
  last_brain_cycle_timestamp: string | null;
  scheduler: {
    status: string;
    heartbeat_timestamp: string | null;
    age_seconds?: number | null;
  };
  risk_guardian: {
    status: string;
    emergency_stop_active?: boolean;
    circuit_breaker_tripped?: boolean;
  };
}

export type ArbitrageSymbol = 'BTC/USDT' | 'ETH/USDT' | 'SOL/USDT';

export interface ArbitrageBookEntry {
  exchange: string;
  status: 'ok' | 'unavailable' | string;
  freshness?: 'fresh' | 'stale' | 'unverified' | 'unavailable' | string;
  ask?: number;
  bid?: number;
  ask_size?: number;
  bid_size?: number;
  top_ask_notional_usd?: number;
  top_bid_notional_usd?: number;
  depth_levels?: number;
  book_timestamp_utc?: string | null;
  age_seconds?: number | null;
  error_type?: string;
}

export interface ArbitrageComparisonEntry {
  buy_exchange: string;
  sell_exchange: string;
  ask: number;
  bid: number;
  buy_ask_size?: number | null;
  sell_bid_size?: number | null;
  gross_spread_pct: number;
  gross_spread_bps?: number;
  net_spread_pct: number | null;
  fees_slippage_verified?: boolean;
  threshold_pct?: number;
  candidate: boolean;
  executable: false;
  quote_age_diff_seconds?: number | null;
  blockers?: string[];
  reason: string;
}

export interface ArbitrageResearchPayload {
  mode: 'READ_ONLY_RESEARCH' | string;
  live_execution: false;
  symbol: string;
  timestamp_utc: string;
  books: ArbitrageBookEntry[];
  comparisons: ArbitrageComparisonEntry[];
  threshold_pct: number;
  max_allocation_pct: number;
  daily_drawdown_limit_pct: number;
  fee_status: string;
  slippage_status: string;
  depth_status?: string;
  inventory_status: string;
  blockers?: string[];
  rate_limit_min_interval_sec?: number;
  cached?: boolean;
  cache_age_seconds?: number;
  note?: string;
}

export async function fetchUnifiedBrainStatus(
  initData?: string,
  signal?: AbortSignal
): Promise<UnifiedBrainStatusPayload> {
  const res = await fetch(`${getBaseUrl()}/api/violet/brain/status`, {
    method: 'GET',
    headers: buildAuthHeaders(initData),
    credentials: 'same-origin',
    cache: 'no-store',
    signal
  });
  if (res.status === 401 || res.status === 403) {
    throw new TelegramAuthError('Authentication required to read Unified AI Brain telemetry.', res.status);
  }
  if (!res.ok) {
    throw new ApiFetchError(`Unified AI Brain endpoint returned HTTP ${res.status}`, res.status);
  }
  const json = (await res.json()) as UnifiedBrainStatusPayload;
  if (json.live_execution_enabled !== false) {
    throw new ApiFetchError('Safety policy violation: live_execution_enabled must be false');
  }
  return json;
}

export async function fetchPersistenceHealth(
  initData?: string,
  signal?: AbortSignal
): Promise<PersistenceHealthPayload> {
  const res = await fetch(`${getBaseUrl()}/api/violet/persistence/health`, {
    method: 'GET',
    headers: buildAuthHeaders(initData),
    credentials: 'same-origin',
    cache: 'no-store',
    signal
  });
  if (res.status === 401 || res.status === 403) {
    throw new TelegramAuthError('Authentication required to inspect persistence health.', res.status);
  }
  if (!res.ok && res.status !== 503) {
    throw new ApiFetchError(`Persistence health endpoint returned HTTP ${res.status}`, res.status);
  }
  const json = (await res.json()) as Record<string, unknown>;
  if (json.live_execution_enabled !== false || json.live_execution_permanently_disabled !== true) {
    throw new ApiFetchError('Safety policy violation: persistence health live execution guard mismatch');
  }
  if (!json.sqlite || !json.scheduler || !json.risk_guardian) {
    throw new ApiFetchError('Invalid persistence health payload');
  }
  return {
    ...(json as unknown as PersistenceHealthPayload),
    http_status: res.status
  };
}

export async function fetchArbitrageResearch(
  symbol: ArbitrageSymbol,
  initData?: string,
  signal?: AbortSignal
): Promise<ArbitrageResearchPayload> {
  const res = await fetch(
    `${getBaseUrl()}/api/violet/arbitrage?symbol=${encodeURIComponent(symbol)}`,
    {
      method: 'GET',
      headers: buildAuthHeaders(initData),
      credentials: 'same-origin',
      cache: 'no-store',
      signal
    }
  );
  if (res.status === 401 || res.status === 403) {
    throw new TelegramAuthError('Authentication required to read Cross-Exchange Arbitrage Lab.', res.status);
  }
  if (!res.ok) {
    throw new ApiFetchError(`Arbitrage research endpoint returned HTTP ${res.status}`, res.status);
  }
  const json = (await res.json()) as ArbitrageResearchPayload;
  if (json.live_execution !== false || json.mode !== 'READ_ONLY_RESEARCH') {
    throw new ApiFetchError('Safety policy violation: arbitrage research must remain READ_ONLY_RESEARCH');
  }
  if (!Array.isArray(json.books) || !Array.isArray(json.comparisons)) {
    throw new ApiFetchError('Invalid arbitrage research payload');
  }
  if (json.comparisons.some((c) => c.executable !== false)) {
    throw new ApiFetchError('Safety policy violation: executable comparisons are forbidden');
  }
  return json;
}

export interface VioletPnlOverview {
  starting_capital_usdt: number;
  current_equity_usdt: number;
  available_cash_usdt: number;
  reserved_margin_usdt: number;
  realized_pnl_usdt: number;
  unrealized_pnl_usdt: number;
  total_fees_usdt: number;
  total_funding_usdt: number;
  net_pnl_after_fees_usdt: number;
  roi_pct: number;
  daily_return_pct: number;
  weekly_return_pct: number;
  monthly_return_pct: number;
  lifetime_return_pct: number;
  max_drawdown_pct: number;
  win_rate_pct: number;
  profit_factor: number;
  total_trades: number;
  active_positions: number;
  total_orders: number;
  total_fills: number;
}

export interface VioletPnlAssetRow {
  asset: string;
  exchange: string;
  contract_type: string;
  market_cap_band: string;
  research_cap_category: string;
  market_cap_source: string;
  strategy: string;
  entry_price: number;
  qty: number;
  entry_timestamp: string | null;
  mark_price: number;
  notional_usd: number;
  margin_usd: number;
  realized_pnl_usd: number;
  unrealized_pnl_usd: number;
  fees_usd: number;
  funding_usd: number;
  net_pnl_usd: number;
  roi_pct: number;
  status: string;
  order_count: number;
  fill_count: number;
  avg_entry_cost: number;
}

export interface VioletLedgerEvent {
  seq: number;
  event_id: string;
  timestamp_utc: string;
  event_type: string;
  bot_id: string;
  exchange: string;
  symbol: string;
  market_cap_band: string;
  strategy: string;
  order_id: string;
  fill_id: string;
  amount_usd: string;
  fee_usd: string;
  funding_usd: string;
  realized_pnl_usd: string;
}

export interface VioletPnlReport {
  mode: string;
  live_accounts_excluded: boolean;
  overview: VioletPnlOverview;
  assets: VioletPnlAssetRow[];
  charts: {
    equity_curve: Array<{ timestamp: string; equity_usd: number; realized_pnl_usd: number; unrealized_pnl_usd: number }>;
    cumulative_realized_pnl: Array<{ timestamp: string; cumulative_pnl_usd: number }>;
    daily_pnl_histogram: Array<{ date: string; realized_pnl_usd: number; fees_usd: number; trades: number }>;
    asset_leaderboard: VioletPnlAssetRow[];
    exchange_comparison: Array<{ exchange: string; trades: number; realized_pnl_usd: number; unrealized_pnl_usd: number; fees_usd: number; net_pnl_usd: number }>;
    cap_category_performance: Array<{ category: string; trades: number; realized_pnl_usd: number; unrealized_pnl_usd: number; fees_usd: number; net_pnl_usd: number }>;
    strategy_performance: Array<{ strategy: string; trades: number; win_rate_pct: number; net_pnl_usd: number; fees_usd: number; sample_size: number; oos_validated: boolean; confidence: string }>;
    drawdown_series: Array<{ timestamp: string; drawdown_pct: number }>;
    fees_funding_breakdown: { trading_fees_usd: number; funding_usd: number; gas_usd: number; total_costs_usd: number };
    timeline: Array<{ id: string; timestamp: string; type: string; exchange: string; symbol: string; band: string; strategy: string; amount_usd: number; fee_usd: number; realized_pnl_usd: number }>;
  };
  ledger: VioletLedgerEvent[];
  reconciliation: { balanced: boolean; drift_usd: number; expected_equity_usd: number; actual_equity_usd: number };
}

export interface VioletPnlFilters {
  asset?: string;
  exchange?: string;
  strategy?: string;
  cap_band?: string;
  status?: string;
  start_date?: string;
  end_date?: string;
}

export async function fetchVioletPnl(
  filters: VioletPnlFilters = {},
  initData?: string,
  signal?: AbortSignal
): Promise<VioletPnlReport> {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => {
    if (v && v.trim()) params.set(k, v.trim());
  });
  const qs = params.toString() ? `?${params.toString()}` : '';
  const res = await fetch(`${getBaseUrl()}/api/violet/pnl${qs}`, {
    method: 'GET',
    headers: buildAuthHeaders(initData),
    credentials: 'same-origin',
    cache: 'no-store',
    signal
  });
  if (res.status === 401 || res.status === 403) {
    throw new TelegramAuthError('Authentication required to read PnL Command Center.', res.status);
  }
  if (!res.ok) {
    throw new ApiFetchError(`VIOLET PnL endpoint returned HTTP ${res.status}`, res.status);
  }
  const json = (await res.json()) as VioletPnlReport;
  if (json.mode !== 'PAPER_ONLY' || json.live_accounts_excluded !== true) {
    throw new ApiFetchError('Safety policy violation: PnL report must remain PAPER_ONLY and exclude live accounts');
  }
  return json;
}
