"""Configuration loader — YAML + dataclass (same pattern as futures_demo & execution_layer).

Loads config.yaml from project root. Provides typed access to all settings.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8004


@dataclass
class DataSourceConfig:
    base_url: str = "http://127.0.0.1:8000"
    request_timeout_sec: float = 30.0


@dataclass
class ExecutionLayerConfig:
    base_url: str = "http://127.0.0.1:8003"
    request_timeout_sec: float = 30.0
    default_context_id: str = "default"
    default_scenario_id: str = ""


@dataclass
class StrategyConfig:
    poll_interval_seconds: int = 60
    trading_hours_only: bool = True
    max_position_per_symbol: int = 10


@dataclass
class StorageConfig:
    type: str = "sqlite"
    path: str = "./data/strategy_layer.db"


@dataclass
class AppConfig:
    server: ServerConfig = field(default_factory=ServerConfig)
    data_source: DataSourceConfig = field(default_factory=DataSourceConfig)
    execution_layer: ExecutionLayerConfig = field(default_factory=ExecutionLayerConfig)
    strategy: StrategyConfig = field(default_factory=StrategyConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


# Module-level global cache (same pattern as futures_demo & execution_layer)
_config: Optional[AppConfig] = None


def load_config(path: str = "config.yaml") -> AppConfig:
    """Load config from YAML file. Caches globally after first load."""
    global _config
    if _config is not None:
        return _config

    cfg_path = Path(path)
    if not cfg_path.exists():
        cfg_path = Path(__file__).resolve().parent.parent / path
    if not cfg_path.exists():
        raise FileNotFoundError(f"Config not found: {path}")

    with open(cfg_path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    server_raw = raw.get("server", {})
    data_source_raw = raw.get("data_source", {})
    exec_raw = raw.get("execution_layer", {})
    strategy_raw = raw.get("strategy", {})
    storage_raw = raw.get("storage", {})

    _config = AppConfig(
        server=ServerConfig(
            host=server_raw.get("host", "127.0.0.1"),
            port=server_raw.get("port", 8004),
        ),
        data_source=DataSourceConfig(
            base_url=data_source_raw.get("base_url", "http://127.0.0.1:8000"),
            request_timeout_sec=data_source_raw.get("request_timeout_sec", 30.0),
        ),
        execution_layer=ExecutionLayerConfig(
            base_url=exec_raw.get("base_url", "http://127.0.0.1:8003"),
            request_timeout_sec=exec_raw.get("request_timeout_sec", 30.0),
            default_context_id=exec_raw.get("default_context_id", "default"),
            default_scenario_id=exec_raw.get("default_scenario_id", ""),
        ),
        strategy=StrategyConfig(
            poll_interval_seconds=strategy_raw.get("poll_interval_seconds", 60),
            trading_hours_only=strategy_raw.get("trading_hours_only", True),
            max_position_per_symbol=strategy_raw.get("max_position_per_symbol", 10),
        ),
        storage=StorageConfig(
            type=storage_raw.get("type", "sqlite"),
            path=storage_raw.get("path", "./data/strategy_layer.db"),
        ),
    )
    return _config


def get_config() -> AppConfig:
    """Get cached config. Loads defaults if not yet loaded."""
    if _config is None:
        return load_config()
    return _config


def reload_config() -> AppConfig:
    """Force reload config (clear cache)."""
    global _config
    _config = None
    return load_config()
