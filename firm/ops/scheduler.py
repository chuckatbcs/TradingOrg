"""APScheduler jobs: premarket scan, midday monitor, close review."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from firm.config import FIRM_CONFIG
from firm.ops.killswitch import evaluate_kill_switch, is_new_buy_blocked
from firm.ops.notifications import notify
from firm.portfolio.monitor import scan_all_positions
from firm.portfolio.sync import sync_positions

if TYPE_CHECKING:
    from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def job_premarket_scan() -> dict:
    """Screen watchlist before the open; queue top finalists for analysis."""
    logger.info("premarket scan starting")
    evaluate_kill_switch()
    from firm.ops.screen_queue import queue_screener_finalists

    result = queue_screener_finalists(
        top_n=FIRM_CONFIG.premarket_screen_top_n,
        notify_discord=True,
        source="premarket_screen",
    )
    tickers = result["candidates"]
    if tickers and not result["queued"]:
        notify(
            "Premarket Screen",
            f"Top candidates (already queued): {', '.join(tickers)}",
        )
    return result


def job_midday_check() -> dict:
    """Sync positions, run kill switch, scan for downside alerts."""
    logger.info("midday check starting")
    sync_positions()
    ks = evaluate_kill_switch()
    alerts = scan_all_positions()
    if ks.triggered:
        notify(
            "Kill Switch Triggered",
            f"{ks.message}\nClosed: {', '.join(ks.closed_symbols) or 'none'}",
            color=0xF85149,
        )
    elif alerts:
        summary = "; ".join(f"{a.ticker}:{a.alert_type}" for a in alerts[:8])
        notify("Position Alerts", summary, color=0xD29922)
    return {
        "kill_switch": ks.triggered,
        "alerts": len(alerts),
        "new_buy_blocked": is_new_buy_blocked(),
    }


def job_close_review() -> dict:
    """End-of-day position sync and regime lesson injection."""
    logger.info("close review starting")
    sync_positions()
    from firm.learning.reviewer import run_regime_review

    review = run_regime_review(notify=True)
    return review


def start_scheduler() -> BackgroundScheduler | None:
    """Start the 3-job ET scheduler if enabled."""
    global _scheduler
    if not FIRM_CONFIG.scheduler_enabled:
        return None
    if _scheduler is not None:
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.warning("apscheduler not installed — pip install tradingagents[firm]")
        return None

    _scheduler = BackgroundScheduler(timezone="America/New_York")
    _scheduler.add_job(job_premarket_scan, CronTrigger(hour=8, minute=45))
    _scheduler.add_job(job_midday_check, CronTrigger(hour=12, minute=30))
    _scheduler.add_job(job_close_review, CronTrigger(hour=16, minute=5))
    _scheduler.start()
    logger.info("firm scheduler started (premarket/midday/close)")
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
