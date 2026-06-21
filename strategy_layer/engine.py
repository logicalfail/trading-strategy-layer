"""Strategy Engine — Heart of the strategy layer.

Responsibilities:
  - Load/manage strategy instances
  - Poll data from futures_demo at configured intervals
  - Feed bars into strategies → generate signals
  - Run risk checks on signals
  - Translate & dispatch signals to execution_layer
  - Log signals and trades
  - Maintain strategy lifecycle (start/stop/pause)

Architecture:
  Engine uses a single async loop that polls all active strategies.
  Each strategy is identified by a strategy_id (UUID) and has its own config.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .clients.data_client import get_data_client
from .clients.exec_client import get_execution_client
from .config import get_config
from .db.sqlite import get_conn
from .models import (
    BarData,
    Direction,
    EngineStatus,
    RiskParams,
    Signal,
    StrategyConfig,
    StrategyConfigCreate,
    StrategyStatus,
    TradeLogCreate,
)
from .risk import RiskCheckResult, risk_manager
from .translator import format_signal_summary, translate_signal

log = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _now_ns() -> int:
    return int(time.time_ns())


# ── Strategy instance (runtime state for one strategy) ────────────────

class StrategyRunner:
    """Runtime instance of a single strategy configuration."""

    def __init__(self, config: StrategyConfig):
        self.config = config
        self.is_running = False
        self.last_signal: Optional[Signal] = None
        self.last_trade_at: Optional[str] = None
        self.active_since: Optional[str] = None
        self.total_signals = 0
        self.total_trades = 0
        self._last_bar_ts: Optional[int] = None  # last processed bar timestamp

    def start(self):
        self.is_running = True
        self.active_since = _utc_now_iso()
        log.info("Strategy started: %s (%s on %s)", self.config.name, self.config.strategy_type, self.config.symbol)

    def stop(self):
        self.is_running = False
        log.info("Strategy stopped: %s", self.config.name)

    @property
    def status(self) -> StrategyStatus:
        return StrategyStatus(
            strategy_id=self.config.id,
            name=self.config.name,
            symbol=self.config.symbol,
            is_running=self.is_running,
            last_signal=self.last_signal,
            last_trade_at=self.last_trade_at,
            current_position=risk_manager.get_position(self.config.symbol),
            active_since=self.active_since,
            total_signals=self.total_signals,
            total_trades=self.total_trades,
        )


# ── Strategy Engine ──────────────────────────────────────────────────

class StrategyEngine:
    """Main engine: polls data, runs strategies, dispatches orders."""

    def __init__(self):
        self._runners: Dict[str, StrategyRunner] = {}  # strategy_id → runner
        self._is_running = False
        self._started_at: Optional[float] = None
        self._last_poll_ts: Optional[str] = None
        self._data_client = get_data_client()
        self._exec_client = get_execution_client()

    # ── Strategy CRUD ──

    def create_strategy(self, req: StrategyConfigCreate) -> StrategyConfig:
        """Create a new strategy config and persist to DB."""
        sid = str(uuid.uuid4())
        now = _utc_now_iso()

        with get_conn() as conn:
            conn.execute(
                """INSERT INTO strategy_configs
                   (id, name, description, symbol, period, strategy_type,
                    params, risk_params, extra_config, is_active, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sid, req.name, req.description, req.symbol, req.period,
                    req.strategy_type,
                    json.dumps(req.params, ensure_ascii=False),
                    req.risk_params.model_dump_json(),
                    json.dumps(req.extra_config, ensure_ascii=False),
                    1, now, now,
                ),
            )

        config = StrategyConfig(
            id=sid, name=req.name, description=req.description,
            symbol=req.symbol, period=req.period, strategy_type=req.strategy_type,
            params=req.params,
            risk_params=req.risk_params,
            extra_config=req.extra_config,
            is_active=True, created_at=now, updated_at=now,
        )
        return config

    def get_strategy(self, strategy_id: str) -> Optional[StrategyConfig]:
        """Get strategy config from DB."""
        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_configs WHERE id = ?", (strategy_id,)
            ).fetchone()
        return self._row_to_config(row) if row else None

    def list_strategies(self, active_only: bool = False) -> List[StrategyConfig]:
        """List all strategy configs."""
        query = "SELECT * FROM strategy_configs"
        params: List[Any] = []
        if active_only:
            query += " WHERE is_active = 1"
        query += " ORDER BY created_at DESC"

        with get_conn() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_config(r) for r in rows]

    def delete_strategy(self, strategy_id: str) -> bool:
        """Delete a strategy config."""
        # Stop if running
        if strategy_id in self._runners:
            self._runners[strategy_id].stop()
            del self._runners[strategy_id]
        with get_conn() as conn:
            cur = conn.execute("DELETE FROM strategy_configs WHERE id = ?", (strategy_id,))
        return cur.rowcount > 0

    def update_strategy_params(
        self, strategy_id: str, params: Dict[str, Any]
    ) -> Optional[StrategyConfig]:
        """Update strategy parameters."""
        now = _utc_now_iso()
        with get_conn() as conn:
            conn.execute(
                "UPDATE strategy_configs SET params = ?, updated_at = ? WHERE id = ?",
                (json.dumps(params, ensure_ascii=False), now, strategy_id),
            )
        return self.get_strategy(strategy_id)

    # ── Strategy lifecycle ──

    def start_strategy(self, strategy_id: str) -> bool:
        """Start a strategy runner for the given config."""
        config = self.get_strategy(strategy_id)
        if not config:
            log.warning("Cannot start strategy %s: not found", strategy_id)
            return False
        if not config.is_active:
            log.warning("Cannot start strategy %s: inactive", strategy_id)
            return False

        if strategy_id in self._runners and self._runners[strategy_id].is_running:
            log.info("Strategy %s already running", strategy_id)
            return True

        runner = StrategyRunner(config)
        runner.start()
        self._runners[strategy_id] = runner
        return True

    def stop_strategy(self, strategy_id: str) -> bool:
        """Stop a strategy runner."""
        if strategy_id in self._runners:
            self._runners[strategy_id].stop()
            return True
        return False

    def is_strategy_running(self, strategy_id: str) -> bool:
        return strategy_id in self._runners and self._runners[strategy_id].is_running

    # ── Engine lifecycle ──

    def start(self):
        """Start the engine (load active strategies and begin polling)."""
        self._is_running = True
        self._started_at = time.time()

        # Auto-load all active strategies
        configs = self.list_strategies(active_only=True)
        for cfg in configs:
            if cfg.id not in self._runners:
                runner = StrategyRunner(cfg)
                runner.start()
                self._runners[cfg.id] = runner
                log.info("Auto-started strategy: %s (%s)", cfg.name, cfg.symbol)

        log.info("Strategy engine started with %d active strategies", len(self._runners))

    def stop(self):
        """Stop engine and all strategies."""
        self._is_running = False
        for runner in self._runners.values():
            runner.stop()
        log.info("Strategy engine stopped")

    @property
    def is_running(self) -> bool:
        return self._is_running

    # ── Main poll cycle ──

    def poll_once(self) -> int:
        """Single poll cycle: fetch data → run strategies → dispatch orders.

        Returns: number of trades dispatched this cycle.
        """
        if not self._runners:
            log.debug("No active strategies to poll")
            return 0

        trades_dispatched = 0
        cfg = get_config()

        for strategy_id, runner in list(self._runners.items()):
            if not runner.is_running:
                continue

            try:
                # Reload config in case it was updated
                config = self.get_strategy(strategy_id)
                if not config or not config.is_active:
                    runner.stop()
                    continue

                # Fetch latest bars
                is_timer_based = config.strategy_type == "interval_test"
                bars = self._data_client.get_latest_bars(
                    config.symbol,
                    n=200,  # Get enough for indicator calculation
                )

                # Timer-based strategies (interval_test) fire on every poll
                # regardless of bar arrival, so they bypass both the empty-bars
                # check and the _last_bar_ts dedup check.
                if not is_timer_based:
                    if not bars:
                        log.debug("No data for %s (%s)", config.symbol, config.name)
                        continue
                    # Check for new bar (avoid re-processing same bar)
                    latest_ts = bars[-1].ts_ns
                    if runner._last_bar_ts is not None and latest_ts <= runner._last_bar_ts:
                        continue  # No new data
                    runner._last_bar_ts = latest_ts

                # Run strategy logic (dispatch by strategy_type)
                signal = self._run_strategy_logic(
                    strategy_type=config.strategy_type,
                    bars=bars,
                    params=config.params,
                    strategy_id=strategy_id,
                    symbol=config.symbol,
                )

                if signal is None:
                    continue

                # Log signal
                self._log_signal(signal)
                runner.last_signal = signal
                runner.total_signals += 1

                # Run risk checks
                risk_params = RiskParams(**json.loads(config.risk_params)) if isinstance(config.risk_params, str) else config.risk_params
                risk_result = risk_manager.check_signal(signal, risk_params)

                log.info(format_signal_summary(signal, risk_result))

                if not risk_result.passed:
                    continue

                # Translate signal → order
                order_payload = translate_signal(signal)
                # place_order() reconstructs order_type from price internally,
                # and expects Direction enum (not string). Strip incompatible fields.
                order_payload.pop("order_type", None)
                if "direction" in order_payload and isinstance(order_payload["direction"], str):
                    order_payload["direction"] = Direction(order_payload["direction"])

                # Log trade attempt
                trade = self._log_trade(signal, risk_result, order_payload)

                # Dispatch to execution_layer
                exec_result = self._exec_client.place_order(**order_payload)

                # Update trade log with execution result
                self._update_trade_result(trade.id, exec_result)

                # Update position cache
                if exec_result.get("success"):
                    risk_manager.update_position(signal.symbol, signal.direction, signal.qty)
                    runner.last_trade_at = _utc_now_iso()
                    runner.total_trades += 1
                    trades_dispatched += 1
                else:
                    log.warning("Order dispatch failed: %s", exec_result.get("error"))

            except Exception as e:
                log.error("Strategy poll failed for %s: %s", strategy_id, e, exc_info=True)

        self._last_poll_ts = _utc_now_iso()
        return trades_dispatched

    # ── Strategy type dispatcher ──

    @staticmethod
    def _run_strategy_logic(
        strategy_type: str,
        bars: List[BarData],
        params: Dict[str, Any],
        strategy_id: str,
        symbol: str,
    ) -> Optional[Signal]:
        """Dispatch to the correct strategy implementation by type."""
        if strategy_type == "ma_cross":
            from .strategies import ma_cross
            return ma_cross.generate_signals(bars, params, strategy_id, symbol)
        elif strategy_type == "rsi_reversal":
            from .strategies import rsi_reversal
            return rsi_reversal.generate_signals(bars, params, strategy_id, symbol)
        elif strategy_type == "interval_test":
            from .strategies import interval_test
            return interval_test.generate_signals(bars, params, strategy_id, symbol)
        elif strategy_type == "support_resistance":
            from .strategies import support_resistance
            return support_resistance.generate_signals(bars, params, strategy_id, symbol)
        elif strategy_type == "swing_extremum":
            from .strategies import swing_extremum
            return swing_extremum.generate_signals(bars, params, strategy_id, symbol)
        else:
            log.warning("Unknown strategy type: %s", strategy_type)
            return None

    # ── DB logging ──

    def _log_signal(self, signal: Signal) -> None:
        """Persist a signal to signal_log."""
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO signal_log
                   (strategy_id, symbol, direction, price, qty, confidence, reason, indicator_values, ts_ns, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.strategy_id, signal.symbol, signal.direction.value,
                    str(signal.price) if signal.price is not None else None,
                    signal.qty, signal.confidence, signal.reason,
                    json.dumps(signal.indicator_values, ensure_ascii=False),
                    signal.ts_ns, _utc_now_iso(),
                ),
            )

    def _log_trade(self, signal: Signal, risk_result: RiskCheckResult, order_payload: Dict[str, Any]) -> Any:
        """Persist a trade attempt to trade_log. Returns the row."""
        with get_conn() as conn:
            cur = conn.execute(
                """INSERT INTO trade_log
                   (strategy_id, symbol, direction, qty, price, order_type,
                    risk_check, ts_ns, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal.strategy_id, signal.symbol, signal.direction.value,
                    signal.qty, str(order_payload.get("price", "")) if order_payload.get("price") else None,
                    order_payload.get("order_type", "MARKET"),
                    json.dumps(risk_result.to_dict(), ensure_ascii=False),
                    signal.ts_ns, _utc_now_iso(),
                ),
            )
            # Get last insert id
            row = conn.execute("SELECT last_insert_rowid()").fetchone()
            if row:
                trade_id = row[0]
                # Update signal_id link
                conn.execute(
                    "UPDATE trade_log SET signal_id = (SELECT MAX(id) FROM signal_log WHERE strategy_id = ?) WHERE id = ?",
                    (signal.strategy_id, trade_id),
                )
                return type('TradeRow', (), {'id': trade_id})()
        return type('TradeRow', (), {'id': 0})()

    def _update_trade_result(self, trade_id: int, exec_result: Dict[str, Any]) -> None:
        """Update trade_log with execution result."""
        record = exec_result.get("record", {})
        with get_conn() as conn:
            conn.execute(
                """UPDATE trade_log SET
                   order_plan_id = ?, execution_id = ?, execution_status = ?
                   WHERE id = ?""",
                (
                    record.get("order_plan_id") if record else None,
                    record.get("id") if record else None,
                    record.get("status") if record else exec_result.get("success", False),
                    trade_id,
                ),
            )

    # ── Status ──

    def get_status(self) -> EngineStatus:
        """Get overall engine status."""
        uptime = time.time() - self._started_at if self._started_at else None
        return EngineStatus(
            is_running=self._is_running,
            active_strategies=len([r for r in self._runners.values() if r.is_running]),
            strategies=[r.status for r in self._runners.values()],
            last_poll_ts=self._last_poll_ts,
            uptime_seconds=uptime,
        )

    # ── Helpers ──

    @staticmethod
    def _row_to_config(row) -> StrategyConfig:
        return StrategyConfig(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            symbol=row["symbol"],
            period=row["period"],
            strategy_type=row["strategy_type"],
            params=json.loads(row["params"]) if row["params"] else {},
            risk_params=RiskParams(**json.loads(row["risk_params"])) if row["risk_params"] else RiskParams(),
            extra_config=json.loads(row["extra_config"]) if row["extra_config"] else {},
            is_active=bool(row["is_active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# Singleton
engine = StrategyEngine()
