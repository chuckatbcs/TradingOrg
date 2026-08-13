"""SPY regime detection (Gemini reviewer pattern)."""

from __future__ import annotations

from dataclasses import dataclass

import yfinance as yf


@dataclass
class RegimeState:
    label: str  # bull | choppy | bear
    multiplier: float
    spy_close: float | None = None
    sma20: float | None = None
    detail: str = ""


def detect_regime() -> RegimeState:
    """Classify market regime from SPY vs 20-day SMA."""
    try:
        hist = yf.Ticker("SPY").history(period="3mo", interval="1d")
        if hist.empty or len(hist) < 25:
            return RegimeState("choppy", 0.5, detail="insufficient SPY data")
        close = float(hist["Close"].iloc[-1])
        sma20 = float(hist["Close"].rolling(20).mean().iloc[-1])
        if close >= sma20 * 1.01:
            return RegimeState("bull", 1.0, close, sma20, "SPY above 20-SMA")
        if close <= sma20 * 0.99:
            return RegimeState("bear", 0.0, close, sma20, "SPY below 20-SMA")
        return RegimeState("choppy", 0.5, close, sma20, "SPY near 20-SMA")
    except Exception as exc:
        return RegimeState("choppy", 0.5, detail=str(exc))
