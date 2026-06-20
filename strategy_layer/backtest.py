"""Backtesting Engine — replay historical bars, evaluate strategy performance.

Usage:
    from strategy_layer.backtest import run_backtest

    result = run_backtest(
        strategy_type="ma_cross",
        symbol="RB2609.SHFE",
        params={"fast_ma": 5, "slow_ma": 20, "ma_type": "SMA"},
        start_date="2026-05-01",
        end_date="2026-06-13",
    )
    print(result.summary())
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .clients.data_client import get_data_client
from .models import BarData, Direction

log = logging.getLogger(__name__)


@dataclass
class BacktestTrade:
    """A simulated trade during backtest."""
    entry_time: str
    exit_time: Optional[str] = None
    direction: str = ""
    entry_price: float = 0.0
    exit_price: Optional[float] = None
    qty: int = 1
    pnl: float = 0.0
    pnl_pct: float = 0.0
    reason: str = ""
    bars_held: int = 0


@dataclass
class BacktestResult:
    """Complete backtest result."""
    strategy_name: str = ""
    symbol: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    start_date: str = ""
    end_date: str = ""
    total_bars: int = 0
    total_trades: int = 0
    win_trades: int = 0
    loss_trades: int = 0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    sharpe_ratio: Optional[float] = None
    win_rate: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    trades: List[BacktestTrade] = field(default_factory=list)
    # Bar series for frontend charting
    bar_timestamps: List[str] = field(default_factory=list)
    bar_close: List[float] = field(default_factory=list)
    bar_high: List[float] = field(default_factory=list)
    bar_low: List[float] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"{'='*60}",
            f"  回测报告: {self.strategy_name} @ {self.symbol}",
            f"  区间: {self.start_date} → {self.end_date}",
            f"  参数: {self.params}",
            f"{'='*60}",
            f"  K线数量:     {self.total_bars}",
            f"  交易次数:     {self.total_trades}",
            f"  胜率:        {self.win_rate:.1%}",
            f"  总盈亏:       {self.total_pnl:+.2f} 点",
            f"  最大回撤:     {self.max_drawdown:.2%}",
            f"  盈亏比:       {self.profit_factor:.2f}",
            f"  夏普比率:     {self.sharpe_ratio if self.sharpe_ratio else 'N/A'}",
            f"  平均盈利:     {self.avg_win:+.2f}",
            f"  平均亏损:     {self.avg_loss:+.2f}",
            f"{'='*60}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        def _clean(obj):
            """Recursively replace inf/nan with their string representation."""
            import math
            if obj is None:
                return None
            if isinstance(obj, float):
                if math.isinf(obj) or math.isnan(obj):
                    return str(obj)
                return obj
            if isinstance(obj, dict):
                return {k: _clean(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_clean(v) for v in obj]
            return obj

        return _clean({
            "strategy_name": self.strategy_name,
            "symbol": self.symbol,
            "params": self.params,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "total_bars": self.total_bars,
            "total_trades": self.total_trades,
            "win_trades": self.win_trades,
            "loss_trades": self.loss_trades,
            "total_pnl": self.total_pnl,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "trades": [t.__dict__ for t in self.trades],
            "bar_timestamps": self.bar_timestamps,
            "bar_close": self.bar_close,
            "bar_high": self.bar_high,
            "bar_low": self.bar_low,
        })


def run_backtest(
    strategy_type: str,
    symbol: str,
    params: Dict[str, Any],
    start_date: str,
    end_date: str,
    period: str = "1m",
    initial_capital: float = 100000.0,
    commission_per_trade: float = 0.0,
    cancel_event: Optional[threading.Event] = None,
) -> BacktestResult:
    """Run a backtest for a given strategy over historical data.

    Fetches data from futures_demo, replays bar by bar,
    and computes performance metrics.
    """
    result = BacktestResult(
        strategy_name=strategy_type,
        symbol=symbol,
        params=params,
        start_date=start_date,
        end_date=end_date,
    )

    # 1. Fetch historical data
    client = get_data_client()
    all_bars = client.get_bars(
        symbol=symbol,
        period=period,
        limit=10000,
        days_back=90,
    )
    if not all_bars:
        log.warning("No data returned for backtest: %s %s-%s", symbol, start_date, end_date)
        return result

    # Filter by date range
    start_ns = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1e9)
    end_ns = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1e9) + 86400 * int(1e9)
    bars = [b for b in all_bars if start_ns <= b.ts_ns <= end_ns]

    if not bars:
        log.warning("No bars in date range for backtest")
        return result

    result.total_bars = len(bars)
    log.info("Backtest: %d bars for %s", len(bars), symbol)

    # 2. Bar-by-bar replay
    trades: List[BacktestTrade] = []
    current_position = 0  # 0=flat, >0=long, <0=short
    current_trade: Optional[BacktestTrade] = None
    pnl_values: List[float] = [0.0]
    peak_equity = 0.0
    max_drawdown = 0.0
    gross_profit = 0.0
    gross_loss = 0.0

    for i in range(len(bars)):
        # Check cancellation
        if cancel_event and cancel_event.is_set():
            log.info("Backtest cancelled at bar %d/%d for %s", i, len(bars), symbol)
            # Close open position if any before returning partial result
            if current_trade:
                current_trade.exit_time = datetime.fromtimestamp(bars[i].ts_ns / 1e9).isoformat()
                current_trade.exit_price = bars[i].close
                if current_position > 0:
                    current_trade.pnl = (current_trade.exit_price - current_trade.entry_price) * current_trade.qty
                else:
                    current_trade.pnl = (current_trade.entry_price - current_trade.exit_price) * current_trade.qty
                current_trade.pnl_pct = current_trade.pnl / (current_trade.entry_price * current_trade.qty) if current_trade.entry_price else 0
                trades.append(current_trade)
            # Compute partial stats
            closed = [t for t in trades if t.exit_time is not None]
            result.total_trades = len(closed)
            result.win_trades = sum(1 for t in closed if t.pnl > 0)
            result.loss_trades = sum(1 for t in closed if t.pnl <= 0)
            result.total_pnl = sum(t.pnl for t in closed) if closed else 0.0
            result.trades = closed
            result.bar_timestamps = [datetime.fromtimestamp(b.ts_ns / 1e9).isoformat() for b in bars[:i]]
            result.bar_close = [b.close for b in bars[:i]]
            result.bar_high = [b.high for b in bars[:i]]
            result.bar_low = [b.low for b in bars[:i]]
            return result

        current_window = bars[: i + 1]
        close = current_window[-1].close

        # Run strategy to check for signal
        signal = _generate_signal(strategy_type, current_window, params, symbol, "backtest")

        if signal:
            direction = signal["direction"]
            price = close  # Assume fill at close

            if direction == "BUY" and current_position <= 0:
                # Close short if any
                if current_trade and current_position < 0:
                    current_trade.exit_time = datetime.fromtimestamp(bars[i].ts_ns / 1e9).isoformat()
                    current_trade.exit_price = price
                    current_trade.pnl = (current_trade.entry_price - price) * current_trade.qty
                    current_trade.pnl_pct = current_trade.pnl / (current_trade.entry_price * current_trade.qty) if current_trade.entry_price else 0
                    if current_trade.pnl > 0:
                        gross_profit += current_trade.pnl
                    else:
                        gross_loss += abs(current_trade.pnl)
                    trades.append(current_trade)
                    current_trade = None

                # Open long
                current_position = 1
                current_trade = BacktestTrade(
                    entry_time=datetime.fromtimestamp(bars[i].ts_ns / 1e9).isoformat(),
                    direction="BUY",
                    entry_price=price,
                    qty=1,
                    reason=signal.get("reason", ""),
                )

            elif direction == "SELL" and current_position >= 0:
                # Close long if any
                if current_trade and current_position > 0:
                    current_trade.exit_time = datetime.fromtimestamp(bars[i].ts_ns / 1e9).isoformat()
                    current_trade.exit_price = price
                    current_trade.pnl = (price - current_trade.entry_price) * current_trade.qty
                    current_trade.pnl_pct = current_trade.pnl / (current_trade.entry_price * current_trade.qty) if current_trade.entry_price else 0
                    if current_trade.pnl > 0:
                        gross_profit += current_trade.pnl
                    else:
                        gross_loss += abs(current_trade.pnl)
                    trades.append(current_trade)
                    current_trade = None

                # Open short
                current_position = -1
                current_trade = BacktestTrade(
                    entry_time=datetime.fromtimestamp(bars[i].ts_ns / 1e9).isoformat(),
                    direction="SELL",
                    entry_price=price,
                    qty=1,
                    reason=signal.get("reason", ""),
                )

        # Track unrealized PnL for drawdown calculation
        if current_trade:
            if current_position > 0:
                unrealized = (close - current_trade.entry_price) * current_trade.qty
            else:
                unrealized = (current_trade.entry_price - close) * current_trade.qty
            equity = initial_capital + sum(t.pnl for t in trades) + unrealized
        else:
            equity = initial_capital + sum(t.pnl for t in trades)

        pnl_values.append(equity)
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0
        max_drawdown = max(max_drawdown, dd)

    # Close any open position at the end
    if current_trade and bars:
        final_price = bars[-1].close
        current_trade.exit_time = datetime.fromtimestamp(bars[-1].ts_ns / 1e9).isoformat()
        current_trade.exit_price = final_price
        if current_position > 0:
            current_trade.pnl = (final_price - current_trade.entry_price) * current_trade.qty
        else:
            current_trade.pnl = (current_trade.entry_price - final_price) * current_trade.qty
        current_trade.pnl_pct = current_trade.pnl / (current_trade.entry_price * current_trade.qty) if current_trade.entry_price else 0
        if current_trade.pnl > 0:
            gross_profit += current_trade.pnl
        else:
            gross_loss += abs(current_trade.pnl)
        trades.append(current_trade)

    # 3. Compute stats
    closed_trades = [t for t in trades if t.exit_time is not None]
    result.total_trades = len(closed_trades)
    result.win_trades = sum(1 for t in closed_trades if t.pnl > 0)
    result.loss_trades = sum(1 for t in closed_trades if t.pnl <= 0)
    result.total_pnl = sum(t.pnl for t in closed_trades)
    result.max_drawdown = max_drawdown
    result.win_rate = result.win_trades / result.total_trades if result.total_trades > 0 else 0
    result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    wins = [t.pnl for t in closed_trades if t.pnl > 0]
    losses = [t.pnl for t in closed_trades if t.pnl <= 0]
    result.avg_win = sum(wins) / len(wins) if wins else 0
    result.avg_loss = sum(losses) / len(losses) if losses else 0

    # Sharpe ratio (simplified: using trade returns as series)
    if len(closed_trades) > 1:
        returns = [t.pnl_pct for t in closed_trades]
        mean_ret = sum(returns) / len(returns)
        std_ret = (sum((r - mean_ret) ** 2 for r in returns) / len(returns)) ** 0.5
        if std_ret > 0:
            result.sharpe_ratio = (mean_ret / std_ret) * (252 ** 0.5)

    # Populate bar series for frontend charting
    result.bar_timestamps = [datetime.fromtimestamp(b.ts_ns / 1e9).isoformat() for b in bars]
    result.bar_close = [b.close for b in bars]
    result.bar_high = [b.high for b in bars]
    result.bar_low = [b.low for b in bars]

    result.trades = closed_trades
    log.info("Backtest complete: %d trades, PnL=%.2f", result.total_trades, result.total_pnl)
    return result


def run_backtest_from_config(
    symbol: str,
    strategy_type: str,
    params: Dict[str, Any],
    start_date: str,
    end_date: str,
    period: str = "1m",
    cancel_event: Optional[threading.Event] = None,
    task_id: Optional[str] = None,
) -> BacktestResult:
    """Run backtest and persist result to DB."""
    result = run_backtest(
        strategy_type=strategy_type,
        symbol=symbol,
        params=params,
        start_date=start_date,
        end_date=end_date,
        period=period,
        cancel_event=cancel_event,
    )

    # Persist to backtest_results
    from .db.sqlite import get_conn
    import json
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO backtest_results
               (strategy_name, symbol, period, start_date, end_date,
                params, total_trades, win_trades, total_pnl, max_drawdown,
                sharpe_ratio, win_rate, trades_json, created_at, task_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                strategy_type, symbol, period, start_date, end_date,
                json.dumps(params, ensure_ascii=False),
                result.total_trades, result.win_trades,
                str(result.total_pnl), result.max_drawdown,
                result.sharpe_ratio, result.win_rate,
                json.dumps(result.to_dict(), ensure_ascii=False),
                now,
                task_id,
            ),
        )

    return result


def _generate_signal(
    strategy_type: str,
    bars: List[BarData],
    params: Dict[str, Any],
    symbol: str,
    strategy_id: str = "backtest",
) -> Optional[Dict[str, Any]]:
    """Generate a signal for backtesting (same logic as real strategy)."""
    if strategy_type == "ma_cross":
        return _ma_cross_signal(bars, params, symbol)
    elif strategy_type == "rsi_reversal":
        return _rsi_reversal_signal(bars, params, symbol)
    elif strategy_type == "interval_test":
        return _interval_test_signal(bars, params, symbol, strategy_id)
    log.warning("Unknown strategy type for backtest: %s", strategy_type)
    return None


def _rsi_reversal_signal(
    bars: List[BarData],
    params: Dict[str, Any],
    symbol: str,
) -> Optional[Dict[str, Any]]:
    """RSI reversal signal for backtest (same logic as strategies/rsi_reversal.py)."""
    if len(bars) < 20:
        return None

    from .indicators import RSI

    rsi_period = int(params.get("rsi_period", 14))
    oversold = float(params.get("oversold_threshold", 30))
    overbought = float(params.get("overbought_threshold", 70))

    rsi_series = RSI(bars, rsi_period)
    rsi_vals = rsi_series.dropna()
    if len(rsi_vals) < 3:
        return None

    curr_rsi = rsi_vals.iloc[-1]
    prev_rsi = rsi_vals.iloc[-2]
    close = bars[-1].close

    if prev_rsi <= oversold < curr_rsi:
        return {
            "direction": "BUY",
            "price": close,
            "reason": f"RSI超卖反弹: RSI({rsi_period})={curr_rsi:.1f} 上穿 {oversold} @ {close}",
        }
    if prev_rsi >= overbought > curr_rsi:
        return {
            "direction": "SELL",
            "price": close,
            "reason": f"RSI超买回落: RSI({rsi_period})={curr_rsi:.1f} 下穿 {overbought} @ {close}",
        }
    return None


def _interval_test_signal(
    bars: List[BarData],
    params: Dict[str, Any],
    symbol: str,
    strategy_id: str = "backtest",
) -> Optional[Dict[str, Any]]:
    """Interval test signal for backtest (same logic as strategies/interval_test.py)."""
    if not bars:
        return None

    interval_minutes = int(params.get("interval_minutes", 5))
    start_direction = params.get("start_direction", "BUY")
    qty = int(params.get("qty", 1))
    interval_seconds = interval_minutes * 60

    key = f"bt:{strategy_id}:{symbol}"
    bar_time_s = bars[-1].ts_ns / 1e9

    last_time = _bt_interval_last.get(key, 0.0)
    if bar_time_s - last_time < interval_seconds:
        return None

    parity = _bt_interval_parity.get(key, 0)
    if start_direction == "BUY":
        direction = "BUY" if parity % 2 == 0 else "SELL"
    else:
        direction = "SELL" if parity % 2 == 0 else "BUY"

    _bt_interval_last[key] = bar_time_s
    _bt_interval_parity[key] = parity + 1

    close = bars[-1].close
    dir_label = "买入" if direction == "BUY" else "卖出"
    return {
        "direction": direction,
        "price": close,
        "reason": f"定时测试({dir_label}): 第{parity + 1}次信号, 间隔{interval_minutes}分钟 @ {close}",
    }


# Backtest interval-test state (separate from live strategy state)
_bt_interval_last: Dict[str, float] = {}
_bt_interval_parity: Dict[str, int] = {}


# ── Async Backtest Task Manager ────────────────────────────────────────


class BacktestTaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass
class BacktestTaskInfo:
    """Info about an async backtest task."""
    task_id: str
    status: BacktestTaskStatus = BacktestTaskStatus.PENDING
    strategy_type: str = ""
    symbol: str = ""
    params: Dict[str, Any] = field(default_factory=dict)
    start_date: str = ""
    end_date: str = ""
    period: str = "1m"
    created_at: str = ""
    result: Optional[BacktestResult] = None
    error: Optional[str] = None
    cancel_event: Optional[threading.Event] = None


class BacktestTaskManager:
    """Manages async backtest execution via thread pool executor.

    Usage:
        manager = BacktestTaskManager()
        task_id = manager.submit(...)
        status = manager.get_status(task_id)
        result = manager.get_result(task_id)
    """

    def __init__(self):
        self._tasks: Dict[str, BacktestTaskInfo] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        strategy_type: str,
        symbol: str,
        params: Dict[str, Any],
        start_date: str,
        end_date: str,
        period: str = "1m",
    ) -> str:
        """Submit a backtest task. Returns task_id immediately."""
        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        cancel_event = threading.Event()

        info = BacktestTaskInfo(
            task_id=task_id,
            status=BacktestTaskStatus.PENDING,
            strategy_type=strategy_type,
            symbol=symbol,
            params=params,
            start_date=start_date,
            end_date=end_date,
            period=period,
            created_at=now,
            cancel_event=cancel_event,
        )

        with self._lock:
            self._tasks[task_id] = info

        # Start execution in thread pool
        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, self._execute, task_id)

        return task_id

    def _execute(self, task_id: str) -> None:
        """Execute backtest (runs in thread pool)."""
        with self._lock:
            info = self._tasks.get(task_id)
            if not info:
                return
            # Check if cancelled before starting
            if info.cancel_event and info.cancel_event.is_set():
                info.status = BacktestTaskStatus.CANCELLED
                return
            info.status = BacktestTaskStatus.RUNNING

        try:
            result = run_backtest_from_config(
                symbol=info.symbol,
                strategy_type=info.strategy_type,
                params=info.params,
                start_date=info.start_date,
                end_date=info.end_date,
                period=info.period,
                cancel_event=info.cancel_event,
                task_id=task_id,
            )

            with self._lock:
                info = self._tasks.get(task_id)
                if not info:
                    return
                # If cancelled during execution, mark as cancelled
                if info.cancel_event and info.cancel_event.is_set():
                    info.status = BacktestTaskStatus.CANCELLED
                    info.result = result
                else:
                    info.status = BacktestTaskStatus.DONE
                    info.result = result
        except Exception as e:
            log.exception("Backtest task %s failed: %s", task_id, e)
            with self._lock:
                info = self._tasks.get(task_id)
                if info:
                    info.status = BacktestTaskStatus.ERROR
                    info.error = str(e)

    def cancel(self, task_id: str) -> bool:
        """Cancel a running backtest task. Returns True if cancelled."""
        with self._lock:
            info = self._tasks.get(task_id)
            if not info:
                return False
            if info.status not in (BacktestTaskStatus.PENDING, BacktestTaskStatus.RUNNING):
                return False  # Already done / error / cancelled
            if info.cancel_event:
                info.cancel_event.set()
            if info.status == BacktestTaskStatus.PENDING:
                info.status = BacktestTaskStatus.CANCELLED
            # RUNNING status will be updated by _execute when it checks the event
            return True

    def replay(self, task_id: str) -> Optional[str]:
        """Re-run a task with the same parameters. Returns new task_id."""
        with self._lock:
            info = self._tasks.get(task_id)
            if not info:
                return None
            # Copy params from existing task
            return self.submit(
                strategy_type=info.strategy_type,
                symbol=info.symbol,
                params=info.params,
                start_date=info.start_date,
                end_date=info.end_date,
                period=info.period,
            )

    def delete(self, task_id: str) -> bool:
        """Delete a task (cancel if running, then remove)."""
        with self._lock:
            info = self._tasks.get(task_id)
            if not info:
                return False
            # Cancel first if running/pending
            if info.status in (BacktestTaskStatus.PENDING, BacktestTaskStatus.RUNNING):
                if info.cancel_event:
                    info.cancel_event.set()
            del self._tasks[task_id]
            return True

    def get_status(self, task_id: str) -> Optional[BacktestTaskInfo]:
        """Get task status info. Returns None if task_id not found."""
        with self._lock:
            return self._tasks.get(task_id)

    def get_result(self, task_id: str) -> Optional[BacktestResult]:
        """Get task result. Returns None if not done or not found."""
        with self._lock:
            info = self._tasks.get(task_id)
            if info and info.status == BacktestTaskStatus.DONE:
                return info.result
            return None

    def list_tasks(self) -> List[BacktestTaskInfo]:
        """List all tasks (ordered by creation time descending)."""
        with self._lock:
            tasks = list(self._tasks.values())
            tasks.sort(key=lambda t: t.created_at, reverse=True)
            return tasks

    def cleanup_old(self, max_age_hours: int = 24) -> int:
        """Remove tasks older than max_age_hours. Returns count removed."""
        cutoff = time.time() - max_age_hours * 3600
        removed = 0
        with self._lock:
            to_delete = [
                tid for tid, info in self._tasks.items()
                if info.created_at and self._parse_iso_to_ts(info.created_at) < cutoff
            ]
            for tid in to_delete:
                del self._tasks[tid]
                removed += 1
        return removed

    @staticmethod
    def _parse_iso_to_ts(iso_str: str) -> float:
        try:
            dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            return dt.timestamp()
        except Exception:
            return 0


# Singleton
backtest_manager = BacktestTaskManager()


def _ma_cross_signal(
    bars: List[BarData],
    params: Dict[str, Any],
    symbol: str,
) -> Optional[Dict[str, Any]]:
    """Dual MA crossover signal for backtest (same logic as strategies/ma_cross.py)."""
    if len(bars) < 50:
        return None

    from .indicators import SMA, EMA

    fast_period = int(params.get("fast_ma", 5))
    slow_period = int(params.get("slow_ma", 20))
    ma_type = params.get("ma_type", "SMA")

    if ma_type.upper() == "EMA":
        fast_ma = EMA(bars, fast_period)
        slow_ma = EMA(bars, slow_period)
    else:
        fast_ma = SMA(bars, fast_period)
        slow_ma = SMA(bars, slow_period)

    fast_vals = fast_ma.dropna()
    slow_vals = slow_ma.dropna()
    if len(fast_vals) < 2 or len(slow_vals) < 2:
        return None

    curr_fast = fast_vals.iloc[-1]
    prev_fast = fast_vals.iloc[-2]
    curr_slow = slow_vals.iloc[-1]
    prev_slow = slow_vals.iloc[-2]

    prev_diff = prev_fast - prev_slow
    curr_diff = curr_fast - curr_slow
    close = bars[-1].close

    # Golden cross
    if prev_diff <= 0 < curr_diff:
        return {
            "direction": "BUY",
            "price": close,
            "reason": f"金叉: {ma_type}({fast_period})上穿{ma_type}({slow_period}) @ {close}",
        }
    # Death cross
    if prev_diff >= 0 > curr_diff:
        return {
            "direction": "SELL",
            "price": close,
            "reason": f"死叉: {ma_type}({fast_period})下穿{ma_type}({slow_period}) @ {close}",
        }
    return None
