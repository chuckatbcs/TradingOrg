"""Technical screener ported from trading-platform filter logic (yfinance)."""



from __future__ import annotations



from dataclasses import dataclass, field



import pandas as pd

import yfinance as yf

from stockstats import wrap as stockstats_wrap



from firm.config import FIRM_CONFIG





@dataclass

class ScreenResult:

    ticker: str

    passed: bool

    score: float

    filters: dict[str, bool] = field(default_factory=dict)

    metrics: dict[str, float] = field(default_factory=dict)

    blockers: list[str] = field(default_factory=list)
    advisory: list[str] = field(default_factory=list)





def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:

    ss = stockstats_wrap(df.rename(columns={

        "Open": "open", "High": "high", "Low": "low",

        "Close": "close", "Volume": "volume",

    }))

    for col in ("close_200_sma", "close_50_sma", "close_10_ema", "rsi", "macdh", "adx"):

        _ = ss[col]

    return ss





def _volume_ratio(df: pd.DataFrame) -> float:

    """Current volume vs 20-day average (trading-platform convention)."""

    vol = df["volume"]

    sma20 = vol.rolling(20).mean().iloc[-1]

    if sma20 and sma20 > 0:

        return float(vol.iloc[-1] / sma20)

    return 1.0





def screen_symbol(ticker: str) -> ScreenResult:

    """Apply core trend/momentum/volume filters; score 0-100."""

    blockers: list[str] = []
    advisory: list[str] = []

    cfg = FIRM_CONFIG

    try:

        hist = yf.Ticker(ticker).history(period="1y", interval="1d")

        if hist.empty or len(hist) < 210:

            return ScreenResult(ticker, False, 0.0, blockers=["insufficient history"])

        df = hist.rename(columns={

            "Open": "open", "High": "high", "Low": "low",

            "Close": "close", "Volume": "volume",

        })

        ss = _compute_indicators(df)

        row = ss.iloc[-1]

        close = float(row["close"])

        ema200 = float(row.get("close_200_sma") or 0)

        ema50 = float(row.get("close_50_sma") or 0)

        ema21 = float(row.get("close_10_ema") or 0)  # proxy short EMA

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

        if cfg.screener_mode == "scoring":

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

                "close": close, "adx": adx, "rsi": rsi,

                "macdh": macdh, "volume_ratio": vol_ratio,

            },

            blockers=blockers,

            advisory=advisory,

        )

    except Exception as exc:

        return ScreenResult(ticker, False, 0.0, blockers=[str(exc)])





def screen_universe(

    symbols: list[str],

    top_n: int | None = None,

    *,

    pass_only: bool = True,

) -> list[ScreenResult]:

    results = [screen_symbol(s) for s in symbols]

    results.sort(key=lambda r: r.score, reverse=True)

    if pass_only:

        results = [r for r in results if r.passed]

    if top_n:

        return results[:top_n]

    return results


