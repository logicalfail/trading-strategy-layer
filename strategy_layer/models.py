"""Domain models for the strategy layer.

Core models:
  StrategyConfig — persisted strategy configuration (what, how, risk params)
  Signal         — a trading signal produced by a strategy (BUY/SELL at price)
  TradeLog       — record of an executed trade
  BarData        — simplified bar for indicator calculation
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class SignalStrength(str, Enum):
    STRONG = "STRONG"
    MEDIUM = "MEDIUM"
    WEAK = "WEAK"


# ── Bar Data (lightweight, for indicator computation) ──────────────────

class BarData(BaseModel):
    """A single OHLCV bar used for indicator calculation."""
    ts_ns: int
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def ts_dt(self) -> datetime:
        return datetime.fromtimestamp(self.ts_ns / 1e9)


# ── Strategy Configuration ────────────────────────────────────────────

class RiskParams(BaseModel):
    """Risk control parameters per strategy."""
    max_position_qty: int = 10        # 最大持仓手数
    max_daily_loss: Optional[float] = None   # 日内最大亏损
    cooldown_minutes: int = 0          # 信号冷却时间（避免频繁触发）
    allow_short: bool = True           # 允许做空


class StrategyConfigCreate(BaseModel):
    """Request to create a strategy."""
    name: str
    description: str = ""
    symbol: str = Field(..., description="合约代码, e.g. RB2609.SHFE")
    period: str = "1m"
    strategy_type: str = Field(..., description="策略类型标识, e.g. ma_cross")
    params: Dict[str, Any] = Field(default_factory=dict, description="策略参数, e.g. {'fast_ma':5, 'slow_ma':20}")
    risk_params: RiskParams = Field(default_factory=RiskParams)
    extra_config: Dict[str, Any] = Field(default_factory=dict)


class BacktestRunRequest(BaseModel):
    """Request to run a backtest."""
    strategy_type: str = Field(..., description="策略类型, e.g. ma_cross")
    symbol: str = Field(..., description="合约代码, e.g. RB2609.SHFE")
    params: Dict[str, Any] = Field(default_factory=dict, description="策略参数")
    start_date: str = Field(..., description="开始日期, YYYY-MM-DD")
    end_date: str = Field(..., description="结束日期, YYYY-MM-DD")
    period: str = Field("1m", description="K线周期")


class StrategyConfig(BaseModel):
    """Persisted strategy configuration."""
    id: str
    name: str
    description: str = ""
    symbol: str
    period: str = "1m"
    strategy_type: str
    params: Dict[str, Any] = Field(default_factory=dict)
    risk_params: RiskParams = Field(default_factory=RiskParams)
    extra_config: Dict[str, Any] = Field(default_factory=dict)
    is_active: bool = True
    created_at: str = ""
    updated_at: str = ""


# ── Signal ────────────────────────────────────────────────────────────

class Signal(BaseModel):
    """A trading signal produced by a strategy.

    The signal represents an intent to trade. It goes through risk checks
    before being translated to an actual order.
    """
    strategy_id: str
    symbol: str
    direction: Direction
    price: Optional[float] = None       # 期望价格, None = 市价
    qty: int = 1
    confidence: float = 1.0             # 0.0 ~ 1.0
    strength: SignalStrength = SignalStrength.MEDIUM
    reason: str = ""                     # 信号原因描述
    indicator_values: Dict[str, Any] = Field(default_factory=dict)  # 触发时的指标值快照
    ts_ns: int = Field(default_factory=lambda: int(datetime.now().timestamp() * 1e9))


# ── Trade / Order Log ─────────────────────────────────────────────────

class TradeLogCreate(BaseModel):
    """Record of a trade attempt (before execution)."""
    strategy_id: str
    signal_id: Optional[int] = None
    symbol: str
    direction: Direction
    qty: int
    price: Optional[float] = None
    order_type: str = "MARKET"
    risk_check: Dict[str, Any] = Field(default_factory=dict)


class TradeLog(BaseModel):
    """Persisted trade record."""
    id: int
    strategy_id: str
    signal_id: Optional[int] = None
    symbol: str
    direction: str
    qty: int
    price: Optional[str] = None
    order_type: str = "MARKET"
    order_plan_id: Optional[str] = None
    execution_id: Optional[str] = None
    execution_status: Optional[str] = None
    risk_check: str = "{}"
    pnl: Optional[str] = None
    ts_ns: int
    created_at: str = ""


# ── API Response Models ───────────────────────────────────────────────

class StrategyStatus(BaseModel):
    """Current status of a running strategy."""
    strategy_id: str
    name: str
    symbol: str
    is_running: bool
    last_signal: Optional[Signal] = None
    last_trade_at: Optional[str] = None
    current_position: int = 0
    active_since: Optional[str] = None
    total_signals: int = 0
    total_trades: int = 0


class EngineStatus(BaseModel):
    """Overall engine status."""
    is_running: bool
    active_strategies: int = 0
    strategies: List[StrategyStatus] = Field(default_factory=list)
    last_poll_ts: Optional[str] = None
    uptime_seconds: Optional[float] = None
