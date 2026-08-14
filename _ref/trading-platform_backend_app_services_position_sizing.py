"""Buy quantity helpers for whole-share and fractional auto-execution."""

from __future__ import annotations

import math
from typing import Any

from app.config import settings


def _int_qty(raw_shares: Any, equity: float, price: float) -> int:
    try:
        n = int(float(raw_shares or 0))
    except (TypeError, ValueError):
        n = 0
    if n > 0:
        return n
    if price <= 0:
        return 0
    cap_pct = float(getattr(settings, "max_position_pct", 0.05) or 0.05)
    cap_notional = equity * cap_pct
    n = int(math.floor(cap_notional / price))
    if n < 1 and price <= cap_notional:
        n = 1
    return max(n, 0)


def _confidence_weight(confidence: float) -> float:
    """Map ML confidence to a 0.5–1.0 sizing multiplier."""
    min_conf = float(getattr(settings, "min_signal_confidence", 0.65) or 0.65)
    conf = max(float(confidence or 0), min_conf)
    span = max(1.0 - min_conf, 0.01)
    return 0.5 + 0.5 * min(1.0, (conf - min_conf) / span)


def _volume_weight(avg_dollar_volume: float | None) -> float:
    """Higher liquidity → slightly larger slot (0.5–1.0)."""
    if not avg_dollar_volume or avg_dollar_volume <= 0:
        return 1.0
    baseline = float(getattr(settings, "fractional_volume_baseline", 10_000_000) or 10_000_000)
    ratio = min(1.0, math.sqrt(avg_dollar_volume / baseline))
    return 0.5 + 0.5 * ratio


def compute_buy_qty(
    *,
    raw_shares: Any,
    equity: float,
    price: float,
    confidence: float = 0.0,
    avg_dollar_volume: float | None = None,
) -> float:
    """
    Return share quantity for a buy (int when fractional disabled, else fractional).

    Fractional mode targets ``equity * max_position_pct`` notional, scaled by the
    configured weight (confidence or dollar volume) and capped at the same pct.
    """
    if price <= 0 or equity <= 0:
        return 0.0

    if not getattr(settings, "fractional_shares_enabled", False):
        return float(_int_qty(raw_shares, equity, price))

    cap_pct = float(getattr(settings, "max_position_pct", 0.05) or 0.05)
    min_notional = float(getattr(settings, "fractional_min_notional", 1.0) or 1.0)

    try:
        hinted = float(raw_shares or 0)
    except (TypeError, ValueError):
        hinted = 0.0
    if hinted > 0:
        # Strategy engine already ATR-sized shares; do not haircut again.
        base_notional = hinted * price
        weight = 1.0
    else:
        base_notional = equity * cap_pct
        mode = str(
            getattr(settings, "fractional_sizing_weight", "confidence") or "confidence"
        ).lower()
        if mode == "volume":
            weight = _volume_weight(avg_dollar_volume)
        elif mode == "equal":
            weight = 1.0
        else:
            weight = _confidence_weight(confidence)

    notional = min(base_notional * weight, equity * cap_pct)
    if notional < min_notional:
        return 0.0

    qty = round(notional / price, 4)
    if qty * price < min_notional:
        return 0.0
    return qty


def format_signal_outcome(
    *,
    direction: str,
    acted_on: bool,
    decision_state: str | None,
    decision_reason: str | None,
) -> str:
    """Human-readable one-liner for UI."""
    state = (decision_state or "pending").lower()
    reason = (decision_reason or "").strip()
    side = (direction or "").lower()

    if state == "placed":
        return f"Order placed{': ' + reason if reason else ''}"
    if state == "blocked":
        return f"Blocked: {reason or 'market or risk gate'}"
    if state == "failed":
        from app.services.alpaca_errors import humanize_decision_reason

        detail = humanize_decision_reason(reason) or reason
        return f"Failed: {detail or 'broker error'}"
    if state == "skipped":
        return f"Skipped: {reason or 'executor filter'}"

    if not acted_on and state == "pending":
        if side == "sell":
            return "No trade — auto-sell only runs for symbols you hold"
        return "Pending — waiting for auto-execute on a scheduled scan"

    if reason:
        return reason
    return "—"
