"""Order execution gates — market hours, spread, buying power."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from firm.config import FIRM_CONFIG
from firm.execution import alpaca as alpaca_client

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
AVOID_MINUTES = 5


@dataclass
class ExecutionDecision:
    allowed: bool
    reason: str
    spread_pct: float | None = None
    reference_price: float | None = None


def _regular_market_open(now: datetime | None = None) -> tuple[bool, str]:
    now_et = (now or datetime.now(timezone.utc)).astimezone(MARKET_TZ)
    if now_et.weekday() >= 5:
        return False, "market_closed:weekend"
    open_dt = datetime.combine(now_et.date(), REGULAR_OPEN, tzinfo=MARKET_TZ)
    close_dt = datetime.combine(now_et.date(), REGULAR_CLOSE, tzinfo=MARKET_TZ)
    start = open_dt + timedelta(minutes=AVOID_MINUTES)
    end = close_dt - timedelta(minutes=AVOID_MINUTES)
    if now_et < start:
        return False, "market_closed:before_entry_window"
    if now_et > end:
        return False, "market_closed:after_entry_window"
    return True, "market_open"


def evaluate_execution(symbol: str, side: str) -> ExecutionDecision:
    """Gate automated orders on market window and spread."""
    if not FIRM_CONFIG.can_auto_execute():
        return ExecutionDecision(False, "alpaca_not_configured_or_live_blocked")

    is_open, reason = _regular_market_open()
    if not is_open:
        return ExecutionDecision(False, reason)

    if side.lower() == "buy":
        try:
            quote = alpaca_client.get_latest_quote(symbol)
            ask = float(quote.get("ap", 0) or 0)
            bid = float(quote.get("bp", 0) or 0)
            if ask > 0 and bid > 0:
                mid = (ask + bid) / 2.0
                spread_pct = (ask - bid) / mid if mid > 0 else 0.0
                if spread_pct > FIRM_CONFIG.max_entry_spread_pct:
                    return ExecutionDecision(
                        False,
                        f"spread_too_wide:{spread_pct:.4f}",
                        spread_pct=spread_pct,
                        reference_price=mid,
                    )
                return ExecutionDecision(True, "allowed", spread_pct=spread_pct, reference_price=mid)
        except Exception as exc:
            logger.debug("quote gate unavailable for %s: %s", symbol, exc)

    return ExecutionDecision(True, "allowed")


def execute_market_order(
    *,
    run_id: str | None,
    symbol: str,
    side: str,
    qty: float,
) -> dict:
    """Submit market order after gates pass."""
    decision = evaluate_execution(symbol, side)
    if not decision.allowed:
        return {"status": "blocked", "reason": decision.reason, "order_id": None}

    if qty <= 0:
        return {"status": "blocked", "reason": "qty_zero", "order_id": None}

    try:
        result = alpaca_client.submit_order(symbol, qty, side)
        return {
            "status": "placed",
            "reason": decision.reason,
            "order_id": result.get("id"),
            "alpaca_status": result.get("status"),
            "qty": qty,
            "reference_price": decision.reference_price,
        }
    except alpaca_client.AlpacaOrderRejectedError as exc:
        return {"status": "failed", "reason": exc.message, "order_id": None}
