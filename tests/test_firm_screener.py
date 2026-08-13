"""Tests for firm screener."""



import pandas as pd

import pytest



from firm.universe.screener import ScreenResult, _volume_ratio, screen_symbol, screen_universe





@pytest.mark.unit

def test_screen_insufficient_history(monkeypatch):

    monkeypatch.setattr(

        "firm.universe.screener.yf.Ticker",

        lambda t: type("T", (), {

            "history": lambda self, **kw: pd.DataFrame(),

        })(),

    )

    result = screen_symbol("FAKE")

    assert result.passed is False

    assert "insufficient" in result.blockers[0]





@pytest.mark.unit

def test_volume_ratio_uses_20_day_average():

    df = pd.DataFrame({

        "open": [1.0] * 20,

        "high": [1.0] * 20,

        "low": [1.0] * 20,

        "close": [1.0] * 20,

        "volume": [100.0] * 19 + [200.0],

    })

    assert _volume_ratio(df) == pytest.approx(200.0 / 105.0)





@pytest.mark.unit

def test_screen_universe_filters_and_sorts(monkeypatch):

    def fake_screen(ticker):

        scores = {"AAA": 90.0, "BBB": 70.0, "CCC": 50.0}

        score = scores.get(ticker, 0.0)

        return ScreenResult(

            ticker=ticker,

            passed=score >= 60,

            score=score,

            filters={},

            metrics={},

            blockers=[] if score >= 60 else ["low score"],

        )



    monkeypatch.setattr("firm.universe.screener.screen_symbol", fake_screen)

    results = screen_universe(["AAA", "BBB", "CCC"], top_n=2, pass_only=True)

    assert len(results) == 2

    assert results[0].ticker == "AAA"

    assert results[1].ticker == "BBB"





@pytest.mark.unit

def test_screen_universe_pass_only_false_returns_all(monkeypatch):

    def fake_screen(ticker):

        scores = {"AAA": 90.0, "BBB": 50.0}

        score = scores.get(ticker, 0.0)

        return ScreenResult(

            ticker=ticker,

            passed=score >= 60,

            score=score,

            filters={},

            metrics={},

            blockers=[] if score >= 60 else ["low score"],

        )



    monkeypatch.setattr("firm.universe.screener.screen_symbol", fake_screen)

    results = screen_universe(["AAA", "BBB"], pass_only=False)

    assert len(results) == 2

    assert results[0].ticker == "AAA"

    assert results[1].ticker == "BBB"





@pytest.mark.unit

def test_screen_scoring_mode_passes_on_score_only(monkeypatch):

    from firm.config import FIRM_CONFIG

    monkeypatch.setattr(FIRM_CONFIG, "screener_mode", "scoring")

    monkeypatch.setattr(FIRM_CONFIG, "quant_min_score", 50.0)

    metrics = {

        "close": 10.5,

        "close_200_sma": 9.0,

        "close_50_sma": 10.0,

        "close_10_ema": 10.2,

        "adx": 25.0,

        "macdh": -0.1,

        "rsi": 55.0,

    }

    class FakeRow:

        def get(self, key, default=0):

            return metrics.get(key, default)

        def __getitem__(self, key):

            return metrics[key]

    class FakeILoc:

        def __getitem__(self, idx):

            return FakeRow()

    class FakeSS:

        iloc = FakeILoc()

    monkeypatch.setattr(

        "firm.universe.screener.yf.Ticker",

        lambda t: type("T", (), {

            "history": lambda self, **kw: pd.DataFrame({

                "Open": [10.0] * 220,

                "High": [11.0] * 220,

                "Low": [9.0] * 220,

                "Close": [10.5] * 220,

                "Volume": [100000.0] * 219 + [200000.0],

            }),

        })(),

    )

    monkeypatch.setattr("firm.universe.screener._compute_indicators", lambda df: FakeSS())

    result = screen_symbol("TEST")

    assert result.score >= 50

    assert result.passed is True

    assert "macd" in result.advisory

    assert result.blockers == []





@pytest.mark.unit

def test_screen_strict_mode_requires_all_filters(monkeypatch):

    from firm.config import FIRM_CONFIG

    monkeypatch.setattr(FIRM_CONFIG, "screener_mode", "strict")

    monkeypatch.setattr(FIRM_CONFIG, "quant_min_score", 50.0)

    metrics = {

        "close": 10.5,

        "close_200_sma": 9.0,

        "close_50_sma": 10.0,

        "close_10_ema": 10.2,

        "adx": 25.0,

        "macdh": -0.1,

        "rsi": 55.0,

    }

    class FakeRow:

        def get(self, key, default=0):

            return metrics.get(key, default)

        def __getitem__(self, key):

            return metrics[key]

    class FakeILoc:

        def __getitem__(self, idx):

            return FakeRow()

    class FakeSS:

        iloc = FakeILoc()

    monkeypatch.setattr(

        "firm.universe.screener.yf.Ticker",

        lambda t: type("T", (), {

            "history": lambda self, **kw: pd.DataFrame({

                "Open": [10.0] * 220,

                "High": [11.0] * 220,

                "Low": [9.0] * 220,

                "Close": [10.5] * 220,

                "Volume": [100000.0] * 219 + [200000.0],

            }),

        })(),

    )

    monkeypatch.setattr("firm.universe.screener._compute_indicators", lambda df: FakeSS())

    result = screen_symbol("TEST")

    assert result.passed is False

    assert "macd" in result.blockers

    assert result.advisory == []



@pytest.mark.unit

def test_screen_strict_mode_passed_has_no_blockers(monkeypatch):

    from firm.config import FIRM_CONFIG

    monkeypatch.setattr(FIRM_CONFIG, "screener_mode", "strict")

    monkeypatch.setattr(FIRM_CONFIG, "quant_min_score", 50.0)

    metrics = {

        "close": 10.5,

        "close_200_sma": 9.0,

        "close_50_sma": 10.0,

        "close_10_ema": 10.2,

        "adx": 25.0,

        "macdh": 0.1,

        "rsi": 55.0,

    }

    class FakeRow:

        def get(self, key, default=0):

            return metrics.get(key, default)

        def __getitem__(self, key):

            return metrics[key]

    class FakeILoc:

        def __getitem__(self, idx):

            return FakeRow()

    class FakeSS:

        iloc = FakeILoc()

    monkeypatch.setattr(

        "firm.universe.screener.yf.Ticker",

        lambda t: type("T", (), {

            "history": lambda self, **kw: pd.DataFrame({

                "Open": [10.0] * 220,

                "High": [11.0] * 220,

                "Low": [9.0] * 220,

                "Close": [10.5] * 220,

                "Volume": [100000.0] * 219 + [200000.0],

            }),

        })(),

    )

    monkeypatch.setattr("firm.universe.screener._compute_indicators", lambda df: FakeSS())

    result = screen_symbol("TEST")

    assert result.passed is True

    assert result.blockers == []

    assert result.advisory == []

