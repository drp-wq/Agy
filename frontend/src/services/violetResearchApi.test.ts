import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiFetchError, TelegramAuthError } from './quantApi';
import {
  fetchArbitrageResearch,
  fetchPersistenceHealth,
  fetchUnifiedBrainStatus,
  fetchVioletResearch,
  fetchVioletSolana
} from './violetResearchApi';

describe('violetResearchApi authenticated read-only client', () => {
  afterEach(() => vi.restoreAllMocks());

  it('fetches /api/violet/research and /api/violet/solana with GET, same-origin, and no-store', async () => {
    const solanaPayload = {
      status: 'healthy',
      mode: 'PAPER_ONLY',
      live_orders_enabled: false,
      verdict: 'NOT PROVEN PROFITABLE',
      observations: 12,
      routes: [
        {
          dataset: 'jupiter_sol_usdc',
          observations: 12,
          stale: false,
          latest_age_seconds: 2.3,
          mean_gross_lamports: 500,
          mean_conservative_net_lamports: -420,
          median_conservative_net_lamports: -430,
          positive_conservative_quotes: 0,
          cost_assumptions: { network_fee_lamports: 15000, slippage_bps_per_leg: 10 }
        }
      ],
      funding: [
        {
          dataset: 'hyperliquid_7d.csv',
          gross_funding_usd: 2.12,
          fees_usd: 20,
          net_pnl_usd: -17.88,
          hours: 168,
          observations: 168,
          coverage_pct: 100,
          assumption: 'continuous_hold_no_rebalance',
          warning: 'Not realized PnL'
        }
      ],
      blockers: [],
      alerts: ['Profitability is not proven; estimates are not realized trading P&L']
    };
    const researchPayload = {
      mode: 'READ_ONLY_PAPER',
      live_execution_enabled: false,
      violet: {
        campaign: { status: 'running', hours_remaining: 48.2 },
        worker_health: 'ok',
        cycles: 42,
        source: 'isolated_violet_paper_ledger'
      },
      solana: solanaPayload,
      accounting: 'SEPARATE_PAPER_LEDGERS_NO_TRANSFERS'
    };

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => researchPayload })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => solanaPayload });
    vi.stubGlobal('fetch', fetchMock);

    const research = await fetchVioletResearch('signed-tg');
    const solana = await fetchVioletSolana('signed-tg');

    expect(research.mode).toBe('READ_ONLY_PAPER');
    expect(research.violet.cycles).toBe(42);
    expect(solana.routes[0].mean_conservative_net_lamports).toBe(-420);
    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      '/api/violet/research',
      expect.objectContaining({
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store',
        headers: expect.objectContaining({ 'X-Telegram-Init-Data': 'signed-tg' })
      })
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      '/api/violet/solana',
      expect.objectContaining({
        method: 'GET',
        credentials: 'same-origin',
        cache: 'no-store'
      })
    );
  });

  it('fails closed on 401/403 and unsafe live execution flags', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    await expect(fetchVioletResearch()).rejects.toBeInstanceOf(TelegramAuthError);
    await expect(fetchVioletSolana()).rejects.toBeInstanceOf(TelegramAuthError);

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          status: 'healthy',
          mode: 'PAPER_ONLY',
          live_orders_enabled: true,
          verdict: 'UNKNOWN',
          observations: null,
          routes: [],
          blockers: [],
          alerts: []
        })
      })
    );
    await expect(fetchVioletSolana()).rejects.toBeInstanceOf(ApiFetchError);

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          mode: 'READ_ONLY_PAPER',
          live_execution_enabled: true,
          violet: { campaign: null, worker_health: 'ok', cycles: 1, source: 'isolated_violet_paper_ledger' },
          solana: {
            status: 'healthy',
            mode: 'PAPER_ONLY',
            live_orders_enabled: false,
            verdict: 'UNKNOWN',
            observations: null,
            routes: [],
            blockers: [],
            alerts: []
          },
          accounting: 'SEPARATE_PAPER_LEDGERS_NO_TRANSFERS'
        })
      })
    );
    await expect(fetchVioletResearch()).rejects.toBeInstanceOf(ApiFetchError);
  });

  it('preserves unavailable/null data without fabricating metrics', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          status: 'unavailable',
          mode: 'PAPER_ONLY',
          live_orders_enabled: false,
          verdict: 'UNKNOWN',
          observations: null,
          routes: [{ dataset: 'raydium_pool', observations: null, stale: true, mean_conservative_net_lamports: null }],
          blockers: ['upstream_timeout'],
          alerts: ['Solana data unavailable; no live execution permitted']
        })
      })
    );

    const res = await fetchVioletSolana();
    expect(res.status).toBe('unavailable');
    expect(res.observations).toBeNull();
    expect(res.routes[0].stale).toBe(true);
    expect(res.routes[0].mean_conservative_net_lamports).toBeNull();
    expect(res.blockers).toEqual(['upstream_timeout']);
  });

  it('validates arbitrage research, brain status, and persistence health contracts', async () => {
    const arbPayload = {
      mode: 'READ_ONLY_RESEARCH',
      live_execution: false,
      symbol: 'BTC/USDT',
      timestamp_utc: '2026-11-14T07:10:00+00:00',
      books: [{ exchange: 'binance', status: 'ok', freshness: 'fresh', ask: 67500, bid: 67499 }],
      comparisons: [
        {
          buy_exchange: 'binance',
          sell_exchange: 'bybit',
          ask: 67500,
          bid: 67550,
          gross_spread_pct: 0.07407,
          net_spread_pct: null,
          candidate: false,
          executable: false,
          reason: 'FEES_OR_SLIPPAGE_UNVERIFIED'
        }
      ],
      threshold_pct: 0.35,
      max_allocation_pct: 5,
      daily_drawdown_limit_pct: 2,
      fee_status: 'UNVERIFIED',
      slippage_status: 'UNVERIFIED',
      inventory_status: 'NOT_VERIFIED'
    };

    const brainPayload = {
      mode: 'PAPER_SIMULATION_ONLY',
      live_execution_enabled: false,
      agents: ['AGY', 'Codex'],
      shared_skill: 'unified-treasury-router',
      risk_gate: { emergency_stop_active: false, circuit_breaker_tripped: false },
      invariant: {
        conservation_ok: true,
        initial_equity_usd: '27500.00',
        current_venue_total_usd: '27500.00',
        in_flight_usd: '0.00',
        cumulative_fees_usd: '0.00',
        drift_usd: '0.00'
      },
      last_report: null
    };

    const persistenceDegraded = {
      status: 'unavailable',
      live_execution_enabled: false,
      live_execution_permanently_disabled: true,
      sqlite: { status: 'unavailable', reason: 'router_state_missing' },
      last_brain_cycle_timestamp: null,
      scheduler: { status: 'unavailable', heartbeat_timestamp: null },
      risk_guardian: { status: 'ok', emergency_stop_active: false, circuit_breaker_tripped: false }
    };

    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => arbPayload })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => brainPayload })
      .mockResolvedValueOnce({ ok: false, status: 503, json: async () => persistenceDegraded });
    vi.stubGlobal('fetch', fetchMock);

    const arb = await fetchArbitrageResearch('BTC/USDT', 'signed-tg');
    expect(arb.mode).toBe('READ_ONLY_RESEARCH');
    expect(arb.comparisons[0].executable).toBe(false);

    const brain = await fetchUnifiedBrainStatus('signed-tg');
    expect(brain.agents).toEqual(['AGY', 'Codex']);

    const pers = await fetchPersistenceHealth('signed-tg');
    expect(pers.status).toBe('unavailable');
    expect(pers.http_status).toBe(503);
    expect(pers.sqlite.reason).toBe('router_state_missing');
  });

  it('fails closed when arbitrage marks executable=true or live_execution=true', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          mode: 'READ_ONLY_RESEARCH',
          live_execution: false,
          books: [],
          comparisons: [{ buy_exchange: 'binance', sell_exchange: 'bybit', executable: true }]
        })
      })
    );
    await expect(fetchArbitrageResearch('ETH/USDT')).rejects.toBeInstanceOf(ApiFetchError);

    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValueOnce({ ok: false, status: 401 })
    );
    await expect(fetchUnifiedBrainStatus()).rejects.toBeInstanceOf(TelegramAuthError);
  });
});
