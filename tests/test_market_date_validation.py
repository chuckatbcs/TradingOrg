from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
from fastapi.testclient import TestClient


def _history(*dates: str) -> pd.DataFrame:
    idx = pd.to_datetime(list(dates))
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": 1.0, "Volume": 1000},
        index=idx,
    )


def test_validate_market_date_rejects_weekend_without_stock_bar(monkeypatch):
    from webapp import market_dates

    monkeypatch.setattr(
        market_dates,
        "_fetch_history",
        lambda ticker, start, end: _history("2026-07-02", "2026-07-03"),
    )

    result = market_dates.validate_market_date(
        "MU",
        "2026-07-04",
        now=datetime(2026, 7, 4, 18, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result["valid"] is False
    assert result["reason"] == "no_bar"
    assert "weekend" in result["message"].lower()
    assert result["latest_valid_date"] == "2026-07-03"


def test_validate_market_date_rejects_future_date(monkeypatch):
    from webapp import market_dates

    monkeypatch.setattr(
        market_dates,
        "_fetch_history",
        lambda ticker, start, end: _history("2026-07-02", "2026-07-03"),
    )

    result = market_dates.validate_market_date(
        "MU",
        "2026-07-06",
        now=datetime(2026, 7, 4, 18, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result["valid"] is False
    assert result["reason"] == "future"
    assert "latest available market date is 2026-07-03" in result["message"].lower()


def test_validate_market_date_rejects_known_no_bar_holiday(monkeypatch):
    from webapp import market_dates

    monkeypatch.setattr(
        market_dates,
        "_fetch_history",
        lambda ticker, start, end: _history("2024-07-03", "2024-07-05"),
    )

    result = market_dates.validate_market_date(
        "MU",
        "2024-07-04",
        now=datetime(2024, 7, 5, 18, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result["valid"] is False
    assert result["reason"] == "no_bar"
    assert "no yfinance daily bar" in result["message"].lower()
    assert result["latest_valid_date"] == "2024-07-05"


def test_validate_market_date_accepts_valid_market_day(monkeypatch):
    from webapp import market_dates

    monkeypatch.setattr(
        market_dates,
        "_fetch_history",
        lambda ticker, start, end: _history("2024-07-03", "2024-07-05"),
    )

    result = market_dates.validate_market_date(
        "MU",
        "2024-07-03",
        now=datetime(2024, 7, 5, 18, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result["valid"] is True
    assert result["reason"] == "ok"
    assert result["latest_valid_date"] == "2024-07-05"


def test_validate_market_date_rejects_today_before_market_close(monkeypatch):
    from webapp import market_dates

    monkeypatch.setattr(
        market_dates,
        "_fetch_history",
        lambda ticker, start, end: _history("2026-07-02", "2026-07-03", "2026-07-06"),
    )

    result = market_dates.validate_market_date(
        "MU",
        "2026-07-06",
        now=datetime(2026, 7, 6, 12, tzinfo=ZoneInfo("America/New_York")),
    )

    assert result["valid"] is False
    assert result["reason"] == "incomplete_today"
    assert result["latest_valid_date"] == "2026-07-03"


def test_market_date_validate_endpoint(monkeypatch):
    from webapp import market_dates
    from webapp.server import app

    monkeypatch.setattr(
        market_dates,
        "_fetch_history",
        lambda ticker, start, end: _history("2024-07-03", "2024-07-05"),
    )

    client = TestClient(app)
    response = client.get("/api/market-date/validate?ticker=MU&date=2024-07-04")

    assert response.status_code == 200
    assert response.json()["valid"] is False
    assert response.json()["reason"] == "no_bar"


def test_analyze_rejects_invalid_market_date(monkeypatch):
    from webapp import market_dates
    from webapp.server import app

    monkeypatch.setattr(
        market_dates,
        "_fetch_history",
        lambda ticker, start, end: _history("2024-07-03", "2024-07-05"),
    )

    client = TestClient(app)
    response = client.post(
        "/api/analyze",
        json={
            "ticker": "MU",
            "trade_date": "2024-07-04",
            "analysts": ["market"],
        },
    )

    assert response.status_code == 400
    assert "No yfinance daily bar" in response.json()["detail"]

