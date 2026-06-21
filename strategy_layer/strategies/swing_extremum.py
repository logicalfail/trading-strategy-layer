"""Swing Extremum Strategy — trades at newly formed swing high/low points.

Logic:
  1. Scan the bar window for swing highs (local peaks) and swing lows (local troughs).
  2. Track the most recently confirmed swing point by bar index.
  3. On each call, if a NEW swing high appears → SELL signal (expecting reversal down).
     If a NEW swing low appears → BUY signal (expecting reversal up).
  4. Directions alternate naturally: BUY at swing low → wait for next swing high → SELL.
  5. Optional RSI filter: only SELL if overbought, only BUY if oversold.

Parameters:
  swing_window: int — 极值探测半窗口 (default: 5)
  rsi_period: int — RSI 周期 (default: 14)
  oversold: float — RSI 超卖阈值 (default: 30)
  overbought: float — RSI 超买阈值 (default: 70)
  require_rsi: bool — 是否要求 RSI 确认 (default: True)
  lookback: int — 扫描回溯 K 线数 (default: 200)
  qty: int — 每笔数量 (default: 1)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from ..models import BarData, Direction, Signal, SignalStrength

log = logging.getLogger(__name__)


# ── Module-level state (persist across calls) ──────────────────────────
# Tracks the last processed swing bar index per (strategy_id:symbol)
# to avoid re-triggering on the same swing point.
_last_swing_idx: Dict[str, int] = {}


def _key(strategy_id: str, symbol: str) -> str:
    return f"{strategy_id}:{symbol}"


# ── Swing detection (same logic as support_resistance) ─────────────────

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


def _to_df(bars: List[BarData]) -> pd.DataFrame:
    return pd.DataFrame({
        "open":   [b.open for b in bars],
        "high":   [b.high for b in bars],
        "low":    [b.low for b in bars],
        "close":  [b.close for b in bars],
        "volume": [b.volume for b in bars],
    })


# ── Helpers ────────────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int) -> pd.Series:
    import pandas_ta as ta
    return ta.rsi(close, length=period)


# ── Main strategy entry point ──────────────────────────────────────────

def generate_signals(
    bars: List[BarData],
    params: Dict[str, Any],
    strategy_id: str,
    symbol: str,
) -> Optional[Signal]:
    """Generate reversal signals at newly formed swing high/low points.

    A signal fires at most once per swing point. After detecting a swing
    low (BUY), the strategy waits for the next swing high (SELL), and
    vice versa — natural direction alternation.
    """
    if len(bars) < 100:
        return None

    swing_window = int(params.get("swing_window", 5))
    rsi_period = int(params.get("rsi_period", 14))
    oversold = float(params.get("oversold", 30))
    overbought = float(params.get("overbought", 70))
    require_rsi = bool(params.get("require_rsi", True))
    lookback = int(params.get("lookback", 200))
    qty = int(params.get("qty", 1))

    key = _key(strategy_id, symbol)
    prev_last_idx = _last_swing_idx.get(key, 0)

    # Limit lookback to available data
    effective_lookback = min(lookback, len(bars))
    window_bars = bars[-effective_lookback:]
    df = _to_df(window_bars)

    # RSI (on full bars for accuracy)
    close_series = pd.Series([b.close for b in bars])
    rsi_series = _rsi(close_series, rsi_period)
    curr_rsi = rsi_series.iloc[-1] if not rsi_series.empty and pd.notna(rsi_series.iloc[-1]) else 50.0

    # Detect swing points
    sh = _swing_highs(df["high"], swing_window)
    sl = _swing_lows(df["low"], swing_window)

    # Map swing indices back to absolute bar indices
    base_offset = len(bars) - effective_lookback
    sh_abs = [(base_offset + idx, price) for idx, price in sh]
    sl_abs = [(base_offset + idx, price) for idx, price in sl]

    # Find the most recent swing point (highest index)
    latest_swing_high = max(sh_abs, key=lambda x: x[0]) if sh_abs else None
    latest_swing_low = max(sl_abs, key=lambda x: x[0]) if sl_abs else None

    # Find the most recent overall
    candidates = []
    if latest_swing_high:
        candidates.append(("HIGH", latest_swing_high[0], latest_swing_high[1]))
    if latest_swing_low:
        candidates.append(("LOW", latest_swing_low[0], latest_swing_low[1]))

    if not candidates:
        return None

    # Sort by index descending (most recent first)
    candidates.sort(key=lambda x: -x[1])
    latest_type, latest_idx, latest_price = candidates[0]

    # Skip if this swing was already processed
    if latest_idx <= prev_last_idx:
        return None

    # ── RSI confirmation ──
    if require_rsi:
        if latest_type == "HIGH" and curr_rsi < overbought:
            return None  # Swing high without overbought RSI — skip
        if latest_type == "LOW" and curr_rsi > oversold:
            return None  # Swing low without oversold RSI — skip

    # ── Fire signal ──
    _last_swing_idx[key] = latest_idx

    if latest_type == "HIGH":
        direction = Direction.SELL
        dir_label = "卖出"
        reason = (
            f"极值做空: 波峰确认 @ {latest_price:.1f}, "
            f"RSI({rsi_period})={curr_rsi:.1f}, 窗口={swing_window}"
        )
        strength = SignalStrength.MEDIUM
    else:
        direction = Direction.BUY
        dir_label = "买入"
        reason = (
            f"极值做多: 波谷确认 @ {latest_price:.1f}, "
            f"RSI({rsi_period})={curr_rsi:.1f}, 窗口={swing_window}"
        )
        strength = SignalStrength.MEDIUM

    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        direction=direction,
        price=latest_price,
        qty=qty,
        strength=strength,
        confidence=0.7,
        reason=reason,
        indicator_values={
            "swing_type": latest_type,
            "swing_price": round(latest_price, 2),
            "swing_index": latest_idx,
            "rsi": round(curr_rsi, 2),
        },
    )


def reset_state(strategy_id: str, symbol: str) -> None:
    """Clear tracking state for a given strategy/symbol."""
    key = _key(strategy_id, symbol)
    _last_swing_idx.pop(key, None)
