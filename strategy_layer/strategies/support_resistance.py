"""Support / Resistance Bounce Strategy.

Combines four techniques:
  1. Horizontal support/resistance — swing high/low clustering
  2. Volume Profile — high-volume node detection
  3. RSI confirmation — bounce at oversold/overbought
  4. Trend filter — price vs long-term SMA

Signal logic:
  - BUY: price touches support + RSI oversold (+ trend uptrend if filter on)
  - SELL: price touches resistance + RSI overbought (+ trend downtrend if filter on)

Parameters:
  lookback: int — SR/volume回溯周期 (default: 100)
  swing_window: int — 局部极值探测半窗口 (default: 5)
  cluster_atr_mult: float — ATR倍数以内合并同一区域 (default: 0.5)
  atr_period: int — ATR周期 (default: 14)
  rsi_period: int — RSI周期 (default: 14)
  oversold: float — RSI超卖阈值 (default: 30)
  overbought: float — RSI超买阈值 (default: 70)
  bounce_atr: float — 触碰支撑阻力的ATR容差 (default: 0.3)
  trend_ma_period: int — 趋势均线周期 (default: 200)
  require_trend: bool — 是否启用趋势过滤 (default: True)
  vol_profile_bins: int — 成交量分布分区数 (default: 20)
  qty: int — 数量 (default: 1)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..models import BarData, Direction, Signal, SignalStrength

log = logging.getLogger(__name__)


# ── Helpers (pandas-ta wrappers) ───────────────────────────────────────

def _to_df(bars: List[BarData]) -> pd.DataFrame:
    return pd.DataFrame({
        "open":  [b.open for b in bars],
        "high":  [b.high for b in bars],
        "low":   [b.low for b in bars],
        "close": [b.close for b in bars],
        "volume":[b.volume for b in bars],
    })


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    import pandas_ta as ta
    return ta.atr(high, low, close, length=period)


def _rsi(close: pd.Series, period: int) -> pd.Series:
    import pandas_ta as ta
    return ta.rsi(close, length=period)


def _sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period, min_periods=period).mean()


# ── SR level detection ─────────────────────────────────────────────────

def _swing_highs(high: pd.Series, window: int = 5) -> List[Tuple[int, float]]:
    """Find swing high indices and prices."""
    result = []
    for i in range(window, len(high) - window):
        if high.iloc[i] == high.iloc[i - window : i + window + 1].max():
            result.append((i, high.iloc[i]))
    return result


def _swing_lows(low: pd.Series, window: int = 5) -> List[Tuple[int, float]]:
    """Find swing low indices and prices."""
    result = []
    for i in range(window, len(low) - window):
        if low.iloc[i] == low.iloc[i - window : i + window + 1].min():
            result.append((i, low.iloc[i]))
    return result


def _cluster_levels(
    prices: List[float],
    atr_value: float,
    cluster_mult: float = 0.5,
) -> List[Dict[str, Any]]:
    """Cluster nearby swing points into support/resistance zones.

    Returns sorted list of {price, strength} where strength = number of
    swings clustered together (more touches = stronger level).
    """
    if not prices:
        return []
    max_gap = atr_value * cluster_mult
    sorted_p = sorted(prices)
    clusters: List[Dict[str, Any]] = []
    current = {"price": sorted_p[0], "strength": 1, "sum": sorted_p[0]}
    for p in sorted_p[1:]:
        if p - current["price"] <= max_gap:
            current["strength"] += 1
            current["sum"] += p
            current["price"] = current["sum"] / current["strength"]
        else:
            clusters.append(current)
            current = {"price": p, "strength": 1, "sum": p}
    clusters.append(current)
    return clusters


def _detect_levels(
    high: pd.Series,
    low: pd.Series,
    atr_value: float,
    swing_window: int = 3,
    cluster_mult: float = 0.5,
    min_strength: int = 1,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Detect support and resistance levels.

    Returns (supports, resistances) where each is a list of
    {price, strength} sorted by strength descending.
    """
    sh = _swing_highs(high, swing_window)
    sl = _swing_lows(low, swing_window)

    resistances = _cluster_levels([p for _, p in sh], atr_value, cluster_mult)
    supports = _cluster_levels([p for _, p in sl], atr_value, cluster_mult)

    # Filter by minimum strength
    resistances = [r for r in resistances if r["strength"] >= min_strength]
    supports = [s for s in supports if s["strength"] >= min_strength]

    # Sort by strength descending (strongest first)
    resistances.sort(key=lambda x: -x["strength"])
    supports.sort(key=lambda x: -x["strength"])

    return supports, resistances


# ── Volume Profile ─────────────────────────────────────────────────────

def _volume_profile(
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    num_bins: int = 20,
) -> Dict[str, Any]:
    """Simple volume profile: divide price range into bins, sum volume.

    Returns:
        poc_price: Point of Control (highest volume price)
        high_volume_nodes: list of (low, high) price zones with above-avg volume
    """
    price_min = low.min()
    price_max = high.max()
    if price_max <= price_min:
        return {"poc": price_min, "hvn": []}

    bin_width = (price_max - price_min) / num_bins
    bins_vol = np.zeros(num_bins)

    for i in range(len(high)):
        mid = (high.iloc[i] + low.iloc[i]) / 2
        idx = int((mid - price_min) / bin_width)
        idx = min(idx, num_bins - 1)
        bins_vol[idx] += volume.iloc[i]

    avg_vol = bins_vol.mean()
    poc_idx = int(bins_vol.argmax())
    poc_price = price_min + (poc_idx + 0.5) * bin_width

    hvn = []
    for idx in range(num_bins):
        if bins_vol[idx] > avg_vol * 1.5:
            lo = price_min + idx * bin_width
            hi = lo + bin_width
            hvn.append((lo, hi))

    return {"poc": poc_price, "hvn": hvn}


