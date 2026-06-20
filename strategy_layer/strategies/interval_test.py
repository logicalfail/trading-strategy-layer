"""Interval-based Test Strategy.

Generates alternating BUY/SELL signals at fixed wall-clock intervals.
Intended for testing the full pipeline (signal → risk → order dispatch).

This strategy uses wall-clock time (not bar timestamps) for interval
tracking, so the engine MUST call generate_signals() on every poll cycle
(see engine.py _last_bar_ts bypass for interval_test).

Parameters:
  interval_minutes: int — 信号间隔分钟 (default: 5)
  start_direction: str — 首次信号方向 "BUY" 或 "SELL" (default: "BUY")
  qty: int — 每笔数量 (default: 1)
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from ..models import BarData, Direction, Signal, SignalStrength

# State: last signal wall-clock timestamp per (strategy_id:symbol)
_last_signal_at: Dict[str, float] = {}
_direction_parity: Dict[str, int] = {}  # 0=BUY-direction turn, 1=SELL-direction turn


def _key(strategy_id: str, symbol: str) -> str:
    return f"{strategy_id}:{symbol}"


def generate_signals(
    bars: List[BarData],
    params: Dict[str, Any],
    strategy_id: str,
    symbol: str,
) -> Optional[Signal]:
    """Generate alternating BUY/SELL signals at fixed wall-clock intervals.

    Uses time.time() (wall clock) rather than bar timestamps so the
    strategy fires reliably regardless of whether new bars arrive.
    The first signal fires on the very first call.
    """
    interval_minutes = int(params.get("interval_minutes", 5))
    start_direction = params.get("start_direction", "BUY")
    qty = int(params.get("qty", 1))
    interval_seconds = interval_minutes * 60

    key = _key(strategy_id, symbol)
    now = time.time()

    # Check wall-clock interval since last signal
    last_time = _last_signal_at.get(key, 0.0)
    if now - last_time < interval_seconds:
        return None

    # Determine direction: alternate each signal
    parity = _direction_parity.get(key, 0)
    if start_direction == "BUY":
        direction = Direction.BUY if parity % 2 == 0 else Direction.SELL
    else:
        direction = Direction.SELL if parity % 2 == 0 else Direction.BUY

    # Update state
    _last_signal_at[key] = now
    _direction_parity[key] = parity + 1

    # Use latest bar close for price.
    # When no bars available, pass None (market order) instead of 0.0
    # to avoid risk price-sanity rejection.
    close = bars[-1].close if bars else None

    dir_label = "买入" if direction == Direction.BUY else "卖出"
    reason = (
        f"定时测试({dir_label}): 第{parity + 1}次信号, "
        f"间隔{interval_minutes}分钟, 价格={close}"
    )

    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        direction=direction,
        price=close,
        qty=qty,
        strength=SignalStrength.MEDIUM,
        confidence=1.0,
        reason=reason,
        indicator_values={
            "interval_minutes": interval_minutes,
            "signal_number": parity + 1,
            "close": close,
        },
    )


def reset_state(strategy_id: str, symbol: str) -> None:
    """Reset tracking state for a given strategy/symbol pair."""
    key = _key(strategy_id, symbol)
    _last_signal_at.pop(key, None)
    _direction_parity.pop(key, None)
