"""ExecutionClient — HTTP client for trading_execution_layer API.

Provides:
  - place_order(symbol, direction, qty, price, scenario_id, context_id) → dict
  - get_account() → dict
  - get_positions() → list
  - check_health() → bool

Maps strategy signals to execution_layer's OrderPlanCreate format.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_config
from ..models import Direction

log = logging.getLogger(__name__)


class ExecutionClient:
    """HTTP client for trading_execution_layer API."""

    def __init__(self, base_url: Optional[str] = None, timeout_sec: Optional[float] = None):
        cfg = get_config().execution_layer
        self.base_url = (base_url or cfg.base_url).rstrip("/")
        self.timeout_sec = timeout_sec or cfg.request_timeout_sec
        self._default_context_id = cfg.default_context_id
        self._default_scenario_id = cfg.default_scenario_id

    def place_order(
        self,
        symbol: str,
        direction: Direction,
        qty: int,
        price: Optional[float] = None,
        scenario_id: Optional[str] = None,
        context_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Place an order via execution_layer API.

        Returns the full execution result dict from execution_layer.
        """
        url = f"{self.base_url}/api/orders"
        payload = {
            "symbol": symbol,
            "direction": direction.value,
            "order_type": "LIMIT" if price else "MARKET",
            "qty": qty,
            "scenario_id": scenario_id or self._default_scenario_id,
            "context_id": context_id or self._default_context_id,
        }
        if price is not None:
            payload["price"] = str(price)

        try:
            resp = httpx.post(url, json=payload, timeout=self.timeout_sec)
            resp.raise_for_status()
            result = resp.json()
            log.info(
                "Order placed: %s %s %d @ %s -> success=%s",
                symbol, direction.value, qty, price or "MARKET",
                result.get("success"),
            )
            return result
        except httpx.TimeoutException:
            log.error("ExecutionClient place_order timed out for %s", symbol)
            return {"success": False, "error": "下单请求超时"}
        except httpx.HTTPStatusError as e:
            log.error("ExecutionClient place_order HTTP %d: %s", e.response.status_code, e.response.text[:300])
            return {"success": False, "error": f"下单请求失败: HTTP {e.response.status_code}"}
        except Exception as e:
            log.error("ExecutionClient place_order failed: %s", e)
            return {"success": False, "error": str(e)}

    def get_account(self) -> Dict[str, Any]:
        """Get account state from execution_layer."""
        url = f"{self.base_url}/api/accounts/main"
        try:
            resp = httpx.get(url, timeout=self.timeout_sec)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            log.warning("ExecutionClient get_account failed: %s", e)
            return {}

    def get_positions(self) -> List[Dict[str, Any]]:
        """Get current positions from execution_layer."""
        url = f"{self.base_url}/api/positions"
        try:
            resp = httpx.get(url, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
            return data.get("positions", []) if isinstance(data, dict) else []
        except Exception as e:
            log.warning("ExecutionClient get_positions failed: %s", e)
            return []

    def check_health(self) -> bool:
        """Check if execution_layer is reachable. Quick check with short timeout."""
        try:
            resp = httpx.get(f"{self.base_url}/api/health", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False


# Singleton
_client: Optional[ExecutionClient] = None


def get_execution_client() -> ExecutionClient:
    global _client
    if _client is None:
        _client = ExecutionClient()
    return _client
