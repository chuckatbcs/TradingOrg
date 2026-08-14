"""LLM leg of hybrid fusion — TradingAgents run output to rating score."""

from __future__ import annotations

from dataclasses import dataclass

from firm.config import FIRM_CONFIG
from tradingagents.agents.utils.rating import parse_rating

_RATING_SCORES: dict[str, float] = {
    "Buy": 1.0,
    "Overweight": 0.7,
    "Hold": 0.0,
    "Underweight": -0.7,
    "Sell": -1.0,
}


@dataclass
class LLMGateResult:
    passed: bool
    rating: str
    score: float
    blockers: list[str]
    side: str = "buy"


def _decision_text(run: dict) -> str:
    reports = run.get("reports") or {}
    return (
        reports.get("final_trade_decision")
        or reports.get("risk_judge")
        or run.get("decision")
        or ""
    )


def evaluate_llm(run: dict, *, side: str = "buy") -> LLMGateResult:
    """Map a completed web run to LLM gate pass/fail."""
    text = _decision_text(run)
    rating = parse_rating(text)
    score = _RATING_SCORES.get(rating, 0.0)
    blockers: list[str] = []

    if side == "buy":
        passed = rating in FIRM_CONFIG.entry_ratings and score > 0
        if not passed:
            blockers.append(f"LLM rating {rating} not in {FIRM_CONFIG.entry_ratings}")
    else:
        passed = rating in FIRM_CONFIG.exit_ratings and score < 0
        if not passed:
            blockers.append(f"LLM rating {rating} not in {FIRM_CONFIG.exit_ratings}")

    return LLMGateResult(
        passed=passed,
        rating=rating,
        score=score,
        blockers=blockers,
        side=side,
    )
