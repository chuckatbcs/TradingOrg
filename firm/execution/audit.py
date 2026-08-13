"""Signal and execution audit helpers."""

from __future__ import annotations

from firm.storage.db import FirmDB


def log_fused_signal(signal: dict) -> int:
    return FirmDB().save_fused_signal(signal)


def log_execution(record: dict) -> int:
    return FirmDB().save_execution(record)
