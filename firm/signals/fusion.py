"""Hybrid fusion engine — both quant and LLM legs must pass."""

from __future__ import annotations

from firm.config import FIRM_CONFIG
from firm.signals.llm_gate import evaluate_llm
from firm.signals.models import FusedSignal
from firm.signals.quant_gate import evaluate_quant
from firm.universe.regime import detect_regime


def fuse_signal(run: dict, *, side: str = "buy") -> FusedSignal:
    """Evaluate quant + LLM gates and combine into a fused signal."""
    ticker = run.get("ticker", "")
    trade_date = run.get("trade_date", "")
    run_id = run.get("id")

    quant = evaluate_quant(ticker)
    llm = evaluate_llm(run, side=side)
    regime = detect_regime()

    quant_norm = max(0.0, quant.score / 100.0)
    llm_norm = abs(llm.score)
    fused_score = round(quant_norm * llm_norm * regime.multiplier, 4)

    blockers: list[str] = []
    if not quant.passed:
        blockers.extend(quant.blockers or ["quant gate failed"])
    if not llm.passed:
        blockers.extend(llm.blockers or ["llm gate failed"])
    if regime.multiplier == 0.0:
        blockers.append(f"bear regime ({regime.label}) blocks entries")
    if fused_score < FIRM_CONFIG.fusion_entry_threshold:
        blockers.append(
            f"fused score {fused_score} < {FIRM_CONFIG.fusion_entry_threshold}"
        )

    fused_pass = (
        quant.passed
        and llm.passed
        and regime.multiplier > 0
        and fused_score >= FIRM_CONFIG.fusion_entry_threshold
    )

    return FusedSignal(
        ticker=ticker,
        trade_date=trade_date,
        run_id=run_id,
        quant_pass=quant.passed,
        quant_score=quant.score,
        llm_pass=llm.passed,
        llm_rating=llm.rating,
        llm_score=llm.score,
        regime=regime.label,
        regime_multiplier=regime.multiplier,
        fused_score=fused_score,
        fused_pass=fused_pass,
        blockers=blockers,
        side=side,
    )


# Back-compat alias
fuse_run = fuse_signal
