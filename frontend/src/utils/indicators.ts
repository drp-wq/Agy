import { CandleBar } from '../types/quant';

export interface IndicatorPoint {
  time: number;
  value: number;
}

export interface BollingerPoint {
  time: number;
  upper: number;
  middle: number;
  lower: number;
}

export interface MACDPoint {
  time: number;
  macd: number;
  signal: number;
  histogram: number;
}

export interface SupportResistanceLevel {
  price: number;
  type: 'SUPPORT' | 'RESISTANCE';
  touches: number;
}

/**
 * Exponential Moving Average computed strictly from real candle closes
 */
export function calculateEMA(candles: CandleBar[], period: number): IndicatorPoint[] {
  if (candles.length < period) return [];
  const k = 2 / (period + 1);
  const result: IndicatorPoint[] = [];

  let sum = 0;
  for (let i = 0; i < period; i++) {
    sum += candles[i].close;
  }
  let ema = sum / period;
  result.push({ time: candles[period - 1].time, value: ema });

  for (let i = period; i < candles.length; i++) {
    ema = candles[i].close * k + ema * (1 - k);
    result.push({ time: candles[i].time, value: ema });
  }
  return result;
}

/**
 * Session/Window Volume-Weighted Average Price (VWAP)
 */
export function calculateVWAP(candles: CandleBar[]): IndicatorPoint[] {
  if (candles.length === 0) return [];
  const result: IndicatorPoint[] = [];
  let cumulativeTPV = 0;
  let cumulativeVol = 0;

  for (const bar of candles) {
    const typicalPrice = (bar.high + bar.low + bar.close) / 3;
    const vol = Math.max(bar.volume, 1e-8);
    cumulativeTPV += typicalPrice * vol;
    cumulativeVol += vol;
    result.push({
      time: bar.time,
      value: cumulativeTPV / cumulativeVol
    });
  }
  return result;
}

/**
 * Bollinger Bands (SMA +/- stdDev * k)
 */
export function calculateBollingerBands(
  candles: CandleBar[],
  period = 20,
  multiplier = 2
): BollingerPoint[] {
  if (candles.length < period) return [];
  const result: BollingerPoint[] = [];

  for (let i = period - 1; i < candles.length; i++) {
    const slice = candles.slice(i - period + 1, i + 1);
    const mean = slice.reduce((acc, b) => acc + b.close, 0) / period;
    const variance =
      slice.reduce((acc, b) => acc + Math.pow(b.close - mean, 2), 0) / period;
    const stdDev = Math.sqrt(variance);

    result.push({
      time: candles[i].time,
      upper: mean + multiplier * stdDev,
      middle: mean,
      lower: mean - multiplier * stdDev
    });
  }
  return result;
}

/**
 * Wilder's Relative Strength Index (RSI)
 */
export function calculateRSI(candles: CandleBar[], period = 14): IndicatorPoint[] {
  if (candles.length <= period) return [];
  const result: IndicatorPoint[] = [];

  let avgGain = 0;
  let avgLoss = 0;

  for (let i = 1; i <= period; i++) {
    const change = candles[i].close - candles[i - 1].close;
    if (change > 0) avgGain += change;
    else avgLoss += Math.abs(change);
  }
  avgGain /= period;
  avgLoss /= period;

  const firstRS = avgLoss === 0 ? 100 : avgGain / avgLoss;
  result.push({
    time: candles[period].time,
    value: avgLoss === 0 ? 100 : 100 - 100 / (1 + firstRS)
  });

  for (let i = period + 1; i < candles.length; i++) {
    const change = candles[i].close - candles[i - 1].close;
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? Math.abs(change) : 0;

    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;

    const rs = avgLoss === 0 ? 100 : avgGain / avgLoss;
    result.push({
      time: candles[i].time,
      value: avgLoss === 0 ? 100 : 100 - 100 / (1 + rs)
    });
  }
  return result;
}

