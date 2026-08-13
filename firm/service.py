"""High-level firm operations for the web API."""

from __future__ import annotations

import logging

import yfinance as yf

from firm.config import FIRM_CONFIG
from firm.execution.audit import log_execution, log_fused_signal
from firm.execution.orders import execute_market_order
from firm.ops.killswitch import KillSwitch
from firm.portfolio.sync import sync_positions
from firm.risk.manager import PositionInfo, check_sector_limit
from firm.risk.sizing import apply_regime_size_multiplier, compute_order_qty
from firm.signals.fusion import fuse_run
from firm.universe.watchlist import sector_for

logger = logging.getLogger(__name__)


def evaluate_and_store_fusion(run: dict) -> dict:
    signal = fuse_run(run)
    record = signal.to_dict()
    log_fused_signal(record)
    return record


def execute_fused_run(run: dict) -> dict:
    """Paper-execute a completed run when fused signal passes all gates."""
    record = run.get("fused_signal") or evaluate_and_store_fusion(run)

    if not record.get("fused_pass"):
        return {"status": "blocked", "reason": "fused_signal_failed", "fused": record}

    ks = KillSwitch()
    blocked, block_reason = ks.new_buy_blocked()
    if blocked:
        return {"status": "blocked", "reason": block_reason, "fused": record}

    loss_check = ks.check_daily_loss(auto_trigger=True)
    if loss_check.get("triggered"):
        return {"status": "blocked", "reason": "kill_switch_triggered", "fused": record}

    ticker = record.get("ticker") or run.get("ticker", "")
    positions = sync_positions()

    if len(positions) >= FIRM_CONFIG.max_positions:
        return {
            "status": "blocked",
            "reason": f"max_positions ({FIRM_CONFIG.max_positions})",
            "fused": record,
        }

    sector = sector_for(ticker)
    pos_infos = [
        PositionInfo(symbol=p["ticker"], sector=p.get("sector", "Other"))
        for p in positions
    ]
    if not check_sector_limit(sector, pos_infos):
        return {"status": "blocked", "reason": f"sector_cap:{sector}", "fused": record}

    try:
        hist = yf.Ticker(ticker).history(period="3mo", interval="1d")
        if hist.empty:
            return {"status": "blocked", "reason": "no_price_data", "fused": record}
        entry_price = float(hist["Close"].iloc[-1])
        high = hist["High"]
        low = hist["Low"]
        close = hist["Close"]
        tr = (high - low).combine((high - close.shift()).abs(), max).combine(
            (low - close.shift()).abs(), max
        )
        atr = float(tr.rolling(14).mean().iloc[-1])
    except Exception as exc:
        return {"status": "blocked", "reason": f"pricing_error:{exc}", "fused": record}

    try:
        from firm.execution import alpaca as alpaca_client

        account = alpaca_client.get_account()
        equity = float(account.get("equity", 0))
    except Exception as exc:
        return {"status": "blocked", "reason": f"account_error:{exc}", "fused": record}

    shares, position_pct, stop, target = compute_order_qty(
        equity=equity,
        entry_price=entry_price,
        atr=atr,
    )
    regime_mult = float(record.get("regime_multiplier") or 1.0)
    shares = apply_regime_size_multiplier(shares, regime_mult)
    if shares <= 0:
        return {"status": "blocked", "reason": "zero_shares_after_sizing", "fused": record}

    result = execute_market_order(
        run_id=run.get("id"),
        symbol=ticker,
        side="buy",
        qty=shares,
    )
    exec_record = {
        "run_id": run.get("id"),
        "ticker": ticker,
        "side": "buy",
        "qty": shares,
        "notional": round(shares * entry_price, 2),
        "order_id": result.get("order_id"),
        "status": result.get("status", "unknown"),
        "message": result.get("reason"),
    }
    log_execution(exec_record)
    sync_positions()

    return {
        "status": result.get("status"),
        "order_id": result.get("order_id"),
        "qty": shares,
        "entry_price": entry_price,
        "stop_loss": stop,
        "take_profit": target,
        "position_pct": position_pct,
        "fused": record,
        "execution": exec_record,
    }
