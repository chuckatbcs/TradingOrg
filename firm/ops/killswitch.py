"""Daily loss protection — simplified sync kill switch."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from firm.config import FIRM_CONFIG
from firm.execution import alpaca
from firm.risk.manager import current_drawdown
from firm.storage.db import FirmDB

logger = logging.getLogger(__name__)

_STATE_FILE = "kill_state.json"


@dataclass
class KillSwitchResult:
    triggered: bool
    loss_pct: float
    equity: float
    message: str
    closed_symbols: list[str]


def _state_path() -> Path:
    return FIRM_CONFIG.data_dir / _STATE_FILE


def _load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def is_new_buy_blocked() -> bool:
    """Return True when daily loss protection is active."""
    state = _load_state()
    until = state.get("new_buy_block_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now()
    except ValueError:
        return False


def evaluate_kill_switch() -> KillSwitchResult:
    """
    Check daily loss vs baseline equity. On breach, close losing positions
    and block new buys for the rest of the session.
    """
    if not FIRM_CONFIG.can_auto_execute():
        return KillSwitchResult(False, 0.0, 0.0, "alpaca_not_configured", [])

    try:
        account = alpaca.get_account()
    except Exception as exc:
        return KillSwitchResult(False, 0.0, 0.0, str(exc), [])

    equity = float(account.get("equity", 0) or 0)
    state = _load_state()
    baseline = float(state.get("baseline_equity") or equity)
    if not state.get("baseline_equity"):
        _save_state({"baseline_equity": equity, "peak_equity": equity})
        baseline = equity

    loss_pct = current_drawdown(equity, baseline)
    limit = -FIRM_CONFIG.daily_loss_limit_pct

    if loss_pct > limit:
        peak = max(float(state.get("peak_equity") or equity), equity)
        _save_state({
            "baseline_equity": baseline,
            "peak_equity": peak,
            "last_check": datetime.now().isoformat(timespec="seconds"),
        })
        return KillSwitchResult(False, loss_pct, equity, "within_limit", [])

    # Breach — close losers
    closed: list[str] = []
    try:
        for pos in alpaca.get_positions():
            pl = float(pos.get("unrealized_pl", 0) or 0)
            sym = str(pos.get("symbol", "")).upper()
            if pl < 0 and sym:
                try:
                    alpaca.close_position(sym)
                    closed.append(sym)
                except Exception:
                    logger.exception("failed to close %s", sym)
    except Exception:
        logger.exception("kill switch position scan failed")

    now = datetime.now().isoformat(timespec="seconds")
    _save_state({
        "baseline_equity": equity,
        "peak_equity": equity,
        "last_protection_at": now,
        "new_buy_block_until": f"{datetime.now().date().isoformat()}T23:59:59",
    })

    db = FirmDB()
    db.save_kill_event("daily_loss", equity, loss_pct, f"closed {len(closed)} losers")

    return KillSwitchResult(
        True, loss_pct, equity,
        f"daily loss {loss_pct:.2%} breached limit {limit:.2%}",
        closed,
    )


class KillSwitch:
    """Object-oriented wrapper used by firm.service and the web API."""

    def new_buy_blocked(self) -> tuple[bool, str]:
        if is_new_buy_blocked():
            return True, "loss_protection_new_buy_block"
        return False, ""

    def check_daily_loss(self, *, auto_trigger: bool = True) -> dict:
        if not auto_trigger:
            return {"triggered": False}
        result = evaluate_kill_switch()
        return {
            "triggered": result.triggered,
            "loss_pct": result.loss_pct,
            "equity": result.equity,
            "message": result.message,
            "closed_symbols": result.closed_symbols,
        }

    def status(self) -> dict:
        state = _load_state()
        return {
            "new_buy_blocked": is_new_buy_blocked(),
            "baseline_equity": state.get("baseline_equity"),
            "last_protection_at": state.get("last_protection_at"),
            "new_buy_block_until": state.get("new_buy_block_until"),
        }