/**
 * MACD (12, 26, 9)
 */
export function calculateMACD(
  candles: CandleBar[],
  fastPeriod = 12,
  slowPeriod = 26,
  signalPeriod = 9
): MACDPoint[] {
  const fastEma = calculateEMA(candles, fastPeriod);
  const slowEma = calculateEMA(candles, slowPeriod);
  if (slowEma.length === 0) return [];

  const fastByTime = new Map<number, number>();
  for (const pt of fastEma) fastByTime.set(pt.time, pt.value);

  const macdLine: IndicatorPoint[] = [];
  for (const slowPt of slowEma) {
    const fastVal = fastByTime.get(slowPt.time);
    if (fastVal !== undefined) {
      macdLine.push({ time: slowPt.time, value: fastVal - slowPt.value });
    }
  }

  if (macdLine.length < signalPeriod) return [];

  const k = 2 / (signalPeriod + 1);
  let signal =
    macdLine.slice(0, signalPeriod).reduce((acc, p) => acc + p.value, 0) / signalPeriod;

  const result: MACDPoint[] = [
    {
      time: macdLine[signalPeriod - 1].time,
      macd: macdLine[signalPeriod - 1].value,
      signal,
      histogram: macdLine[signalPeriod - 1].value - signal
    }
  ];

  for (let i = signalPeriod; i < macdLine.length; i++) {
    signal = macdLine[i].value * k + signal * (1 - k);
    result.push({
      time: macdLine[i].time,
      macd: macdLine[i].value,
      signal,
      histogram: macdLine[i].value - signal
    });
  }

  return result;
}

/**
 * Average True Range (ATR)
 */
export function calculateATR(candles: CandleBar[], period = 14): IndicatorPoint[] {
  if (candles.length <= period) return [];
  const trueRanges: number[] = [];

  for (let i = 1; i < candles.length; i++) {
    const high = candles[i].high;
    const low = candles[i].low;
    const prevClose = candles[i - 1].close;
    const tr = Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose));
    trueRanges.push(tr);
  }

  let atr = trueRanges.slice(0, period).reduce((a, b) => a + b, 0) / period;
  const result: IndicatorPoint[] = [{ time: candles[period].time, value: atr }];

  for (let i = period; i < trueRanges.length; i++) {
    atr = (atr * (period - 1) + trueRanges[i]) / period;
    result.push({ time: candles[i + 1].time, value: atr });
  }
  return result;
}

/**
 * Pivot-based Support & Resistance detection from historical candle highs/lows
 */
export function detectSupportResistance(
  candles: CandleBar[],
  window = 5
): SupportResistanceLevel[] {
  if (candles.length < window * 2 + 1) return [];
  const lastClose = candles[candles.length - 1].close;
  const rawLevels: number[] = [];

  for (let i = window; i < candles.length - window; i++) {
    const slice = candles.slice(i - window, i + window + 1);
    const maxHigh = Math.max(...slice.map((c) => c.high));
    const minLow = Math.min(...slice.map((c) => c.low));

    if (candles[i].high === maxHigh) rawLevels.push(candles[i].high);
    if (candles[i].low === minLow) rawLevels.push(candles[i].low);
  }

  // Cluster levels within 0.35% of each other
  const clustered: Array<{ sum: number; count: number }> = [];
  for (const lvl of rawLevels) {
    const match = clustered.find((c) => Math.abs(c.sum / c.count - lvl) / lvl < 0.0035);
    if (match) {
      match.sum += lvl;
      match.count += 1;
    } else {
      clustered.push({ sum: lvl, count: 1 });
    }
  }

  return clustered
    .map((c) => {
      const price = c.sum / c.count;
      return {
        price,
        type: (price >= lastClose ? 'RESISTANCE' : 'SUPPORT') as 'SUPPORT' | 'RESISTANCE',
        touches: c.count
      };
    })
    .sort((a, b) => b.touches - a.touches)
    .slice(0, 4);
}
