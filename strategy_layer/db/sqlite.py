"""SQLite connection & schema management (same pattern as execution_layer).

- Single file DB: data/strategy_layer.db
- WAL mode: concurrent reads, single writer
- Short-lived connections: per-operation
- init_db() runs at startup (idempotent)
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "strategy_layer.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS strategy_configs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    symbol          TEXT NOT NULL,
    period          TEXT NOT NULL DEFAULT '1m',
    strategy_type   TEXT NOT NULL,
    params          TEXT NOT NULL DEFAULT '{}',
    risk_params     TEXT NOT NULL DEFAULT '{}',
    extra_config    TEXT NOT NULL DEFAULT '{}',
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_active ON strategy_configs(is_active);

CREATE TABLE IF NOT EXISTS signal_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    price           TEXT,
    qty             INTEGER NOT NULL DEFAULT 0,
    confidence      REAL NOT NULL DEFAULT 1.0,
    reason          TEXT NOT NULL DEFAULT '',
    indicator_values TEXT NOT NULL DEFAULT '{}',
    ts_ns           INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_configs(id)
);

CREATE INDEX IF NOT EXISTS idx_signal_strategy ON signal_log(strategy_id, created_at DESC);

CREATE TABLE IF NOT EXISTS trade_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT NOT NULL,
    signal_id       INTEGER,
    symbol          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    qty             INTEGER NOT NULL,
    price           TEXT,
    order_type      TEXT NOT NULL DEFAULT 'MARKET',
    order_plan_id   TEXT,
    execution_id    TEXT,
    execution_status TEXT,
    risk_check      TEXT NOT NULL DEFAULT '{}',
    pnl             TEXT,
    ts_ns           INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_configs(id),
    FOREIGN KEY (signal_id) REFERENCES signal_log(id)
);

CREATE INDEX IF NOT EXISTS idx_trade_strategy ON trade_log(strategy_id, created_at DESC);

CREATE TABLE IF NOT EXISTS position_cache (
    symbol          TEXT PRIMARY KEY,
    direction       TEXT NOT NULL DEFAULT 'LONG',
    qty             INTEGER NOT NULL DEFAULT 0,
    avg_price       TEXT,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS backtest_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy_id     TEXT,
    strategy_name   TEXT NOT NULL,
    symbol          TEXT NOT NULL,
    period          TEXT NOT NULL DEFAULT '1m',
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    params          TEXT NOT NULL DEFAULT '{}',
    total_trades    INTEGER NOT NULL DEFAULT 0,
    win_trades      INTEGER NOT NULL DEFAULT 0,
    total_pnl       TEXT NOT NULL DEFAULT '0',
    max_drawdown    REAL NOT NULL DEFAULT 0.0,
    sharpe_ratio    REAL,
    win_rate        REAL,
    trades_json     TEXT NOT NULL DEFAULT '[]',
    created_at      TEXT NOT NULL
);
"""


def _migrate(conn) -> None:
    """Incremental column migrations for existing tables."""
    # v1: add task_id to backtest_results (for cross-system task tracking)
    try:
        conn.execute("ALTER TABLE backtest_results ADD COLUMN task_id TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists


def init_db() -> None:
    """Initialize database: create dir, create tables (idempotent)."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(SCHEMA_SQL)
        _migrate(conn)
    log.info("Database initialized at %s", DB_PATH)


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Get a short-lived SQLite connection.

    - WAL mode for concurrent reads
    - Foreign keys enabled
    - row_factory = Row for dict-like access
    - Commits on success, rollback on exception
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
