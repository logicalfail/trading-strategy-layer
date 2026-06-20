"""Signal -> OrderPlan translator.

Converts a verified Signal into an OrderPlanCreate payload
for the execution_layer API.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .config import get_config
from .models import Direction, Signal

log = logging.getLogger(__name__)


def translate_signal(signal: Signal, qty: Optional[int] = None) -> Dict[str, Any]:
    """Translate a Signal to an execution_layer OrderPlanCreate dict.

    Args:
        signal: The verified signal to translate.
        qty: Override quantity. If None, uses signal.qty.

    Returns:
        Dict ready to POST to execution_layer /api/orders.
    """
    cfg = get_config().execution_layer
    order_qty = qty if qty is not None else signal.qty

    payload: Dict[str, Any] = {
        "symbol": signal.symbol,
        "direction": signal.direction.value,
        "qty": order_qty,
        "scenario_id": cfg.default_scenario_id,
        "context_id": cfg.default_context_id,
    }

    if signal.price is not None and signal.price > 0:
        payload["order_type"] = "LIMIT"
        payload["price"] = str(signal.price)
    else:
        payload["order_type"] = "MARKET"

    return payload


def format_signal_summary(signal: Signal, risk_result: Any = None) -> str:
    """Format a human-readable signal summary for logging."""
    lines = [
        "[Signal] {} {}".format(signal.symbol, signal.direction.value),
        "         qty={}, price={}".format(signal.qty, signal.price or "MARKET"),
        "         confidence={:.2f}, strength={}".format(signal.confidence, signal.strength.value),
        "         reason: {}".format(signal.reason),
    ]
    if risk_result is not None:
        status = "PASS" if risk_result.passed else "REJECTED - " + risk_result.reason
        lines.append("         risk: {}".format(status))
    return "\n".join(lines)
