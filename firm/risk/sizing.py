"""Unified position sizing — ATR risk path with Gemini cap min(5%, 0.03/atr_pct)."""

from __future__ import annotations

import math

from firm.config import FIRM_CONFIG
from firm.risk.manager import calculate_position_size


def gemini_cap_pct(atr_pct: float) -> float:
    """Gemini arbitrator cap: min(5%, 0.03 / atr_pct)."""
    if atr_pct <= 0:
        return FIRM_CONFIG.max_position_pct
    return min(FIRM_CONFIG.max_position_pct, 0.03 / atr_pct)


def compute_order_qty(
    *,
    equity: float,
    entry_price: float,
    atr: float,
    drawdown_pct: float = 0.0,
) -> tuple[int, float, float, float]:
    """
    Single sizing path: ATR risk sizing with Gemini notional cap.

    Returns (shares, position_pct, stop_loss, take_profit).
    """
    atr_pct = (atr / entry_price) if entry_price > 0 else 0.0
    cap_pct = gemini_cap_pct(atr_pct)

    position_pct, shares = calculate_position_size(
        equity,
        entry_price,
        atr,
        drawdown_pct=drawdown_pct,
        max_position_pct=cap_pct,
    )
    if shares <= 0:
        return 0, 0.0, 0.0, 0.0

    from firm.risk.manager import calculate_stop_loss, calculate_take_profit

    stop = calculate_stop_loss(entry_price, atr)
    target = calculate_take_profit(entry_price, atr)
    return shares, position_pct, stop, target


def apply_regime_size_multiplier(shares: int, regime_multiplier: float) -> int:
    """Reduce size in choppy regimes (0.5x) or block in bear (0x)."""
    if regime_multiplier <= 0:
        return 0
    if regime_multiplier >= 1.0:
        return shares
    return max(0, math.floor(shares * regime_multiplier))
