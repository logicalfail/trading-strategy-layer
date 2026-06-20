"""Trading Strategy Layer — FastAPI Application Entry Point.

Provides:
  - Strategy CRUD (create/list/start/stop)
  - Engine management (start/stop/status)
  - Signal history query
  - Trade log query
  - Health check

Architecture:
  - FastAPI + uvicorn
  - Lifespan-based init (DB, engine)
  - Background polling loop (asyncio)
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_config, load_config
from .db.sqlite import init_db, get_conn
from .engine import EngineStatus, StrategyConfig, StrategyConfigCreate, engine
from .models import BacktestRunRequest, Direction, RiskParams, Signal, StrategyStatus
from .risk import risk_manager

log = logging.getLogger(__name__)

APP_VERSION = "0.1.0"

# Background polling task reference
_poll_task: Optional[asyncio.Task] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifecycle: init DB + start engine + background poll loop."""
    load_config()
    init_db()

    # Load engine with persisted strategies
    engine.start()

    # Start background polling loop
    global _poll_task
    _poll_task = asyncio.create_task(_poll_loop())

    log.info("Strategy Layer v%s started", APP_VERSION)
    yield

    # Shutdown
    if _poll_task:
        _poll_task.cancel()
    engine.stop()
    log.info("Strategy Layer shutting down")


app = FastAPI(
    title="Trading Strategy Layer",
    version=APP_VERSION,
    description="交易策略层 — 策略管理 / 信号生成 / 自动下单",
    lifespan=lifespan,
)


async def _poll_loop():
    """Background loop: poll engine at configured interval."""
    cfg = get_config()
    interval = cfg.strategy.poll_interval_seconds

    while True:
        try:
            await asyncio.sleep(interval)
            # Run poll in thread pool to avoid blocking the event loop
            loop = asyncio.get_event_loop()
            trades = await loop.run_in_executor(None, engine.poll_once)
            if trades > 0:
                log.info("Poll cycle dispatched %d trades", trades)
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.error("Poll loop error: %s", e, exc_info=True)


# ═══════════════════════════════════════════════════════════
# Health
# ═══════════════════════════════════════════════════════════

@app.get("/api/health")
async def health():
    """Health check with upstream service status. Checks run concurrently."""
    from .clients.data_client import get_data_client
    from .clients.exec_client import get_execution_client

    loop = asyncio.get_event_loop()
    data_ok, exec_ok = await asyncio.gather(
        loop.run_in_executor(None, get_data_client().check_health),
        loop.run_in_executor(None, get_execution_client().check_health),
    )

    all_ok = data_ok and exec_ok
    status = "ok" if all_ok else "degraded"
    return {
        "status": status,
        "version": APP_VERSION,
        "name": "strategy-layer",
        "data_source_connected": data_ok,
        "execution_layer_connected": exec_ok,
    }


# ═══════════════════════════════════════════════════════════
# Strategy CRUD
# ═══════════════════════════════════════════════════════════

@app.post("/api/strategies", status_code=201)
async def create_strategy(req: StrategyConfigCreate):
    """Create a new strategy configuration."""
    config = engine.create_strategy(req)
    return {"success": True, "strategy": config.model_dump(mode="json")}


@app.get("/api/strategies")
async def list_strategies(active_only: bool = Query(False)):
    """List all strategy configurations."""
    configs = engine.list_strategies(active_only=active_only)
    return {
        "strategies": [c.model_dump(mode="json") for c in configs],
        "count": len(configs),
    }


@app.get("/api/strategies/{strategy_id}")
async def get_strategy(strategy_id: str):
    """Get a single strategy configuration."""
    config = engine.get_strategy(strategy_id)
    if not config:
        return JSONResponse({"error": "策略不存在"}, status_code=404)
    return {"strategy": config.model_dump(mode="json")}


