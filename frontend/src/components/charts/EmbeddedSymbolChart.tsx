import React, { Suspense, lazy, useState } from 'react';
import { LineChart, X, ChevronDown, ChevronUp } from 'lucide-react';
import { ExchangeId, FuturesPosition, MarketType, Timeframe } from '../../types/quant';
import { formatPrice, formatSignedUsd } from '../../utils/formatters';

const ProMarketChart = lazy(() =>
  import('./ProMarketChart').then((m) => ({ default: m.ProMarketChart }))
);

interface EmbeddedSymbolChartProps {
  exchange: ExchangeId;
  marketType: MarketType;
  symbol: string;
  defaultTimeframe?: Timeframe;
  activePosition?: FuturesPosition | null;
  initData?: string;
  onClose?: () => void;
}

export const EmbeddedSymbolChart: React.FC<EmbeddedSymbolChartProps> = ({
  exchange,
  marketType,
  symbol,
  defaultTimeframe = '15m',
  activePosition = null,
  initData,
  onClose
}) => {
  const [timeframe, setTimeframe] = useState<Timeframe>(defaultTimeframe);
  const matchedPosition =
    activePosition && activePosition.exchange === exchange && activePosition.symbol === symbol
      ? activePosition
      : null;

  return (
    <div
      className="rounded-xl border border-[#ff3555]/30 bg-[#080305] overflow-hidden"
      data-testid="embedded-symbol-chart"
    >
      <div className="px-3 py-2 bg-[#120508] border-b border-[#ff3555]/20 flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
          <LineChart className="w-3.5 h-3.5 text-[#ff6375]" />
          <span className="text-[#ffe3e5] font-semibold">
            {exchange.toUpperCase()} · {symbol}
          </span>
          <span className="text-[10px] uppercase px-1.5 py-0.5 rounded bg-[#ff3555]/15 text-[#ff6375]">
            {marketType}
          </span>
          {matchedPosition && (
            <span
              data-testid="embedded-chart-active-position"
              className="text-[10px] px-2 py-0.5 rounded bg-[#10B981]/10 border border-[#10B981]/30 text-[#ffe3e5] flex items-center gap-1.5"
            >
              <span className={matchedPosition.side === 'LONG' ? 'text-[#10B981] font-semibold' : 'text-[#EF4444] font-semibold'}>
                {matchedPosition.side} {matchedPosition.leverage}x
              </span>
              <span className="text-[#c28e95]">@ {formatPrice(matchedPosition.entry_price)}</span>
              <span
                className={
                  (matchedPosition.status === 'open'
                    ? matchedPosition.unrealized_pnl_usd
                    : matchedPosition.realized_pnl_usd) >= 0
                    ? 'text-[#10B981] font-semibold'
                    : 'text-[#EF4444] font-semibold'
                }
              >
                {formatSignedUsd(
                  matchedPosition.status === 'open'
                    ? matchedPosition.unrealized_pnl_usd
                    : matchedPosition.realized_pnl_usd
                )}
              </span>
            </span>
          )}
        </div>
        {onClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label={`Close embedded chart for ${symbol}`}
            className="p-1 rounded-md text-[#d4a5ac] hover:text-[#ffe3e5] hover:bg-[#ff3555]/15 transition-colors"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
      <Suspense
        fallback={
          <div className="h-[290px] flex items-center justify-center text-xs font-mono text-[#c28e95]">
            Loading {symbol} chart...
          </div>
        }
      >
        <ProMarketChart
          exchange={exchange}
          marketType={marketType}
          symbol={symbol}
          timeframe={timeframe}
          onTimeframeChange={setTimeframe}
          activePosition={activePosition}
          initData={initData}
          compact
        />
      </Suspense>
    </div>
  );
};

interface SymbolChartToggleButtonProps {
  symbol: string;
  expanded: boolean;
  onToggle: () => void;
}

export const SymbolChartToggleButton: React.FC<SymbolChartToggleButtonProps> = ({
  symbol,
  expanded,
  onToggle
}) => (
  <button
    type="button"
    onClick={(e) => {
      e.stopPropagation();
      onToggle();
    }}
    aria-expanded={expanded}
    aria-label={`${expanded ? 'Hide' : 'Show'} embedded chart for ${symbol}`}
    title={`${expanded ? 'Hide' : 'Show'} embedded chart for ${symbol}`}
    className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded border text-[10px] font-mono transition-colors ${
      expanded
        ? 'bg-[#ff3555]/25 border-[#ff3555]/60 text-[#ffe3e5]'
        : 'bg-[#080305] border-[#ff3555]/25 text-[#ff6375] hover:bg-[#ff3555]/15 hover:border-[#ff3555]/45'
    }`}
  >
    <LineChart className="w-3 h-3" />
    <span>Chart</span>
    {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
  </button>
);
