import {
  CandleBar,
  ExchangeId,
  MarketType,
  MiniAppHealthResponse,
  MiniAppStateResponse,
  Timeframe
} from '../types/quant';

const TIMEFRAME_SEC: Record<Timeframe, number> = {
  '1m': 60,
  '5m': 300,
  '15m': 900,
  '1h': 3600,
  '4h': 14400,
  '1d': 86400
};

const BASE_PRICES: Record<string, number> = {
  'BTC/USDT': 93420,
  'ETH/USDT': 3418,
  'SOL/USDT': 188.4,
  'BNB/USDT': 634.5,
  'AVAX/USDT': 36.8,
  'ARB/USDT': 1.14
};

/**
 * Deterministic timestamped OHLCV generator strictly for DEMO PREVIEW mode
 * so indicator calculations (EMA, RSI, MACD, VWAP, BB, ATR) operate on valid series.
 */
export function generateDemoCandles(
  exchange: ExchangeId,
  marketType: MarketType,
  symbol: string,
  timeframe: Timeframe,
  count = 160
): CandleBar[] {
  const step = TIMEFRAME_SEC[timeframe] || 900;
  const nowAligned = Math.floor(Date.now() / 1000 / step) * step;
  const baseAnchor = BASE_PRICES[symbol] || 100;
  const exchangeSeed = exchange === 'binance' ? 1 : exchange === 'bybit' ? 2 : 3;
  const marketBias = marketType === 'futures' ? 0.0004 : 0;

  let price = baseAnchor * (0.965 + (exchangeSeed - 1) * 0.0002);
  const bars: CandleBar[] = [];

  for (let i = count - 1; i >= 0; i--) {
    const time = nowAligned - i * step;
    const wave = Math.sin((count - i + exchangeSeed * 7) * 0.14) * 0.0042;
    const micro = Math.cos((count - i) * 0.43 + exchangeSeed) * 0.0024;
    const drift = 0.00022 + marketBias * 0.1;

    const open = price;
    const close = Math.max(0.01, open * (1 + wave + micro + drift));
    const spread = Math.abs(close - open) + open * (0.0018 + Math.abs(micro) * 0.5);
    const high = Math.max(open, close) + spread * 0.55;
    const low = Math.min(open, close) - spread * 0.48;
    const volume =
      (baseAnchor > 10000 ? 180 : baseAnchor > 1000 ? 1400 : 18000) *
      (1 + Math.abs(wave) * 180 + Math.abs(micro) * 90);

    bars.push({
      time,
      open: Number(open.toFixed(baseAnchor < 10 ? 4 : 2)),
      high: Number(high.toFixed(baseAnchor < 10 ? 4 : 2)),
      low: Number(low.toFixed(baseAnchor < 10 ? 4 : 2)),
      close: Number(close.toFixed(baseAnchor < 10 ? 4 : 2)),
      volume: Number(volume.toFixed(2))
    });

    price = close;
  }

  return bars;
}

export function buildDemoHealthResponse(): MiniAppHealthResponse {
  const now = Math.floor(Date.now() / 1000);
  return {
    status: 'ok',
    service: 'multi-exchange-paper-fastapi',
    version: '3.0.0-demo-preview',
    live_execution_permanently_disabled: true,
    paper_engine_running: true,
    telegram_auth_verified: false,
    owner_authorized: false,
    uptime_seconds: 618420,
    vps_host: 'ubuntu-24.04-preview (isolated)',
    caddy_tls: 'TLS 1.3 (trade.187-52-118-25.sslip.io)',
    adapters: {
      binance: {
        public_ws_ok: true,
        public_rest_ok: true,
        private_auth_ok: false,
        private_auth_note: 'Demo preview: private API keys not attached in browser sandbox',
        latency_ms: null,
        last_tick_ts: now - 2
      },
      bybit: {
        public_ws_ok: true,
        public_rest_ok: true,
        private_auth_ok: false,
        private_auth_note: 'Demo preview: read-only UTA observer requires VPS session',
        latency_ms: null,
        last_tick_ts: now - 3
      },
      okx: {
        public_ws_ok: true,
        public_rest_ok: true,
        private_auth_ok: false,
        private_auth_note: 'Demo preview: OKX passphrase-scoped key isolated on VPS',
        latency_ms: null,
        last_tick_ts: now - 2
      }
    },
    timestamp: now
  };
}

