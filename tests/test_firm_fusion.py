"""Tests for hybrid signal fusion."""

from unittest.mock import patch

import pytest

from firm.signals.fusion import fuse_signal
from firm.signals.models import FusedSignal


def _mock_quant(passed=True, score=80.0):
    from firm.signals.quant_gate import QuantGateResult

    return QuantGateResult(
        passed=passed,
        score=score,
        filters={"trend": passed},
        metrics={"close": 100.0},
        blockers=[] if passed else ["trend"],
    )


def _mock_llm(passed=True, rating="Buy", score=1.0):
    from firm.signals.llm_gate import LLMGateResult

    return LLMGateResult(
        passed=passed,
        rating=rating,
        score=score,
        blockers=[] if passed else [f"rating {rating}"],
    )


def _mock_regime(label="bull", multiplier=1.0):
    from firm.universe.regime import RegimeState

    return RegimeState(label, multiplier, 500.0, 490.0, "test")


@pytest.mark.unit
@patch("firm.signals.fusion.detect_regime")
@patch("firm.signals.fusion.evaluate_llm")
@patch("firm.signals.fusion.evaluate_quant")
def test_fuse_signal_pass(mock_quant, mock_llm, mock_regime):
    mock_quant.return_value = _mock_quant(True, 80.0)
    mock_llm.return_value = _mock_llm(True, "Buy", 1.0)
    mock_regime.return_value = _mock_regime("bull", 1.0)

    run = {
        "id": "abc123",
        "ticker": "NVDA",
        "trade_date": "2025-07-01",
        "reports": {"final_trade_decision": "Rating: Buy"},
    }
    result = fuse_signal(run)

    assert isinstance(result, FusedSignal)
    assert result.fused_pass is True
    assert result.fused_score == 0.8
    assert result.quant_pass is True
    assert result.llm_pass is True


@pytest.mark.unit
@patch("firm.signals.fusion.detect_regime")
@patch("firm.signals.fusion.evaluate_llm")
@patch("firm.signals.fusion.evaluate_quant")
def test_fuse_signal_blocked_by_bear_regime(mock_quant, mock_llm, mock_regime):
    mock_quant.return_value = _mock_quant(True, 90.0)
    mock_llm.return_value = _mock_llm(True, "Buy", 1.0)
    mock_regime.return_value = _mock_regime("bear", 0.0)

    result = fuse_signal({"ticker": "AAPL", "trade_date": "2025-07-01"})

    assert result.fused_pass is False
    assert result.fused_score == 0.0
    assert any("bear" in b for b in result.blockers)


@pytest.mark.unit
@patch("firm.signals.fusion.detect_regime")
@patch("firm.signals.fusion.evaluate_llm")
@patch("firm.signals.fusion.evaluate_quant")
def test_fuse_signal_blocked_by_llm(mock_quant, mock_llm, mock_regime):
    mock_quant.return_value = _mock_quant(True, 80.0)
    mock_llm.return_value = _mock_llm(False, "Hold", 0.0)
    mock_regime.return_value = _mock_regime("bull", 1.0)

    result = fuse_signal({"ticker": "AAPL", "trade_date": "2025-07-01"})

    assert result.fused_pass is False
    assert result.llm_pass is False
