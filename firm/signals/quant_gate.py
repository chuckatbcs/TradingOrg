"""Quant leg of hybrid fusion — screener score + core filters."""

from __future__ import annotations

from dataclasses import dataclass

from firm.config import FIRM_CONFIG
from firm.universe.screener import ScreenResult, screen_symbol


@dataclass
class QuantGateResult:
    passed: bool
    score: float
    filters: dict[str, bool]
    metrics: dict[str, float]
    blockers: list[str]


def evaluate_quant(ticker: str) -> QuantGateResult:
    """Run screener filters; pass when score >= threshold and all core filters pass."""
    result: ScreenResult = screen_symbol(ticker)
    passed = result.passed
    blockers = list(result.blockers)
    if not passed and not blockers:
        blockers.append(f"quant score {result.score} < {FIRM_CONFIG.quant_min_score}")
    return QuantGateResult(
        passed=passed,
        score=result.score,
        filters=result.filters,
        metrics=result.metrics,
        blockers=blockers,
    )