export function buildDemoStateResponse(): MiniAppStateResponse {
  const now = Math.floor(Date.now() / 1000);

  const equityCurve = Array.from({ length: 30 }, (_, idx) => {
    const dayTs = now - (29 - idx) * 86400;
    const base = 30000;
    const growth = idx * 245 + Math.sin(idx * 0.65) * 420;
    return {
      time: dayTs,
      equity: Number((base + growth).toFixed(2))
    };
  });

  return {
    is_demo: true,
    live_execution_permanently_disabled: true,
    generated_at: now,
    telegram_owner: {
      authenticated: false,
      telegram_id: null,
      username: 'demo_preview',
      auth_method: 'demo_preview'
    },
    exchanges: {
      binance: {
        exchange: 'binance',
        label: 'Binance',
        public_feed_ok: true,
        public_latency_ms: null,
        private_auth_ok: false,
        private_auth_error: 'Private read-only sync unavailable in isolated DEMO preview',
        last_sync_ts: now - 2,
        paper_equity_usd: 15420.5,
        live_readonly_equity_usd: null,
        available_margin_usd: 10840.2,
        locked_margin_usd: 4580.3,
        realized_pnl_24h: 312.4,
        unrealized_pnl: 284.15,
        exchange_meta: {
          account_type: 'Portfolio / USD-M Futures + Spot',
          fee_tier: 'VIP 0 (Paper 4 bps Taker / 2 bps Maker)',
          margin_ratio_pct: 14.2
        },
        supported_pairs: ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'AVAX/USDT']
      },
      bybit: {
        exchange: 'bybit',
        label: 'Bybit',
        public_feed_ok: true,
        public_latency_ms: null,
        private_auth_ok: false,
        private_auth_error: 'Private read-only UTA sync unavailable in isolated DEMO preview',
        last_sync_ts: now - 3,
        paper_equity_usd: 11890.25,
        live_readonly_equity_usd: null,
        available_margin_usd: 8910.0,
        locked_margin_usd: 2980.25,
        realized_pnl_24h: 194.8,
        unrealized_pnl: 162.4,
        exchange_meta: {
          account_type: 'Unified Trading Account (UTA)',
          fee_tier: 'Non-VIP (Paper 5.5 bps Taker)',
          im_rate_pct: 18.6,
          mm_rate_pct: 3.1
        },
        supported_pairs: ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT']
      },
      okx: {
        exchange: 'okx',
        label: 'OKX',
        public_feed_ok: true,
        public_latency_ms: null,
        private_auth_ok: false,
        private_auth_error: 'Private read-only OKX account sync unavailable in isolated DEMO preview',
        last_sync_ts: now - 2,
        paper_equity_usd: 9840.8,
        live_readonly_equity_usd: null,
        available_margin_usd: 7810.5,
        locked_margin_usd: 2030.3,
        realized_pnl_24h: 88.1,
        unrealized_pnl: -41.2,
        exchange_meta: {
          account_type: 'Multi-Currency Margin Mode',
          fee_tier: 'Regular Lv1 (Paper 5 bps Taker)',
          margin_ratio_pct: 9.8
        },
        supported_pairs: ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'ARB/USDT']
      }
    },
    spot_holdings: [
      {
        id: 'spot-bin-usdt',
        exchange: 'binance',
        account_mode: 'paper',
        asset: 'USDT',
        symbol: 'USDT',
        available: 6420.5,
        locked: 1200.0,
        total: 7620.5,
        avg_entry_price: 1.0,
        mark_price: 1.0,
        valuation_usd: 7620.5,
        unrealized_pnl_usd: 0,
        unrealized_pnl_pct: 0,
        updated_at: now - 2
      },
      {
        id: 'spot-bin-btc',
        exchange: 'binance',
        account_mode: 'paper',
        asset: 'BTC',
        symbol: 'BTC/USDT',
        available: 0.045,
        locked: 0,
        total: 0.045,
        avg_entry_price: 89450.0,
        mark_price: 93420.5,
        valuation_usd: 4203.92,
        unrealized_pnl_usd: 178.67,
        unrealized_pnl_pct: 4.44,
        updated_at: now - 2
      },
      {
        id: 'spot-byb-usdt',
        exchange: 'bybit',
        account_mode: 'paper',
        asset: 'USDT',
        symbol: 'USDT',
        available: 5910.0,
        locked: 0,
        total: 5910.0,
        avg_entry_price: 1.0,
        mark_price: 1.0,
        valuation_usd: 5910.0,
        unrealized_pnl_usd: 0,
        unrealized_pnl_pct: 0,
        updated_at: now - 3
      },
      {
        id: 'spot-byb-sol',
        exchange: 'bybit',
        account_mode: 'paper',
        asset: 'SOL',
        symbol: 'SOL/USDT',
        available: 16.0,
        locked: 0,
        total: 16.0,
        avg_entry_price: 176.2,
        mark_price: 188.4,
        valuation_usd: 3014.4,
        unrealized_pnl_usd: 195.2,
        unrealized_pnl_pct: 6.92,
        updated_at: now - 3
      },
      {
        id: 'spot-okx-usdt',
        exchange: 'okx',
        account_mode: 'paper',
        asset: 'USDT',
        symbol: 'USDT',
        available: 6250.5,
        locked: 0,
        total: 6250.5,
        avg_entry_price: 1.0,
        mark_price: 1.0,
        valuation_usd: 6250.5,
        unrealized_pnl_usd: 0,
        unrealized_pnl_pct: 0,
        updated_at: now - 2
      },
      {
        id: 'spot-okx-eth',
        exchange: 'okx',
        account_mode: 'paper',
        asset: 'ETH',
        symbol: 'ETH/USDT',
        available: 0.48,
        locked: 0,
        total: 0.48,
        avg_entry_price: 3290.0,
        mark_price: 3418.2,
        valuation_usd: 1640.74,
        unrealized_pnl_usd: 61.54,
        unrealized_pnl_pct: 3.9,
        updated_at: now - 2
      }
    ],
    futures_positions: [
      {
        id: 'pos-bin-btc-1',
        exchange: 'binance',
        account_mode: 'paper',
        market_type: 'futures',
        status: 'open',
        symbol: 'BTC/USDT',
        instrument_id: 'BTCUSDT-PERP',
        side: 'LONG',
        quantity: 0.12,
        entry_price: 91640.0,
        mark_price: 93420.5,
        notional_usd: 11210.46,
        margin_usd: 2242.09,
        leverage: 5,
        margin_mode: 'ISOLATED',
        liquidation_price: 74280.0,
        liquidation_distance_pct: 20.49,
        unrealized_pnl_usd: 213.66,
        unrealized_pnl_pct: 9.53,
        realized_pnl_usd: 0,
        stop_loss: 90200.0,
        take_profit: 96500.0,
        sl_tp_status: 'ACTIVE',
        strategy_name: 'EMA_VWAP_Breakout_v3',
        opened_at: now - 14400,
        pnl_history: [
          { time: now - 14400, pnl_usd: -12 },
          { time: now - 10800, pnl_usd: 45 },
          { time: now - 7200, pnl_usd: 138 },
          { time: now - 3600, pnl_usd: 184 },
          { time: now, pnl_usd: 213.66 }
        ]
      },
      {
        id: 'pos-bin-eth-2',
        exchange: 'binance',
        account_mode: 'paper',
        market_type: 'futures',
        status: 'open',
        symbol: 'ETH/USDT',
        instrument_id: 'ETHUSDT-PERP',
        side: 'LONG',
        quantity: 1.8,
        entry_price: 3379.0,
        mark_price: 3418.2,
        notional_usd: 6152.76,
        margin_usd: 1538.19,
        leverage: 4,
        margin_mode: 'CROSS',
        liquidation_price: 2540.0,
        liquidation_distance_pct: 25.69,
        unrealized_pnl_usd: 70.56,
        unrealized_pnl_pct: 4.59,
        realized_pnl_usd: 0,
        stop_loss: 3290.0,
        take_profit: 3560.0,
        sl_tp_status: 'ACTIVE',
        strategy_name: 'Momentum_Pullback_Alpha',
        opened_at: now - 25200,
        pnl_history: [
          { time: now - 25200, pnl_usd: -8 },
          { time: now - 18000, pnl_usd: 22 },
          { time: now - 9000, pnl_usd: 54 },
          { time: now, pnl_usd: 70.56 }
        ]
      },
      {
        id: 'pos-byb-sol-1',
        exchange: 'bybit',
        account_mode: 'paper',
        market_type: 'futures',
        status: 'open',
        symbol: 'SOL/USDT',
        instrument_id: 'SOLUSDT-LINEAR',
        side: 'LONG',
        quantity: 32,
        entry_price: 183.3,
        mark_price: 188.4,
        notional_usd: 6028.8,
        margin_usd: 1507.2,
        leverage: 4,
        margin_mode: 'UTA_CROSS',
        liquidation_price: 141.2,
        liquidation_distance_pct: 25.05,
        unrealized_pnl_usd: 163.2,
        unrealized_pnl_pct: 10.83,
        realized_pnl_usd: 0,
        stop_loss: 177.5,
        take_profit: 198.0,
        sl_tp_status: 'ACTIVE',
        strategy_name: 'Liquidity_Sweep_Linear',
        opened_at: now - 10800,
        pnl_history: [
          { time: now - 10800, pnl_usd: 0 },
          { time: now - 7200, pnl_usd: 68 },
          { time: now - 3600, pnl_usd: 120 },
          { time: now, pnl_usd: 163.2 }
        ]
      },
      {
        id: 'pos-okx-avax-1',
        exchange: 'okx',
        account_mode: 'paper',
        market_type: 'futures',
        status: 'open',
        symbol: 'AVAX/USDT',
        instrument_id: 'AVAX-USDT-SWAP',
        side: 'SHORT',
        quantity: 95,
        entry_price: 36.36,
        mark_price: 36.8,
        notional_usd: 3496.0,
        margin_usd: 1165.33,
        leverage: 3,
        margin_mode: 'MULTI_CCY',
        liquidation_price: 47.9,
        liquidation_distance_pct: 30.16,
        unrealized_pnl_usd: -41.8,
        unrealized_pnl_pct: -3.59,
        realized_pnl_usd: 0,
        stop_loss: 37.9,
        take_profit: 33.8,
        sl_tp_status: 'ACTIVE',
        strategy_name: 'Mean_Reversion_BB_Upper',
        opened_at: now - 5400,
        pnl_history: [
          { time: now - 5400, pnl_usd: 10 },
          { time: now - 3600, pnl_usd: -15 },
          { time: now, pnl_usd: -41.8 }
        ]
      },
      {
        id: 'pos-bin-sol-closed',
        exchange: 'binance',
        account_mode: 'paper',
        market_type: 'futures',
        status: 'closed',
        symbol: 'SOL/USDT',
        instrument_id: 'SOLUSDT-PERP',
        side: 'LONG',
        quantity: 25,
        entry_price: 175.4,
        mark_price: 184.2,
        notional_usd: 4605.0,
        margin_usd: 921.0,
        leverage: 5,
        margin_mode: 'ISOLATED',
        liquidation_price: null,
        liquidation_distance_pct: null,
        unrealized_pnl_usd: 0,
        unrealized_pnl_pct: 0,
        realized_pnl_usd: 220.0,
        stop_loss: 171.0,
        take_profit: 184.2,
        sl_tp_status: 'NONE',
        strategy_name: 'EMA_VWAP_Breakout_v3',
        opened_at: now - 86400,
        closed_at: now - 21600
      },
      {
        id: 'pos-byb-btc-hist',
        exchange: 'bybit',
        account_mode: 'paper',
        market_type: 'futures',
        status: 'historical',
        symbol: 'BTC/USDT',
        instrument_id: 'BTCUSDT-LINEAR',
        side: 'LONG',
        quantity: 0.08,
        entry_price: 88920.0,
        mark_price: 91355.0,
        notional_usd: 7308.4,
        margin_usd: 1461.68,
        leverage: 5,
        margin_mode: 'UTA_CROSS',
        liquidation_price: null,
        liquidation_distance_pct: null,
        unrealized_pnl_usd: 0,
        unrealized_pnl_pct: 0,
        realized_pnl_usd: 194.8,
        stop_loss: 87400.0,
        take_profit: 91350.0,
        sl_tp_status: 'NONE',
        strategy_name: 'Momentum_Pullback_Alpha',
        opened_at: now - 172800,
        closed_at: now - 93600
      }
    ],
    orders: [
      {
        id: 'ord-1001',
        exchange: 'binance',
        account_mode: 'paper',
        market_type: 'futures',
        symbol: 'BTC/USDT',
        side: 'SELL',
        order_type: 'TAKE_PROFIT',
        status: 'OPEN',
        price: 96500.0,
        avg_fill_price: null,
        quantity: 0.12,
        filled_quantity: 0,
        notional_usd: 11580.0,
        fee_usd: 0,
        slippage_bps: 0,
        strategy_name: 'EMA_VWAP_Breakout_v3',
        created_at: now - 14380,
        updated_at: now - 14380
      },
      {
        id: 'ord-1002',
        exchange: 'binance',
        account_mode: 'paper',
        market_type: 'futures',
        symbol: 'BTC/USDT',
        side: 'BUY',
        order_type: 'MARKET',
        status: 'FILLED',
        price: 91632.0,
        avg_fill_price: 91640.0,
        quantity: 0.12,
        filled_quantity: 0.12,
        notional_usd: 10996.8,
        fee_usd: 4.39,
        slippage_bps: 0.87,
        strategy_name: 'EMA_VWAP_Breakout_v3',
        created_at: now - 14400,
        updated_at: now - 14400
      },
      {
        id: 'ord-1003',
        exchange: 'bybit',
        account_mode: 'paper',
        market_type: 'futures',
        symbol: 'SOL/USDT',
        side: 'BUY',
        order_type: 'LIMIT',
        status: 'FILLED',
        price: 183.3,
        avg_fill_price: 183.3,
        quantity: 32,
        filled_quantity: 32,
        notional_usd: 5865.6,
        fee_usd: 1.17,
        slippage_bps: 0.0,
        strategy_name: 'Liquidity_Sweep_Linear',
        created_at: now - 10800,
        updated_at: now - 10750
      },
      {
        id: 'ord-1004',
        exchange: 'okx',
        account_mode: 'paper',
        market_type: 'futures',
        symbol: 'AVAX/USDT',
        side: 'SELL',
        order_type: 'LIMIT',
        status: 'FILLED',
        price: 36.36,
        avg_fill_price: 36.36,
        quantity: 95,
        filled_quantity: 95,
        notional_usd: 3454.2,
        fee_usd: 0.69,
        slippage_bps: 0.0,
        strategy_name: 'Mean_Reversion_BB_Upper',
        created_at: now - 5400,
        updated_at: now - 5380
      },
      {
        id: 'ord-1005',
        exchange: 'okx',
        account_mode: 'paper',
        market_type: 'spot',
        symbol: 'ARB/USDT',
        side: 'BUY',
        order_type: 'LIMIT',
        status: 'REJECTED',
        price: 1.14,
        avg_fill_price: null,
        quantity: 1200,
        filled_quantity: 0,
        notional_usd: 1368.0,
        fee_usd: 0,
        slippage_bps: 0,
        strategy_name: 'Breakout_Scanner_v2',
        created_at: now - 3900,
        updated_at: now - 3900
      }
    ],
    opportunities: [
      {
        id: 'opp-bin-btc',
        exchange: 'binance',
        market_type: 'futures',
        symbol: 'BTC/USDT',
        confirmed: {
          last_price: 93420.5,
          change_24h_pct: 2.84,
          volume_24h_usd: 18420000000,
          spread_bps: 0.11,
          bid_depth_1pct_usd: 42500000,
          ask_depth_1pct_usd: 39800000,
          realized_volatility_pct: 2.15,
          momentum_score: 0.78,
          regime: 'TRENDING_UP',
          observed_at: now - 2
        },
        estimates: {
          strategy_name: 'EMA_VWAP_Breakout_v3',
          direction: 'LONG',
          confidence_pct: 82.5,
          est_fees_bps: 8.0,
          est_slippage_bps: 1.2,
          risk_reward_ratio: 2.85,
          suggested_entry: 93380,
          suggested_sl: 91900,
          suggested_tp: 97600,
          screening_decision: 'APPROVED',
          rejection_reasons: []
        }
      },
      {
        id: 'opp-byb-sol',
        exchange: 'bybit',
        market_type: 'futures',
        symbol: 'SOL/USDT',
        confirmed: {
          last_price: 188.4,
          change_24h_pct: 4.12,
          volume_24h_usd: 3420000000,
          spread_bps: 0.53,
          bid_depth_1pct_usd: 8900000,
          ask_depth_1pct_usd: 8450000,
          realized_volatility_pct: 3.65,
          momentum_score: 0.84,
          regime: 'HIGH_VOL_BREAKOUT',
          observed_at: now - 3
        },
        estimates: {
          strategy_name: 'Liquidity_Sweep_Linear',
          direction: 'LONG',
          confidence_pct: 79.0,
          est_fees_bps: 11.0,
          est_slippage_bps: 2.4,
          risk_reward_ratio: 2.45,
          suggested_entry: 188.1,
          suggested_sl: 183.0,
          suggested_tp: 200.6,
          screening_decision: 'APPROVED',
          rejection_reasons: []
        }
      },
      {
        id: 'opp-okx-eth',
        exchange: 'okx',
        market_type: 'futures',
        symbol: 'ETH/USDT',
        confirmed: {
          last_price: 3418.2,
          change_24h_pct: 1.92,
          volume_24h_usd: 7180000000,
          spread_bps: 0.29,
          bid_depth_1pct_usd: 19200000,
          ask_depth_1pct_usd: 18600000,
          realized_volatility_pct: 2.48,
          momentum_score: 0.62,
          regime: 'TRENDING_UP',
          observed_at: now - 2
        },
        estimates: {
          strategy_name: 'Momentum_Pullback_Alpha',
          direction: 'LONG',
          confidence_pct: 68.5,
          est_fees_bps: 10.0,
          est_slippage_bps: 1.5,
          risk_reward_ratio: 2.1,
          suggested_entry: 3405.0,
          suggested_sl: 3340.0,
          suggested_tp: 3541.5,
          screening_decision: 'WATCHLIST',
          rejection_reasons: ['Awaiting 15m VWAP retest confirmation']
        }
      },
      {
        id: 'opp-okx-arb',
        exchange: 'okx',
        market_type: 'spot',
        symbol: 'ARB/USDT',
        confirmed: {
          last_price: 1.142,
          change_24h_pct: -1.45,
          volume_24h_usd: 142000000,
          spread_bps: 8.75,
          bid_depth_1pct_usd: 380000,
          ask_depth_1pct_usd: 410000,
          realized_volatility_pct: 4.92,
          momentum_score: 0.28,
          regime: 'RANGING',
          observed_at: now - 2
        },
        estimates: {
          strategy_name: 'Breakout_Scanner_v2',
          direction: 'LONG',
          confidence_pct: 44.0,
          est_fees_bps: 10.0,
          est_slippage_bps: 6.8,
          risk_reward_ratio: 1.35,
          suggested_entry: 1.14,
          suggested_sl: 1.09,
          suggested_tp: 1.207,
          screening_decision: 'REJECTED',
          rejection_reasons: [
            'Spread 8.75 bps exceeds 5.0 bps threshold',
            'Risk/Reward 1.35 below 1.80 minimum floor',
            'Weak 4h momentum alignment'
          ]
        }
      },
      {
        id: 'opp-byb-avax',
        exchange: 'bybit',
        market_type: 'futures',
        symbol: 'AVAX/USDT',
        confirmed: {
          last_price: 36.82,
          change_24h_pct: -0.65,
          volume_24h_usd: 410000000,
          spread_bps: 2.15,
          bid_depth_1pct_usd: 1650000,
          ask_depth_1pct_usd: 1490000,
          realized_volatility_pct: 4.1,
          momentum_score: 0.41,
          regime: 'RANGING',
          observed_at: now - 3
        },
        estimates: {
          strategy_name: 'Mean_Reversion_BB_Upper',
          direction: 'SHORT',
          confidence_pct: 59.0,
          est_fees_bps: 11.0,
          est_slippage_bps: 2.8,
          risk_reward_ratio: 1.9,
          suggested_entry: 36.9,
          suggested_sl: 38.05,
          suggested_tp: 34.71,
          screening_decision: 'WATCHLIST',
          rejection_reasons: ['BTC upward trend increases short squeeze risk']
        }
      }
    ],
    cross_exchange_spreads: [
      {
        symbol: 'BTC/USDT',
        binance_price: 93420.5,
        bybit_price: 93438.0,
        okx_price: 93412.1,
        max_spread_bps: 2.77,
        best_buy_exchange: 'okx',
        best_sell_exchange: 'bybit',
        est_net_arb_bps: -7.23,
        executable_note: 'Gross spread < combined taker fees (10 bps); non-viable for arb'
      },
      {
        symbol: 'ETH/USDT',
        binance_price: 3418.2,
        bybit_price: 3420.9,
        okx_price: 3417.6,
        max_spread_bps: 9.65,
        best_buy_exchange: 'okx',
        best_sell_exchange: 'bybit',
        est_net_arb_bps: -0.35,
        executable_note: 'Near breakeven after maker rebates; monitoring depth'
      },
      {
        symbol: 'SOL/USDT',
        binance_price: 188.35,
        bybit_price: 188.62,
        okx_price: 188.31,
        max_spread_bps: 16.46,
        best_buy_exchange: 'okx',
        best_sell_exchange: 'bybit',
        est_net_arb_bps: 6.46,
        executable_note: 'Positive net maker-taker spread detected (Paper simulation only)'
      }
    ],
    risk: {
      total_paper_equity_usd: 37151.55,
      total_live_readonly_equity_usd: null,
      live_readonly_auth_summary: {
        authenticated_exchanges: [],
        total_exchanges: 3,
        usd_valuation_available: false,
        reason: 'Isolated DEMO preview active; live read-only account sync requires VPS session.'
      },
      daily_realized_pnl_usd: 595.3,
      daily_realized_available: true,
      daily_realized_scope: 'utc_day',
      lifetime_realized_pnl_usd: 595.3,
      daily_unrealized_pnl_usd: 405.35,
      current_drawdown_pct: 0.82,
      drawdown_available: true,
      hwm_equity_usd: 37458.71,
      max_drawdown_pct: 3.14,
      drawdown_limit_pct: 8.0,
      risk_allocation_pct: 25.81,
      margin_allocation_pct: 25.81,
      notional_exposure_pct: 53.55,
      reserved_margin_usd: 9590.85,
      free_paper_capital_usd: 27560.7,
      gross_notional_exposure_usd: 19896.02,
      gross_margin_exposure_usd: 9590.85,
      net_delta_exposure_usd: 19896.02,
      circuit_breaker_state: 'ARMED_NOMINAL',
      emergency_stop_active: false,
      concentration_by_asset: [
        { asset: 'BTC', exposure_usd: 15414.38, share_pct: 44.3 },
        { asset: 'SOL', exposure_usd: 9043.2, share_pct: 26.0 },
        { asset: 'ETH', exposure_usd: 7793.5, share_pct: 22.4 },
        { asset: 'AVAX', exposure_usd: 3496.0, share_pct: 7.3 }
      ],
      stale_data_warnings: [
        {
          component: 'Private Account Observers',
          age_sec: 0,
          message: 'Isolated DEMO preview active; live read-only account sync requires VPS session.'
        }
      ],
      reconciliation_errors: []
    },
    analytics: {
      equity_curve: equityCurve,
      win_rate_pct: 64.8,
      profit_factor: 2.18,
      sharpe_ratio: 2.41,
      sortino_ratio: 3.12,
      total_trades: 128,
      closed_trades: 128,
      max_historical_drawdown_pct: 3.14,
      avg_win_usd: 186.4,
      avg_loss_usd: -85.2,
      pnl_by_exchange: {
        binance: 4120.5,
        bybit: 2190.25,
        okx: 840.8
      },
      pnl_by_strategy: [
        { strategy: 'EMA_VWAP_Breakout_v3', trades: 48, win_rate: 68.8, net_pnl_usd: 3420.1 },
        { strategy: 'Liquidity_Sweep_Linear', trades: 39, win_rate: 66.7, net_pnl_usd: 2285.4 },
        { strategy: 'Momentum_Pullback_Alpha', trades: 27, win_rate: 59.3, net_pnl_usd: 1180.2 },
        { strategy: 'Mean_Reversion_BB_Upper', trades: 14, win_rate: 57.1, net_pnl_usd: 265.85 }
      ]
    },
    alerts: [
      {
        id: 'tg-1',
        timestamp: now - 180,
        category: 'SIGNAL',
        severity: 'success',
        exchange: 'binance',
        symbol: 'BTC/USDT',
        title: 'EMA/VWAP Breakout Confirmed (Paper)',
        message: 'BTC/USDT 15m closed above VWAP + upper volume threshold. Paper Long entered at $91,640.',
        delivered_to_bot: true,
        account_mode: 'paper'
      },
      {
        id: 'tg-2',
        timestamp: now - 1200,
        category: 'POSITION',
        severity: 'info',
        exchange: 'bybit',
        symbol: 'SOL/USDT',
        title: 'Take-Profit & Trailing Stop Armed',
        message: 'Bybit Linear SOL/USDT paper position +10.8% ROE. Stop-loss raised to $177.50.',
        delivered_to_bot: true,
        account_mode: 'paper'
      },
      {
        id: 'tg-3',
        timestamp: now - 3900,
        category: 'RISK',
        severity: 'warning',
        exchange: 'okx',
        symbol: 'ARB/USDT',
        title: 'Scanner Gatekeeper Rejected Signal',
        message: 'ARB/USDT order blocked by spread filter (8.75 bps > 5.0 bps cap).',
        delivered_to_bot: true,
        account_mode: 'paper'
      },
      {
        id: 'tg-4',
        timestamp: now - 7200,
        category: 'SYSTEM',
        severity: 'info',
        title: 'Telegram Mini App Security Check',
        message: 'LIVE_EXECUTION_PERMANENTLY_DISABLED verified. Paper engine heartbeat nominal.',
        delivered_to_bot: true,
        account_mode: 'paper'
      }
    ]
  };
}
