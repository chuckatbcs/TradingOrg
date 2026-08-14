"""Downside monitor on held positions (Gemini portfolio monitor pattern)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf
from stockstats import wrap as stockstats_wrap

from firm.storage.db import FirmDB


@dataclass
class MonitorAlert:
    ticker: str
    alert_type: str
    detail: str
    metrics: dict[str, float] = field(default_factory=dict)


def _indicators(df: pd.DataFrame) -> pd.DataFrame:
    ss = stockstats_wrap(df.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    }))
    for col in ("close_50_sma", "rsi"):
        _ = ss[col]
    return ss


def scan_position(ticker: str, avg_entry: float | None = None) -> list[MonitorAlert]:
    """Flag positions below 20-SMA proxy, RSI < 45, or >5% drawdown from entry."""
    alerts: list[MonitorAlert] = []
    try:
        hist = yf.Ticker(ticker).history(period="6mo", interval="1d")
        if hist.empty or len(hist) < 25:
            return alerts
        ss = _indicators(hist)
        row = ss.iloc[-1]
        close = float(row["close"])
        sma20 = float(hist["Close"].rolling(20).mean().iloc[-1])
        rsi = float(row.get("rsi") or 50)

        if close < sma20:
            alerts.append(MonitorAlert(
                ticker, "below_sma20", f"{ticker} below 20-SMA",
                {"close": close, "sma20": sma20, "rsi": rsi},
            ))
        if rsi < 45:
            alerts.append(MonitorAlert(
                ticker, "weak_rsi", f"{ticker} RSI {rsi:.0f} < 45",
                {"close": close, "rsi": rsi},
            ))
        if avg_entry and avg_entry > 0:
            dd = (close - avg_entry) / avg_entry
            if dd <= -0.05:
                alerts.append(MonitorAlert(
                    ticker, "drawdown", f"{ticker} down {dd:.1%} from entry",
                    {"close": close, "drawdown_pct": dd},
                ))
    except Exception as exc:
        alerts.append(MonitorAlert(ticker, "error", str(exc)))
    return alerts


def scan_all_positions() -> list[MonitorAlert]:
    """Run downside scan on all synced positions."""
    positions = FirmDB().list_positions()
    alerts: list[MonitorAlert] = []
    for p in positions:
        qty = float(p.get("qty") or 0)
        if qty <= 0:
            continue
        alerts.extend(scan_position(
            p["ticker"],
            avg_entry=float(p.get("avg_entry") or 0) or None,
        ))
    return alerts
