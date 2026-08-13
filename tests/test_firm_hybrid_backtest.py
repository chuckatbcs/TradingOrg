"""Tests for hybrid backtest (mocked price data, no network)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from firm.backtest.hybrid_backtest import (
    HistoricalSignal,
    _compute_metrics,
    _detect_regime_at_index,
    _forward_return,
    _fuse_historical,
    _llm_proxy_hindsight,
    _llm_proxy_momentum,
    _screen_at_index,
    run_hybrid_backtest,
)
from firm.universe.screener import ScreenResult


def _synthetic_ohlcv(n: int = 260, *, trend: float = 0.001) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    dates = pd.bdate_range("2023-01-01", periods=n)
    close = 100 * np.cumprod(1 + trend + rng.normal(0, 0.005, n))
    df = pd.DataFrame(
        {
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": rng.integers(800_000, 1_200_000, n).astype(float),
        },
        index=dates,
    )
    return df


@pytest.mark.unit
def test_forward_return():
    close = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0, 110.0])
    assert _forward_return(close, 0, 5) == pytest.approx(0.10)
    assert _forward_return(close, 0, 20) is None


@pytest.mark.unit
def test_detect_regime_bull():
    df = _synthetic_ohlcv(60, trend=0.002)
    label, mult = _detect_regime_at_index(df)
    assert label in ("bull", "choppy", "bear")
    assert mult in (0.0, 0.5, 1.0)


@pytest.mark.unit
def test_fuse_historical_blocks_bear():
    quant = ScreenResult("TEST", True, 80.0, metrics={"close": 10})
    score, passed, blockers = _fuse_historical(quant, "Buy", True, 1.0, "bear", 0.0)
    assert passed is False
    assert score == 0.0
    assert any("bear" in b for b in blockers)


@pytest.mark.unit
def test_llm_proxy_momentum_buy(monkeypatch):
    from firm.config import FIRM_CONFIG

    monkeypatch.setattr(FIRM_CONFIG, "quant_min_score", 60.0)
    quant = ScreenResult(
        "TEST",
        True,
        75.0,
        metrics={"close": 110.0, "sma50": 100.0},
    )
    rating, score, passed = _llm_proxy_momentum(quant)
    assert rating == "Buy"
    assert passed is True
    assert score == 1.0


@pytest.mark.unit
def test_llm_proxy_hindsight_lookahead():
    rating, score, passed = _llm_proxy_hindsight(0.08)
    assert rating == "Buy"
    assert passed is True
    rating2, _, passed2 = _llm_proxy_hindsight(0.01)
    assert passed2 is False
    assert rating2 == "Hold"


@pytest.mark.unit
def test_compute_metrics_hit_rate():
    signals = [
        HistoricalSignal(
            trade_date="2024-01-05",
            ticker="AAA",
            quant_pass=True,
            quant_score=80,
            llm_rating="Buy",
            llm_pass=True,
            llm_score=1.0,
            regime="bull",
            regime_multiplier=1.0,
            fused_score=0.8,
            fused_pass=True,
            forward_return_5d=0.02,
            forward_return_20d=0.05,
            strategy="fused",
            strategy_pass=True,
        ),
        HistoricalSignal(
            trade_date="2024-01-12",
            ticker="BBB",
            quant_pass=True,
            quant_score=70,
            llm_rating="Buy",
            llm_pass=True,
            llm_score=1.0,
            regime="bull",
            regime_multiplier=1.0,
            fused_score=0.7,
            fused_pass=True,
            forward_return_5d=-0.01,
            forward_return_20d=-0.02,
            strategy="fused",
            strategy_pass=True,
        ),
    ]
    m = _compute_metrics(signals, "fused")
    assert m.signals == 2
    assert m.hit_rate_5d == 0.5
    assert m.avg_forward_return_20d == pytest.approx(0.015)
    assert m.max_drawdown is not None


@pytest.mark.unit
def test_screen_at_index_monkeypatched(monkeypatch):
    from firm.config import FIRM_CONFIG

    monkeypatch.setattr(FIRM_CONFIG, "screener_mode", "scoring")
    monkeypatch.setattr(FIRM_CONFIG, "quant_min_score", 50.0)
    df = _synthetic_ohlcv(220)

    class FakeRow:
        def get(self, key, default=0):
            data = {
                "close": 110.0,
                "close_200_sma": 100.0,
                "close_50_sma": 105.0,
                "close_10_ema": 108.0,
                "adx": 25.0,
                "macdh": 0.5,
                "rsi": 55.0,
            }
            return data.get(key, default)

        def __getitem__(self, key):
            return self.get(key)

    class FakeILoc:
        def __getitem__(self, idx):
            return FakeRow()

    class FakeSS:
        iloc = FakeILoc()

    monkeypatch.setattr("firm.backtest.hybrid_backtest._compute_indicators", lambda d: FakeSS())
    result = _screen_at_index("TEST", df)
    assert result.ticker == "TEST"
    assert result.score >= 50


@pytest.mark.unit
def test_run_hybrid_backtest_mocked(monkeypatch):
    spy = _synthetic_ohlcv(300)
    aaa = _synthetic_ohlcv(300, trend=0.0015)
    bbb = _synthetic_ohlcv(300, trend=0.0005)

    def fake_fetch(ticker, start, end):
        if ticker == "SPY":
            return spy
        if ticker == "AAA":
            return aaa
        if ticker == "BBB":
            return bbb
        return pd.DataFrame()

    monkeypatch.setattr("firm.backtest.hybrid_backtest._fetch_history", fake_fetch)

    result = run_hybrid_backtest(
        "2024-01-01",
        "2024-06-30",
        tickers=["AAA", "BBB"],
        mode="compare",
        llm_proxy="momentum",
    )
    assert result["tickers_loaded"] == 2
    assert "scoring" in result["screener_comparison"]
    assert "strict" in result["screener_comparison"]
    for sm in ("scoring", "strict"):
        metrics = result["screener_comparison"][sm]["metrics"]
        assert "fused" in metrics
        assert "quant_only" in metrics
        assert "llm_only" in metrics


@pytest.mark.unit
def test_backtest_api_endpoint(monkeypatch):
    from fastapi.testclient import TestClient

    from webapp.server import app

    def fake_run(*args, **kwargs):
        return {
            "start_date": "2024-01-01",
            "end_date": "2024-06-30",
            "universe_size": 2,
            "tickers_loaded": 2,
            "evaluation_dates": 10,
            "llm_proxy_disclaimer": "test",
            "screener_comparison": {"scoring": {"metrics": {}, "sample_signals": []}},
        }

    monkeypatch.setattr("firm.backtest.hybrid_backtest.run_hybrid_backtest", fake_run)
    client = TestClient(app)
    resp = client.post(
        "/api/firm/backtest",
        json={"start_date": "2024-01-01", "end_date": "2024-06-30", "mode": "compare"},
    )
    assert resp.status_code == 200
    assert resp.json()["evaluation_dates"] == 10
