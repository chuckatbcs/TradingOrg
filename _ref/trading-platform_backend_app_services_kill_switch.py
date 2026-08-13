"""
Daily loss protection — liquidates losing positions and re-bases the loss limit.

Does not halt trading or block sells. On breach: close positions with negative
unrealized P&L, set equity baseline at the trigger point (persists across calendar
days), optionally block new automated buys until the US session ends, and log the
event for UI notifications.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.config import settings
from app.models import KillSwitchEvent, KillSwitchMonitorState
from app.services import alpaca_client
from app.services.trade_sync import record_kill_switch_close_orders

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("America/New_York")
SESSION_OPEN = time(9, 30)
SESSION_CLOSE = time(16, 0)


@dataclass(frozen=True)
class NewBuyBlockDecision:
    blocked: bool
    reason: str = ""
    until: datetime | None = None


def new_buy_block_until_after_protection(now: datetime | None = None) -> datetime:
    """When automated buys may resume after loss protection (naive UTC)."""
    ref = now or datetime.utcnow()
    now_et = ref.replace(tzinfo=ZoneInfo("UTC")).astimezone(MARKET_TZ)
    close_today = datetime.combine(now_et.date(), SESSION_CLOSE, tzinfo=MARKET_TZ)
    if now_et < close_today:
        return close_today.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    from app.services.exit_pdt_guard import next_trading_day_open_utc

    return next_trading_day_open_utc(ref)


def is_kill_switch_active() -> bool:
    """Trading halt removed; kept for API compatibility."""
    return False


def _position_unrealized_pl(position: dict) -> float | None:
    for key in ("unrealized_pl", "unrealized_intraday_pl"):
        raw = position.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _losing_positions(positions: list[dict]) -> list[dict]:
    losers: list[dict] = []
    for pos in positions:
        symbol = str(pos.get("symbol") or "").upper()
        if not symbol:
            continue
        pl = _position_unrealized_pl(pos)
        if pl is not None and pl < 0:
            losers.append(pos)
    return losers


def _baseline_reference_equity(
    last_equity: float,
    monitor: KillSwitchMonitorState,
) -> tuple[float, str]:
    baseline = monitor.daily_loss_baseline_equity
    if baseline is None or monitor.daily_loss_baseline_set_at is None:
        return last_equity, "broker_session"
    return float(baseline), "protection_baseline"


def _protection_cooldown_active(monitor: KillSwitchMonitorState) -> bool:
    if not monitor.last_protection_at:
        return False
    elapsed = (datetime.utcnow() - monitor.last_protection_at).total_seconds()
    return elapsed < settings.loss_protection_cooldown_sec


def _new_buy_block_active(monitor: KillSwitchMonitorState, now: datetime | None = None) -> bool:
    if not getattr(settings, "block_new_buys_after_loss_protection", True):
        return False
    until = getattr(monitor, "new_buy_block_until", None)
    if not until:
        return False
    return until > (now or datetime.utcnow())


async def evaluate_new_buy_block(db: AsyncSession) -> NewBuyBlockDecision:
    """Return whether automated buys should be blocked after loss protection."""
    if not getattr(settings, "block_new_buys_after_loss_protection", True):
        return NewBuyBlockDecision(blocked=False)
    monitor = await _get_monitor_state(db)
    until = getattr(monitor, "new_buy_block_until", None)
    if not until or not _new_buy_block_active(monitor):
        return NewBuyBlockDecision(blocked=False)
    return NewBuyBlockDecision(
        blocked=True,
        reason="loss_protection_new_buy_block",
        until=until,
    )


def _encode_symbols(symbols: list[str]) -> str | None:
    if not symbols:
        return None
    return json.dumps(symbols)


def _decode_symbols(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(s).upper() for s in parsed if s]
    except (TypeError, ValueError):
        pass
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


async def _get_monitor_state(db: AsyncSession) -> KillSwitchMonitorState:
    result = await db.execute(
        select(KillSwitchMonitorState).where(KillSwitchMonitorState.id == 1)
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = KillSwitchMonitorState(id=1)
        db.add(state)
        await db.flush()
    return state


async def _set_daily_loss_baseline(db: AsyncSession, equity: float) -> None:
    monitor = await _get_monitor_state(db)
    monitor.daily_loss_baseline_equity = equity
    monitor.daily_loss_baseline_set_at = datetime.utcnow()
    await db.flush()


async def _latest_liquidation_event(db: AsyncSession) -> Optional[KillSwitchEvent]:
    result = await db.execute(
        select(KillSwitchEvent).order_by(desc(KillSwitchEvent.triggered_at)).limit(1)
    )
    return result.scalar_one_or_none()


def _event_to_liquidation_payload(event: KillSwitchEvent | None) -> dict | None:
    if not event:
        return None
    symbols = _decode_symbols(event.symbols_liquidated)
    return {
        "event_id": event.id,
        "reason": event.trigger_reason,
        "symbols": symbols,
        "positions_closed": event.positions_closed or len(symbols),
        "triggered_at": event.triggered_at.isoformat() if event.triggered_at else None,
        "equity_at_trigger": event.equity_at_trigger,
        "daily_pnl_at_trigger": event.daily_pnl_at_trigger,
    }


async def restore_kill_switch_state(db: AsyncSession) -> None:
    """Ensure monitor row exists on startup (no trading halt restore)."""
    await _get_monitor_state(db)
    await db.commit()


async def check_daily_loss(db: AsyncSession, *, auto_trigger: bool = True) -> dict:
    """
    Check if loss vs protection baseline exceeds the configured limit.
    Triggers loser-only liquidation and baseline re-set when breached.
    """
    try:
        monitor = await _get_monitor_state(db)
        if _protection_cooldown_active(monitor):
            return {
                "check": "skip",
                "reason": "protection_cooldown",
                "triggered": False,
                "cooldown_sec": settings.loss_protection_cooldown_sec,
            }

        account = await alpaca_client.get_account()
        equity = float(account.get("equity", 0))
        last_equity = float(account.get("last_equity", equity))

        if last_equity <= 0:
            return {"check": "skip", "reason": "no_previous_equity"}

        reference_equity, reference_source = _baseline_reference_equity(last_equity, monitor)
        if reference_equity <= 0:
            return {"check": "skip", "reason": "no_reference_equity"}

        daily_pnl = equity - reference_equity
        daily_pnl_pct = daily_pnl / reference_equity

        result = {
            "equity": equity,
            "last_equity": last_equity,
            "reference_equity": reference_equity,
            "reference_source": reference_source,
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": round(daily_pnl_pct, 4),
            "limit_pct": settings.daily_loss_limit_pct,
            "triggered": False,
        }

        if daily_pnl_pct < -settings.daily_loss_limit_pct:
            reason = (
                f"Daily loss {daily_pnl_pct:.2%} exceeded limit "
                f"{settings.daily_loss_limit_pct:.2%} "
                f"(reference: {reference_source})"
            )
            if auto_trigger:
                logger.critical("LOSS PROTECTION TRIGGERED: %s", reason)
                protection = await trigger_kill_switch(
                    db=db,
                    reason=reason,
                    equity=equity,
                    daily_pnl=daily_pnl,
                )
                result["triggered"] = protection.get("status") == "loss_protection_executed"
                result["protection"] = protection
            else:
                result["would_trigger"] = True
                result["trigger_reason"] = reason

        return result

    except Exception as e:
        logger.error("Kill switch check failed: %s", e)
        return {"check": "error", "error": str(e)}


async def trigger_kill_switch(
    db: AsyncSession,
    reason: str,
    equity: Optional[float] = None,
    daily_pnl: Optional[float] = None,
) -> dict:
    """
    Loss protection action:
    1. Liquidate open positions with negative unrealized P&L only
    2. Re-base daily loss monitoring from current equity (cross-day)
    3. Log event (auto-resolved; trading continues)
    """
    monitor = await _get_monitor_state(db)
    if _protection_cooldown_active(monitor):
        return {
            "status": "protection_cooldown",
            "reason": reason,
            "cooldown_sec": settings.loss_protection_cooldown_sec,
        }

    now = datetime.utcnow()
    monitor.last_protection_at = now
    if getattr(settings, "block_new_buys_after_loss_protection", True):
        monitor.new_buy_block_until = new_buy_block_until_after_protection(now)
    else:
        monitor.new_buy_block_until = None
    await db.flush()
    await db.commit()

    if equity is None:
        try:
            account = await alpaca_client.get_account()
            equity = float(account.get("equity", 0))
        except Exception as err:
            logger.warning("Could not read equity for loss protection: %s", err)
            equity = None

    logger.critical("=== LOSS PROTECTION: %s ===", reason)

    positions_closed = 0
    trades_recorded = 0
    errors: list[str] = []
    positions_before: dict[str, dict] = {}
    closed_orders: list[dict] = []
    symbols_liquidated: list[str] = []

    try:
        open_positions = await alpaca_client.get_positions()
        positions_before = {
            str(pos.get("symbol") or "").upper(): pos
            for pos in open_positions
            if pos.get("symbol")
        }
        losers = _losing_positions(open_positions)
        for pos in losers:
            symbol = str(pos.get("symbol") or "").upper()
            if not symbol:
                continue
            try:
                order = await alpaca_client.close_position(symbol)
                closed_orders.append(order)
                symbols_liquidated.append(symbol)
                positions_closed += 1
                logger.info("Loss protection liquidated loser %s", symbol)
            except Exception as err:
                errors.append(f"Close {symbol} failed: {err}")
                logger.error("Failed to close loser %s: %s", symbol, err)
    except Exception as e:
        errors.append(f"Load positions failed: {e}")
        logger.error("Failed to load positions for loss protection: %s", e)

    if closed_orders:
        try:
            record_result = await record_kill_switch_close_orders(
                db,
                closed_orders,
                positions_before=positions_before,
                kill_switch_reason=reason,
                commit=False,
            )
            trades_recorded = record_result.get("recorded", 0) + record_result.get("updated", 0)
        except Exception as e:
            errors.append(f"Trade history recording failed: {e}")
            logger.error("Failed to record loss protection trades: %s", e)

    if equity is not None and equity > 0:
        await _set_daily_loss_baseline(db, equity)

    now = datetime.utcnow()
    event = KillSwitchEvent(
        trigger_reason=reason,
        equity_at_trigger=equity,
        daily_pnl_at_trigger=daily_pnl,
        positions_closed=positions_closed,
        symbols_liquidated=_encode_symbols(symbols_liquidated),
        resolved_at=now,
        resolved_by="auto_protection",
    )
    db.add(event)
    await db.commit()

    return {
        "status": "loss_protection_executed",
        "reason": reason,
        "positions_closed": positions_closed,
        "symbols_liquidated": symbols_liquidated,
        "trades_recorded": trades_recorded,
        "errors": errors,
        "event_id": event.id,
        "daily_loss_baseline_equity": equity,
        "halt_active": False,
        "new_buy_block_until": (
            monitor.new_buy_block_until.isoformat()
            if monitor.new_buy_block_until
            else None
        ),
    }


async def reset_kill_switch(db: AsyncSession, resolved_by: str = "manual") -> dict:
    """Re-base daily loss monitoring from current equity without liquidating."""
    baseline_equity = None
    try:
        account = await alpaca_client.get_account()
        baseline_equity = float(account.get("equity", 0))
    except Exception as err:
        logger.warning("Could not read account equity for kill switch baseline: %s", err)

    monitor = await _get_monitor_state(db)
    if baseline_equity is not None and baseline_equity > 0:
        await _set_daily_loss_baseline(db, baseline_equity)
    monitor.new_buy_block_until = None

    await db.commit()

    logger.info(
        "Loss protection baseline reset by %s; baseline=%s",
        resolved_by,
        baseline_equity,
    )
    return {
        "status": "baseline_reset",
        "resolved_by": resolved_by,
        "daily_loss_baseline_equity": baseline_equity,
        "halt_active": False,
        "message": (
            "Daily loss limit re-based from current equity. "
            "Trading continues; only losers are sold when the limit is hit. "
            "Any new-buy block from loss protection was cleared."
            if baseline_equity
            else "Baseline reset requested but equity was unavailable."
        ),
    }


async def get_kill_switch_status(db: AsyncSession) -> dict:
    """Monitor status, baseline context, and latest liquidation for notifications."""
    payload: dict = {
        "active": False,
        "halt_active": False,
        "reason": None,
        "limit_pct": settings.daily_loss_limit_pct,
    }

    try:
        check = await check_daily_loss(db, auto_trigger=False)
        if check.get("check") != "error":
            payload["daily_pnl_pct"] = check.get("daily_pnl_pct")
            payload["reference_source"] = check.get("reference_source")
            payload["reference_equity"] = check.get("reference_equity")
            payload["would_trigger"] = check.get("would_trigger", False)
    except Exception:
        pass

    event = await _latest_liquidation_event(db)
    payload["last_liquidation"] = _event_to_liquidation_payload(event)

    monitor = await _get_monitor_state(db)
    payload["block_new_buys_after_loss_protection"] = bool(
        getattr(settings, "block_new_buys_after_loss_protection", True)
    )
    until = getattr(monitor, "new_buy_block_until", None)
    payload["new_buy_block_active"] = _new_buy_block_active(monitor)
    payload["new_buy_block_until"] = until.isoformat() if until else None

    return payload


async def get_kill_switch_history(db: AsyncSession, limit: int = 20) -> list[dict]:
    """Get loss protection event history."""
    result = await db.execute(
        select(KillSwitchEvent)
        .order_by(desc(KillSwitchEvent.triggered_at))
        .limit(limit)
    )
    events = result.scalars().all()
    return [
        {
            "id": e.id,
            "reason": e.trigger_reason,
            "equity": e.equity_at_trigger,
            "daily_pnl": e.daily_pnl_at_trigger,
            "positions_closed": e.positions_closed,
            "symbols": _decode_symbols(e.symbols_liquidated),
            "triggered_at": e.triggered_at.isoformat() if e.triggered_at else None,
            "resolved_at": e.resolved_at.isoformat() if e.resolved_at else None,
            "resolved_by": e.resolved_by,
        }
        for e in events
    ]