@app.delete("/api/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str):
    """Delete a strategy configuration."""
    ok = engine.delete_strategy(strategy_id)
    if not ok:
        return JSONResponse({"error": "策略不存在"}, status_code=404)
    return {"success": True, "message": "策略已删除"}


@app.patch("/api/strategies/{strategy_id}/params")
async def update_strategy_params(strategy_id: str, body: Dict[str, Any]):
    """Update strategy parameters (hot-reload)."""
    config = engine.update_strategy_params(strategy_id, body)
    if not config:
        return JSONResponse({"error": "策略不存在"}, status_code=404)
    return {"success": True, "strategy": config.model_dump(mode="json")}


# ═══════════════════════════════════════════════════════════
# Strategy Lifecycle (start/stop)
# ═══════════════════════════════════════════════════════════

@app.post("/api/strategies/{strategy_id}/start")
async def start_strategy(strategy_id: str):
    """Start a strategy."""
    ok = engine.start_strategy(strategy_id)
    if not ok:
        return JSONResponse({"error": "无法启动策略"}, status_code=400)
    return {"success": True, "message": "策略已启动"}


@app.post("/api/strategies/{strategy_id}/stop")
async def stop_strategy(strategy_id: str):
    """Stop a strategy."""
    ok = engine.stop_strategy(strategy_id)
    if not ok:
        return JSONResponse({"error": "策略未在运行"}, status_code=400)
    return {"success": True, "message": "策略已停止"}


# ═══════════════════════════════════════════════════════════
# Engine Status
# ═══════════════════════════════════════════════════════════

@app.get("/api/engine/status")
async def engine_status():
    """Get engine status with all strategy runners."""
    return engine.get_status().model_dump(mode="json")


@app.post("/api/engine/poll")
async def trigger_poll():
    """Manually trigger one poll cycle."""
    trades = engine.poll_once()
    return {"success": True, "trades_dispatched": trades}


# ═══════════════════════════════════════════════════════════
# Signal / Trade History
# ═══════════════════════════════════════════════════════════

@app.get("/api/signals")
async def list_signals(
    strategy_id: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List signals with optional filters."""
    where: List[str] = []
    params: List[Any] = []

    if strategy_id:
        where.append("strategy_id = ?")
        params.append(strategy_id)
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM signal_log{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    signals = [dict(r) for r in rows]
    for s in signals:
        if isinstance(s.get("indicator_values"), str):
            s["indicator_values"] = json.loads(s["indicator_values"])

    return {"signals": signals, "count": len(signals)}


@app.get("/api/trades")
async def list_trades(
    strategy_id: Optional[str] = Query(None),
    symbol: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """List trade log with optional filters."""
    where: List[str] = []
    params: List[Any] = []

    if strategy_id:
        where.append("strategy_id = ?")
        params.append(strategy_id)
    if symbol:
        where.append("symbol = ?")
        params.append(symbol)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT * FROM trade_log{where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()

    return {"trades": [dict(r) for r in rows], "count": len(rows)}


@app.get("/api/positions")
async def list_positions():
    """List current positions from cache."""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM position_cache ORDER BY symbol").fetchall()
    return {"positions": [dict(r) for r in rows]}


# ═══════════════════════════════════════════════════════════
# Backtest API
# ═══════════════════════════════════════════════════════════

@app.post("/api/backtest/run", status_code=201)
async def run_backtest(req: BacktestRunRequest):
    """Submit a backtest task. Runs async, returns task_id."""
    from .backtest import backtest_manager

    task_id = backtest_manager.submit(
        strategy_type=req.strategy_type,
        symbol=req.symbol,
        params=req.params,
        start_date=req.start_date,
        end_date=req.end_date,
        period=req.period,
    )
    return {"success": True, "task_id": task_id, "status": "pending"}


@app.get("/api/backtest/tasks")
async def backtest_tasks():
    """List all in-memory backtest tasks with status and parameters."""
    from .backtest import backtest_manager

    tasks = backtest_manager.list_tasks()
    return {
        "tasks": [
            {
                "task_id": t.task_id,
                "status": t.status.value,
                "strategy_type": t.strategy_type,
                "symbol": t.symbol,
                "params": t.params,
                "start_date": t.start_date,
                "end_date": t.end_date,
                "period": t.period,
                "created_at": t.created_at,
                "error": t.error,
            }
            for t in tasks
        ],
        "count": len(tasks),
    }


@app.get("/api/backtest/{task_id}/status")
async def backtest_status(task_id: str):
    """Get backtest task status."""
    from .backtest import backtest_manager

    info = backtest_manager.get_status(task_id)
    if not info:
        return JSONResponse({"error": "回测任务不存在"}, status_code=404)
    return {
        "task_id": info.task_id,
        "status": info.status.value,
        "strategy_type": info.strategy_type,
        "symbol": info.symbol,
        "start_date": info.start_date,
        "end_date": info.end_date,
        "created_at": info.created_at,
        "error": info.error,
    }


@app.get("/api/backtest/{task_id}/result")
async def backtest_result(task_id: str):
    """Get backtest result (only available when status=done)."""
    from .backtest import backtest_manager

    info = backtest_manager.get_status(task_id)
    if not info:
        return JSONResponse({"error": "回测任务不存在"}, status_code=404)
    if info.status.value == "running":
        return {"status": "running", "message": "回测执行中, 请稍后查看"}
    if info.status.value == "error":
        return {"status": "error", "error": info.error}
    if info.status.value == "pending":
        return {"status": "pending", "message": "回测任务排队中"}
    if info.status.value == "cancelled":
        return {"status": "cancelled", "message": "回测已取消"}
    result = info.result
    if not result:
        return JSONResponse({"error": "回测结果不可用"}, status_code=500)
    return {
        "status": "done",
        "result": result.to_dict(),
    }


@app.post("/api/backtest/{task_id}/cancel")
async def cancel_backtest(task_id: str):
    """Cancel a running/pending backtest task."""
    from .backtest import backtest_manager

    ok = backtest_manager.cancel(task_id)
    if not ok:
        return JSONResponse({"error": "无法取消: 任务不存在或已结束"}, status_code=400)
    return {"success": True, "task_id": task_id, "status": "cancelled"}


@app.post("/api/backtest/{task_id}/replay")
async def replay_backtest(task_id: str):
    """Re-run a completed/failed/cancelled backtest with same parameters."""
    from .backtest import backtest_manager

    new_task_id = backtest_manager.replay(task_id)
    if not new_task_id:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return {"success": True, "task_id": new_task_id, "status": "pending"}


@app.delete("/api/backtest/{task_id}")
async def delete_backtest(task_id: str):
    """Delete a backtest task (cancel if running, then remove)."""
    from .backtest import backtest_manager

    ok = backtest_manager.delete(task_id)
    if not ok:
        return JSONResponse({"error": "任务不存在"}, status_code=404)
    return {"success": True, "message": "任务已删除"}


@app.get("/api/backtest/history")
async def backtest_history():
    """List backtest history from DB."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, task_id, strategy_name, symbol, period, start_date, end_date, "
            "params, total_trades, win_trades, total_pnl, max_drawdown, sharpe_ratio, win_rate, "
            "trades_json, created_at "
            "FROM backtest_results ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
    return {"results": [dict(r) for r in rows], "count": len(rows)}


@app.delete("/api/backtest/history/{history_id}")
async def delete_backtest_history(history_id: int):
    """Delete a backtest history record from DB."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM backtest_results WHERE id = ?", (history_id,))
        if cur.rowcount == 0:
            return JSONResponse({"error": "回测记录不存在"}, status_code=404)
    return {"success": True, "message": "记录已删除"}


# ═══════════════════════════════════════════════════════════
# Frontend static files (debug page)
# ═══════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT / "frontend"

@app.get("/")
async def serve_debug():
    """Serve the debug frontend page."""
    debug_html = FRONTEND_DIST / "debug.html"
    if debug_html.exists():
        return FileResponse(str(debug_html))
    return {"message": "Frontend not found. Run: cd frontend && ... (no build required)"}

@app.get("/{full_path:path}")
async def spa_fallback(full_path: str):
    """SPA fallback for non-API routes."""
    if full_path.startswith("api/") or full_path.startswith("docs") or full_path.startswith("openapi"):
        return JSONResponse({"error": "not found"}, status_code=404)
    # Try serving static files
    static_file = FRONTEND_DIST / full_path
    if static_file.exists() and static_file.is_file():
        return FileResponse(str(static_file))
    # Fallback to debug.html
    debug_html = FRONTEND_DIST / "debug.html"
    if debug_html.exists():
        return FileResponse(str(debug_html))
    return JSONResponse({"error": "not found"}, status_code=404)


# ═══════════════════════════════════════════════════════════
# Error handler
# ═══════════════════════════════════════════════════════════

@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    log.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"error": f"Internal server error: {exc}"},
    )
