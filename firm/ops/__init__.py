"""Firm operations: scheduler, kill switch, notifications."""

from firm.ops.killswitch import (
    KillSwitch,
    KillSwitchResult,
    evaluate_kill_switch,
    is_new_buy_blocked,
)
from firm.ops.notifications import notify, notify_execution, notify_fused_signal
from firm.ops.scheduler import (
    job_close_review,
    job_midday_check,
    job_premarket_scan,
    start_scheduler,
    stop_scheduler,
)

__all__ = [
    "KillSwitch",
    "KillSwitchResult",
    "evaluate_kill_switch",
    "is_new_buy_blocked",
    "job_close_review",
    "job_midday_check",
    "job_premarket_scan",
    "notify",
    "notify_execution",
    "notify_fused_signal",
    "start_scheduler",
    "stop_scheduler",
]
