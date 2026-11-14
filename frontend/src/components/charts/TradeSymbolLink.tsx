import React from 'react';
import type { ExchangeId, MarketType } from '../../types/quant';

export const TRADE_SYMBOL_CHART_EVENT = 'violet:open-trade-symbol-chart';
export type TradeSymbolChartDetail = { exchange: ExchangeId; marketType: MarketType; symbol: string };

/** A single, accessible chart entry point for touch, mouse, and keyboard throughout the dashboard. */
export const TradeSymbolLink: React.FC<TradeSymbolChartDetail & { className?: string }> = ({
  exchange, marketType, symbol, className = ''
}) => (
  <button
    type="button"
    aria-label={`Open ${exchange.toUpperCase()} ${symbol} ${marketType} chart`}
    title={`Open ${symbol} chart`}
    className={`inline-flex max-w-full items-center text-left font-semibold text-[#ffe3e5] underline decoration-[#ff3555]/35 underline-offset-4 hover:text-[#ff6375] hover:decoration-[#ff6375] focus-visible:rounded-sm touch-manipulation ${className}`}
    onClick={(event) => {
      event.stopPropagation();
      window.dispatchEvent(new CustomEvent<TradeSymbolChartDetail>(TRADE_SYMBOL_CHART_EVENT, {
        detail: { exchange, marketType, symbol }
      }));
    }}
  >{symbol}</button>
);
