"""Tests for ATR risk sizing."""

import pytest

from firm.risk.manager import calculate_position_size
from firm.risk.sizing import apply_regime_size_multiplier, compute_order_qty, gemini_cap_pct


@pytest.mark.unit
def test_gemini_cap_limits_high_volatility():
    assert gemini_cap_pct(0.05) == pytest.approx(0.05)
    # Very high vol shrinks below 5% cap
    assert gemini_cap_pct(1.0) == pytest.approx(0.03)


@pytest.mark.unit
def test_calculate_position_size_respects_cap():
    pct, shares = calculate_position_size(
        equity=100_000,
        entry_price=200.0,
        atr=5.0,
        risk_per_trade=0.012,
        max_position_pct=0.05,
    )
    assert shares >= 1
    assert pct <= 0.05


@pytest.mark.unit
def test_compute_order_qty_returns_stops():
    shares, pct, stop, target = compute_order_qty(
        equity=50_000,
        entry_price=100.0,
        atr=2.0,
    )
    assert shares >= 1
    assert stop < 100.0
    assert target > 100.0


@pytest.mark.unit
def test_regime_multiplier_reduces_shares():
    assert apply_regime_size_multiplier(10, 0.5) == 5
    assert apply_regime_size_multiplier(10, 0.0) == 0
    assert apply_regime_size_multiplier(10, 1.0) == 10
