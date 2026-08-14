"""
Risk Manager
=============
ATR-based position sizing, stop-loss / take-profit / trailing-stop
calculation, drawdown circuit breakers, and sector exposure limits.

All pure functions — no I/O, no DB, no broker calls.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (sensible defaults, overridable via function args)
# ---------------------------------------------------------------------------
DEFAULT_RISK_PER_TRADE = 0.015        # 1.5 % of equity risked per trade
DEFAULT_STOP_ATR_MULT = 2.0           # stop-loss = 2 × ATR below entry
DEFAULT_TP_ATR_MULT = 3.0             # take-profit = 3 × ATR above entry  (3:1 R/R)
DEFAULT_TRAIL_ATR_MULT = 2.0          # trailing stop distance = 2 × ATR
TRAIL_ACTIVATION_ATR_MULT = 1.5       # trailing stop activates after 1.5 × ATR profit
MAX_POSITION_PCT = 0.20               # never more than 20 % of equity in one position
MAX_SECTOR_POSITIONS = 3              # max 3 positions in the same sector
DRAWDOWN_REDUCE_THRESHOLD = -0.10     # -10 % → cut sizes 50 %
DRAWDOWN_HALT_THRESHOLD = -0.15       # -15 % → halt all new trades


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------

def calculate_position_size(
    equity: float,
    entry_price: float,
    atr: float,
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
    stop_atr_mult: float = DEFAULT_STOP_ATR_MULT,
    drawdown_pct: float = 0.0,
    max_position_pct: float = MAX_POSITION_PCT,
) -> tuple[float, int]:
    """
    ATR-based position sizing.

    Position size = (equity × risk%) / (ATR × stop_multiplier)
    Capped at MAX_POSITION_PCT of equity.
    """
    if atr <= 0 or entry_price <= 0 or equity <= 0:
        return 0.0, 0

    # Drawdown circuit breaker adjustments
    effective_risk = risk_per_trade
    if drawdown_pct <= DRAWDOWN_HALT_THRESHOLD:
        logger.warning("Drawdown %.1f%% — HALTING new trades", drawdown_pct * 100)
        return 0.0, 0
    elif drawdown_pct <= DRAWDOWN_REDUCE_THRESHOLD:
        effective_risk *= 0.5
        logger.info("Drawdown %.1f%% — reducing risk to %.2f%%",
                     drawdown_pct * 100, effective_risk * 100)

    risk_amount = equity * effective_risk          # dollars we can lose
    stop_distance = atr * stop_atr_mult            # dollar distance to stop
    shares_float = risk_amount / stop_distance
    shares = max(1, math.floor(shares_float))

    position_value = shares * entry_price
    position_pct = position_value / equity

    # Cap at max position size
    max_position_pct = max(min(float(max_position_pct or MAX_POSITION_PCT), 1.0), 0.0)
    if position_pct > max_position_pct:
        shares = math.floor((equity * max_position_pct) / entry_price)
        position_pct = (shares * entry_price) / equity

    # Cap-aware 1-share floor: only when a single share fits within max_position_pct.
    max_notional = equity * max_position_pct
    if shares < 1 and entry_price > 0 and entry_price <= max_notional:
        shares = 1
        position_pct = (shares * entry_price) / equity

    return round(position_pct, 4), shares


# ---------------------------------------------------------------------------
# Stop / Target calculations
# ---------------------------------------------------------------------------

def calculate_stop_loss(
    entry_price: float,
    atr: float,
    multiplier: float = DEFAULT_STOP_ATR_MULT,
) -> float:
    """Stop-loss = entry - (ATR × multiplier)."""
    return round(entry_price - atr * multiplier, 2)


def calculate_take_profit(
    entry_price: float,
    atr: float,
    multiplier: float = DEFAULT_TP_ATR_MULT,
) -> float:
    """Take-profit = entry + (ATR × multiplier)."""
    return round(entry_price + atr * multiplier, 2)


def calculate_trailing_stop_distance(
    atr: float,
    multiplier: float = DEFAULT_TRAIL_ATR_MULT,
) -> float:
    """Distance the trailing stop trails behind highest-since-entry."""
    return round(atr * multiplier, 2)


def trailing_stop_price(
    highest_since_entry: float,
    trail_distance: float,
) -> float:
    """Current trailing stop price given the high-water mark."""
    return round(highest_since_entry - trail_distance, 2)


def is_trailing_stop_active(
    current_price: float,
    entry_price: float,
    atr: float,
    activation_mult: float = TRAIL_ACTIVATION_ATR_MULT,
) -> bool:
    """Trailing stop activates only after price moves 1.5 × ATR above entry."""
    return current_price >= entry_price + atr * activation_mult


# ---------------------------------------------------------------------------
# Drawdown helpers
# ---------------------------------------------------------------------------

def current_drawdown(equity: float, peak_equity: float) -> float:
    """Return drawdown as a negative fraction (e.g. -0.12 for 12 % down)."""
    if peak_equity <= 0:
        return 0.0
    return (equity - peak_equity) / peak_equity


def should_halt_trading(drawdown_pct: float) -> bool:
    """True when drawdown exceeds the halt threshold (-15 %)."""
    return drawdown_pct <= DRAWDOWN_HALT_THRESHOLD


def should_reduce_size(drawdown_pct: float) -> bool:
    """True when drawdown exceeds the reduce threshold (-10 %)."""
    return drawdown_pct <= DRAWDOWN_REDUCE_THRESHOLD


# ---------------------------------------------------------------------------
# Sector exposure
# ---------------------------------------------------------------------------

@dataclass
class PositionInfo:
    symbol: str
    sector: str = "unknown"
    shares: int = 0
    entry_price: float = 0.0


def check_sector_limit(
    sector: str,
    current_positions: list[PositionInfo],
    max_positions: int = MAX_SECTOR_POSITIONS,
) -> bool:
    """
    Return True if we can open a new position in `sector`.
    False if the sector already has `max_positions` open.
    """
    count = sum(1 for p in current_positions if p.sector == sector)
    return count < max_positions
