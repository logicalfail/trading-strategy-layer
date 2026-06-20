"""Dual Moving Average Crossover Strategy.

Strategy logic:
  - Calculate fast_ma and slow_ma over close prices
  - Golden cross (BUY): fast_ma crosses ABOVE slow_ma
  - Death cross (SELL): fast_ma crosses BELOW slow_ma

Parameters:
  fast_ma: int — fast MA period (default: 5)
  slow_ma: int — slow MA period (default: 20)
  ma_type: str — "SMA" or "EMA" (default: "SMA")
  min_volume: int — minimum volume to consider signal valid (default: 0)

Risk params (from StrategyConfig.risk_params):
  max_position_qty: max position size
  cooldown_minutes: minimum minutes between signals
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..indicators import SMA, EMA
from ..models import BarData, Direction, Signal, SignalStrength


def generate_signals(
    bars: List[BarData],
    params: Dict[str, Any],
    strategy_id: str,
    symbol: str,
) -> Optional[Signal]:
    """Generate a trading signal based on MA crossover.

    Returns None if no signal, or a Signal if a crossover is detected.
    Only evaluates on the LAST complete bar (the newest data point).
    """
    if len(bars) < 50:  # Need enough data for both MAs
        return None

    fast_period = int(params.get("fast_ma", 5))
    slow_period = int(params.get("slow_ma", 20))
    ma_type = params.get("ma_type", "SMA")
    min_volume = int(params.get("min_volume", 0))

    # Calculate MAs
    if ma_type.upper() == "EMA":
        fast_ma = EMA(bars, fast_period)
        slow_ma = EMA(bars, slow_period)
    else:
        fast_ma = SMA(bars, fast_period)
        slow_ma = SMA(bars, slow_period)

    # Need at least 2 data points in both series to detect crossover
    fast_vals = fast_ma.dropna()
    slow_vals = slow_ma.dropna()
    if len(fast_vals) < 2 or len(slow_vals) < 2:
        return None

    # Current and previous values
    curr_fast = fast_vals.iloc[-1]
    prev_fast = fast_vals.iloc[-2]
    curr_slow = slow_vals.iloc[-1]
    prev_slow = slow_vals.iloc[-2]

    # Latest bar
    latest_bar = bars[-1]

    # Volume filter
    if latest_bar.volume < min_volume:
        return None

    # Price filter: skip if any price is zero
    if latest_bar.close <= 0 or latest_bar.high <= 0:
        return None

    # Detect crossover
    prev_diff = prev_fast - prev_slow
    curr_diff = curr_fast - curr_slow

    indicator_values = {
        "fast_ma": round(float(curr_fast), 2),
        "slow_ma": round(float(curr_slow), 2),
        "close": latest_bar.close,
        "volume": latest_bar.volume,
    }

    # Golden cross: fast crosses above slow (prev_diff <= 0 < curr_diff)
    if prev_diff <= 0 < curr_diff:
        gap_pct = abs(curr_diff / curr_slow * 100) if curr_slow != 0 else 0
        strength = SignalStrength.STRONG if gap_pct > 0.5 else SignalStrength.MEDIUM
        return Signal(
            strategy_id=strategy_id,
            symbol=symbol,
            direction=Direction.BUY,
            price=latest_bar.close,
            qty=1,
            strength=strength,
            confidence=min(1.0, gap_pct * 2),
            reason=f"金叉: {ma_type}({fast_period})={curr_fast:.2f} 上穿 {ma_type}({slow_period})={curr_slow:.2f}, 价={latest_bar.close}",
            indicator_values=indicator_values,
        )

    # Death cross: fast crosses below slow (prev_diff >= 0 > curr_diff)
    if prev_diff >= 0 > curr_diff:
        gap_pct = abs(curr_diff / curr_slow * 100) if curr_slow != 0 else 0
        strength = SignalStrength.STRONG if gap_pct > 0.5 else SignalStrength.MEDIUM
        return Signal(
            strategy_id=strategy_id,
            symbol=symbol,
            direction=Direction.SELL,
            price=latest_bar.close,
            qty=1,
            strength=strength,
            confidence=min(1.0, gap_pct * 2),
            reason=f"死叉: {ma_type}({fast_period})={curr_fast:.2f} 下穿 {ma_type}({slow_period})={curr_slow:.2f}, 价={latest_bar.close}",
            indicator_values=indicator_values,
        )

    return None
