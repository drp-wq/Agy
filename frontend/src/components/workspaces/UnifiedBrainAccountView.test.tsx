import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { UnifiedBrainAccountContent } from './UnifiedBrainAccountView';
import {
  PersistenceHealthPayload,
  UnifiedAccountPayload,
  UnifiedBrainStatusPayload
} from '../../services/violetResearchApi';

const sampleBrain: UnifiedBrainStatusPayload = {
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
  last_report: {
    timestamp_utc: '2026-11-14T07:10:00+00:00',
    mode: 'PAPER_SIMULATION_ONLY',
    live_execution_enabled: false,
    halted_by_risk_guardian: false,
    actions_executed: [],
    skipped_reasons: ['service_metrics_unavailable:ValueError:service_metrics_missing_or_stale'],
    invariant: { conservation_ok: true }
  }
};

const sampleAccount: UnifiedAccountPayload = {
  mode: 'PAPER_SIMULATION_ONLY',
  live_execution_enabled: false,
  active_cex_venues: ['cex_binance', 'cex_okx', 'cex_bybit'],
  active_dex_venues: ['dex_solana'],
  hub_venue: 'hub_binance_master',
  balances_usd: {
    hub_binance_master: '12000.00',
    cex_binance: '4000.00',
    cex_okx: '5500.00',
    cex_bybit: '4500.00',
    dex_solana: '1500.00'
  },
  allowlist: {
    hub_binance_master: 'loop://binance/master-hub',
    cex_binance: 'loop://binance/trading-sub',
    cex_okx: 'loop://okx/trading-sub',
    cex_bybit: 'loop://bybit/trading-sub',
    dex_solana: 'loop://solana/treasury-dex-wallet'
  },
  invariant: sampleBrain.invariant,
  risk_gate: sampleBrain.risk_gate,
  transfer_count: 0,
  transfers: []
};

const okPersistence: PersistenceHealthPayload = {
  status: 'ok',
  http_status: 200,
  live_execution_enabled: false,
  live_execution_permanently_disabled: true,
  sqlite: { status: 'ok', integrity: 'ok' },
  last_brain_cycle_timestamp: '2026-11-14T07:10:00+00:00',
  scheduler: { status: 'ok', heartbeat_timestamp: '2026-11-14T07:10:01+00:00', age_seconds: 4 },
  risk_guardian: { status: 'ok', emergency_stop_active: false, circuit_breaker_tripped: false }
};

describe('UnifiedBrainAccountContent', () => {
  it('renders Unified AI Brain, closed-loop account balances, conservation invariant, and persistence health', () => {
    const html = renderToStaticMarkup(
      <UnifiedBrainAccountContent
        brain={sampleBrain}
        account={sampleAccount}
        persistence={okPersistence}
        loading={false}
        refreshing={false}
        authError={null}
        fetchError={null}
        onRefresh={() => undefined}
      />
    );

    expect(html).toContain('Unified AI Brain &amp; Closed-Loop Account');
    expect(html).toContain('LIVE EXECUTION BLOCKED');
    expect(html).toContain('PAPER_SIMULATION_ONLY');
    expect(html).toContain('AGY + Codex');
    expect(html).toContain('CONSERVED ($0.00 DRIFT)');
    expect(html).toContain('12,000.00');
    expect(html).toContain('loop://solana/treasury-dex-wallet');
    expect(html).toContain('service_metrics_unavailable:ValueError:service_metrics_missing_or_stale');
    expect(html).toContain('HTTP 200');
  });

  it('renders degraded persistence health, auth error, and unavailable fetch error honestly', () => {
    const degradedHtml = renderToStaticMarkup(
      <UnifiedBrainAccountContent
        brain={sampleBrain}
        account={sampleAccount}
        persistence={{
          ...okPersistence,
          status: 'unavailable',
          http_status: 503,
          sqlite: { status: 'unavailable', reason: 'router_state_missing' },
          scheduler: { status: 'stale', heartbeat_timestamp: null }
        }}
        loading={false}
        refreshing={false}
        authError={null}
        fetchError={null}
        onRefresh={() => undefined}
      />
    );
    expect(degradedHtml).toContain('HTTP 503');
    expect(degradedHtml).toContain('router_state_missing');
    expect(degradedHtml).toContain('STALE');

    const errorHtml = renderToStaticMarkup(
      <UnifiedBrainAccountContent
        brain={null}
        account={null}
        persistence={null}
        loading={false}
        refreshing={false}
        authError={null}
        fetchError="Backend unreachable"
        onRefresh={() => undefined}
      />
    );
    expect(errorHtml).toContain('Unified Brain &amp; Account Unavailable');
    expect(errorHtml).toContain('Backend unreachable');
  });
});
