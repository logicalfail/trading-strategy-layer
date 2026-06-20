"""Technical indicator library.

All indicators are pure functions that operate on BarData lists.
Each returns a pandas Series aligned with the input.

Indicators:
  - SMA / EMA: simple / exponential moving average
  - MACD: moving average convergence divergence
  - RSI: relative strength index
  - Bollinger Bands
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from .models import BarData


def _close_series(bars: List[BarData]) -> pd.Series:
    """Extract close prices as a pandas Series."""
    return pd.Series([b.close for b in bars], dtype=np.float64)


def _high_series(bars: List[BarData]) -> pd.Series:
    return pd.Series([b.high for b in bars], dtype=np.float64)


def _low_series(bars: List[BarData]) -> pd.Series:
    return pd.Series([b.low for b in bars], dtype=np.float64)


def _volume_series(bars: List[BarData]) -> pd.Series:
    return pd.Series([b.volume for b in bars], dtype=np.float64)


# ── Moving Averages ────────────────────────────────────────────────────

def SMA(bars: List[BarData], period: int = 20) -> pd.Series:
    """Simple Moving Average over close prices."""
    close = _close_series(bars)
    return close.rolling(window=period, min_periods=period).mean()


def EMA(bars: List[BarData], period: int = 20) -> pd.Series:
    """Exponential Moving Average over close prices."""
    close = _close_series(bars)
    return close.ewm(span=period, adjust=False, min_periods=period).mean()


# ── MACD ───────────────────────────────────────────────────────────────

def MACD(
    bars: List[BarData],
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """MACD indicator.

    Returns (MACD_line, signal_line, histogram).
    """
    close = _close_series(bars)
    ema_fast = close.ewm(span=fast_period, adjust=False, min_periods=fast_period).mean()
    ema_slow = close.ewm(span=slow_period, adjust=False, min_periods=slow_period).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal_period, adjust=False, min_periods=signal_period).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


# ── RSI ────────────────────────────────────────────────────────────────

def RSI(bars: List[BarData], period: int = 14) -> pd.Series:
    """Relative Strength Index."""
    close = _close_series(bars)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()

    # Use Wilder's smoothing method after initial SMA
    for i in range(period, len(avg_gain)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


# ── Bollinger Bands ────────────────────────────────────────────────────

def BollingerBands(
    bars: List[BarData],
    period: int = 20,
    std_dev: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands.

    Returns (upper_band, middle_band, lower_band).
    """
    close = _close_series(bars)
    middle = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)
    upper = middle + std_dev * std
    lower = middle - std_dev * std
    return upper, middle, lower


# ── KDJ (Stochastic) ──────────────────────────────────────────────────

def KDJ(
    bars: List[BarData],
    k_period: int = 9,
    d_period: int = 3,
    j_period: int = 3,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """KDJ Stochastic Oscillator.

    Returns (K, D, J).
    """
    high = _high_series(bars)
    low = _low_series(bars)
    close = _close_series(bars)

    hh = high.rolling(window=k_period, min_periods=k_period).max()
    ll = low.rolling(window=k_period, min_periods=k_period).min()

    rsv = (close - ll) / (hh - ll).replace(0, np.nan) * 100

    k = rsv.ewm(alpha=1.0 / d_period, adjust=False, min_periods=d_period).mean()
    d = k.ewm(alpha=1.0 / j_period, adjust=False, min_periods=j_period).mean()
    j = 3 * k - 2 * d
    return k, d, j


# ── ATR (Average True Range) ──────────────────────────────────────────

def ATR(bars: List[BarData], period: int = 14) -> pd.Series:
    """Average True Range (volatility indicator)."""
    high = _high_series(bars)
    low = _low_series(bars)
    close = _close_series(bars)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.rolling(window=period, min_periods=period).mean()
    return atr
