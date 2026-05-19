from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _fetch_return(symbol: str, trade_date: str, holding_days: int) -> dict[str, Any]:
    start = datetime.strptime(trade_date, "%Y-%m-%d")
    end = start + timedelta(days=holding_days + 7)

    data = yf.Ticker(symbol).history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))
    spy = yf.Ticker("SPY").history(start=start.strftime("%Y-%m-%d"), end=end.strftime("%Y-%m-%d"))

    if len(data) < 2 or len(spy) < 2:
        return {
            "available": False,
            "reason": "Not enough market data yet for requested holding period.",
        }

    idx = min(holding_days, len(data) - 1, len(spy) - 1)
    start_close = float(data["Close"].iloc[0])
    end_close = float(data["Close"].iloc[idx])
    spy_start = float(spy["Close"].iloc[0])
    spy_end = float(spy["Close"].iloc[idx])
    raw_return = (end_close - start_close) / start_close
    spy_return = (spy_end - spy_start) / spy_start

    return {
        "available": True,
        "start_close": start_close,
        "end_close": end_close,
        "raw_return": raw_return,
        "spy_return": spy_return,
        "alpha_vs_spy": raw_return - spy_return,
        "actual_holding_days": idx,
    }


def _direction_correct(rating: str, raw_return: float | None) -> bool | None:
    if raw_return is None:
        return None
    bullish = rating in {"Buy", "Overweight"}
    bearish = rating in {"Sell", "Underweight"}
    neutral = rating == "Hold"
    if bullish:
        return raw_return > 0
    if bearish:
        return raw_return < 0
    if neutral:
        return abs(raw_return) < 0.01
    return None


def build_review(args: argparse.Namespace) -> dict[str, Any]:
    signal = _read_json(args.signal)
    ticker = args.ticker or signal.get("ticker")
    analysis_date = args.date or signal.get("analysis_date")
    rating = signal.get("rating", "Hold")

    outcome = _fetch_return(ticker, analysis_date, args.holding_days)
    raw_return = outcome.get("raw_return") if outcome.get("available") else None

    review = {
        "schema_version": "tradingorg.postgame.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ticker": ticker,
        "analysis_date": analysis_date,
        "holding_days_requested": args.holding_days,
        "original_rating": rating,
        "provider": signal.get("provider"),
        "quick_model": signal.get("quick_model"),
        "deep_model": signal.get("deep_model"),
        "outcome": outcome,
        "direction_correct": _direction_correct(rating, raw_return),
        "lesson": None,
        "next_prompt_hint": None,
        "notes": [
            "This post-game review is deterministic and outcome-based. Add an AI reflection step later if desired, but do not treat reflection as proof of strategy edge.",
        ],
    }

    if outcome.get("available"):
        alpha = outcome["alpha_vs_spy"]
        correctness = review["direction_correct"]
        review["lesson"] = (
            f"Rating {rating} produced raw return {outcome['raw_return']:+.2%} "
            f"and alpha vs SPY {alpha:+.2%} over {outcome['actual_holding_days']} trading day(s)."
        )
        if correctness is True:
            review["next_prompt_hint"] = "Keep this setup in memory, but require confirmation from price/volume and market regime before increasing confidence."
        elif correctness is False:
            review["next_prompt_hint"] = "Ask analysts to identify which thesis assumption failed and whether the signal conflicted with SPY/sector direction."
        else:
            review["next_prompt_hint"] = "For Hold ratings, evaluate whether opportunity cost or avoided drawdown was the real outcome."

    if args.output_json:
        _write_json(args.output_json, review)

    return review


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create a post-game outcome review for a TradingOrg signal JSON.")
    parser.add_argument("--signal", type=Path, required=True, help="Signal JSON from analyze-run.")
    parser.add_argument("--ticker", help="Override ticker from signal JSON.")
    parser.add_argument("--date", help="Override analysis date from signal JSON.")
    parser.add_argument("--holding-days", type=int, default=1, help="Trading-day horizon to evaluate.")
    parser.add_argument("--output-json", type=Path, help="Where to write post-game review JSON.")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    review = build_review(args)
    print(json.dumps(review, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
