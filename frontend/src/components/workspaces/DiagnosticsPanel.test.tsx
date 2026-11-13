import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import { DiagnosticsContent, freshnessLabel } from './DiagnosticsPanel';
import { PaperDiagnostics } from '../../services/quantApi';

const missing = { status: 'missing' as const, timestamp: null, age_seconds: null };
const data: PaperDiagnostics = {
  mode: 'PAPER_ONLY', read_only: true, generated_at: '2026-11-13T00:00:00Z', pnl_semantics: 'Net of fees',
  exchanges: { binance: { orders_by_status: { rejected: 62 }, rejections_by_reason: { risk_limit: 62 },
    fills: 3, fees_usd: 1.25, realized_pnl_usd: -2, unrealized_pnl_usd: 4,
    account_freshness: missing, order_freshness: missing, fill_freshness: missing } },
  system: { cycle: missing, observation: { status: 'stale', timestamp: '2020-01-01', age_seconds: 500 } },
  screening: { cached_market_snapshots: 20, active_signals: 2, note: 'Execution outcomes' },
  risk: { emergency_stop_active: true, live_execution_disabled: true }
};
describe('paper diagnostic panels', () => {
  it('distinguishes missing and stale telemetry', () => {
    expect(freshnessLabel(missing)).toBe('missing');
    const html = renderToStaticMarkup(<DiagnosticsContent data={data} section="system" />);
    expect(html).toContain('stale · 500s old');
    expect(html).toContain('missing');
  });
  it('renders exchange rejection counts, net PnL and fees', () => {
    const orders = renderToStaticMarkup(<DiagnosticsContent data={data} section="orders" />);
    expect(orders).toContain('risk_limit');
    expect(orders).toContain('62');
    const analytics = renderToStaticMarkup(<DiagnosticsContent data={data} section="analytics" />);
    expect(analytics).toContain('-2.00');
    expect(analytics).toContain('1.2500');
    expect(analytics).toContain('Net of fees');
    expect(analytics).toContain('md:grid-cols-3');
  });
  it('renders cached screening and emergency stop', () => {
    expect(renderToStaticMarkup(<DiagnosticsContent data={data} section="scanner" />)).toContain('20');
    expect(renderToStaticMarkup(<DiagnosticsContent data={data} section="risk" />)).toContain('ACTIVE');
  });
});
