/**
 * NEON QUANT V3 — Core Domain Types & Non-Negotiable Read-Only Security Contracts
 */

export const LIVE_EXECUTION_PERMANENTLY_DISABLED = true as const;

export class LiveExecutionBlockedError extends Error {
  public readonly code = 'LIVE_EXECUTION_BLOCKED';
  constructor(action = 'live order execution') {
    super(
      `Security Policy Violation: ${action} is permanently blocked. LIVE_EXECUTION_PERMANENTLY_DISABLED=true.`
    );
    this.name = 'LiveExecutionBlockedError';
  }
}

export function assertPaperOnlyPolicy(attemptedAction: string): never {
  throw new LiveExecutionBlockedError(attemptedAction);
}

export type ExchangeId = 'binance' | 'bybit' | 'okx';
export type MarketType = 'spot' | 'futures';
export type AccountMode = 'paper' | 'live_readonly';
export type PositionStatus = 'open' | 'closed' | 'historical';
export type Timeframe = '1m' | '5m' | '15m' | '1h' | '4h' | '1d';

export type NavSectionId =
  | 'overview'
  | 'scanner'
  | 'opportunities'
  | 'binance'
  | 'bybit'
  | 'okx'
  | 'positions'
  | 'orders'
  | 'analytics'
  | 'risk'
  | 'treasury'
  | 'alerts'
  | 'system';

