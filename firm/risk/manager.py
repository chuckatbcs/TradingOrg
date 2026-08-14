"""ATR-based risk management — ported from trading-platform risk_manager."""

from __future__ import annotations

import math
from dataclasses import dataclass

from firm.config import FIRM_CONFIG

DRAWDOWN_REDUCE_THRESHOLD = -0.10
DRAWDOWN_HALT_THRESHOLD = -0.15


@dataclass
class PositionInfo:
    symbol: str
    sector: str = "unknown"
    shares: int = 0
    entry_price: float = 0.0


def calculate_position_size(
    equity: float,
    entry_price: float,
    atr: float,
    *,
    risk_per_trade: float | None = None,
    stop_atr_mult: float | None = None,
    drawdown_pct: float = 0.0,
    max_position_pct: float | None = None,
) -> tuple[float, int]:
    """ATR sizing capped at max_position_pct of equity."""
    risk_per_trade = risk_per_trade if risk_per_trade is not None else FIRM_CONFIG.risk_per_trade
    stop_atr_mult = stop_atr_mult if stop_atr_mult is not None else FIRM_CONFIG.stop_atr_mult
    max_position_pct = max_position_pct if max_position_pct is not None else FIRM_CONFIG.max_position_pct

    if atr <= 0 or entry_price <= 0 or equity <= 0:
        return 0.0, 0

    effective_risk = risk_per_trade
    if drawdown_pct <= DRAWDOWN_HALT_THRESHOLD:
        return 0.0, 0
    if drawdown_pct <= DRAWDOWN_REDUCE_THRESHOLD:
        effective_risk *= 0.5

    risk_amount = equity * effective_risk
    stop_distance = atr * stop_atr_mult
    shares = max(1, math.floor(risk_amount / stop_distance))

    position_value = shares * entry_price
    position_pct = position_value / equity
    cap = max(min(float(max_position_pct), 1.0), 0.0)
    if position_pct > cap:
        shares = math.floor((equity * cap) / entry_price)
        position_pct = (shares * entry_price) / equity if equity else 0.0

    max_notional = equity * cap
    if shares < 1 and entry_price <= max_notional:
        shares = 1
        position_pct = (shares * entry_price) / equity if equity else 0.0

    return round(position_pct, 4), shares


def calculate_stop_loss(entry_price: float, atr: float, multiplier: float | None = None) -> float:
    mult = multiplier if multiplier is not None else FIRM_CONFIG.stop_atr_mult
    return round(entry_price - atr * mult, 2)


def calculate_take_profit(entry_price: float, atr: float, multiplier: float | None = None) -> float:
    mult = multiplier if multiplier is not None else FIRM_CONFIG.target_atr_mult
    return round(entry_price + atr * mult, 2)


def current_drawdown(equity: float, peak_equity: float) -> float:
    if peak_equity <= 0:
        return 0.0
    return (equity - peak_equity) / peak_equity


def check_sector_limit(
    sector: str,
    current_positions: list[PositionInfo],
    max_positions: int | None = None,
) -> bool:
    cap = max_positions if max_positions is not None else FIRM_CONFIG.max_sector_positions
    count = sum(1 for p in current_positions if p.sector == sector)
    return count < cap
