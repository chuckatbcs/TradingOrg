"""Tests for ATR position sizing."""

import pytest

from firm.risk.manager import calculate_position_size, current_drawdown
from firm.risk.sizing import apply_regime_size_multiplier, compute_order_qty, gemini_cap_pct


@pytest.mark.unit
def test_gemini_cap_pct_high_volatility():
    # Very high ATR% → cap shrinks below max_position_pct (5%)
    cap = gemini_cap_pct(1.0)
    assert cap == pytest.approx(0.03, abs=0.001)


@pytest.mark.unit
def test_calculate_position_size_basic():
    pct, shares = calculate_position_size(
        equity=100_000,
        entry_price=100.0,
        atr=2.0,
        risk_per_trade=0.012,
        stop_atr_mult=2.0,
        max_position_pct=0.05,
    )
    assert shares >= 1
    assert pct <= 0.05


@pytest.mark.unit
def test_drawdown_halts_sizing():
    pct, shares = calculate_position_size(
        equity=100_000,
        entry_price=100.0,
        atr=2.0,
        drawdown_pct=-0.16,
    )
    assert shares == 0
    assert pct == 0.0


@pytest.mark.unit
def test_compute_order_qty_returns_stops():
    shares, pct, stop, target = compute_order_qty(
        equity=50_000,
        entry_price=50.0,
        atr=1.5,
    )
    assert shares > 0
    assert stop < 50.0
    assert target > 50.0


@pytest.mark.unit
def test_apply_regime_size_multiplier():
    assert apply_regime_size_multiplier(10, 0.0) == 0
    assert apply_regime_size_multiplier(10, 1.0) == 10
    assert apply_regime_size_multiplier(11, 0.5) == 5


@pytest.mark.unit
def test_current_drawdown():
    assert current_drawdown(90_000, 100_000) == pytest.approx(-0.10)