# ── Main strategy entry point ─────────────────────────────────────────

def generate_signals(
    bars: List[BarData],
    params: Dict[str, Any],
    strategy_id: str,
    symbol: str,
) -> Optional[Signal]:
    """Generate bounce signals at support/resistance levels.

    Fires when price touches a significant SR level with RSI confirmation,
    optionally filtered by long-term trend.
    """
    if len(bars) < 100:
        return None

    lookback = int(params.get("lookback", 100))
    swing_window = int(params.get("swing_window", 3))
    cluster_atr_mult = float(params.get("cluster_atr_mult", 0.5))
    atr_period = int(params.get("atr_period", 14))
    rsi_period = int(params.get("rsi_period", 14))
    oversold = float(params.get("oversold", 30))
    overbought = float(params.get("overbought", 70))
    bounce_atr = float(params.get("bounce_atr", 0.8))
    trend_ma_period = int(params.get("trend_ma_period", 200))
    require_trend = bool(params.get("require_trend", True))
    vol_bins = int(params.get("vol_profile_bins", 20))
    qty = int(params.get("qty", 1))

    # Build DataFrames
    window_bars = bars[-lookback:]
    df = _to_df(window_bars)

    # Current/latest values
    latest = bars[-1]
    curr_close = latest.close
    curr_high = latest.high
    curr_low = latest.low

    # ATR for distance scaling
    full_df = _to_df(bars)
    atr_series = _atr(full_df["high"], full_df["low"], full_df["close"], atr_period)
    if atr_series.empty or pd.isna(atr_series.iloc[-1]):
        return None
    atr_value = atr_series.iloc[-1]

    # RSI
    rsi_series = _rsi(full_df["close"], rsi_period)
    if rsi_series.empty or pd.isna(rsi_series.iloc[-1]):
        return None
    curr_rsi = rsi_series.iloc[-1]

    # Trend filter: price vs SMA
    trend_ok = True
    if require_trend and len(bars) >= trend_ma_period:
        sma_series = _sma(full_df["close"], trend_ma_period)
        if not sma_series.empty and not pd.isna(sma_series.iloc[-1]):
            trend_up = curr_close > sma_series.iloc[-1]
            trend_ok = trend_up  # uptrend allows long only
            # Will be used per-direction below
        else:
            trend_ok = False
            trend_up = True
    else:
        trend_up = True

    # Detect SR levels
    supports, resistances = _detect_levels(
        df["high"], df["low"], atr_value,
        swing_window, cluster_atr_mult,
    )

    # Volume profile
    vp = _volume_profile(df["high"], df["low"], df["volume"], vol_bins)

    # ── Check for BUY signal (bounce off support) ──
    if (not require_trend or not trend_up):  # downtrend or filter off → SELL only
        pass  # handled below
    else:
        # Uptrend + filter on → BUY only
        for s in supports:
            distance = (curr_close - s["price"]) / atr_value
            # Price near support (within bounce_atr * ATR)
            if abs(distance) <= bounce_atr and curr_rsi <= oversold:
                dir_label = "买入"
                reason = (
                    f"支撑反弹: RSI({rsi_period})={curr_rsi:.1f}超卖, "
                    f"支撑{s['price']:.1f}(强度{s['strength']}), "
                    f"现价{curr_close:.1f}, 距离{distance:.2f}ATR"
                )
                log.info("SR-BOUNCE BUY: %s", reason)
                return Signal(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    direction=Direction.BUY,
                    price=curr_close,
                    qty=qty,
                    strength=SignalStrength.MEDIUM if s["strength"] >= 3 else SignalStrength.WEAK,
                    confidence=min(1.0, s["strength"] / 5.0),
                    reason=reason,
                    indicator_values={
                        "support_price": round(s["price"], 2),
                        "support_strength": s["strength"],
                        "atr": round(atr_value, 4),
                        "rsi": round(curr_rsi, 2),
                        "poc": round(vp["poc"], 2),
                    },
                )

    # ── Check for SELL signal (rejection at resistance) ──
    if require_trend and trend_up:
        pass  # uptrend + filter on → no SELL
    else:
        # Downtrend or filter off → SELL allowed
        for r in resistances:
            distance = (curr_close - r["price"]) / atr_value
            if abs(distance) <= bounce_atr and curr_rsi >= overbought:
                reason = (
                    f"阻力回落: RSI({rsi_period})={curr_rsi:.1f}超买, "
                    f"阻力{r['price']:.1f}(强度{r['strength']}), "
                    f"现价{curr_close:.1f}, 距离{distance:.2f}ATR"
                )
                log.info("SR-BOUNCE SELL: %s", reason)
                return Signal(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    direction=Direction.SELL,
                    price=curr_close,
                    qty=qty,
                    strength=SignalStrength.MEDIUM if r["strength"] >= 3 else SignalStrength.WEAK,
                    confidence=min(1.0, r["strength"] / 5.0),
                    reason=reason,
                    indicator_values={
                        "resistance_price": round(r["price"], 2),
                        "resistance_strength": r["strength"],
                        "atr": round(atr_value, 4),
                        "rsi": round(curr_rsi, 2),
                        "poc": round(vp["poc"], 2),
                    },
                )

    return None
