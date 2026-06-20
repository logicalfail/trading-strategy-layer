# 交易策略层 — 设计文档

> **项目代号**: `trading-strategy-layer`
> **目标路径**: `C:\trading_strategy_layer`
> **状态**: v0.1.0 — 骨架完成, 已实现 MA Cross + RSI 策略
> **更新日期**: 2026-06-14

---

## 1. 项目目标与范围

### 1.1 核心目标

构造交易系统的**策略层**（Strategy Layer），作为行情数据与下单执行之间的决策引擎：

- **行情消费**：从 futures_demo 获取实时/历史 K 线数据
- **技术分析**：计算常用指标（MA、MACD、RSI、布林带等）
- **信号生成**：根据策略规则产生买卖信号
- **风控过滤**：仓位上限、冷却期、价格合理性检查
- **订单转换**：将信号转为执行层的下单请求
- **绩效评估**：回测引擎评估策略表现

### 1.2 系统定位

```
┌──────────────────────────────────────────────────────────┐
│   Futures Demo (行情数据服务)                              │
│   - AKShare/Sina 分钟K线                                  │
│   - SQLite/TimescaleDB 存储                               │
│   - REST API / WebSocket 推送                             │
└─────────────────────┬────────────────────────────────────┘
                      │  GET /api/v1/bars
                      ▼
┌──────────────────────────────────────────────────────────┐
│   策略层 (Strategy Layer)                                  │
│                                                           │
│  ┌────────────┐  ┌──────────┐  ┌──────────────────────┐  │
│  │ 指标计算     │  │ 信号生成  │  │ 风控检查              │  │
│  │ SMA/MACD   │→│ 金叉/死叉 │→│ 仓位/冷却/价格         │  │
│  │ RSI/KDJ/BB │  │ 超买/卖  │  │                       │  │
│  └────────────┘  └────┬─────┘  └──────────┬───────────┘  │
│                       │                   │               │
│  ┌────────────────────▼───────────────────▼────────────┐  │
│  │  订单转换器 → HTTP POST →                              │  │
│  └────────────────────┬─────────────────────────────────┘  │
└───────────────────────┼─────────────────────────────────────┘
                        │  POST /api/orders
                        ▼
┌──────────────────────────────────────────────────────────┐
│   执行层 (Execution Layer)                                 │
│   - PreHook (余额/风控) → ScenarioBroker → PostHook       │
│   - Mouse Replicator → 券商终端 UI                        │
└──────────────────────────────────────────────────────────┘
```

### 1.3 范围

| 范围 | 包含 | 不包含 |
|------|------|--------|
| 策略定义 | 策略参数、品种、周期、指标配置 | 策略代码的热加载/远程部署 |
| 指标计算 | SMA, EMA, MACD, RSI, Bollinger, KDJ, ATR | 机器学习/深度学习模型 |
| 信号生成 | 规则引擎（均线交叉/阈值突破） | 强化学习、遗传算法优化 |
| 回测 | 历史 Bar 回放, PnL, Sharpe, Max Drawdown | 逐笔Tick级回测、滑点模型 |
| 风控 | 单品种仓位上限、冷却期、短仓控制 | 组合VaR、希腊字母风控 |
| 实盘 | 通过execution_layer下单 | 直连券商API |

---

## 2. 系统架构

### 2.1 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│  API Layer (FastAPI REST)                                    │
│  ┌──────────┐ ┌──────────┐ ┌────────┐ ┌──────────────────┐  │
│  │Strategy  │ │Engine    │ │Signal  │ │Health            │  │
│  │CRUD      │ │Start/Stop│ │Trade   │ │                  │  │
│  │/api/     │ │/api/     │ │History │ │/api/             │  │
│  │strategies│ │engine/   │ │/api/   │ │health            │  │
│  │          │ │poll      │ │signals │ │                  │  │
│  └────┬─────┘ └────┬─────┘ └───┬────┘ └──────────────────┘  │
└───────┼────────────┼───────────┼─────────────────────────────┘
        │            │           │
┌───────▼────────────▼───────────▼─────────────────────────────┐
│  Engine Layer (策略引擎)                                       │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │ StrategyEngine                                            │ │
│  │  - poll_once() → 遍历所有活跃策略                         │ │
│  │  - 管理 StrategyRunner 生命周期 (start/stop)              │ │
│  │  - 后台异步轮询 (asyncio loop)                            │ │
│  └────────────────┬────────────────────────────────────────┘ │
│                   │                                           │
│  ┌────────────────▼────────────────────────────────────────┐ │
│  │ StrategyRunner (策略运行时实例)                            │ │
│  │  - 持有 StrategyConfig                                   │ │
│  │  - 调用 generate_signals()                                │ │
│  │  - 记录 last_bar_ts (避免重复处理)                        │ │
│  └─────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘
        │               │               │
┌───────▼───────────────▼───────────────▼──────────────────────┐
│  Service Layer (业务模块)                                      │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────┐  │
│  │Risk     │ │Signal   │ │Position │ │Backtest       │  │
│  │Manager  │ │Gen +    │ │Cache    │ │Engine         │  │
│  │(风控检查) │ │Translator│ │(持仓跟踪)│ │(回测评估)      │  │
│  └──────────┘ └──────────┘ └──────────┘ └────────────────┘  │
└──────────────────────────────────────────────────────────────┘
        │               │
┌───────▼───────────────▼──────────────────────────────────────┐
│  Client Layer (外部系统 HTTP 客户端)                           │
│  ┌────────────────────┐ ┌────────────────────────────────┐  │
│  │ DataClient          │ │ ExecutionClient               │  │
│  │ → futures_demo API  │ │ → execution_layer API         │  │
│  │ GET /api/v1/bars    │ │ POST /api/orders              │  │
│  │ GET /api/kline      │ │ GET /api/accounts             │  │
│  └────────────────────┘ └────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
        │