export interface CandleBar {
  time: number; // Unix seconds UTC
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface CandlesResponse {
  exchange: ExchangeId;
  market_type: MarketType;
  symbol: string;
  timeframe: Timeframe;
  candles: CandleBar[];
  source: 'fast_api' | 'demo_deterministic';
  fetched_at: number;
}

export interface SpotHolding {
  id: string;
  exchange: ExchangeId;
  account_mode: AccountMode;
  asset: string;
  symbol: string;
  available: number;
  locked: number;
  total: number;
  avg_entry_price: number | null;
  mark_price: number;
  valuation_usd: number;
  unrealized_pnl_usd: number | null;
  unrealized_pnl_pct: number | null;
  updated_at: number;
}

export interface FuturesPosition {
  id: string;
  exchange: ExchangeId;
  account_mode: AccountMode;
  market_type: 'futures';
  status: PositionStatus;
  symbol: string;
  instrument_id?: string; // e.g. OKX BTC-USDT-SWAP
  side: 'LONG' | 'SHORT';
  quantity: number;
  entry_price: number;
  mark_price: number;
  notional_usd: number;
  margin_usd: number;
  leverage: number;
  margin_mode: 'ISOLATED' | 'CROSS' | 'UTA_CROSS' | 'MULTI_CCY';
  liquidation_price: number | null;
  liquidation_distance_pct: number | null;
  unrealized_pnl_usd: number;
  unrealized_pnl_pct: number;
  realized_pnl_usd: number;
  stop_loss: number | null;
  take_profit: number | null;
  sl_tp_status: 'ACTIVE' | 'PARTIAL' | 'NONE';
  strategy_name: string;
  opened_at: number;
  closed_at?: number | null;
  pnl_history?: Array<{ time: number; pnl_usd: number }>;
}

export interface PaperOrderRecord {
  id: string;
  exchange: ExchangeId;
  account_mode: AccountMode;
  market_type: MarketType;
  symbol: string;
  side: 'BUY' | 'SELL';
  order_type: 'LIMIT' | 'MARKET' | 'STOP_MARKET' | 'TAKE_PROFIT';
  status: 'OPEN' | 'FILLED' | 'CANCELLED' | 'REJECTED';
  price: number;
  avg_fill_price: number | null;
  quantity: number;
  filled_quantity: number;
  notional_usd: number;
  fee_usd: number;
  slippage_bps: number;
  strategy_name: string;
  created_at: number;
  updated_at: number;
}

export interface ExchangeAccountStatus {
  exchange: ExchangeId;
  label: string;
  public_feed_ok: boolean;
  public_latency_ms: number | null;
  private_auth_ok: boolean;
  private_auth_error: string | null;
  last_sync_ts: number;
  paper_equity_usd: number;
  live_readonly_equity_usd: number | null;
  live_readonly_snapshot?: {
    status: string;
    timestamp: number | null;
    balances: Record<string, number>;
    positions: Array<{ symbol: string; side: string; contracts: number; entry_price: number; unrealized_pnl: number }>;
    error_type?: string;
  };
  available_margin_usd: number;
  locked_margin_usd: number;
  realized_pnl_24h: number;
  unrealized_pnl: number;
  exchange_meta: {
    account_type: string;
    fee_tier: string;
    margin_ratio_pct?: number | null;
    im_rate_pct?: number | null;
    mm_rate_pct?: number | null;
  };
  supported_pairs: string[];
}

export interface MarketOpportunity {
  id: string;
  exchange: ExchangeId;
  market_type: MarketType;
  symbol: string;
  // Confirmed Market Data
  confirmed: {
    last_price: number;
    change_24h_pct: number;
    volume_24h_usd: number;
    spread_bps: number;
    bid_depth_1pct_usd: number;
    ask_depth_1pct_usd: number;
    realized_volatility_pct: number;
    momentum_score: number;
    regime: 'TRENDING_UP' | 'TRENDING_DOWN' | 'RANGING' | 'HIGH_VOL_BREAKOUT';
    observed_at: number;
  };
  // Strategy Model Estimates (Strictly Separated)
  estimates: {
    strategy_name: string;
    direction: 'LONG' | 'SHORT' | 'NEUTRAL';
    confidence_pct: number;
    est_fees_bps: number;
    est_slippage_bps: number;
    risk_reward_ratio: number;
    suggested_entry: number;
    suggested_sl: number;
    suggested_tp: number;
    screening_decision: 'APPROVED' | 'REJECTED' | 'WATCHLIST';
    rejection_reasons: string[];
  };
}

export interface CrossExchangeSpread {
  symbol: string;
  binance_price: number | null;
  bybit_price: number | null;
  okx_price: number | null;
  max_spread_bps: number;
  best_buy_exchange: ExchangeId;
  best_sell_exchange: ExchangeId;
  est_net_arb_bps: number;
  executable_note: string;
}

export interface RiskCommandState {
  total_paper_equity_usd: number;
  total_live_readonly_equity_usd: number | null;
  daily_realized_pnl_usd: number;
  daily_unrealized_pnl_usd: number;
  current_drawdown_pct: number;
  max_drawdown_pct: number;
  drawdown_limit_pct: number;
  risk_allocation_pct: number;
  gross_margin_exposure_usd: number;
  net_delta_exposure_usd: number;
  circuit_breaker_state: 'ARMED_NOMINAL' | 'TRIPPED_VOLATILITY' | 'COOLDOWN';
  emergency_stop_active: boolean;
  concentration_by_asset: Array<{
    asset: string;
    exposure_usd: number;
    share_pct: number;
  }>;
  stale_data_warnings: Array<{
    component: string;
    age_sec: number;
    message: string;
  }>;
  reconciliation_errors: Array<{
    id: string;
    exchange: ExchangeId;
    timestamp: number;
    detail: string;
    severity: 'warning' | 'critical';
  }>;
}

export interface EquityPoint {
  time: number;
  equity: number;
}

export interface PortfolioAnalyticsData {
  equity_curve: EquityPoint[];
  win_rate_pct: number;
  profit_factor: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  total_trades: number;
  avg_win_usd: number;
  avg_loss_usd: number;
  pnl_by_exchange: Record<ExchangeId, number>;
  pnl_by_strategy: Array<{ strategy: string; trades: number; win_rate: number; net_pnl_usd: number }>;
}

export interface TelegramAlertItem {
  id: string;
  timestamp: number;
  category: 'SIGNAL' | 'POSITION' | 'RISK' | 'AUTH' | 'SYSTEM';
  severity: 'info' | 'warning' | 'critical' | 'success';
  exchange?: ExchangeId;
  symbol?: string;
  title: string;
  message: string;
  delivered_to_bot: boolean;
  account_mode: AccountMode;
}

export interface MiniAppHealthResponse {
  status: 'ok' | 'degraded' | 'error';
  service: string;
  version: string;
  live_execution_permanently_disabled: boolean;
  paper_engine_running: boolean;
  telegram_auth_verified: boolean;
  owner_authorized: boolean;
  uptime_seconds: number;
  vps_host: string;
  caddy_tls: string;
  adapters: Record<
    ExchangeId,
    {
      public_ws_ok: boolean;
      public_rest_ok: boolean;
      private_auth_ok: boolean;
      private_auth_note: string;
      latency_ms: number | null;
      last_tick_ts: number;
    }
  >;
  timestamp: number;
}

export interface UnifiedTreasurySlot {
  agent_id: string;
  slot_index: number;
  name: string;
  strategy: string;
  exchange: ExchangeId | string;
  account_id: string;
  allowed_symbols: string[];
  capital_budget: string;
  reserved_capital: string;
  available_capital: string;
  trading_capital: string;
  max_risk_per_trade_pct: string;
  daily_loss_limit_pct: string;
  max_drawdown_pct: string;
  max_leverage: string;
  status: string;
  is_paper: boolean;
  updated_at: string;
}

export interface UnifiedTreasuryState {
  enabled: boolean;
  mode: string;
  live_order_submission?: boolean;
  external_writes?: boolean;
  signing_enabled?: boolean;
  transfers_enabled?: boolean;
  custody_model?: string;
  max_agent_slots?: number;
  configured_slots?: number;
  active_slots?: number;
  risk_limits?: {
    max_single_agent_concentration_pct: number;
    max_aggregate_allocated_pct: number;
    agent_daily_loss_cap_pct: number;
    rebalance_cooldown_sec: number;
  };
  slots?: UnifiedTreasurySlot[];
}

export interface TreasuryReconciliation {
  balanced: boolean;
  journal_entries_checked: number;
  journal_imbalances: Array<Record<string, unknown>>;
  pool_conservation_ok: boolean;
  master_pool: {
    asset: string;
    total_capital: string;
    free_capital: string;
    reserved_capital: string;
    slot_allocated_sum: string;
  };
  usd_valuation?: {
    fx_pair: string;
    fx_rate: string;
    fx_timestamp: string;
    total_usd: string;
    free_usd: string;
    reserved_usd: string;
  };
  ledger_scope?: string;
  exchange_readonly_balances?: Record<
    string,
    {
      status: string;
      balances?: Record<string, number>;
      positions?: Array<Record<string, unknown>>;
      timestamp?: number;
      error_type?: string;
    }
  >;
}

export interface PaperPnlAudit {
  paper_fills: number;
  fee_attributions: number;
  recorded_closures: number;
  closure_attributions: number;
  funding_settlements: number;
  funding_attributions: number;
  account_realized_usd: string;
  attributed_realized_usd: string;
  unexplained_usd: string;
  recorded_evidence_coverage_ok: boolean;
  historical_completeness_certified: boolean;
  reason?: string;
}

export interface MiniAppStateResponse {
  is_demo: boolean;
  live_execution_permanently_disabled: true;
  generated_at: number;
  telegram_owner: {
    authenticated: boolean;
    telegram_id: number | null;
    username: string | null;
    auth_method: 'signed_init_data' | 'demo_preview' | 'unverified';
  };
  exchanges: Record<ExchangeId, ExchangeAccountStatus>;
  spot_holdings: SpotHolding[];
  futures_positions: FuturesPosition[];
  orders: PaperOrderRecord[];
  opportunities: MarketOpportunity[];
  cross_exchange_spreads: CrossExchangeSpread[];
  risk: RiskCommandState;
  analytics: PortfolioAnalyticsData;
  alerts: TelegramAlertItem[];
  unified_treasury?: UnifiedTreasuryState;
  treasury_reconciliation?: TreasuryReconciliation | null;
  paper_pnl_audit?: PaperPnlAudit | null;
}
