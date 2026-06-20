# Trading Strategy Layer

交易策略层 — 连接行情数据与下单执行的策略引擎。

## 系统定位

```
futures_demo (行情数据)             trading_execution_layer (下单执行层)
  ┌──────────────┐                  ┌──────────────────────┐
  │ /api/v1/bars │                  │ POST /api/orders     │
  │ /api/v1/dominant                │ GET /api/accounts    │
  │ WS /ws (实时推送)                │ GET /api/positions   │
  └──────┬───────┘                  └─────────┬────────────┘
         │                                    │
         ▼                                    ▼
  ┌────────────────────────────────────────────────────────┐
  │          策略层 (Strategy Layer) — 你在这里              │
  │                                                        │
  │  轮询行情 → 计算指标 → 产生信号 → 风控检查 → 调用下单    │
  └────────────────────────────────────────────────────────┘
```

## 项目结构

```
C:\trading_strategy_layer\
├── config.yaml                    # 全局配置
├── strategy_layer\
│   ├── main.py                    # FastAPI 入口 + REST API
│   ├── config.py                  # YAML + dataclass 配置加载器
│   ├── models.py                  # 数据模型 (Signal, StrategyConfig, TradeLog, BarData)
│   ├── engine.py                  # 策略引擎 (核心调度循环)
│   ├── indicators.py              # 技术指标库 (SMA, EMA, MACD, RSI, Bollinger, KDJ, ATR)
│   ├── risk.py                    # 风控模块 (仓位上限, 冷却期, 价格检查)
│   ├── translator.py              # 信号 → 订单转换器
│   ├── backtest.py                # 回测引擎 (历史Bar回放, PnL/Sharpe/MDD)
│   ├── strategies\
│   │   ├── ma_cross.py            # 双均线金叉死叉策略
│   │   └── rsi_reversal.py        # RSI 超买超卖反转策略
│   ├── clients\
│   │   ├── data_client.py         # HTTP 客户端 → futures_demo API
│   │   └── exec_client.py         # HTTP 客户端 → execution_layer API
│   └── db\
│       └── sqlite.py              # SQLite WAL 持久化 (6张表)
├── scripts\
│   ├── install.bat                # 安装依赖
│   └── run.bat                    # 启动服务
├── docs\
│   ├── DESIGN.md                  # 架构设计文档
│   └── DECISIONS.md               # 设计决策记录
└── requirements.txt
```

## 快速开始

### 前置依赖

需要先启动:
- [futures_demo](C:\futures_demo) — 行情数据服务 (端口 8000)
- [trading_execution_layer](C:\trading_execution_layer) — 下单执行层 (端口 8003)

### 启动

```bat
# 1. 安装依赖
scripts\install.bat

# 2. 启动服务 (默认 8004)
scripts\run.bat

# 3. 访问 API 文档
#    http://127.0.0.1:8004/docs
```

### 直接启动 (CMD/PowerShell)

```powershell
cd C:\trading_strategy_layer
$env:PYTHONPATH='C:\trading_strategy_layer'
python -m uvicorn strategy_layer.main:app --host 127.0.0.1 --port 8004
```

## API 概览

| Method | Path | 说明 |
|--------|------|------|
| `GET` | `/api/health` | 健康检查 (含上游服务状态) |
| `POST` | `/api/strategies` | 创建策略 |
| `GET` | `/api/strategies` | 策略列表 |
| `GET` | `/api/strategies/{id}` | 策略详情 |
| `DELETE` | `/api/strategies/{id}` | 删除策略 |
| `PATCH` | `/api/strategies/{id}/params` | 热更新策略参数 |
| `POST` | `/api/strategies/{id}/start` | 启动策略 |
| `POST` | `/api/strategies/{id}/stop` | 停止策略 |
| `GET` | `/api/engine/status` | 引擎状态 + 各策略运行状态 |
| `POST` | `/api/engine/poll` | 手动触发一次轮询 |
| `GET` | `/api/signals` | 信号历史 |
| `GET` | `/api/trades` | 交易历史 |
| `GET` | `/api/positions` | 当前持仓 |

## 内置策略

| 策略 | 文件 | 说明 |
|------|------|------|
| 双均线交叉 | `strategies/ma_cross.py` | 快慢均线金叉做多、死叉做空 |
| RSI 反转 | `strategies/rsi_reversal.py` | RSI 超买(>70)做空、超卖(<30)做多 |

### 创建双均线策略示例

```powershell
curl -X POST http://127.0.0.1:8004/api/strategies `
  -H "Content-Type: application/json" `
  -d '{
    "name": "RB MA Cross",
    "description": "螺纹钢5/20双均线",
    "symbol": "RB2609.SHFE",
    "period": "1m",
    "strategy_type": "ma_cross",
    "params": {
      "fast_ma": 5,
      "slow_ma": 20,
      "ma_type": "SMA"
    },
    "risk_params": {
      "max_position_qty": 10,
      "cooldown_minutes": 30
    }
  }'

# 启动
curl -X POST http://127.0.0.1:8004/api/strategies/{id}/start
```

## 回测

```python
from strategy_layer.backtest import run_backtest

result = run_backtest(
    strategy_type="ma_cross",
    symbol="RB2609.SHFE",
    params={"fast_ma": 5, "slow_ma": 20, "ma_type": "SMA"},
    start_date="2026-05-01",
    end_date="2026-06-13",
)
print(result.summary())
```

## 数据流

```
[每60s轮询]
     │
     ▼
data_client.get_latest_bars()  ───  futures_demo /api/v1/bars
     │
     ▼
策略逻辑 (ma_cross / rsi_reversal)
     │
     ▼
Signal(direction, price, qty, reason)
     │
     ▼
risk_manager.check_signal()  ───  仓位上限 / 冷却期 / 价格检查
     │
     ▼  pass
translate_signal()  ───  Signal → OrderPlanCreate
     │
     ▼
exec_client.place_order()  ───  execution_layer POST /api/orders
     │
     ▼
trade_log DB 记录
```

## 与上下游依赖的边界

| 项目 | 角色 | 通信方式 |
|------|------|---------|
| **Futures Demo** (8000) | 行情数据源 | HTTP (`/api/v1/bars`, `/api/kline`) |
| **Execution Layer** (8003) | 下单执行 + Hook链 | HTTP (`POST /api/orders`) |
| **Mouse Replicator** (8002) | 场景执行引擎 | 通过 execution_layer 间接调用 |
