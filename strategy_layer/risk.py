"""Risk Manager — pre-trade risk checks.

Validates signals against risk parameters before allowing execution.
All checks must pass for a signal to proceed to order translation.

Checks:
  - max_position: ensure we don't exceed max position per symbol
  - cooldown: minimum time between signals for same strategy
  - price_sanity: reject signals with obviously bad prices
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from .db.sqlite import get_conn
from .models import Direction, RiskParams, Signal

log = logging.getLogger(__name__)


class RiskCheckResult:
    """Result of a single risk check."""
    def __init__(self, passed: bool, reason: str = "", detail: Optional[Dict[str, Any]] = None):
        self.passed = passed
        self.reason = reason
        self.detail = detail or {}

    def to_dict(self) -> Dict[str, Any]:
        return {"passed": self.passed, "reason": self.reason, "detail": self.detail}


class RiskManager:
    """Evaluates signals against risk constraints.

    Maintains local position cache synced with execution_layer.
    """

    def __init__(self):
        self._position_cache: Dict[str, int] = {}  # symbol -> net qty (positive=LONG, negative=SHORT)

    def check_signal(self, signal: Signal, risk_params: RiskParams) -> RiskCheckResult:
        """Run ALL risk checks on a signal. Returns first failure or pass."""
        # 1. Price sanity check
        price_check = self._check_price_sanity(signal)
        if not price_check.passed:
            return price_check

        # 2. Max position check
        pos_check = self._check_max_position(signal, risk_params)
        if not pos_check.passed:
            return pos_check

        # 3. Cooldown check
        cooldown_check = self._check_cooldown(signal, risk_params)
        if not cooldown_check.passed:
            return cooldown_check

        # 4. Short allowed check
        short_check = self._check_short_allowed(signal, risk_params)
        if not short_check.passed:
            return short_check

        return RiskCheckResult(passed=True, reason="所有风控检查通过")

    def _check_price_sanity(self, signal: Signal) -> RiskCheckResult:
        """Reject signals with zero/negative prices."""
        if signal.price is not None and signal.price <= 0:
            return RiskCheckResult(
                passed=False,
                reason=f"无效价格: {signal.price}",
                detail={"price": signal.price},
            )
        return RiskCheckResult(passed=True)

    def _check_max_position(self, signal: Signal, risk_params: RiskParams) -> RiskCheckResult:
        """Check that order won't exceed max position."""
        max_qty = risk_params.max_position_qty
        current_qty = self._position_cache.get(signal.symbol, 0)

        if signal.direction == Direction.BUY:
            new_qty = current_qty + signal.qty
            if new_qty > max_qty:
                return RiskCheckResult(
                    passed=False,
                    reason=f"多头仓位超限: 当前{current_qty}, 请求{signal.qty}, 上限{max_qty}",
                    detail={"current": current_qty, "requested": signal.qty, "max": max_qty, "new": new_qty},
                )
        else:  # SELL
            new_qty = current_qty - signal.qty
            if new_qty < -max_qty:
                return RiskCheckResult(
                    passed=False,
                    reason=f"空头仓位超限: 当前{current_qty}, 请求{signal.qty}, 上限{max_qty}",
                    detail={"current": current_qty, "requested": signal.qty, "max": max_qty, "new": new_qty},
                )

        return RiskCheckResult(passed=True)

    def _check_cooldown(self, signal: Signal, risk_params: RiskParams) -> RiskCheckResult:
        """Check minimum time since last signal from same strategy."""
        if risk_params.cooldown_minutes <= 0:
            return RiskCheckResult(passed=True)

        cutoff = datetime.now() - timedelta(minutes=risk_params.cooldown_minutes)
        cutoff_ns = int(cutoff.timestamp() * 1e9)

        with get_conn() as conn:
            row = conn.execute(
                "SELECT MAX(ts_ns) FROM signal_log WHERE strategy_id = ? AND symbol = ?",
                (signal.strategy_id, signal.symbol),
            ).fetchone()

        if row and row[0] and row[0] > cutoff_ns:
            remaining_sec = (row[0] - cutoff_ns) / 1e9
            return RiskCheckResult(
                passed=False,
                reason=f"冷却中: 还需 {remaining_sec:.0f} 秒才能产生新信号",
                detail={"remaining_seconds": remaining_sec, "cooldown_minutes": risk_params.cooldown_minutes},
            )

        return RiskCheckResult(passed=True)

    def _check_short_allowed(self, signal: Signal, risk_params: RiskParams) -> RiskCheckResult:
        """Check if short selling is allowed."""
        if signal.direction == Direction.SELL and not risk_params.allow_short:
            return RiskCheckResult(
                passed=False,
                reason="做空未启用",
                detail={"allow_short": False},
            )
        return RiskCheckResult(passed=True)

    # ── Position cache management ──

    def update_position(self, symbol: str, direction: Direction, qty: int) -> None:
        """Update local position cache after a trade."""
        current = self._position_cache.get(symbol, 0)
        if direction == Direction.BUY:
            self._position_cache[symbol] = current + qty
        else:
            self._position_cache[symbol] = current - qty

        # Persist to DB
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with get_conn() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO position_cache (symbol, direction, qty, updated_at)
                   VALUES (?, ?, ?, ?)""",
                (symbol, "LONG" if self._position_cache[symbol] >= 0 else "SHORT",
                 abs(self._position_cache[symbol]), now),
            )

    def get_position(self, symbol: str) -> int:
        """Get net position for a symbol from cache."""
        if symbol in self._position_cache:
            return self._position_cache[symbol]
        # Try loading from DB
        with get_conn() as conn:
            row = conn.execute(
                "SELECT qty, direction FROM position_cache WHERE symbol = ?", (symbol,)
            ).fetchone()
        if row:
            qty = row["qty"]
            self._position_cache[symbol] = qty if row["direction"] == "LONG" else -qty
            return self._position_cache[symbol]
        return 0

    def sync_from_execution(self, positions: List[Dict[str, Any]]) -> None:
        """Sync position cache from execution_layer API response."""
        for pos in positions:
            symbol = pos.get("symbol", "")
            direction = pos.get("direction", "LONG")
            qty = pos.get("qty", 0)
            if direction == "LONG":
                self._position_cache[symbol] = qty
            else:
                self._position_cache[symbol] = -qty

            now = datetime.now().isoformat(timespec="seconds")
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO position_cache (symbol, direction, qty, updated_at) VALUES (?, ?, ?, ?)",
                    (symbol, direction, qty, now),
                )


# Singleton
risk_manager = RiskManager()
