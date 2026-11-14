import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import App from '../../App';
import { ArbitrageResearchContent } from './ArbitrageResearchView';
import { ArbitrageResearchPayload } from '../../services/violetResearchApi';

const sampleArbitrage: ArbitrageResearchPayload = {
  mode: 'READ_ONLY_RESEARCH',
  live_execution: false,
  symbol: 'BTC/USDT',
  timestamp_utc: '2026-11-14T07:12:00+00:00',
  books: [
    {
      exchange: 'binance',
      status: 'ok',
      freshness: 'fresh',
      ask: 67500.1,
      bid: 67499.9,
      ask_size: 0.45,
      bid_size: 1.2,
      top_ask_notional_usd: 30375,
      top_bid_notional_usd: 81000,
      depth_levels: 5,
      book_timestamp_utc: '2026-11-14T07:11:58+00:00',
      age_seconds: 2.0
    },
    {
      exchange: 'bybit',
      status: 'ok',
      freshness: 'stale',
      ask: 67620.0,
      bid: 67605.0,
      ask_size: 0.3,
      bid_size: 0.5,
      top_ask_notional_usd: 20286,
      top_bid_notional_usd: 33802,
      depth_levels: 5,
      book_timestamp_utc: '2026-11-14T07:11:30+00:00',
      age_seconds: 30.0
    },
    {
      exchange: 'okx',
      status: 'unavailable',
      freshness: 'unavailable',
      error_type: 'TimeoutError'
    }
  ],
  comparisons: [
    {
      buy_exchange: 'binance',
      sell_exchange: 'bybit',
      ask: 67500.1,
      bid: 67605.0,
      gross_spread_pct: 0.15541,
      gross_spread_bps: 15.54,
      net_spread_pct: null,
      candidate: false,
      executable: false,
      quote_age_diff_seconds: 28.0,
      blockers: [
        'fees_unverified',
        'slippage_unverified',
        'depth_unverified',
        'inventory_not_verified',
        'no_order_routing',
        'stale_or_untimestamped_quote'
      ],
      reason: 'FEES_OR_SLIPPAGE_UNVERIFIED'
    }
  ],
  threshold_pct: 0.35,
  max_allocation_pct: 5.0,
  daily_drawdown_limit_pct: 2.0,
  fee_status: 'UNVERIFIED',
  slippage_status: 'UNVERIFIED',
  inventory_status: 'NOT_VERIFIED',
  blockers: [
    'fees_unverified',
    'slippage_unverified',
    'depth_unverified',
    'inventory_not_verified',
    'no_order_routing',
    'partial_exchange_unavailability',
    'stale_or_untimestamped_quote'
  ],
  cached: false,
  cache_age_seconds: 0
};

describe('ArbitrageResearchContent', () => {
  it('renders order-book freshness, partial outages, gross spreads, unverified net, and blockers', () => {
    const html = renderToStaticMarkup(
      <ArbitrageResearchContent
        symbol="BTC/USDT"
        data={sampleArbitrage}
        loading={false}
        refreshing={false}
        authError={null}
        fetchError={null}
        onSymbolChange={() => undefined}
        onRefresh={() => undefined}
      />
    );

    expect(html).toContain('Cross-Exchange Arbitrage Lab');
    expect(html).toContain('READ-ONLY RESEARCH');
    expect(html).toContain('FRESH · 2.0s');
    expect(html).toContain('STALE · 30.0s');
    expect(html).toContain('Public feed unavailable: TimeoutError');
    expect(html).toContain('0.1554% (15.5 bps)');
    expect(html).toContain('UNVERIFIED');
    expect(html).toContain('NOT EXECUTABLE');
    expect(html).toContain('Fees Unverified');
    expect(html).toContain('Slippage Unverified');
    expect(html).toContain('Depth Unverified');
    expect(html).toContain('Inventory Not Verified');
    expect(html).toContain('No Order Routing');
    expect(html).toContain('Quote skew 28.0s');
  });

  it('renders loading, auth error, and fetch error states honestly', () => {
    const loadingHtml = renderToStaticMarkup(
      <ArbitrageResearchContent
        symbol="ETH/USDT"
        data={null}
        loading={true}
        refreshing={false}
        authError={null}
        fetchError={null}
        onSymbolChange={() => undefined}
        onRefresh={() => undefined}
      />
    );
    expect(loadingHtml).toContain('Loading terminal telemetry');

    const authHtml = renderToStaticMarkup(
      <ArbitrageResearchContent
        symbol="ETH/USDT"
        data={null}
        loading={false}
        refreshing={false}
        authError="Unauthorized"
        fetchError={null}
        onSymbolChange={() => undefined}
        onRefresh={() => undefined}
      />
    );
    expect(authHtml).toContain('Telegram Authentication Required');

    const errorHtml = renderToStaticMarkup(
      <ArbitrageResearchContent
        symbol="SOL/USDT"
        data={null}
        loading={false}
        refreshing={false}
        authError={null}
        fetchError="Research endpoint HTTP 502"
        onSymbolChange={() => undefined}
        onRefresh={() => undefined}
      />
    );
    expect(errorHtml).toContain('Arbitrage Research Telemetry Unavailable');
    expect(errorHtml).toContain('Research endpoint HTTP 502');
  });

  it('registers Arbitrage Lab and Unified AI Brain in App navigation with a 5-slot mobile bar', () => {
    const appHtml = renderToStaticMarkup(<App />);
    expect(appHtml).toContain('Cross-Exchange Arbitrage Lab');
    expect(appHtml).toContain('Unified AI Brain &amp; Account');
    const mobileBarMatch = appHtml.match(/aria-label="Mobile Bottom Navigation"[\s\S]*?<\/nav>/);
    expect(mobileBarMatch).not.toBeNull();
    const buttonCount = (mobileBarMatch![0].match(/<button/g) || []).length;
    expect(buttonCount).toBe(5);
  });
});
