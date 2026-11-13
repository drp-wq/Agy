import React, { useEffect, useRef, useState, useMemo, useCallback } from 'react';
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  ColorType,
  IChartApi,
  ISeriesApi,
  Time
} from 'lightweight-charts';
import { Maximize2, Minimize2, RotateCcw, RefreshCw } from 'lucide-react';
import {
  CandleBar,
  ExchangeId,
  FuturesPosition,
  MarketType,
  Timeframe
} from '../../types/quant';
import { quantApi } from '../../services/quantApi';
import {
  calculateATR,
  calculateBollingerBands,
  calculateEMA,
  calculateMACD,
  calculateRSI,
  calculateVWAP,
  detectSupportResistance
} from '../../utils/indicators';
import { formatPrice } from '../../utils/formatters';

interface ProMarketChartProps {
  exchange: ExchangeId;
  marketType: MarketType;
  symbol: string;
  timeframe: Timeframe;
  onExchangeChange?: (ex: ExchangeId) => void;
  onMarketTypeChange?: (mt: MarketType) => void;
  onSymbolChange?: (sym: string) => void;
  onTimeframeChange?: (tf: Timeframe) => void;
  activePosition?: FuturesPosition | null;
  initData?: string;
  compact?: boolean;
}

type OscillatorMode = 'none' | 'rsi' | 'macd' | 'atr';

const AVAILABLE_SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'AVAX/USDT', 'ARB/USDT'];
const AVAILABLE_TIMEFRAMES: Timeframe[] = ['1m', '5m', '15m', '1h', '4h', '1d'];

