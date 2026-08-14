"""Regime reviewer lessons → TradingMemoryLog injection."""

from __future__ import annotations

import logging
from datetime import datetime

from firm.portfolio.monitor import scan_all_positions
from firm.storage.db import FirmDB
from firm.universe.regime import detect_regime
from tradingagents.agents.utils.memory import TradingMemoryLog
from tradingagents.default_config import DEFAULT_CONFIG

logger = logging.getLogger(__name__)


def generate_regime_lessons() -> list[str]:
    """Rule-based lessons from regime state and position monitor."""
    lessons: list[str] = []
    regime = detect_regime()

    if regime.label == "bear":
        lessons.append(
            "SPY is below 20-SMA — avoid new long entries until regime turns bullish."
        )
    elif regime.label == "choppy":
        lessons.append(
            "SPY is near 20-SMA — reduce position sizes and require stronger fused scores."
        )
    else:
        lessons.append("Bull regime — standard entry thresholds apply.")

    alerts = scan_all_positions()
    for alert in alerts[:5]:
        if alert.alert_type == "drawdown":
            lessons.append(
                f"Position {alert.ticker} is down >5% — review stop-loss or exit."
            )
        elif alert.alert_type == "below_sma20":
            lessons.append(
                f"{alert.ticker} trading below 20-SMA — momentum may be weakening."
            )

    return lessons


def inject_lessons_to_memory(lessons: list[str] | None = None) -> int:
    """
    Persist lessons to firm.db and append cross-ticker directives to TradingMemoryLog.
    Returns count of lessons injected.
    """
    lessons = lessons or generate_regime_lessons()
    if not lessons:
        return 0

    db = FirmDB()
    today = datetime.now().strftime("%Y-%m-%d")
    log = TradingMemoryLog(DEFAULT_CONFIG)

    for lesson in lessons:
        db.save_lesson(lesson, source="regime_reviewer")
        if not log._log_path:  # noqa: SLF001
            continue
        tag = f"[{today} | REGIME | directive | n/a]"
        entry = (
            f"{tag}\n\nDECISION:\n{lesson}\n\n"
            f"{TradingMemoryLog._SEPARATOR}"
        )
        with open(log._log_path, "a", encoding="utf-8") as f:  # noqa: SLF001
            f.write(entry)

    return len(lessons)


def run_regime_review(*, notify: bool = True) -> dict:
    """End-of-day review: lessons + optional Discord alert."""
    regime = detect_regime()
    lessons = generate_regime_lessons()
    count = inject_lessons_to_memory(lessons)

    result = {
        "regime": regime.label,
        "regime_multiplier": regime.multiplier,
        "lessons_injected": count,
        "lessons": lessons,
    }

    if notify:
        from firm.ops.notifications import notify

        body = "\n".join(f"• {lesson}" for lesson in lessons[:6])
        notify(
            f"Regime Review — {regime.label.upper()}",
            body or "No new lessons.",
            color=0xD29922,
        )

    return result
