import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, it, expect, vi } from 'vitest';
import { EmbeddedSymbolChart, SymbolChartToggleButton } from './EmbeddedSymbolChart';
import { ProMarketChart } from './ProMarketChart';
import { PositionsTerminalView } from '../workspaces/PositionsTerminalView';
import { PaperOrdersView } from '../workspaces/PaperOrdersView';
import { PositionDetailDrawer } from '../workspaces/PositionDetailDrawer';
import { buildDemoStateResponse } from '../../services/demoFixtures';

describe('EmbeddedSymbolChart & trade symbol integration', () => {
  it('renders SymbolChartToggleButton with correct accessibility attributes', () => {
    const collapsedHtml = renderToStaticMarkup(
      <SymbolChartToggleButton symbol="BTC/USDT" expanded={false} onToggle={() => {}} />
    );
    expect(collapsedHtml).toContain('aria-expanded="false"');
    expect(collapsedHtml).toContain('Show embedded chart for BTC/USDT');

    const expandedHtml = renderToStaticMarkup(
      <SymbolChartToggleButton symbol="BTC/USDT" expanded={true} onToggle={() => {}} />
    );
    expect(expandedHtml).toContain('aria-expanded="true"');
    expect(expandedHtml).toContain('Hide embedded chart for BTC/USDT');
  });

  it('renders EmbeddedSymbolChart header with exchange, symbol, marketType, and close button', () => {
    const onClose = vi.fn();
    const html = renderToStaticMarkup(
      <EmbeddedSymbolChart
        exchange="binance"
        marketType="futures"
        symbol="ETH/USDT"
        onClose={onClose}
      />
    );
    expect(html).toContain('data-testid="embedded-symbol-chart"');
    expect(html).toContain('BINANCE · ETH/USDT');
    expect(html).toContain('futures');
    expect(html).toContain('Close embedded chart for ETH/USDT');
  });

  it('renders embedded chart toggle buttons within PositionsTerminalView trade symbols', () => {
    const state = buildDemoStateResponse();
    const html = renderToStaticMarkup(
      <PositionsTerminalView state={state} onInspectPosition={() => {}} />
    );
    const firstPos = state.futures_positions.find((p) => p.account_mode === 'paper' && p.status === 'open');
    expect(firstPos).toBeDefined();
    expect(html).toContain(`Show embedded chart for ${firstPos!.symbol}`);
  });

  it('renders embedded chart toggle buttons within PaperOrdersView trade symbols', () => {
    const state = buildDemoStateResponse();
    const html = renderToStaticMarkup(<PaperOrdersView state={state} />);
    const firstOrder = state.orders[0];
    expect(firstOrder).toBeDefined();
    expect(html).toContain(`Show embedded chart for ${firstOrder.symbol}`);
  });

  it('embeds EmbeddedSymbolChart directly inside PositionDetailDrawer', () => {
    const state = buildDemoStateResponse();
    const pos = state.futures_positions[0];
    const html = renderToStaticMarkup(
      <PositionDetailDrawer position={pos} onClose={() => {}} onOpenChart={() => {}} />
    );
    expect(html).toContain('data-testid="embedded-symbol-chart"');
    expect(html).toContain(`${pos.exchange.toUpperCase()} · ${pos.symbol}`);
  });

  it('displays active position badge in EmbeddedSymbolChart only when exchange and symbol match', () => {
    const state = buildDemoStateResponse();
    const pos = state.futures_positions.find((p) => p.status === 'open')!;

    const matchedHtml = renderToStaticMarkup(
      <EmbeddedSymbolChart
        exchange={pos.exchange}
        marketType="futures"
        symbol={pos.symbol}
        activePosition={pos}
      />
    );
    expect(matchedHtml).toContain('data-testid="embedded-chart-active-position"');
    expect(matchedHtml).toContain(`${pos.side} ${pos.leverage}x`);

    const unmatchedHtml = renderToStaticMarkup(
      <EmbeddedSymbolChart
        exchange={pos.exchange === 'binance' ? 'bybit' : 'binance'}
        marketType="futures"
        symbol={pos.symbol}
        activePosition={pos}
      />
    );
    expect(unmatchedHtml).not.toContain('data-testid="embedded-chart-active-position"');
  });

  it('renders live active trade position strip inside ProMarketChart when trade is active on chart', () => {
    const state = buildDemoStateResponse();
    const pos = state.futures_positions.find((p) => p.status === 'open')!;

    const html = renderToStaticMarkup(
      <ProMarketChart
        exchange={pos.exchange}
        marketType="futures"
        symbol={pos.symbol}
        timeframe="15m"
        activePosition={pos}
      />
    );
    expect(html).toContain('data-testid="chart-active-position-strip"');
    expect(html).toContain('Active Trade');
    expect(html).toContain(`${pos.side} ${pos.leverage}x`);
    expect(html).toContain('uPnL');

    const otherExchangeHtml = renderToStaticMarkup(
      <ProMarketChart
        exchange={pos.exchange === 'binance' ? 'okx' : 'binance'}
        marketType="futures"
        symbol={pos.symbol}
        timeframe="15m"
        activePosition={pos}
      />
    );
    expect(otherExchangeHtml).not.toContain('data-testid="chart-active-position-strip"');
  });
});