export const ProMarketChart: React.FC<ProMarketChartProps> = ({
  exchange,
  marketType,
  symbol,
  timeframe,
  onExchangeChange,
  onMarketTypeChange,
  onSymbolChange,
  onTimeframeChange,
  activePosition,
  initData,
  compact = false
}) => {
  const mainContainerRef = useRef<HTMLDivElement>(null);
  const oscContainerRef = useRef<HTMLDivElement>(null);
  const mainChartRef = useRef<IChartApi | null>(null);
  const oscChartRef = useRef<IChartApi | null>(null);

  const [candles, setCandles] = useState<CandleBar[]>([]);
  const [dataSource, setDataSource] = useState<'fast_api' | 'demo_deterministic'>('demo_deterministic');
  const [loading, setLoading] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Overlays & Oscillators
  const [showEma, setShowEma] = useState(true);
  const [showVwap, setShowVwap] = useState(true);
  const [showBollinger, setShowBollinger] = useState(false);
  const [showSR, setShowSR] = useState(true);
  const [oscillator, setOscillator] = useState<OscillatorMode>('rsi');

  // Crosshair OHLC readout
  const [hoveredBar, setHoveredBar] = useState<CandleBar | null>(null);

  const candleAbortRef = useRef<AbortController | null>(null);

  const loadCandles = useCallback(async () => {
    candleAbortRef.current?.abort();
    const controller = new AbortController();
    candleAbortRef.current = controller;

    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await quantApi.getCandles(exchange, marketType, symbol, timeframe, initData, controller.signal);
      if (!controller.signal.aborted) {
        setCandles(res.candles);
        setDataSource(res.source);
      }
    } catch (err) {
      if (err instanceof DOMException && err.name === 'AbortError') return;
      if (!controller.signal.aborted) {
        setErrorMsg(err instanceof Error ? err.message : 'Failed to fetch market candles');
      }
    } finally {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    }
  }, [exchange, marketType, symbol, timeframe, initData]);

  useEffect(() => {
    loadCandles();
    return () => {
      candleAbortRef.current?.abort();
    };
  }, [loadCandles]);

  // Compute real technical indicators strictly from timestamped candles
  const indicators = useMemo(() => {
    return {
      ema9: calculateEMA(candles, 9),
      ema21: calculateEMA(candles, 21),
      vwap: calculateVWAP(candles),
      bb: calculateBollingerBands(candles, 20, 2),
      rsi: calculateRSI(candles, 14),
      macd: calculateMACD(candles, 12, 26, 9),
      atr: calculateATR(candles, 14),
      srLevels: detectSupportResistance(candles, 5)
    };
  }, [candles]);

  const latestBar = hoveredBar || (candles.length > 0 ? candles[candles.length - 1] : null);
  const latestRsi = indicators.rsi.length > 0 ? indicators.rsi[indicators.rsi.length - 1].value : null;

  // Main Candlestick + Volume + Overlays Chart
  useEffect(() => {
    const container = mainContainerRef.current;
    if (!container || candles.length === 0) return;

    const chart = createChart(container, {
      width: container.clientWidth,
      height: isFullscreen ? Math.max(420, window.innerHeight - 280) : compact ? 290 : 380,
      layout: {
        background: { type: ColorType.Solid, color: '#05080F' },
        textColor: '#94A3B8',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.06)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.06)' }
      },
      crosshair: {
        vertLine: { color: 'rgba(0, 229, 255, 0.45)', width: 1, style: 2 },
        horzLine: { color: 'rgba(0, 229, 255, 0.45)', width: 1, style: 2 }
      },
      rightPriceScale: {
        borderColor: 'rgba(148, 163, 184, 0.14)'
      },
      timeScale: {
        borderColor: 'rgba(148, 163, 184, 0.14)',
        timeVisible: true,
        secondsVisible: false
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false },
      handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true }
    });

    mainChartRef.current = chart;

    // Candlestick Series
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#10B981',
      downColor: '#EF4444',
      borderUpColor: '#10B981',
      borderDownColor: '#EF4444',
      wickUpColor: '#10B981',
      wickDownColor: '#EF4444'
    });

    candleSeries.setData(
      candles.map((c) => ({
        time: c.time as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close
      }))
    );

    // Volume Histogram Overlay
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume'
    });
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.82, bottom: 0 }
    });
    volumeSeries.setData(
      candles.map((c) => ({
        time: c.time as Time,
        value: c.volume,
        color: c.close >= c.open ? 'rgba(16, 185, 129, 0.28)' : 'rgba(239, 68, 68, 0.28)'
      }))
    );

    // EMA 9 & 21 Overlays
    if (showEma && indicators.ema9.length > 0) {
      const ema9Series = chart.addSeries(LineSeries, {
        color: '#00E5FF',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false
      });
      ema9Series.setData(indicators.ema9.map((p) => ({ time: p.time as Time, value: p.value })));

      const ema21Series = chart.addSeries(LineSeries, {
        color: '#8B5CF6',
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false
      });
      ema21Series.setData(indicators.ema21.map((p) => ({ time: p.time as Time, value: p.value })));
    }

    // VWAP Overlay
    if (showVwap && indicators.vwap.length > 0) {
      const vwapSeries = chart.addSeries(LineSeries, {
        color: '#F59E0B',
        lineWidth: 1,
        lineStyle: 2,
        priceLineVisible: false,
        lastValueVisible: false
      });
      vwapSeries.setData(indicators.vwap.map((p) => ({ time: p.time as Time, value: p.value })));
    }

    // Bollinger Bands Overlay
    if (showBollinger && indicators.bb.length > 0) {
      const bbUpper = chart.addSeries(LineSeries, {
        color: 'rgba(148, 163, 184, 0.5)',
        lineWidth: 1,
        lineStyle: 1,
        priceLineVisible: false,
        lastValueVisible: false
      });
      const bbLower = chart.addSeries(LineSeries, {
        color: 'rgba(148, 163, 184, 0.5)',
        lineWidth: 1,
        lineStyle: 1,
        priceLineVisible: false,
        lastValueVisible: false
      });
      bbUpper.setData(indicators.bb.map((p) => ({ time: p.time as Time, value: p.upper })));
      bbLower.setData(indicators.bb.map((p) => ({ time: p.time as Time, value: p.lower })));
    }

    // Support & Resistance Lines
    if (showSR) {
      indicators.srLevels.forEach((lvl) => {
        candleSeries.createPriceLine({
          price: lvl.price,
          color: lvl.type === 'RESISTANCE' ? 'rgba(239, 68, 68, 0.45)' : 'rgba(16, 185, 129, 0.45)',
          lineWidth: 1,
          lineStyle: 3,
          axisLabelVisible: true,
          title: lvl.type === 'RESISTANCE' ? 'RES' : 'SUP'
        });
      });
    }

    // Position Entry / SL / TP Price Lines
    if (activePosition && activePosition.symbol === symbol) {
      candleSeries.createPriceLine({
        price: activePosition.entry_price,
        color: '#00E5FF',
        lineWidth: 2,
        lineStyle: 0,
        axisLabelVisible: true,
        title: `ENTRY (${activePosition.side})`
      });
      if (activePosition.stop_loss) {
        candleSeries.createPriceLine({
          price: activePosition.stop_loss,
          color: '#EF4444',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: 'SL'
        });
      }
      if (activePosition.take_profit) {
        candleSeries.createPriceLine({
          price: activePosition.take_profit,
          color: '#10B981',
          lineWidth: 1,
          lineStyle: 2,
          axisLabelVisible: true,
          title: 'TP'
        });
      }
    }

    chart.timeScale().fitContent();

    // Crosshair hover sync
    const candleMap = new Map<number, CandleBar>();
    candles.forEach((c) => candleMap.set(c.time, c));

    chart.subscribeCrosshairMove((param) => {
      if (!param.time) {
        setHoveredBar(null);
        return;
      }
      const bar = candleMap.get(Number(param.time));
      if (bar) setHoveredBar(bar);
    });

    const resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width && width > 0) {
        chart.applyOptions({ width: Math.floor(width) });
      }
    });
    resizeObserver.observe(container);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      mainChartRef.current = null;
    };
  }, [candles, indicators, showEma, showVwap, showBollinger, showSR, activePosition, symbol, isFullscreen, compact]);

  // Oscillator Sub-Chart (RSI / MACD / ATR)
  useEffect(() => {
    const container = oscContainerRef.current;
    if (!container || oscillator === 'none' || candles.length === 0) return;

    const oscChart = createChart(container, {
      width: container.clientWidth,
      height: 120,
      layout: {
        background: { type: ColorType.Solid, color: '#05080F' },
        textColor: '#94A3B8',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 10
      },
      grid: {
        vertLines: { color: 'rgba(148, 163, 184, 0.05)' },
        horzLines: { color: 'rgba(148, 163, 184, 0.05)' }
      },
      rightPriceScale: {
        borderColor: 'rgba(148, 163, 184, 0.14)'
      },
      timeScale: {
        borderColor: 'rgba(148, 163, 184, 0.14)',
        timeVisible: true
      }
    });

    oscChartRef.current = oscChart;

    if (oscillator === 'rsi' && indicators.rsi.length > 0) {
      const rsiSeries = oscChart.addSeries(LineSeries, {
        color: '#8B5CF6',
        lineWidth: 2
      });
      rsiSeries.setData(indicators.rsi.map((p) => ({ time: p.time as Time, value: p.value })));
      rsiSeries.createPriceLine({ price: 70, color: 'rgba(239, 68, 68, 0.4)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '70' });
      rsiSeries.createPriceLine({ price: 30, color: 'rgba(16, 185, 129, 0.4)', lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: '30' });
    } else if (oscillator === 'macd' && indicators.macd.length > 0) {
      const histSeries = oscChart.addSeries(HistogramSeries, {});
      histSeries.setData(
        indicators.macd.map((p) => ({
          time: p.time as Time,
          value: p.histogram,
          color: p.histogram >= 0 ? 'rgba(16, 185, 129, 0.55)' : 'rgba(239, 68, 68, 0.55)'
        }))
      );
      const macdLine = oscChart.addSeries(LineSeries, { color: '#00E5FF', lineWidth: 1, priceLineVisible: false });
      const sigLine = oscChart.addSeries(LineSeries, { color: '#F59E0B', lineWidth: 1, priceLineVisible: false });
      macdLine.setData(indicators.macd.map((p) => ({ time: p.time as Time, value: p.macd })));
      sigLine.setData(indicators.macd.map((p) => ({ time: p.time as Time, value: p.signal })));
    } else if (oscillator === 'atr' && indicators.atr.length > 0) {
      const atrSeries = oscChart.addSeries(LineSeries, {
        color: '#00E5FF',
        lineWidth: 2
      });
      atrSeries.setData(indicators.atr.map((p) => ({ time: p.time as Time, value: p.value })));
    }

    oscChart.timeScale().fitContent();

    // Sync time scale movement from main chart
    const main = mainChartRef.current;
    let unsub: (() => void) | null = null;
    if (main) {
      const handler = () => {
        const range = main.timeScale().getVisibleLogicalRange();
        if (range) {
          oscChart.timeScale().setVisibleLogicalRange(range);
        }
      };
      main.timeScale().subscribeVisibleLogicalRangeChange(handler);
      unsub = () => main.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    }

    const resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width && width > 0) {
        oscChart.applyOptions({ width: Math.floor(width) });
      }
    });
    resizeObserver.observe(container);

    return () => {
      if (unsub) unsub();
      resizeObserver.disconnect();
      oscChart.remove();
      oscChartRef.current = null;
    };
  }, [oscillator, indicators, candles]);

  const handleResetZoom = () => {
    mainChartRef.current?.timeScale().fitContent();
    oscChartRef.current?.timeScale().fitContent();
  };

  return (
    <div
      className={`nq-panel rounded-xl border border-white/[0.1] overflow-hidden flex flex-col ${
        isFullscreen ? 'fixed inset-3 z-50 bg-[#05080F] shadow-2xl' : ''
      }`}
    >
      {/* Top Toolbar: Exchange, Market, Symbol, Timeframe, Fullscreen */}
      <div className="px-4 py-3 border-b border-white/[0.08] flex flex-wrap items-center justify-between gap-2 bg-[#0B1120]/90">
        <div className="flex flex-wrap items-center gap-2">
          {/* Exchange Selector */}
          {onExchangeChange && (
            <div className="flex items-center p-0.5 rounded-lg bg-[#05080F] border border-white/[0.08]">
              {(['binance', 'bybit', 'okx'] as ExchangeId[]).map((ex) => (
                <button
                  key={ex}
                  onClick={() => onExchangeChange(ex)}
                  className={`px-2.5 py-1 min-h-[32px] rounded-md text-xs font-medium uppercase whitespace-nowrap shrink-0 transition-colors ${
                    exchange === ex
                      ? 'bg-[#00E5FF]/20 text-[#00E5FF]'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {ex}
                </button>
              ))}
            </div>
          )}

          {/* Market Type Selector */}
          {onMarketTypeChange && (
            <div className="flex items-center p-0.5 rounded-lg bg-[#05080F] border border-white/[0.08]">
              {(['spot', 'futures'] as MarketType[]).map((mt) => (
                <button
                  key={mt}
                  onClick={() => onMarketTypeChange(mt)}
                  className={`px-2.5 py-1 min-h-[32px] rounded-md text-xs font-medium capitalize whitespace-nowrap shrink-0 transition-colors ${
                    marketType === mt
                      ? 'bg-[#8B5CF6]/25 text-white'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {mt}
                </button>
              ))}
            </div>
          )}

          {/* Symbol Dropdown */}
          {onSymbolChange ? (
            <select
              value={symbol}
              onChange={(e) => onSymbolChange(e.target.value)}
              aria-label="Trading pair"
              className="bg-[#05080F] border border-white/[0.12] rounded-lg px-2.5 py-1.5 min-h-[34px] text-xs font-mono font-semibold text-white focus:outline-none focus:border-[#00E5FF]"
            >
              {AVAILABLE_SYMBOLS.map((sym) => (
                <option key={sym} value={sym}>
                  {sym}
                </option>
              ))}
            </select>
          ) : (
            <span className="text-xs font-mono font-semibold text-white px-2">{symbol}</span>
          )}

          {/* Timeframe Selector */}
          {onTimeframeChange && (
            <div className="flex items-center p-0.5 rounded-lg bg-[#05080F] border border-white/[0.08]">
              {AVAILABLE_TIMEFRAMES.map((tf) => (
                <button
                  key={tf}
                  onClick={() => onTimeframeChange(tf)}
                  className={`px-2 py-1 min-h-[32px] rounded text-[11px] font-mono whitespace-nowrap shrink-0 transition-colors ${
                    timeframe === tf
                      ? 'bg-white/15 text-[#00E5FF] font-semibold'
                      : 'text-slate-400 hover:text-white'
                  }`}
                >
                  {tf}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Chart Actions */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={loadCandles}
            className="p-2 min-h-[34px] min-w-[34px] flex items-center justify-center rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
            title="Refresh candles"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin text-[#00E5FF]' : ''}`} />
          </button>
          <button
            onClick={handleResetZoom}
            className="p-2 min-h-[34px] min-w-[34px] flex items-center justify-center rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
            title="Reset chart zoom"
          >
            <RotateCcw className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setIsFullscreen(!isFullscreen)}
            className="p-2 min-h-[34px] min-w-[34px] flex items-center justify-center rounded-lg text-slate-400 hover:text-white hover:bg-white/5 transition-colors"
            title={isFullscreen ? 'Exit fullscreen' : 'Fullscreen chart'}
          >
            {isFullscreen ? <Minimize2 className="w-3.5 h-3.5" /> : <Maximize2 className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Indicator Controls & Crosshair OHLCV Strip */}
      <div className="px-4 py-2 border-b border-white/[0.06] bg-[#05080F]/90 flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono">
        {/* Live OHLCV Crosshair Readout */}
        <div className="flex flex-wrap items-center gap-3 text-slate-400">
          {latestBar ? (
            <>
              <span className="text-slate-300 font-semibold">
                {exchange.toUpperCase()} {symbol} · {timeframe}
              </span>
              <span>O <strong className="text-white">{formatPrice(latestBar.open)}</strong></span>
              <span>H <strong className="text-[#10B981]">{formatPrice(latestBar.high)}</strong></span>
              <span>L <strong className="text-[#EF4444]">{formatPrice(latestBar.low)}</strong></span>
              <span>C <strong className="text-white">{formatPrice(latestBar.close)}</strong></span>
              {latestRsi !== null && (
                <span>RSI(14) <strong className="text-[#8B5CF6]">{latestRsi.toFixed(1)}</strong></span>
              )}
              <span className="text-slate-500">
                {dataSource === 'fast_api' ? 'Live Candles' : 'DEMO Candles'}
              </span>
            </>
          ) : (
            <span>Loading OHLCV series...</span>
          )}
        </div>

        {/* Overlay & Oscillator Toggles */}
        <div className="flex flex-wrap items-center gap-1.5">
          <button
            onClick={() => setShowEma(!showEma)}
            className={`px-2 py-0.5 rounded border text-[10px] transition-colors ${
              showEma ? 'border-[#00E5FF]/50 text-[#00E5FF] bg-[#00E5FF]/10' : 'border-white/10 text-slate-500'
            }`}
          >
            EMA 9/21
          </button>
          <button
            onClick={() => setShowVwap(!showVwap)}
            className={`px-2 py-0.5 rounded border text-[10px] transition-colors ${
              showVwap ? 'border-[#F59E0B]/50 text-[#F59E0B] bg-[#F59E0B]/10' : 'border-white/10 text-slate-500'
            }`}
          >
            VWAP
          </button>
          <button
            onClick={() => setShowBollinger(!showBollinger)}
            className={`px-2 py-0.5 rounded border text-[10px] transition-colors ${
              showBollinger ? 'border-slate-300/50 text-slate-200 bg-white/10' : 'border-white/10 text-slate-500'
            }`}
          >
            BB(20)
          </button>
          <button
            onClick={() => setShowSR(!showSR)}
            className={`px-2 py-0.5 rounded border text-[10px] transition-colors ${
              showSR ? 'border-[#10B981]/50 text-[#10B981] bg-[#10B981]/10' : 'border-white/10 text-slate-500'
            }`}
          >
            S/R
          </button>

          <span className="text-slate-600 mx-0.5">|</span>

          {(['none', 'rsi', 'macd', 'atr'] as OscillatorMode[]).map((mode) => (
            <button
              key={mode}
              onClick={() => setOscillator(mode)}
              className={`px-2 py-0.5 rounded text-[10px] uppercase transition-colors ${
                oscillator === mode
                  ? 'bg-[#8B5CF6]/25 text-white border border-[#8B5CF6]/50'
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {mode === 'none' ? 'No Osc' : mode}
            </button>
          ))}
        </div>
      </div>

      {/* Main Chart Viewport */}
      {errorMsg ? (
        <div className="p-10 text-center text-xs text-[#EF4444]">
          <p>{errorMsg}</p>
          <button
            onClick={loadCandles}
            className="mt-3 px-3 py-1.5 rounded bg-[#EF4444]/15 border border-[#EF4444]/40 text-white"
          >
            Retry Candles
          </button>
        </div>
      ) : (
        <div className="bg-[#05080F] relative">
          <div ref={mainContainerRef} className="w-full" />
          {oscillator !== 'none' && (
            <div className="border-t border-white/[0.08]">
              <div className="px-4 py-1 text-[10px] font-mono text-slate-400 uppercase bg-[#080D1A]">
                {oscillator === 'rsi' && 'RSI (14) · Overbought 70 / Oversold 30'}
                {oscillator === 'macd' && 'MACD (12, 26, 9) · Signal & Histogram'}
                {oscillator === 'atr' && 'ATR (14) · Volatility Range'}
              </div>
              <div ref={oscContainerRef} className="w-full" />
            </div>
          )}
        </div>
      )}
    </div>
  );
};
