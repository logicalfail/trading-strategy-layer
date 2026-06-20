"""DataClient — HTTP client for futures_demo API.

Provides:
  - get_bars(symbol, period, limit) → list[BarData]
  - get_quote(symbol) → dict
  - check_health() → bool

Uses httpx (same as execution_layer's mouse_replicator client).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import httpx

from ..config import get_config
from ..models import BarData

log = logging.getLogger(__name__)


class DataClient:
    """HTTP client for futures_demo market data API."""

    def __init__(self, base_url: Optional[str] = None, timeout_sec: Optional[float] = None):
        cfg = get_config().data_source
        self.base_url = (base_url or cfg.base_url).rstrip("/")
        self.timeout_sec = timeout_sec or cfg.request_timeout_sec

    def get_bars(
        self,
        symbol: str,
        period: str = "1m",
        limit: int = 500,
        days_back: int = 20,
    ) -> List[BarData]:
        """Fetch historical K-line bars from futures_demo.

        Calls /api/v1/bars/{symbol} with period aggregation.
        Returns list of BarData sorted by time ascending.
        """
        url = f"{self.base_url}/api/v1/bars/{symbol}"
        params = {
            "period": period,
            "limit": min(limit, 10000),
            "source": "db",
        }
        now = datetime.now()
        start = now - timedelta(days=days_back)
        params["start"] = start.strftime("%Y-%m-%d")
        params["end"] = now.strftime("%Y-%m-%d %H:%M:%S")

        try:
            resp = httpx.get(url, params=params, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
            raw_bars = data.get("bars", [])
            return [self._to_bar_data(b) for b in raw_bars]
        except httpx.TimeoutException:
            log.warning("DataClient get_bars timed out for %s", symbol)
            return []
        except httpx.HTTPStatusError as e:
            log.warning("DataClient get_bars HTTP %d for %s: %s", e.response.status_code, symbol, e.response.text[:200])
            return []
        except Exception as e:
            log.warning("DataClient get_bars failed for %s: %s", symbol, e)
            return []

    def get_latest_bars(self, symbol: str, n: int = 100) -> List[BarData]:
        """Fetch the latest n bars for a symbol.

        Calls /api/kline/{symbol} for the most recent data.
        """
        url = f"{self.base_url}/api/kline/{symbol}"
        params = {"limit": n}
        try:
            resp = httpx.get(url, params=params, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
            raw_bars = data.get("bars", [])
            return [self._to_bar_data(b) for b in raw_bars]
        except Exception as e:
            log.warning("DataClient get_latest_bars failed for %s: %s", symbol, e)
            return []

    def get_quote(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Get the latest quote for a symbol."""
        url = f"{self.base_url}/api/quote/{symbol}"
        try:
            resp = httpx.get(url, timeout=self.timeout_sec)
            resp.raise_for_status()
            data = resp.json()
            return data.get("quote")
        except Exception as e:
            log.debug("DataClient get_quote failed for %s: %s", symbol, e)
            return None

    def check_health(self) -> bool:
        """Check if futures_demo is reachable. Quick check with short timeout."""
        try:
            resp = httpx.get(f"{self.base_url}/api/status", timeout=2.0)
            return resp.status_code == 200
        except Exception:
            return False

    @staticmethod
    def _to_bar_data(raw: Dict[str, Any]) -> BarData:
        """Convert raw API bar dict to BarData."""
        return BarData(
            ts_ns=raw.get("ts_ns", 0),
            open=float(raw.get("open", 0)),
            high=float(raw.get("high", 0)),
            low=float(raw.get("low", 0)),
            close=float(raw.get("close", 0)),
            volume=int(raw.get("volume", 0)),
        )


# Singleton
_client: Optional[DataClient] = None


def get_data_client() -> DataClient:
    global _client
    if _client is None:
        _client = DataClient()
    return _client
