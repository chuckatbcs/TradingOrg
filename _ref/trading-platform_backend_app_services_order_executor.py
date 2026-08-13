from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Trade, Position, StrategyPerformance
from app.services import alpaca_client
from app.services.alpaca_client import AlpacaOrderRejectedError
from app.services.alpaca_errors import AlpacaErrorInfo, combine_ledger_reasons
from app.services.exit_pdt_guard import (
    apply_exit_block_from_failure,
    apply_pdt_block,
    clear_exit_block,
    exit_block_summary,
    is_exit_blocked,
    is_pdt_message,
    min_sell_qty,
    resolve_automated_sell_qty,
)
from app.services.performance_ledger import record_strategy_event
from app.services.runtime_config import extended_hours_enabled
from app.services.watchlist_manager import add_symbol as watchlist_add_symbol

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("America/New_York")
REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
EXTENDED_OPEN = time(4, 0)
EXTENDED_CLOSE = time(20, 0)


@dataclass
class ExecutionDecision:
    allowed: bool
    reason: str
    spread_pct: float | None = None
    reference_price: float | None = None


def _regular_market_window(now: datetime | None = None) -> tuple[bool, str]:
    now_et = (now or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(MARKET_TZ)
    if now_et.weekday() >= 5:
        return False, "market_closed:weekend"

    open_dt = datetime.combine(now_et.date(), REGULAR_OPEN, tzinfo=MARKET_TZ)
    close_dt = datetime.combine(now_et.date(), REGULAR_CLOSE, tzinfo=MARKET_TZ)
    start = open_dt + timedelta(minutes=settings.avoid_open_close_minutes)
    end = close_dt - timedelta(minutes=settings.avoid_open_close_minutes)

    if now_et < start:
        return False, "market_closed:before_entry_window"
    if now_et > end:
        return False, "market_closed:after_entry_window"
    return True, "market_open"


def _extended_market_window(now: datetime | None = None) -> tuple[bool, str]:
    now_et = (now or datetime.utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(MARKET_TZ)
    if now_et.weekday() >= 5:
        return False, "extended_hours_closed:weekend"

    open_dt = datetime.combine(now_et.date(), EXTENDED_OPEN, tzinfo=MARKET_TZ)
    close_dt = datetime.combine(now_et.date(), EXTENDED_CLOSE, tzinfo=MARKET_TZ)
    if now_et < open_dt:
        return False, "extended_hours_closed:before_4am_et"
    if now_et > close_dt:
        return False, "extended_hours_closed:after_8pm_et"
    return True, "extended_hours_open"


async def _reference_limit_price(
    symbol: str,
    side: str,
    reference_price: float | None,
) -> float | None:
    if reference_price and reference_price > 0:
        return round(float(reference_price), 2)
    try:
        quote = await alpaca_client.get_latest_quote(symbol)
        bid = float(quote.get("bp", 0) or 0)
        ask = float(quote.get("ap", 0) or 0)
        if side.lower() == "buy" and ask > 0:
            return round(ask, 2)
        if side.lower() == "sell" and bid > 0:
            return round(bid, 2)
        if ask > 0 and bid > 0:
            return round((ask + bid) / 2.0, 2)
    except Exception as err:
        logger.debug("Extended-hours reference price unavailable for %s: %s", symbol, err)
    return None


async def evaluate_execution(
    symbol: str,
    side: str,
    *,
    automated: bool = True,
    order_type: str = "market",
    limit_price: float | None = None,
) -> ExecutionDecision:
    side = side.lower()

    if automated and not settings.can_auto_execute and side == "buy":
        return ExecutionDecision(False, "auto_buy_disabled")

    if automated:
        is_open, reason = _regular_market_window()
        if not is_open:
            if not extended_hours_enabled():
                return ExecutionDecision(False, reason)
            extended_open, extended_reason = _extended_market_window()
            if not extended_open:
                return ExecutionDecision(False, extended_reason)
            if order_type != "limit":
                return ExecutionDecision(False, "extended_hours_requires_limit_order")
            if not limit_price or limit_price <= 0:
                return ExecutionDecision(False, "extended_hours_requires_limit_price")

    if side == "buy" and order_type == "market":
        try:
            quote = await alpaca_client.get_latest_quote(symbol)
            ask = float(quote.get("ap", 0) or 0)
            bid = float(quote.get("bp", 0) or 0)
            if ask > 0 and bid > 0:
                mid = (ask + bid) / 2.0
                spread_pct = (ask - bid) / mid if mid > 0 else 0.0
                if spread_pct > settings.max_entry_spread_pct:
                    return ExecutionDecision(
                        False,
                        f"spread_too_wide:{spread_pct:.4f}",
                        spread_pct=spread_pct,
                        reference_price=mid,
                    )
                return ExecutionDecision(True, "allowed", spread_pct=spread_pct, reference_price=mid)
        except Exception as err:
            logger.debug("Execution quote gate unavailable for %s: %s", symbol, err)

    return ExecutionDecision(True, "allowed")


async def execute_order(
    db: AsyncSession,
    *,
    symbol: str,
    qty: float,
    side: str,
    order_type: str = "market",
    time_in_force: str = "day",
    limit_price: float | None = None,
    automated: bool = True,
    source: str = "managed",
    signal_confidence: float | None = None,
    entry_reason: str | None = None,
    exit_reason: str | None = None,
    expected_risk: float | None = None,
    market_regime: str | None = None,
    timeframe: str | None = None,
    reference_price: float | None = None,
    stop_loss: float | None = None,
    atr_at_entry: float | None = None,
    trailing_stop_distance: float | None = None,
    commit: bool = True,
) -> dict:
    regular_open, _ = _regular_market_window()
    extended_hours = (
        extended_hours_enabled()
        and order_type == "limit"
        and time_in_force == "day"
    )

    if automated and extended_hours_enabled() and not regular_open and order_type == "market":
        converted_price = await _reference_limit_price(symbol, side, reference_price)
        if converted_price:
            logger.info(
                "Converting automated extended-hours %s %s order to limit @ %.2f",
                side,
                symbol,
                converted_price,
            )
            order_type = "limit"
            limit_price = converted_price
            extended_hours = True

    decision = await evaluate_execution(
        symbol,
        side,
        automated=automated,
        order_type=order_type,
        limit_price=limit_price,
    )
    if not decision.allowed:
        await record_strategy_event(
            db,
            symbol=symbol,
            side=side,
            qty=qty,
            timeframe=timeframe,
            entry_price=reference_price or decision.reference_price,
            exit_price=reference_price or decision.reference_price,
            signal_confidence=signal_confidence,
            entry_reason=entry_reason,
            exit_reason=exit_reason or decision.reason,
            expected_risk=expected_risk,
            market_regime=market_regime,
            status=f"blocked:{decision.reason}",
            source=source,
            commit=commit,
        )
        return {"blocked": True, "reason": decision.reason}

    sym_u = symbol.upper()
    sell_cost_basis_price: float | None = None
    sell_pos: Position | None = None
    if side.lower() == "sell":
        pos_row = await db.execute(select(Position).where(Position.symbol == sym_u))
        sell_pos = pos_row.scalars().first()
        if automated and sell_pos and not is_exit_blocked(sell_pos):
            await _apply_recent_pdt_backoff_from_ledger(db, sell_pos, sym_u)
        if automated and sell_pos and is_exit_blocked(sell_pos):
            block_reason = f"exit_blocked:{exit_block_summary(sell_pos)}"
            await record_strategy_event(
                db,
                symbol=symbol,
                side=side,
                qty=qty,
                timeframe=timeframe,
                exit_price=reference_price,
                exit_reason=exit_reason or block_reason,
                signal_confidence=signal_confidence,
                expected_risk=expected_risk,
                market_regime=market_regime,
                status=f"blocked:{block_reason}",
                source=source,
                commit=commit,
            )
            return {"blocked": True, "reason": block_reason}
        if sell_pos and sell_pos.avg_entry_price:
            sell_cost_basis_price = float(sell_pos.avg_entry_price)
        if not sell_cost_basis_price or sell_cost_basis_price <= 0:
            try:
                apos = await alpaca_client.get_position(sym_u)
                if apos:
                    sell_cost_basis_price = float(apos.get("avg_entry_price", 0) or 0)
            except Exception as err:
                logger.debug("Alpaca position (cost basis) unavailable for %s: %s", sym_u, err)
        if (not sell_cost_basis_price or sell_cost_basis_price <= 0) or signal_confidence is None:
            buy_row = await db.execute(
                select(Trade)
                .where(Trade.symbol == sym_u, Trade.side == "buy")
                .order_by(desc(Trade.created_at))
                .limit(1)
            )
            buy_t = buy_row.scalars().first()
            if buy_t:
                if not sell_cost_basis_price or sell_cost_basis_price <= 0:
                    sell_cost_basis_price = float(
                        buy_t.filled_price or buy_t.price or 0
                    )
                # Carry the entry confidence onto the exit so reflection /
                # attribution can bucket by the confidence we opened with
                # instead of "conf_unknown".
                if signal_confidence is None and buy_t.signal_confidence is not None:
                    signal_confidence = float(buy_t.signal_confidence)

        if automated:
            min_qty = min_sell_qty()
            broker_qty = await resolve_automated_sell_qty(sym_u, float(qty))
            if broker_qty < min_qty:
                # Nothing sellable at the broker. Two cases:
                #   (a) the broker is flat (stale local row) -> reconcile by
                #       removing the local position so we stop firing phantom
                #       exits on a position we no longer hold, and
                #   (b) the qty is reserved by an open sell order or the lookup
                #       failed -> leave local state untouched and just defer.
                broker_held: float | None = None
                try:
                    apos = await alpaca_client.get_position(sym_u)
                    broker_held = float(apos.get("qty", 0) or 0) if apos else 0.0
                except Exception as err:
                    logger.debug(
                        "Broker position lookup failed during exit reconcile for %s: %s",
                        sym_u,
                        err,
                    )
                reconciled = (
                    broker_held is not None
                    and broker_held <= 0
                    and sell_pos is not None
                )
                if reconciled:
                    block_reason = "exit_reconciled:position_flat_at_broker"
                    status = f"reconciled:{block_reason}"
                    await db.delete(sell_pos)
                    logger.info(
                        "Reconciled stale local position %s (flat at broker); "
                        "removed so exits stop retrying.",
                        sym_u,
                    )
                else:
                    block_reason = "exit_blocked:no_broker_qty_available"
                    status = f"blocked:{block_reason}"
                await record_strategy_event(
                    db,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    timeframe=timeframe,
                    exit_price=reference_price,
                    exit_reason=exit_reason or block_reason,
                    signal_confidence=signal_confidence,
                    expected_risk=expected_risk,
                    market_regime=market_regime,
                    status=status,
                    source=source,
                    commit=commit,
                )
                return {"blocked": True, "reason": block_reason, "reconciled": reconciled}
            if broker_qty < float(qty) - 1e-6:
                logger.info(
                    "Capped automated sell for %s from %s to %s shares (broker available)",
                    sym_u,
                    qty,
                    broker_qty,
                )
                qty = broker_qty

    ref_px = float(
        limit_price or reference_price or decision.reference_price or 0.0
    )
    trade_notes = (entry_reason if side.lower() == "buy" else exit_reason) or ""
    pending_trade = Trade(
        symbol=sym_u,
        side=side.lower(),
        qty=float(qty),
        price=ref_px,
        status="submitting",
        signal_confidence=signal_confidence,
        signal_source=source,
        notes=trade_notes[:500] if trade_notes else None,
    )
    db.add(pending_trade)
    await db.flush()

    try:
        order = await alpaca_client.submit_order(
            symbol=symbol,
            qty=qty,
            side=side,
            order_type=order_type,
            time_in_force=time_in_force,
            limit_price=limit_price,
            extended_hours=extended_hours,
        )
    except AlpacaOrderRejectedError as exc:
        fail_status = f"failed:alpaca_{exc.status_code}"
        broker = AlpacaErrorInfo(
            http_status=exc.status_code,
            message=exc.message,
            code=exc.code,
            raw_body=exc.body,
        )
        fail_reason = broker.ledger_fragment()[:500]
        if side.lower() == "sell":
            ledger_exit = combine_ledger_reasons(exit_reason, broker)
        else:
            ledger_exit = None
        ledger_entry = (
            combine_ledger_reasons(entry_reason, broker)
            if side.lower() == "buy"
            else None
        )
        fail_result = {
            "blocked": False,
            "failed": True,
            "reason": fail_reason,
            "alpaca_status": exc.status_code,
            "alpaca_message": exc.message,
            "alpaca_code": exc.code,
        }
        if side.lower() == "sell" and automated and sell_pos:
            if apply_exit_block_from_failure(sell_pos, fail_result):
                logger.warning(
                    "Exit backoff for %s (%s) until %s UTC (source=%s)",
                    sym_u,
                    sell_pos.exit_block_reason,
                    sell_pos.exit_blocked_until,
                    source,
                )
                if (sell_pos.exit_block_reason or "") == "qty_mismatch":
                    try:
                        from app.services.portfolio_engine import sync_positions

                        await sync_positions(db)
                    except Exception as sync_err:
                        logger.debug(
                            "Position sync after qty mismatch failed for %s: %s",
                            sym_u,
                            sync_err,
                        )
        pending_trade.status = "failed"
        fail_note = (pending_trade.notes or "")[:400]
        pending_trade.notes = f"{fail_note}|{fail_reason}"[:500]
        await record_strategy_event(
            db,
            symbol=symbol,
            side=side,
            qty=qty,
            timeframe=timeframe,
            entry_price=reference_price if side.lower() == "buy" else None,
            exit_price=reference_price if side.lower() == "sell" else None,
            signal_confidence=signal_confidence,
            entry_reason=ledger_entry,
            exit_reason=ledger_exit,
            expected_risk=expected_risk,
            market_regime=market_regime,
            status=fail_status,
            source=source,
            order_id=pending_trade.order_id,
            commit=commit,
        )
        logger.error(
            "Order rejected by Alpaca for %s %s %s: %s",
            side,
            qty,
            symbol,
            exc.message,
        )
        return fail_result

    filled_price = float(order.get("filled_avg_price", 0) or 0) or None
    price = limit_price or reference_price or decision.reference_price or 0.0
    exit_px = float(filled_price or price or 0)

    trade_pnl = None
    if (
        side.lower() == "sell"
        and sell_cost_basis_price
        and sell_cost_basis_price > 0
        and exit_px > 0
        and float(qty) > 0
    ):
        trade_pnl = round((exit_px - sell_cost_basis_price) * float(qty), 2)

    pending_trade.order_id = order.get("id")
    pending_trade.price = float(price or 0)
    pending_trade.filled_price = filled_price
    pending_trade.status = str(order.get("status", "submitted"))[:20]
    pending_trade.pnl = trade_pnl
    if trade_notes:
        pending_trade.notes = trade_notes[:500]
    trade = pending_trade

    await record_strategy_event(
        db,
        symbol=symbol,
        side=side,
        qty=qty,
        timeframe=timeframe,
        entry_price=price if side.lower() == "buy" else sell_cost_basis_price,
        exit_price=price if side.lower() == "sell" else None,
        signal_confidence=signal_confidence,
        entry_reason=entry_reason,
        exit_reason=exit_reason,
        expected_risk=expected_risk,
        pnl=trade_pnl if filled_price is not None else None,
        market_regime=market_regime,
        order_id=order.get("id"),
        status=order.get("status", "submitted"),
        source=source,
        opened_at=datetime.utcnow() if side.lower() == "buy" else None,
        closed_at=datetime.utcnow() if side.lower() == "sell" else None,
        commit=False,
    )

    if side.lower() == "sell":
        pos_row = await db.execute(select(Position).where(Position.symbol == sym_u))
        sell_pos = pos_row.scalars().first()
        if sell_pos:
            clear_exit_block(sell_pos)

    if side.lower() == "buy":
        try:
            await watchlist_add_symbol(db, symbol, "", commit=False)
        except Exception as err:
            logger.warning("Could not add %s to watchlist after buy: %s", symbol, err)
        try:
            from app.services.portfolio_engine import sync_positions
            from app.services.position_stop_state import ensure_position_stop_floor

            await sync_positions(db)
            fill_px = float(filled_price or price or reference_price or 0)
            await ensure_position_stop_floor(
                db,
                symbol,
                entry_price=fill_px,
                atr=atr_at_entry,
                stop_loss=stop_loss,
                trailing_stop_distance=trailing_stop_distance,
                commit=False,
            )
        except Exception as err:
            logger.warning("Could not initialize stop floor for %s: %s", symbol, err)

    if commit:
        await db.commit()
    return {"blocked": False, "order": order, "trade": trade}


def _parse_alpaca_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    text = str(raw).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    return parsed


async def cancel_stale_buy_orders(minutes: int | None = None) -> dict:
    """Cancel unfilled BUY orders older than ``minutes``.

    Extended-hours market->limit conversions (and any other limit buy that
    never fills) sit open as DAY orders and reserve buying power, which starves
    later buys into ``insufficient buying power`` rejections. Sweeping them frees
    that cash so sizing reflects what is actually available.
    """
    if minutes is None:
        minutes = int(getattr(settings, "stale_buy_order_minutes", 15) or 0)
    result = {"cancelled": [], "checked": 0}
    if minutes <= 0:
        return result
    try:
        orders = await alpaca_client.get_open_orders()
    except Exception as err:
        logger.debug("Stale-buy sweep could not list open orders: %s", err)
        return result
    cutoff = datetime.utcnow() - timedelta(minutes=max(int(minutes), 1))
    for order in orders or []:
        if str(order.get("side", "")).lower() != "buy":
            continue
        result["checked"] += 1
        submitted = _parse_alpaca_ts(
            order.get("submitted_at") or order.get("created_at")
        )
        if submitted is not None and submitted > cutoff:
            continue
        oid = order.get("id")
        if not oid:
            continue
        try:
            await alpaca_client.cancel_order(str(oid))
            result["cancelled"].append(str(order.get("symbol", "")).upper())
            logger.info(
                "Cancelled stale buy order %s %s (submitted %s) to free buying power",
                order.get("symbol"),
                oid,
                order.get("submitted_at"),
            )
        except Exception as err:
            logger.debug("Could not cancel stale buy order %s: %s", oid, err)
    return result


async def _apply_recent_pdt_backoff_from_ledger(
    db: AsyncSession,
    pos: Position,
    symbol: str,
) -> bool:
    """Backfill PDT skip state from recent failed sell rows before retrying."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    rows = await db.execute(
        select(StrategyPerformance)
        .where(StrategyPerformance.symbol == symbol.upper())
        .where(StrategyPerformance.side == "sell")
        .where(StrategyPerformance.status == "failed:alpaca_403")
        .where(StrategyPerformance.created_at >= cutoff)
        .order_by(desc(StrategyPerformance.created_at))
        .limit(5)
    )
    for row in rows.scalars().all():
        text = " ".join(
            str(x or "")
            for x in (row.exit_reason, row.entry_reason, row.status)
        )
        if is_pdt_message(text):
            applied = apply_pdt_block(pos)
            if applied:
                await db.flush()
            return True
    return False

