# Trading Strategy Layer — AGENTS.md

> Compact guidance for OpenCode agents working in this repo.
> Last verified: 2026-06-19

## Quick start

```powershell
scripts\install.bat          # pip install -r requirements.txt
scripts\run.bat              # foreground (uvicorn, port from config.yaml)
scripts\run_bg.bat           # background, health-checked, logs → server.log/err
```

**PYTHONPATH must include project root** — both `run.bat` and `run_bg.bat` set `PYTHONPATH=%PYTHONPATH%;%ROOT%`. If launching uvicorn manually:

```powershell
$env:PYTHONPATH='C:\trading_strategy_layer'
python -m uvicorn strategy_layer.main:app --host 127.0.0.1 --port 8004
```

Both `run.bat` and `run_bg.bat` read the port from [config.yaml](file:///C:/trading_strategy_layer/config.yaml) via [scripts/get_port.py](file:///C:/trading_strategy_layer/scripts/get_port.py) and **automatically kill any existing uvicorn** on that port before starting. `run_bg.bat` polls `/api/health` for up to 15s after launch and tails the last 20 lines of `server.err` on failure.

**No `.git` directory exists** — this is not a git repo. No version control history.

## Architecture

Three-layer system:

| Layer | Port | Role |
|---|---|---|
| **this (strategy_layer)** | 8004 | Polls data, runs strategies, dispatches via API |
| [futures_demo](file:///C:/futures_demo) | 7999 | Market data source (HTTP `/api/v1/bars`, `/api/kline`) |
| [execution_layer](file:///C:/trading_execution_layer) | 8003 | Order execution (POST `/api/orders`) |

All ports configurable in [config.yaml](file:///C:/trading_strategy_layer/config.yaml).

## Package structure

```
strategy_layer/
├── main.py            # FastAPI app, lifespan init, endpoints, background poll loop
├── config.py          # YAML → dataclass loader (singleton, same pattern as upstream)
├── models.py          # Pydantic v2 models: BarData, Signal, StrategyConfig, etc.
├── engine.py          # StrategyEngine singleton — core poll/dispatch loop
├── indicators.py      # Pure-function technical indicators (SMA/EMA/MACD/RSI/BB/KDJ/ATR)
├── risk.py            # RiskManager singleton — position/cooldown/price sanity checks
├── translator.py      # Signal → execution_layer OrderPlanCreate payload
├── backtest.py        # Bar replay PnL/Sharpe/MDD + BacktestTaskManager (async)
├── clients/
│   ├── data_client.py # httpx client → futures_demo (singleton)
│   └── exec_client.py # httpx client → execution_layer (singleton)
├── db/
│   └── sqlite.py      # SQLite WAL, short-lived connections, 5 tables
└── strategies/
    ├── ma_cross.py     # Dual MA crossover
    └── rsi_reversal.py # RSI threshold reversal
```

## Patterns & conventions

### Config loading
YAML + dataclass (same as upstream projects). Call `load_config()` once (auto-cached), then `get_config()` for reads. `config.AppConfig` holds all typed sections. See [config.py](file:///C:/trading_strategy_layer/strategy_layer/config.py).

### All singletons (module-level)
- `engine = StrategyEngine()` — [engine.py](file:///C:/trading_strategy_layer/strategy_layer/engine.py#L452)
- `risk_manager = RiskManager()` — [risk.py](file:///C:/trading_strategy_layer/strategy_layer/risk.py#L192)
- `get_data_client()` / `get_execution_client()` — [data_client.py](file:///C:/trading_strategy_layer/strategy_layer/clients/data_client.py#L125) / [exec_client.py](file:///C:/trading_strategy_layer/strategy_layer/clients/exec_client.py#L116)
- `get_config()` / `load_config()` — [config.py](file:///C:/trading_strategy_layer/strategy_layer/config.py#L60)

Always use the getter/singleton — never instantiate clients directly.

### Database
- SQLite WAL mode, single file `data/strategy_layer.db`
- Short-lived connection per operation via `get_conn()` context manager
  - **Commits on success, rollback on exception**, auto-closes on exit
- `init_db()` called once at FastAPI startup (idempotent, creates 5 tables)
- Prices stored as `TEXT` columns (not REAL), JSON serialized for nested data
- `indicator_values` in `signal_log` is stored as JSON `TEXT`, serialized via `json.dumps` at write, deserialized via `json.loads` at read (see [main.py](file:///C:/trading_strategy_layer/strategy_layer/main.py#L235-L237) `list_signals`)
- Schema has 5 tables: `strategy_configs`, `signal_log`, `trade_log`, `position_cache`, `backtest_results`
- `_migrate()` in [sqlite.py](file:///C:/trading_strategy_layer/strategy_layer/db/sqlite.py#L107-L109) is a **no-op placeholder** — add actual column migrations there
- See [sqlite.py](file:///C:/trading_strategy_layer/strategy_layer/db/sqlite.py)

### Lifespan initialization order (in [main.py](file:///C:/trading_strategy_layer/strategy_layer/main.py#L44-L63))
```python
load_config()      # 1. Load YAML into typed dataclass
init_db()          # 2. Create DB dir + tables (idempotent)
engine.start()     # 3. Load active strategies from DB → start runners
asyncio.create_task(_poll_loop())  # 4. Start background polling
```

### Strategy dispatch
Direct `import` + if/elif in [engine.py:_run_strategy_logic()](file:///C:/trading_strategy_layer/strategy_layer/engine.py#L337-L354). Not plugin-based. Add new strategies by:
1. Create `strategies/new_one.py` with `generate_signals()` function
2. Add an `elif` branch in `_run_strategy_logic()`
3. Add corresponding branch in `backtest._generate_signal()` (mandatory — backtest duplicates logic)

### Backtest duplicates strategy logic
[backtest.py](file:///C:/trading_strategy_layer/strategy_layer/backtest.py#L325-L338) re-implements signal generation (`_ma_cross_signal`, `_rsi_reversal_signal`) instead of importing from `strategies/`. **Both copies must be kept in sync** when adding strategy types.

**Backtest is NOT fully offline** — it calls `data_client.get_bars()` to fetch historical data from futures_demo. The upstream service must be running for backtests to work.

### Polling architecture
Background `asyncio` task in [main.py](file:///C:/trading_strategy_layer/strategy_layer/main.py#L74-L91) calls `engine.poll_once()` via `run_in_executor` (blocking I/O). Default interval: 60s (configurable at `strategy.poll_interval_seconds`).

### Frontend
Static SPA at [frontend/debug.html](file:///C:/trading_strategy_layer/frontend/debug.html). No build step — served directly by FastAPI. Fallback catch-all route at `/{full_path:path}` serves debug.html for any non-API path (after checking `api/`, `docs`, `openapi` prefix exclusion and static file existence).

**Tab navigation**: Two tabs — 📊 仪表盘 (Dashboard) and 🔄 回测 (Backtest). Tab state managed by CSS class `tab-content.active`. Switching to backtest tab triggers `loadBacktestTaskList()`.

### Backtest system
Asynchronous backtest engine with task-based execution:

- `BacktestTaskManager` — module-level singleton in [backtest.py](file:///C:/trading_strategy_layer/strategy_layer/backtest.py#L543). Uses `threading.Lock` for thread safety + `asyncio.get_event_loop().run_in_executor()` for non-blocking execution.
- **Task lifecycle**: `PENDING → RUNNING → DONE / ERROR / CANCELLED`
- **Cancellation**: `threading.Event` checked each bar in `run_backtest()` loop. Cancelled tasks return partial results (closed trades up to cancellation point).
- **Replay**: `replay(task_id)` copies params from existing task and submits a new one.
- **Delete**: `delete(task_id)` cancels if running, then removes from in-memory dict.

API endpoints (all in [main.py](file:///C:/trading_strategy_layer/strategy_layer/main.py#L279-L398)):

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/backtest/run` | Submit backtest → returns `task_id` |
| `GET` | `/api/backtest/tasks` | List in-memory tasks with status + params |
| `GET` | `/api/backtest/{id}/status` | Task status query |
| `GET` | `/api/backtest/{id}/result` | Full result (metrics + trades + price series) |
| `POST` | `/api/backtest/{id}/cancel` | Cancel running/pending task |
| `POST` | `/api/backtest/{id}/replay` | Re-run with same params → new task_id |
| `DELETE` | `/api/backtest/{id}` | Delete task (cancel first if running) |
| `GET` | `/api/backtest/history` | Persisted results from `backtest_results` table |

**Frontend task list** (in backtest tab): Table with columns status/strategy/symbol/range/params/time/actions. Buttons per status:
- `running` → ■ 停止 + 🗑
- `done` → 查看 + ⟳ 重放 + 🗑
- `error` → ⟳ 重试 + 🗑
- `cancelled` → ▶ 继续 + 🗑
- `pending` → ✕ 取消 + 🗑

Auto-refresh: 3s poll via `loadBacktestTaskList()`, re-renders only when state changes.

### Position direction mapping
`RiskManager.update_position()` stores direction as **"LONG"/"SHORT"** (not "BUY"/"SELL") in the `position_cache` table. Mapping:
- `net_qty >= 0` → direction `"LONG"`, stores `abs(qty)`
- `net_qty < 0` → direction `"SHORT"`, stores `abs(qty)`
This is read back via `get_position()` which returns signed int (positive=LONG, negative=SHORT).

### Model serialization
All Pydantic v2 models use `model_dump(mode="json")` for API responses. Never use `.dict()` or `.json()` from Pydantic v1.

## Git-tracked artifacts
- `.gitignore` excludes: `__pycache__/`, `*.pyc`, `*.pyo`, `*.db`, `data/`, `.env`, `server.log`, `server.err`
- Database file `data/strategy_layer.db` is gitignored (re-created on startup via `init_db()`)
- `server.log` and `server.err` are gitignored but not auto-deleted

## Critical quirks

### Known bugs
- **`translator.py` ↔ `exec_client.py` contract mismatch** — `translator.translate_signal()` returns an `order_type` key in the payload dict. `exec_client.place_order(**order_payload)` does not accept an `order_type` keyword argument — this causes a `TypeError` at runtime. The `order_type` is reconstructed inside `place_order()` from the `price` parameter. Additionally, `translator` passes `direction` as a string (`signal.direction.value`) while `place_order()` expects a `Direction` enum with `.value` accessor, which would also error. These bugs haven't been hit because upstream services (futures_demo, execution_layer) are typically offline in dev. Fix both sides in tandem: strip `order_type` from translator output, or add it to `place_order()` signature.
- **`position_cache.avg_price` column is never written** — the `position_cache` table has an `avg_price TEXT` column defined in the schema, but `RiskManager.update_position()` never sets it. The column is always `NULL`.

### Configured but unimplemented
- **`trading_hours_only: true`** in [config.yaml](file:///C:/trading_strategy_layer/config.yaml) is parsed by `config.py` but **never referenced** in engine, main, or strategy code. Polling runs 24/7 regardless. To implement: check current time against exchange trading hours before fetching bars.
- **`loguru>=0.7.0`** is in `requirements.txt` but the codebase uses `import logging` (stdlib) exclusively. `loguru` is dead weight — remove the dependency or adopt it.

### Fragile code to be aware of
- **`engine._log_trade()` signal_id linking** — uses `last_insert_rowid()` followed by `SELECT MAX(id) FROM signal_log WHERE strategy_id = ?` to find the just-inserted signal. This is racy under concurrent strategies (though SQLite serializes writes). The method also returns a dummy `type('TradeRow', (), {'id': trade_id})()` instead of a proper model or int.
- **Backtest signal functions duplicate strategy code** — `backtest._ma_cross_signal()` and `backtest._rsi_reversal_signal()` are independent re-implementations of `strategies/ma_cross.py` and `strategies/rsi_reversal.py`. **Any change to signal logic must be applied in both places.**
- **`Signal.ts_ns` default** — uses `datetime.now().timestamp() * 1e9` at model construction time, which is wall-clock time, not exchange/broker time.
- **`backtest.run_backtest_from_config()` previously used deprecated `datetime.utcnow()`** — fixed in 2026-06-19, now uses `datetime.now(timezone.utc)`.

### Environment & runtime
- **`$env:PYTHONPATH` must be set** — no `pyproject.toml` or `setup.py`, so editable install is impossible. Always use `PYTHONPATH` or the batch scripts.
- **Upstream services required** — `/api/health` checks both futures_demo and execution_layer. The app boots without them but logs connection errors every poll cycle.
- **Order dispatch is synchronous** — `engine.poll_once()` blocks on `exec_client.place_order()`. No retry, no queue, no async dispatch. A slow execution_layer blocks all strategies.
- **`server.err` for startup failures** — always check `server.err` first when service won't start. `run_bg.bat` tails the last 20 lines automatically.
- **`scripts/get_port.py`** reads port from `config.yaml`. Used by both `run.bat` and `run_bg.bat`. Standalone fallback: prints `8004` if config is missing.
- **`scripts/server.port` file is empty** — present in the scripts directory but unused by any script.

### Testing & tooling
- **No tests exist** — `tests/` directory does not exist. `pytest` is not in `requirements.txt`. Install it manually: `pip install pytest`.
- **No formatter/linter/typechecker config** — no `.pre-commit-config.yaml` or `pyproject.toml`. If touching code, add config (ruff + mypy preferred, matching upstream projects).
- **`__init__.py` files** — `strategy_layer/__init__.py` has a docstring; `clients/`, `db/`, `strategies/` `__init__.py` are empty.

### Coding conventions
- **Signals must cross a threshold** — strategies only fire when RSI or MA *crosses* the threshold (not while already beyond it). See [ma_cross.py](file:///C:/trading_strategy_layer/strategy_layer/strategies/ma_cross.py#L88) `prev_diff <= 0 < curr_diff`.
- **Position cache** — `RiskManager` holds an in-memory dict synced to DB. Not reloaded from execution_layer on restart (only via explicit `sync_from_execution()`).
- **`get_conn()` transaction behavior** — the context manager commits on success, rolls back on `Exception`, and always closes the connection. Use for any DB read or write.
