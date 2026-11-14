import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import App from '../../App';
import { SolanaResearchContent } from './SolanaResearchView';
import { SolanaSummaryPayload, VioletResearchPayload } from '../../services/violetResearchApi';

const healthySolana: SolanaSummaryPayload = {
  status: 'healthy',
  mode: 'PAPER_ONLY',
  live_orders_enabled: false,
  verdict: 'NOT PROVEN PROFITABLE',
  observations: 18,
  routes: [
    { dataset: 'jupiter_sol_usdc', observations: 10, stale: false, mean_conservative_net_lamports: -150 },
    { dataset: 'raydium_bonk_sol', observations: 8, stale: true, mean_conservative_net_lamports: null }
  ],
  blockers: ['slippage_model_conservative'],
  alerts: ['Profitability is not proven; estimates are not realized trading P&L']
};

const healthyResearch: VioletResearchPayload = {
  mode: 'READ_ONLY_PAPER',
  live_execution_enabled: false,
  violet: {
    campaign: { status: 'running', hours_remaining: 52.5 },
    worker_health: 'ok',
    cycles: 144,
    source: 'isolated_violet_paper_ledger'
  },
  solana: healthySolana,
  accounting: 'SEPARATE_PAPER_LEDGERS_NO_TRANSFERS'
};

describe('SolanaResearchContent', () => {
  it('renders all read-only research tiles without inventing null route metrics', () => {
    const now = Date.now();
    const html = renderToStaticMarkup(
      <SolanaResearchContent
        research={healthyResearch}
        solana={healthySolana}
        fetchedAtMs={now - 12000}
        nowMs={now}
        loading={false}
        refreshing={false}
        authError={null}
        fetchError={null}
        onRefresh={() => undefined}
      />
    );

    expect(html).toContain('Solana Read-Only Research Terminal');
    expect(html).toContain('LIVE ORDERS: DISABLED');
    expect(html).toContain('NOT PROVEN PROFITABLE');
    expect(html).toContain('jupiter_sol_usdc');
    expect(html).toContain('-150 lamports');
    expect(html).toContain('raydium_bonk_sol');
    expect(html).toContain('Unavailable');
    expect(html).toContain('STALE');
    expect(html).toContain('slippage_model_conservative');
    expect(html).toContain('144');
    expect(html).toContain('SEPARATE_PAPER_LEDGERS_NO_TRANSFERS');
  });

  it('renders loading, auth error, and hard fetch error states cleanly', () => {
    const loadingHtml = renderToStaticMarkup(
      <SolanaResearchContent
        research={null}
        solana={null}
        fetchedAtMs={null}
        nowMs={Date.now()}
        loading={true}
        refreshing={false}
        authError={null}
        fetchError={null}
        onRefresh={() => undefined}
      />
    );
    expect(loadingHtml).toContain('Loading Solana research telemetry');

    const authHtml = renderToStaticMarkup(
      <SolanaResearchContent
        research={null}
        solana={null}
        fetchedAtMs={null}
        nowMs={Date.now()}
        loading={false}
        refreshing={false}
        authError="Authentication required"
        fetchError={null}
        onRefresh={() => undefined}
      />
    );
    expect(authHtml).toContain('Solana Research Authentication Required');

    const errorHtml = renderToStaticMarkup(
      <SolanaResearchContent
        research={null}
        solana={null}
        fetchedAtMs={null}
        nowMs={Date.now()}
        loading={false}
        refreshing={false}
        authError={null}
        fetchError="Network unreachable"
        onRefresh={() => undefined}
      />
    );
    expect(errorHtml).toContain('Solana Research Telemetry Unavailable');
    expect(errorHtml).toContain('Network unreachable');
  });

  it('integrates Solana Research as a first-class tab in App navigation without floating overlay', () => {
    const appHtml = renderToStaticMarkup(<App />);
    expect(appHtml).toContain('VIOLET // SCARLET');
    expect(appHtml).toContain('Solana Research');
    expect(appHtml).toContain('Global Overview');
    expect(appHtml).toContain('Market Scanner');
    expect(appHtml).toContain('Positions');
    expect(appHtml).toContain('Risk Management');
    expect(appHtml).toContain('Telegram Alerts');
    expect(appHtml).not.toContain('violet-solana-research');
  });
});