┌───────▼──────────────────────────────────────────────────────┐
│  Storage Layer (SQLite WAL)                                   │
│  - strategy_configs  /  signal_log  /  trade_log              │
│  - position_cache    /  backtest_results                      │
└──────────────────────────────────────────────────────────────┘
```

### 2.2 进程模型

| 进程 | 角色 | 说明 |
|------|------|------|
| **Strategy Layer Server** | 策略引擎 | FastAPI + uvicorn, 常驻, 后台轮询 |
| **Futures Demo** | 行情数据 | 独立进程, HTTP 调用 |
| **Execution Layer** | 下单执行 | 独立进程, HTTP 调用 |

### 2.3 轮询机制

```
background asyncio task:
  while True:
    await sleep(poll_interval_seconds)   ← 默认60s
    for each active strategy:
      fetch latest bars (n=200)
      if new bar:
        run strategy logic
        if signal:
          run risk checks
          if passed:
            translate signal → order
            POST to execution_layer
            log trade
```

### 2.4 状态管理

```
策略状态:  DB(is_active) → Engine Runner(is_running)
  - 启动:  创建 Config → POST /start → StrategyRunner.start()
  - 停止:  POST /stop → StrategyRunner.stop()
  - 删除:  DELETE → stop if running → remove from DB

信号流向:  BarData → Strategy → Signal → RiskCheck → OrderPlan
  - Signal 总是被持久化到 signal_log
  - TradeLog 在dispatch前后更新 execution_status
```

---

## 3. 技术选型

| 项目 | 选择 | 理由 |
|------|------|------|
| 后端 | Python 3.11+ / FastAPI | 与 futures_demo + execution_layer 统一 |
| 存储 | SQLite (WAL) | 单机部署, 零依赖, 与上下游统一 |
| 配置 | YAML + dataclass | 与 futures_demo + execution_layer 统一 |
| HTTP 客户端 | httpx | 与 execution_layer 统一 |
| 指标计算 | numpy + pandas | 向量化计算, 性能足够 |
| 启动方式 | run.bat | 与 mouse_replicator 统一 |

### 关键依赖

```
fastapi>=0.111.0
uvicorn[standard]>=0.30.0
pydantic>=2.0.0
httpx>=0.27.0
pyyaml>=6.0
numpy>=1.24.0
pandas>=2.0.0
loguru>=0.7.0
```

---

## 4. 核心数据模型

### 4.1 StrategyConfig (策略配置)

```json
{
  "id": "uuid",
  "name": "RB MA Cross",
  "symbol": "RB2609.SHFE",
  "period": "1m",
  "strategy_type": "ma_cross",
  "params": {"fast_ma": 5, "slow_ma": 20, "ma_type": "SMA"},
  "risk_params": {"max_position_qty": 10, "cooldown_minutes": 30},
  "is_active": true
}
```

### 4.2 Signal (信号)

```json
{
  "strategy_id": "uuid",
  "symbol": "RB2609.SHFE",
  "direction": "BUY",
  "price": 3500.0,
  "qty": 1,
  "confidence": 0.85,
  "reason": "金叉: SMA(5)=3505 上穿 SMA(20)=3498",
  "indicator_values": {"fast_ma": 3505, "slow_ma": 3498, "close": 3500}
}
```

### 4.3 TradeLog (交易记录)

```json
{
  "id": 1,
  "strategy_id": "uuid",
  "symbol": "RB2609.SHFE",
  "direction": "BUY",
  "qty": 1,
  "order_plan_id": "exec-plan-uuid",
  "execution_id": "exec-record-uuid",
  "execution_status": "CONFIRMED"
}
```

---

## 5. 数据库 Schema

```sql
-- 策略配置
CREATE TABLE strategy_configs (
    id TEXT PRIMARY KEY, name TEXT, symbol TEXT, period TEXT,
    strategy_type TEXT, params TEXT, risk_params TEXT,
    is_active INTEGER DEFAULT 1, created_at TEXT, updated_at TEXT
);

-- 信号日志
CREATE TABLE signal_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT, symbol TEXT, direction TEXT,
    price TEXT, qty INTEGER, confidence REAL,
    reason TEXT, indicator_values TEXT, ts_ns INTEGER
);

-- 交易日志
CREATE TABLE trade_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id TEXT, symbol TEXT, direction TEXT, qty INTEGER,
    order_plan_id TEXT, execution_id TEXT, execution_status TEXT,
    ts_ns INTEGER, created_at TEXT
);

-- 持仓缓存
CREATE TABLE position_cache (
    symbol TEXT PRIMARY KEY, direction TEXT, qty INTEGER, updated_at TEXT
);

-- 回测结果
CREATE TABLE backtest_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_name TEXT, symbol TEXT, period TEXT,
    total_trades INTEGER, win_trades INTEGER,
    total_pnl TEXT, max_drawdown REAL, sharpe_ratio REAL, win_rate REAL
);
```

---

## 6. 预留接口

| 占位 | 说明 |
|------|------|
| WebSocket 数据订阅 | 当前用轮询, 可改为 futures_demo WS 实时推送 |
| 更多策略类型 | 已有 ma_cross + rsi_reversal, 可扩展 |
| 策略组合 | 多信号加权投票 |
| 自动参数优化 | 网格搜索 + 回测评估 |
| Telegram/微信通知 | 信号/成交推送 |
