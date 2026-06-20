# 设计决策记录

> 记录策略层关键设计决策的背景、选项和理由。

## 2026-06-14 — 初始架构

### D1: 策略层放在独立项目，不合并到 futures_demo

- **背景**: futures_demo 已有行情数据+API，execution_layer 已有下单能力
- **选项**:
  - A: 放在 futures_demo 子目录
  - B: 独立项目 `trading_strategy_layer`
- **决策**: B — 独立项目
- **理由**: 独立演进, 不增加 futures_demo 复杂度, 可独立部署和测试

### D2: 用 HTTP API 获取数据，不直连 DB

- **背景**: 策略层需要行情数据
- **选项**:
  - A: 直连 futures_demo 的 SQLite DB
  - B: 通过 futures_demo REST API
- **决策**: B — HTTP API
- **理由**: 解耦, 复用 futures_demo 的主力合约解析(dominant.py)和K线聚合(aggregation.py), 不重复造轮子

### D3: 轮询模式而非事件驱动

- **背景**: 如何触发策略计算
- **选项**:
  - A: 轮询 — 定时检查新 Bar
  - B: WebSocket 订阅 — futures_demo 推送新数据
- **决策**: A (可升级到 B)
- **理由**: 初期简单可靠, 不依赖 WS 连接稳定性; 后续可升级为 WS 事件驱动

### D4: 策略逻辑用 Python 代码，参数用 YAML/JSON 配置

- **背景**: 如何定义和配置策略
- **选项**:
  - A: 纯 YAML 配置 (包括规则)
  - B: 纯 Python 代码
  - C: Python 策略基类 + YAML 参数
- **决策**: C — 策略实现是 Python 代码, 策略参数是 JSON 配置
- **理由**: 代码表达复杂规则灵活, 配置热加载方便调参, 同一个策略代码可复用不同参数

### D5: 策略引擎直接 import 策略模块

- **背景**: 如何发现和加载策略
- **选项**:
  - A: 插件式加载 (setuptools entry_points)
  - B: 策略注册表 dict
  - C: 直接 import
- **决策**: 现阶段用 C (直接 import), 预留 B 的扩展方式
- **理由**: 当前策略少, 简单直接; 策略增多后可改为注册表

### D6: 风控作为独立的检查层

- **背景**: 风控逻辑放在哪里
- **选项**:
  - A: 在每个策略内部自行检查
  - B: 独立 RiskManager, 信号产生后统一检查
- **决策**: B — 独立 RiskManager
- **理由**: 风控规则跨策略统一, 不依赖策略实现者的自觉性; 方便审计和调整
