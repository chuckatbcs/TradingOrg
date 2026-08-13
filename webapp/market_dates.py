"""Market-date validation for web analysis and backtest forms."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from tradingagents.dataflows.symbol_utils import normalize_symbol

MARKET_TZ = ZoneInfo("America/New_York")
RELIABLE_CLOSE_HOUR_ET = 17


def latest_sensible_date(now: datetime | None = None) -> str:
    """Best no-network default for date-picker max values."""

    now_et = _now_et(now)
    candidate = now_et.date()
    if now_et.hour < RELIABLE_CLOSE_HOUR_ET:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.isoformat()


def validate_market_date(
    ticker: str,
    trade_date: str,
    *,
    now: datetime | None = None,
) -> dict[str, str | bool | None]:
    """Return whether a run date has a daily yfinance bar for ``ticker``.

    yfinance is the market calendar here: weekends, holidays, foreign exchange
    closures, stale symbols, and ticker-specific missing data all collapse to
    "no bar" for the requested date.
    """

    try:
        target = date.fromisoformat(trade_date)
    except ValueError:
        return _result(ticker, trade_date, False, "invalid_format", "Date must be YYYY-MM-DD.")

    now_et = _now_et(now)
    today = now_et.date()
    incomplete_today = target == today and now_et.hour < RELIABLE_CLOSE_HOUR_ET

    fetch_end = max(target, today) + timedelta(days=3)
    hist = _fetch_history(
        ticker,
        target - timedelta(days=10),
        fetch_end,
    )
    bar_dates = _bar_dates(hist)
    latest_valid = _latest_valid_date(bar_dates, today, exclude_today=incomplete_today)
    latest_valid_text = latest_valid.isoformat() if latest_valid else None

    if incomplete_today:
        return _result(
            ticker,
            trade_date,
            False,
            "incomplete_today",
            (
                f"Today's market data may be incomplete before "
                f"{RELIABLE_CLOSE_HOUR_ET}:00 ET. Latest available market date is "
                f"{latest_valid_text or 'unknown'}."
            ),
            latest_valid_text,
        )

    if target > today:
        return _result(
            ticker,
            trade_date,
            False,
            "future",
            f"Future dates are not available. Latest available market date is {latest_valid_text or 'unknown'}.",
            latest_valid_text,
        )

    if target not in bar_dates:
        weekend = target.weekday() >= 5
        qualifier = "weekend/no-session date" if weekend else "market holiday or ticker-specific no-data date"
        return _result(
            ticker,
            trade_date,
            False,
            "no_bar",
            (
                f"No yfinance daily bar for {ticker.upper()} on {trade_date} "
                f"({qualifier}). Latest available market date is {latest_valid_text or 'unknown'}."
            ),
            latest_valid_text,
        )

    return _result(
        ticker,
        trade_date,
        True,
        "ok",
        f"{ticker.upper()} has a yfinance daily bar for {trade_date}.",
        latest_valid_text,
    )


def _fetch_history(ticker: str, start: date, end: date) -> pd.DataFrame:
    canonical = normalize_symbol(ticker)
    return yf.Ticker(canonical).history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
    )


def _bar_dates(hist: pd.DataFrame) -> set[date]:
    if hist is None or hist.empty:
        return set()
    idx = pd.to_datetime(hist.index, errors="coerce")
    dates: set[date] = set()
    for value in idx:
        if pd.isna(value):
            continue
        if getattr(value, "tzinfo", None) is not None:
            value = value.tz_convert(MARKET_TZ)
        dates.add(value.date())
    return dates


def _latest_valid_date(
    bar_dates: set[date],
    today: date,
    *,
    exclude_today: bool,
) -> date | None:
    cutoff = today - timedelta(days=1) if exclude_today else today
    candidates = [value for value in bar_dates if value <= cutoff]
    return max(candidates) if candidates else None


def _now_et(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(MARKET_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=MARKET_TZ)
    return now.astimezone(MARKET_TZ)


def _result(
    ticker: str,
    trade_date: str,
    valid: bool,
    reason: str,
    message: str,
    latest_valid_date: str | None = None,
) -> dict[str, str | bool | None]:
    return {
        "ticker": ticker.upper(),
        "date": trade_date,
        "valid": valid,
        "reason": reason,
        "message": message,
        "latest_valid_date": latest_valid_date,
        "can_override": not valid and reason not in {"invalid_format"},
    }

