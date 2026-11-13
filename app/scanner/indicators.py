"""Technical indicators and market regime classification without look-ahead bias."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from app.core.models import Candle, MarketRegime


def candles_to_df(candles: list[Candle]) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    return pd.DataFrame([c.model_dump() for c in candles])


def compute_rsi(closes: pd.Series, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    delta = closes.diff()
    gain = delta.clip(lower=0.0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0.0)).ewm(alpha=1 / period, adjust=False).mean()
    last_gain = float(gain.iloc[-1])
    last_loss = float(loss.iloc[-1])
    if last_loss == 0.0:
        return 100.0 if last_gain > 0.0 else 50.0
    rs = last_gain / last_loss
    val = 100.0 - (100.0 / (1.0 + rs))
    return 50.0 if np.isnan(val) else round(val, 2)


def compute_macd(
    closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[float, float, float]:
    if len(closes) < slow + signal:
        return 0.0, 0.0, 0.0
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return (
        round(float(macd_line.iloc[-1]), 6),
        round(float(signal_line.iloc[-1]), 6),
        round(float(hist.iloc[-1]), 6),
    )


def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return 0.0
    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    atr_series = tr.ewm(alpha=1 / period, adjust=False).mean()
    val = float(atr_series.iloc[-1])
    return 0.0 if np.isnan(val) else round(val, 6)


def compute_trend_strength(df: pd.DataFrame, period: int = 14) -> float:
    """Directional movement trend strength (ADX proxy 0-100)."""
    if len(df) < period * 2:
        return 15.0
    up_move = df["high"].diff()
    down_move = -df["low"].diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean().replace(0.0, np.nan)
    plus_di = 100.0 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    minus_di = 100.0 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / period, adjust=False).mean() / atr
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0.0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    val = float(adx.iloc[-1])
    return 15.0 if np.isnan(val) else round(min(max(val, 0.0), 100.0), 2)


def classify_regime(
    ema_alignment: str,
    trend_strength: float,
    volatility: float,
    liquidity_score: float,
) -> MarketRegime:
    if liquidity_score < 0.25:
        return MarketRegime.LOW_LIQUIDITY
    if volatility >= 0.045:
        return MarketRegime.HIGH_VOLATILITY
    if trend_strength >= 25.0:
        if ema_alignment == "bullish":
            return MarketRegime.TRENDING_BULLISH
        if ema_alignment == "bearish":
            return MarketRegime.TRENDING_BEARISH
    if trend_strength < 20.0:
        return MarketRegime.RANGING
    return MarketRegime.UNCERTAIN


def compute_indicators(
    candles: list[Candle],
    spread_bps: float = 5.0,
    order_book_depth_usd: float = 100000.0,
) -> dict[str, Any]:
    """Compute full indicator bundle from historical candles (no future leakage)."""
    df = candles_to_df(candles)
    if len(df) < 10:
        last_close = float(df["close"].iloc[-1]) if len(df) else 0.0
        return {
            "rsi": 50.0,
            "macd": 0.0,
            "macd_signal": 0.0,
            "macd_hist": 0.0,
            "atr": 0.0,
            "ema_fast": last_close,
            "ema_slow": last_close,
            "ema_trend": last_close,
            "ema_alignment": "neutral",
            "support": last_close * 0.98,
            "resistance": last_close * 1.02,
            "relative_volume": 1.0,
            "volatility": 0.01,
            "trend_strength": 15.0,
            "liquidity_score": 0.5,
            "regime": MarketRegime.UNCERTAIN,
        }

    closes = df["close"]
    ema_fast = float(closes.ewm(span=9, adjust=False).mean().iloc[-1])
    ema_slow = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])
    ema_trend = float(closes.ewm(span=50, adjust=False).mean().iloc[-1])
    last_close = float(closes.iloc[-1])

    if last_close > ema_fast > ema_slow > ema_trend:
        ema_align = "bullish"
    elif last_close < ema_fast < ema_slow < ema_trend:
        ema_align = "bearish"
    else:
        ema_align = "neutral"

    rsi_val = compute_rsi(closes)
    macd_val, macd_sig, macd_hist = compute_macd(closes)
    atr_val = compute_atr(df)
    trend_strength = compute_trend_strength(df)

    returns = closes.pct_change().dropna()
    volatility = float(returns.tail(20).std()) if len(returns) >= 5 else 0.01
    if np.isnan(volatility):
        volatility = 0.01

    avg_vol = float(df["volume"].tail(20).mean()) if len(df) >= 5 else 1.0
    rel_vol = float(df["volume"].iloc[-1]) / avg_vol if avg_vol > 0 else 1.0

    lookback = df.tail(30)
    support = float(lookback["low"].min())
    resistance = float(lookback["high"].max())

    spread_factor = max(0.0, 1.0 - (spread_bps / 50.0))
    depth_factor = min(1.0, order_book_depth_usd / 50000.0)
    liquidity_score = round(0.5 * spread_factor + 0.5 * depth_factor, 3)

    regime = classify_regime(ema_align, trend_strength, volatility, liquidity_score)

    return {
        "rsi": rsi_val,
        "macd": macd_val,
        "macd_signal": macd_sig,
        "macd_hist": macd_hist,
        "atr": atr_val,
        "ema_fast": round(ema_fast, 6),
        "ema_slow": round(ema_slow, 6),
        "ema_trend": round(ema_trend, 6),
        "ema_alignment": ema_align,
        "support": round(support, 6),
        "resistance": round(resistance, 6),
        "relative_volume": round(rel_vol, 3),
        "volatility": round(volatility, 5),
        "trend_strength": trend_strength,
        "liquidity_score": liquidity_score,
        "regime": regime,
    }
