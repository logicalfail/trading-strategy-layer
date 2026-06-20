"""RSI Overbought/Oversold Reversal Strategy.

Strategy logic:
  - Calculate RSI over close prices
  - RSI < oversold_threshold (default: 30) → BUY signal (oversold bounce)
  - RSI > overbought_threshold (default: 70) → SELL signal (overbought pullback)
  - RSI must have crossed the threshold on the current bar (not already there)

Parameters:
  rsi_period: int — RSI calculation period (default: 14)
  oversold_threshold: float — RSI level for oversold (default: 30)
  overbought_threshold: float — RSI level for overbought (default: 70)
  min_volume: int — minimum volume filter (default: 0)
  require_trend_filter: bool — require MA trend confirmation (default: False)
  trend_ma_period: int — MA period for trend filter (default: 50)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..indicators import RSI, SMA
from ..models import BarData, Direction, Signal, SignalStrength


def generate_signals(
    bars: List[BarData],
    params: Dict[str, Any],
    strategy_id: str,
    symbol: str,
) -> Optional[Signal]:
    """Generate a trading signal based on RSI threshold crossover.

    Returns None if no signal, or a Signal when RSI crosses a threshold.
    Only evaluates on the LAST complete bar.
    """
    if len(bars) < 20:
        return None

    rsi_period = int(params.get("rsi_period", 14))
    oversold = float(params.get("oversold_threshold", 30))
    overbought = float(params.get("overbought_threshold", 70))
    min_volume = int(params.get("min_volume", 0))
    require_trend = params.get("require_trend_filter", False)
    trend_period = int(params.get("trend_ma_period", 50))

    # Calculate RSI
    rsi_series = RSI(bars, rsi_period)
    rsi_vals = rsi_series.dropna()

    if len(rsi_vals) < 3:
        return None

    curr_rsi = rsi_vals.iloc[-1]
    prev_rsi = rsi_vals.iloc[-2]

    # Latest bar for price/volume check
    latest_bar = bars[-1]
    if latest_bar.volume < min_volume:
        return None
    if latest_bar.close <= 0:
        return None

    indicator_values = {
        "rsi": round(float(curr_rsi), 2),
        "prev_rsi": round(float(prev_rsi), 2),
        "close": latest_bar.close,
        "volume": latest_bar.volume,
    }

    # Trend filter: require price above MA for BUY, below MA for SELL
    trend_ok = True
    if require_trend and len(bars) > trend_period:
        ma = SMA(bars, trend_period)
        ma_val = ma.iloc[-1]
        indicator_values["trend_ma"] = round(float(ma_val), 2)
        trend_bull = latest_bar.close > ma_val
        trend_bear = latest_bar.close < ma_val

    # BUY signal: RSI crosses ABOVE oversold (was oversold, now recovering)
    if prev_rsi <= oversold < curr_rsi:
        if require_trend and not trend_bull:
            return None  # Trend filter rejects
        strength_val = abs(curr_rsi - oversold) / oversold
        strength = SignalStrength.STRONG if strength_val > 0.15 else SignalStrength.MEDIUM
        confidence = min(0.9, 0.4 + strength_val * 2)
        return Signal(
            strategy_id=strategy_id,
            symbol=symbol,
            direction=Direction.BUY,
            price=latest_bar.close,
            qty=1,
            strength=strength,
            confidence=confidence,
            reason=f"RSI超卖反弹: RSI({rsi_period})={curr_rsi:.1f} 上穿 {oversold}, 价={latest_bar.close}",
            indicator_values=indicator_values,
        )

    # SELL signal: RSI crosses BELOW overbought (was overbought, now falling)
    if prev_rsi >= overbought > curr_rsi:
        if require_trend and not trend_bear:
            return None  # Trend filter rejects
        strength_val = abs(overbought - curr_rsi) / (100 - overbought)
        strength = SignalStrength.STRONG if strength_val > 0.15 else SignalStrength.MEDIUM
        confidence = min(0.9, 0.4 + strength_val * 2)
        return Signal(
            strategy_id=strategy_id,
            symbol=symbol,
            direction=Direction.SELL,
            price=latest_bar.close,
            qty=1,
            strength=strength,
            confidence=confidence,
            reason=f"RSI超买回落: RSI({rsi_period})={curr_rsi:.1f} 下穿 {overbought}, 价={latest_bar.close}",
            indicator_values=indicator_values,
        )

    return None
