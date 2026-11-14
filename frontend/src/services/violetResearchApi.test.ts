import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiFetchError, TelegramAuthError } from './quantApi';
import { fetchVioletResearch, fetchVioletSolana } from './violetResearchApi';

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
});
