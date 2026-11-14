import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect } from 'vitest';
import { VioletCampaignPanel } from './VioletCampaignPanel';
import { VioletResearchPayload } from '../../services/violetResearchApi';

function makePayload(overrides?: Partial<VioletResearchPayload['violet']>): VioletResearchPayload {
  return {
    mode: 'READ_ONLY_PAPER',
    live_execution_enabled: false,
    accounting: 'SEPARATE_PAPER_LEDGERS_NO_TRANSFERS',
    solana: {
      status: 'healthy',
      mode: 'PAPER_ONLY',
      live_orders_enabled: false,
      verdict: 'NOT PROVEN PROFITABLE',
      observations: 10,
      routes: [],
      blockers: [],
      alerts: []
    },
    violet: {
      campaign_id: 'violet-all-cap-500-v1',
      worker_health: 'running',
      cycles: 17,
      source: 'isolated_violet_paper_ledger',
      heartbeat_age_sec: 12,
      campaign: {
        campaign_id: 'violet-all-cap-500-v1',
        status: 'active',
        remaining_hours: 71.5,
        public_paper_fills_during_run: 0,
        open_positions: 0
      },
      budget: {
        total_initial_usdt: 500,
        sleeve_initial_usdt: 100,
        sleeve_count: 5,
        current_equity_usdt: 500,
        current_cash_usdt: 500,
        reserved_margin_usdt: 0,
        free_cash_usdt: 500,
        current_realized_pnl_usdt: 0,
        current_unrealized_pnl_usdt: 0,
        current_total_pnl_usdt: 0,
        leverage: 0,
        borrowed_leverage: 0
      },
      risk_budget: {
        leverage: 0,
        max_leverage_ratio: 1,
        min_order_notional_usd: 5,
        max_position_pct_per_sleeve: 0.2,
        max_simultaneous_positions_per_sleeve: 3,
        daily_loss_limit_pct_per_sleeve: 0.01,
        portfolio_daily_loss_limit_pct: 0.03,
        portfolio_max_drawdown_pct: 0.1,
        portfolio_max_exposure_pct: 0.6,
        per_symbol_max_exposure_pct: 0.2
      },
      entry_gate: {
        status: 'OPEN',
        reasons: [],
        min_notional_blocked_count: 0,
        stale_blocked_count: 0,
        tripped_sleeves: [],
        entries_allowed: true
      },
      historical_archive: {
        status: 'archived_read_only',
        archive_dir: 'violet-all-cap',
        initial_budget_usdt: 5000,
        realized_pnl_usdt: 0,
        fills: 0,
        open_positions: 0,
        separate_from_active_campaign: true
      },
      sleeves: [
        {
          band: 'micro',
          bot_id: 'violet_all_cap_micro_spot_paper',
          initial_usdt: 100,
          cash_usdt: 100,
          reserved_usdt: 0,
          free_usdt: 100,
          realized_pnl_usdt: 0,
          unrealized_pnl_usdt: 0,
          equity_usdt: 100,
          open_positions: 0,
          max_risk_per_trade_pct: 0.004,
          max_risk_per_trade_usdt: 0.4,
          max_position_usdt: 20,
          daily_loss_tripped: false
        },
        {
          band: 'low',
          bot_id: 'violet_all_cap_low_spot_paper',
          initial_usdt: 100,
          cash_usdt: 100,
          reserved_usdt: 0,
          free_usdt: 100,
          realized_pnl_usdt: 0,
          unrealized_pnl_usdt: 0,
          equity_usdt: 100,
          open_positions: 0,
          max_risk_per_trade_pct: 0.005,
          max_risk_per_trade_usdt: 0.5,
          max_position_usdt: 20,
          daily_loss_tripped: false
        },
        {
          band: 'mid',
          bot_id: 'violet_all_cap_mid_spot_paper',
          initial_usdt: 100,
          cash_usdt: 100,
          reserved_usdt: 0,
          free_usdt: 100,
          realized_pnl_usdt: 0,
          unrealized_pnl_usdt: 0,
          equity_usdt: 100,
          open_positions: 0,
          max_risk_per_trade_pct: 0.006,
          max_risk_per_trade_usdt: 0.6,
          max_position_usdt: 20,
          daily_loss_tripped: false
        },
        {
          band: 'large',
          bot_id: 'violet_all_cap_large_spot_paper',
          initial_usdt: 100,
          cash_usdt: 100,
          reserved_usdt: 0,
          free_usdt: 100,
          realized_pnl_usdt: 0,
          unrealized_pnl_usdt: 0,
          equity_usdt: 100,
          open_positions: 0,
          max_risk_per_trade_pct: 0.008,
          max_risk_per_trade_usdt: 0.8,
          max_position_usdt: 20,
          daily_loss_tripped: false
        },
        {
          band: 'mega',
          bot_id: 'violet_all_cap_mega_spot_paper',
          initial_usdt: 100,
          cash_usdt: 100,
          reserved_usdt: 0,
          free_usdt: 100,
          realized_pnl_usdt: 0,
          unrealized_pnl_usdt: 0,
          equity_usdt: 100,
          open_positions: 0,
          max_risk_per_trade_pct: 0.01,
          max_risk_per_trade_usdt: 1,
          max_position_usdt: 20,
          daily_loss_tripped: false
        }
      ],
      ...overrides
    }
  };
}

