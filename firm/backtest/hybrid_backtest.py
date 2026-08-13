"""
Cheap hybrid backtest: quant gate + LLM rating proxy on historical dates.

IMPORTANT: The LLM leg uses a synthetic proxy (momentum or hindsight labels), NOT
replayed TradingAgents output. Results measure quant + proxy fusion mechanics only.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal

import numpy as np
import pandas as pd
import yfinance as yf

from firm.config import FIRM_CONFIG
from firm.universe.screener import ScreenResult, _compute_indicators, _volume_ratio

logger = logging.getLogger(__name__)

_LLM_SCORES: dict[str, float] = {
    "Buy": 1.0,
    "Overweight": 0.7,
    "Hold": 0.0,
    "Underweight": -0.7,
    "Sell": -1.0,
}

StrategyMode = Literal["fused", "quant_only", "llm_only", "compare"]
LlmProxyMode = Literal["momentum", "hindsight"]


@dataclass
class HistoricalSignal:
    trade_date: str
    ticker: str
    quant_pass: bool
    quant_score: float
    llm_rating: str
    llm_pass: bool
    llm_score: float
    regime: str
    regime_multiplier: float
    fused_score: float
    fused_pass: bool
    forward_return_5d: float | None = None
    forward_return_20d: float | None = None
    strategy: str = "fused"
    strategy_pass: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StrategyMetrics:
    strategy: str
    evaluations: int = 0
    signals: int = 0
    pass_rate: float = 0.0
    hit_rate_5d: float | None = None
    hit_rate_20d: float | None = None
    avg_forward_return_5d: float | None = None
    avg_forward_return_20d: float | None = None
    max_drawdown: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _normalize_ohlcv(hist: pd.DataFrame) -> pd.DataFrame:
    return hist.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )


def _screen_at_index(
    ticker: str,
    df: pd.DataFrame,
    *,
    screener_mode: str | None = None,
) -> ScreenResult:
    """Run screener logic on the last row of a pre-truncated OHLCV frame."""
    cfg = FIRM_CONFIG
    mode = (screener_mode or cfg.screener_mode).strip().lower()
    blockers: list[str] = []
    advisory: list[str] = []

    if df.empty or len(df) < 210:
        return ScreenResult(ticker, False, 0.0, blockers=["insufficient history"])

    ss = _compute_indicators(df)
    row = ss.iloc[-1]
    close = float(row["close"])
    ema200 = float(row.get("close_200_sma") or 0)
    ema50 = float(row.get("close_50_sma") or 0)
    ema21 = float(row.get("close_10_ema") or 0)
    adx = float(row.get("adx") or 0)
    macdh = float(row.get("macdh") or 0)
    rsi = float(row.get("rsi") or 50)
    vol_ratio = _volume_ratio(df)

    filters = {
        "trend": close > ema200 if ema200 else False,
        "momentum": adx >= cfg.screener_adx_min,
        "ema_align": ema21 > ema50 if ema21 and ema50 else False,
        "macd": macdh > 0,
        "rsi_band": cfg.screener_rsi_low <= rsi <= cfg.screener_rsi_high,
        "volume": vol_ratio >= cfg.screener_volume_ratio_min,
    }
    score = sum(filters.values()) / len(filters) * 100
    if adx >= 30:
        score = min(100, score + 5)
    if 40 <= rsi <= 60:
        score = min(100, score + 5)

    if mode == "scoring":
        passed = score >= cfg.quant_min_score
        if not passed:
            blockers = [f"score {score:.0f} < {cfg.quant_min_score}"]
        else:
            advisory = [k for k, v in filters.items() if not v]
    else:
        passed = all(filters.values())
        if not passed:
            blockers = [k for k, v in filters.items() if not v]
        if score < cfg.quant_min_score:
            blockers.append(f"score {score:.0f} < {cfg.quant_min_score}")
        passed = passed and score >= cfg.quant_min_score

    return ScreenResult(
        ticker=ticker,
        passed=passed and score >= cfg.quant_min_score,
        score=round(score, 1),
        filters=filters,
        metrics={
            "close": close,
            "adx": adx,
            "rsi": rsi,
            "macdh": macdh,
            "volume_ratio": vol_ratio,
            "sma50": ema50,
        },
        blockers=blockers,
        advisory=advisory,
    )


def _detect_regime_at_index(spy_df: pd.DataFrame) -> tuple[str, float]:
    """Classify regime from SPY history truncated to as-of date."""
    if spy_df.empty or len(spy_df) < 25:
        return "choppy", 0.5
    close = float(spy_df["close"].iloc[-1])
    sma20 = float(spy_df["close"].rolling(20).mean().iloc[-1])
    if close >= sma20 * 1.01:
        return "bull", 1.0
    if close <= sma20 * 0.99:
        return "bear", 0.0
    return "choppy", 0.5


def _forward_return(close: pd.Series, idx: int, horizon: int) -> float | None:
    if idx + horizon >= len(close):
        return None
    start = float(close.iloc[idx])
    end = float(close.iloc[idx + horizon])
    if start <= 0:
        return None
    return round((end - start) / start, 6)


def _llm_proxy_momentum(quant: ScreenResult) -> tuple[str, float, bool]:
    """
    Synthetic LLM proxy (Option A): momentum confirmation.

    NOT a replay of TradingAgents — high quant score + price above SMA50 → Buy.
    """
    close = quant.metrics.get("close", 0.0)
    sma50 = quant.metrics.get("sma50", 0.0)
    if quant.passed and quant.score >= FIRM_CONFIG.quant_min_score and close > sma50 > 0:
        return "Buy", 1.0, True
    if quant.score >= FIRM_CONFIG.quant_min_score * 0.9 and close > sma50 > 0:
        return "Overweight", 0.7, True
    return "Hold", 0.0, False


def _llm_proxy_hindsight(forward_20d: float | None, *, threshold: float = 0.05) -> tuple[str, float, bool]:
    """
    Synthetic LLM proxy (Option B): hindsight labels for validation ONLY.

    Labels entries that would have worked — introduces look-ahead bias by design.
    """
    if forward_20d is None:
        return "Hold", 0.0, False
    if forward_20d >= threshold:
        return "Buy", 1.0, True
    if forward_20d >= threshold / 2:
        return "Overweight", 0.7, True
    return "Hold", 0.0, False


def _fuse_historical(
    quant: ScreenResult,
    llm_rating: str,
    llm_pass: bool,
    llm_score: float,
    regime_label: str,
    regime_mult: float,
) -> tuple[float, bool, list[str]]:
    quant_norm = max(0.0, quant.score / 100.0)
    llm_norm = abs(llm_score)
    fused_score = round(quant_norm * llm_norm * regime_mult, 4)
    blockers: list[str] = []
    if not quant.passed:
        blockers.extend(quant.blockers or ["quant gate failed"])
    if not llm_pass:
        blockers.append(f"LLM proxy rating {llm_rating} not in {FIRM_CONFIG.entry_ratings}")
    if regime_mult == 0.0:
        blockers.append(f"bear regime ({regime_label}) blocks entries")
    if fused_score < FIRM_CONFIG.fusion_entry_threshold:
        blockers.append(
            f"fused score {fused_score} < {FIRM_CONFIG.fusion_entry_threshold}"
        )
    fused_pass = (
        quant.passed
        and llm_pass
        and regime_mult > 0
        and fused_score >= FIRM_CONFIG.fusion_entry_threshold
    )
    return fused_score, fused_pass, blockers


def _strategy_pass(
    strategy: str,
    *,
    quant_pass: bool,
    llm_pass: bool,
    fused_pass: bool,
) -> bool:
    if strategy == "quant_only":
        return quant_pass
    if strategy == "llm_only":
        return llm_pass
    return fused_pass


def _compute_metrics(signals: list[HistoricalSignal], strategy: str) -> StrategyMetrics:
    evals = [s for s in signals if s.strategy == strategy]
    passed = [s for s in evals if s.strategy_pass]
    m = StrategyMetrics(
        strategy=strategy,
        evaluations=len(evals),
        signals=len(passed),
        pass_rate=round(len(passed) / len(evals), 4) if evals else 0.0,
    )

    rets_5 = [s.forward_return_5d for s in passed if s.forward_return_5d is not None]
    rets_20 = [s.forward_return_20d for s in passed if s.forward_return_20d is not None]
    if rets_5:
        m.hit_rate_5d = round(sum(1 for r in rets_5 if r > 0) / len(rets_5), 4)
        m.avg_forward_return_5d = round(float(np.mean(rets_5)), 6)
    if rets_20:
        m.hit_rate_20d = round(sum(1 for r in rets_20 if r > 0) / len(rets_20), 4)
        m.avg_forward_return_20d = round(float(np.mean(rets_20)), 6)

    ordered = sorted(passed, key=lambda s: s.trade_date)
    curve: list[float] = []
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for sig in ordered:
        r = sig.forward_return_20d if sig.forward_return_20d is not None else sig.forward_return_5d
        if r is None:
            continue
        equity *= 1.0 + r
        peak = max(peak, equity)
        dd = (peak - equity) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
        curve.append(equity)
    if curve:
        m.max_drawdown = round(max_dd, 6)
    return m


def _weekly_dates(index: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    in_range = index[(index >= start) & (index <= end)]
    if in_range.empty:
        return []
    frame = pd.DataFrame({"marker": 1}, index=in_range)
    weekly = frame.groupby(pd.Grouper(freq="W-FRI"))["marker"].last().dropna()
    return [pd.Timestamp(d) for d in weekly.index]


def _fetch_history(ticker: str, start: str, end: str) -> pd.DataFrame:
    hist = yf.Ticker(ticker).history(start=start, end=end, interval="1d", auto_adjust=True)
    if hist.empty:
        return pd.DataFrame()
    hist = _normalize_ohlcv(hist)
    hist.index = pd.to_datetime(hist.index).tz_localize(None)
    return hist


def run_hybrid_backtest(
    start_date: str | date,
    end_date: str | date,
    *,
    tickers: list[str] | None = None,
    mode: StrategyMode = "compare",
    llm_proxy: LlmProxyMode = "momentum",
    frequency: str = "weekly",
    screener_mode: str | None = None,
    data_dir=None,
) -> dict[str, Any]:
    """
    Run historical quant screen + LLM proxy + fusion simulation.

    Returns summary metrics and optional per-signal rows.
    """
    from firm.universe.watchlist import resolve_watchlist

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if end <= start:
        raise ValueError("end_date must be after start_date")

    universe = tickers or resolve_watchlist(data_dir or FIRM_CONFIG.data_dir)
    universe = [t.upper() for t in universe if t]
    if not universe:
        raise ValueError("no tickers to backtest")

    # Pad fetch window for indicators and forward returns
    fetch_start = (start - pd.Timedelta(days=400)).strftime("%Y-%m-%d")
    fetch_end = (end + pd.Timedelta(days=35)).strftime("%Y-%m-%d")

    spy_hist = _fetch_history("SPY", fetch_start, fetch_end)
    if spy_hist.empty:
        raise ValueError("unable to load SPY history for regime detection")

    ticker_hist: dict[str, pd.DataFrame] = {}
    for sym in universe:
        df = _fetch_history(sym, fetch_start, fetch_end)
        if not df.empty:
            ticker_hist[sym] = df

    if not ticker_hist:
        raise ValueError("no price history for universe")

    eval_dates = _weekly_dates(spy_hist.index, start, end)
    if frequency != "weekly":
        eval_dates = [d for d in spy_hist.index if start <= d <= end]

    strategies = ["fused", "quant_only", "llm_only"] if mode == "compare" else [mode]
    screener_modes = ["scoring", "strict"] if screener_mode is None else [screener_mode]

    all_signals: list[HistoricalSignal] = []
    mode_comparison: dict[str, dict[str, Any]] = {}

    for sm in screener_modes:
        signals: list[HistoricalSignal] = []
        for as_of in eval_dates:
            spy_slice = spy_hist.loc[:as_of]
            regime_label, regime_mult = _detect_regime_at_index(spy_slice)
            trade_date = as_of.strftime("%Y-%m-%d")

            for ticker, full_df in ticker_hist.items():
                df = full_df.loc[:as_of]
                if len(df) < 210:
                    continue
                quant = _screen_at_index(ticker, df, screener_mode=sm)
                pos = full_df.index.get_indexer([as_of], method="pad")[0]
                if pos < 0:
                    continue
                fwd5 = _forward_return(full_df["close"], pos, 5)
                fwd20 = _forward_return(full_df["close"], pos, 20)

                if llm_proxy == "hindsight":
                    rating, llm_score, llm_pass = _llm_proxy_hindsight(fwd20)
                else:
                    rating, llm_score, llm_pass = _llm_proxy_momentum(quant)

                fused_score, fused_pass, _ = _fuse_historical(
                    quant, rating, llm_pass, llm_score, regime_label, regime_mult
                )

                for strategy in strategies:
                    sig = HistoricalSignal(
                        trade_date=trade_date,
                        ticker=ticker,
                        quant_pass=quant.passed,
                        quant_score=quant.score,
                        llm_rating=rating,
                        llm_pass=llm_pass,
                        llm_score=llm_score,
                        regime=regime_label,
                        regime_multiplier=regime_mult,
                        fused_score=fused_score,
                        fused_pass=fused_pass,
                        forward_return_5d=fwd5,
                        forward_return_20d=fwd20,
                        strategy=strategy,
                        strategy_pass=_strategy_pass(
                            strategy,
                            quant_pass=quant.passed,
                            llm_pass=llm_pass,
                            fused_pass=fused_pass,
                        ),
                    )
                    signals.append(sig)

        metrics = {s: _compute_metrics(signals, s) for s in strategies}
        mode_comparison[sm] = {
            "metrics": {k: v.to_dict() for k, v in metrics.items()},
            "sample_signals": [
                x.to_dict()
                for x in sorted(
                    [s for s in signals if s.strategy_pass and s.strategy == "fused"],
                    key=lambda s: (s.trade_date, s.ticker),
                    reverse=True,
                )[:30]
            ],
        }
        all_signals.extend(signals)

    return {
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "universe_size": len(universe),
        "tickers_loaded": len(ticker_hist),
        "evaluation_dates": len(eval_dates),
        "frequency": frequency,
        "llm_proxy": llm_proxy,
        "llm_proxy_disclaimer": (
            "LLM leg uses a synthetic proxy, not replayed TradingAgents output. "
            "Hindsight mode introduces look-ahead bias for validation only."
        ),
        "mode": mode,
        "screener_comparison": mode_comparison,
        "config_snapshot": {
            "quant_min_score": FIRM_CONFIG.quant_min_score,
            "fusion_entry_threshold": FIRM_CONFIG.fusion_entry_threshold,
            "screener_adx_min": FIRM_CONFIG.screener_adx_min,
            "screener_rsi_low": FIRM_CONFIG.screener_rsi_low,
            "screener_rsi_high": FIRM_CONFIG.screener_rsi_high,
            "screener_volume_ratio_min": FIRM_CONFIG.screener_volume_ratio_min,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run hybrid quant + LLM-proxy backtest")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--tickers", default="", help="Comma-separated tickers (default: watchlist)")
    parser.add_argument(
        "--mode",
        default="compare",
        choices=["fused", "quant_only", "llm_only", "compare"],
    )
    parser.add_argument(
        "--llm-proxy",
        default="momentum",
        choices=["momentum", "hindsight"],
        dest="llm_proxy",
    )
    parser.add_argument("--frequency", default="weekly", choices=["weekly", "daily"])
    parser.add_argument("--screener-mode", default=None, choices=["scoring", "strict", None])
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()] or None
    result = run_hybrid_backtest(
        args.start,
        args.end,
        tickers=tickers,
        mode=args.mode,
        llm_proxy=args.llm_proxy,
        frequency=args.frequency,
        screener_mode=args.screener_mode,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
