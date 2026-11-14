import React from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createRoot, type Root } from 'react-dom/client';
import { act } from 'react';
import { TRADE_SYMBOL_CHART_EVENT, TradeSymbolLink } from './TradeSymbolLink';

describe('trade symbol chart across workspaces', () => {
  let root: Root | undefined;
  let host: HTMLDivElement | undefined;
  afterEach(() => { if (root && host) act(() => root!.unmount()); host?.remove(); root = undefined; host = undefined; });
  it('opens exact exchange, symbol and market type by touch-compatible click', () => {
    host = document.createElement('div'); document.body.appendChild(host);
    root = createRoot(host);
    const listener = vi.fn();
    window.addEventListener(TRADE_SYMBOL_CHART_EVENT, listener);
    act(() => root!.render(<TradeSymbolLink exchange="bybit" marketType="spot" symbol="SOL/USDT" />));
    const button = host.querySelector('button')!;
    expect(button.getAttribute('aria-label')).toContain('BYBIT SOL/USDT spot chart');
    act(() => button.click());
    expect(listener).toHaveBeenCalledOnce();
    expect((listener.mock.calls[0][0] as CustomEvent).detail).toEqual({ exchange: 'bybit', marketType: 'spot', symbol: 'SOL/USDT' });
    window.removeEventListener(TRADE_SYMBOL_CHART_EVENT, listener);
  });
});