describe('VioletCampaignPanel', () => {
  it('displays 500 USDT budget, 5 sleeves of 100 USDT, campaign ID, cycle, risk budget, and historical vs current PnL separation', () => {
    const html = renderToStaticMarkup(<VioletCampaignPanel research={makePayload()} />);
    expect(html).toContain('VIOLET 500 USDT All-Cap Spot Campaign');
    expect(html).toContain('violet-all-cap-500-v1');
    expect(html).toContain('500.00 USDT');
    expect(html).toContain('100.00 USDT');
    expect(html).toContain('Cycle #17');
    expect(html).toContain('0x Lev');
    expect(html).toContain('Min Order 5.00 USDT');
    expect(html).toContain('micro');
    expect(html).toContain('low');
    expect(html).toContain('mid');
    expect(html).toContain('large');
    expect(html).toContain('mega');
    expect(html).not.toContain('Historical Archive (Read-Only, Excluded)');
    expect(html).not.toContain('5,000.00 USDT');
    expect(html).toContain('ENTRY GATE: OPEN');
  });

  it('displays honest BLOCKED and STALE states when entries are blocked or worker heartbeat is stale', () => {
    const blockedHtml = renderToStaticMarkup(
      <VioletCampaignPanel
        research={makePayload({
          entry_gate: {
            status: 'BLOCKED',
            reasons: ['min_order_notional_blocked'],
            min_notional_blocked_count: 2,
            stale_blocked_count: 0,
            tripped_sleeves: [],
            entries_allowed: false
          }
        })}
      />
    );
    expect(blockedHtml).toContain('ENTRY GATE: BLOCKED');
    expect(blockedHtml).toContain('ENTRIES BLOCKED BY FAIL-CLOSED SAFETY / MIN-NOTIONAL GATE');
    expect(blockedHtml).toContain('min_order_notional_blocked');
    expect(blockedHtml).toContain('Min-notional blocks: 2');

    const staleHtml = renderToStaticMarkup(
      <VioletCampaignPanel
        research={makePayload({
          worker_health: 'stale_or_not_started',
          heartbeat_age_sec: 320,
          entry_gate: {
            status: 'STALE',
            reasons: ['worker_heartbeat_stale'],
            min_notional_blocked_count: 0,
            stale_blocked_count: 1,
            tripped_sleeves: [],
            entries_allowed: false
          }
        })}
      />
    );
    const partialHtml = renderToStaticMarkup(
      <VioletCampaignPanel research={makePayload({entry_gate: {
        status: 'OPEN', reasons: [], min_notional_blocked_count: 0,
        stale_blocked_count: 1, partial_feed_degradation: true,
        tripped_sleeves: [], entries_allowed: true
      }})} />
    );
    expect(partialHtml).toContain('PARTIAL FEED DEGRADATION');
    expect(partialHtml).not.toContain('ENTRIES BLOCKED BY FAIL-CLOSED SAFETY');
    expect(staleHtml).toContain('ENTRY GATE: STALE');
    expect(staleHtml).toContain('STALE WORKER TELEMETRY');
  });
});
