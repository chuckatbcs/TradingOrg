from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

_ALLOWED_ANALYSTS = {"market", "social", "news", "fundamentals"}


def _parse_analysts(value: str) -> list[str]:
    analysts = [item.strip().lower() for item in value.split(",") if item.strip()]
    invalid = [item for item in analysts if item not in _ALLOWED_ANALYSTS]
    if invalid:
        raise argparse.ArgumentTypeError(
            f"Invalid analyst(s): {', '.join(invalid)}. Allowed: {', '.join(sorted(_ALLOWED_ANALYSTS))}"
        )
    if not analysts:
        raise argparse.ArgumentTypeError("At least one analyst is required.")
    return analysts


def _build_config(args: argparse.Namespace) -> dict[str, Any]:
    config = DEFAULT_CONFIG.copy()
    config["llm_provider"] = args.provider.lower()
    config["backend_url"] = args.backend_url
    config["quick_think_llm"] = args.quick_model
    config["deep_think_llm"] = args.deep_model
    config["max_debate_rounds"] = args.depth
    config["max_risk_discuss_rounds"] = args.depth
    config["checkpoint_enabled"] = args.checkpoint
    config["output_language"] = args.output_language
    if args.results_dir:
        config["results_dir"] = str(args.results_dir)
    if args.cache_dir:
        config["data_cache_dir"] = str(args.cache_dir)
    return config


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def run(args: argparse.Namespace) -> dict[str, Any]:
    config = _build_config(args)
    graph = TradingAgentsGraph(
        selected_analysts=args.analysts,
        config=config,
        debug=False,
    )
    final_state, decision = graph.propagate(args.ticker, args.date)

    signal = {
        "schema_version": "tradingorg.signal.v1",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "ticker": args.ticker.upper(),
        "analysis_date": args.date,
        "provider": config["llm_provider"],
        "backend_url": config.get("backend_url"),
        "quick_model": config["quick_think_llm"],
        "deep_model": config["deep_think_llm"],
        "analysts": args.analysts,
        "research_depth": args.depth,
        "rating": decision,
        "paper_only": True,
        "final_trade_decision": final_state.get("final_trade_decision", ""),
        "trader_investment_plan": final_state.get("trader_investment_plan", ""),
        "investment_plan": final_state.get("investment_plan", ""),
        "risk_debate_state": final_state.get("risk_debate_state", {}),
        "investment_debate_state": final_state.get("investment_debate_state", {}),
        "reports": {
            "market": final_state.get("market_report"),
            "sentiment": final_state.get("sentiment_report"),
            "news": final_state.get("news_report"),
            "fundamentals": final_state.get("fundamentals_report"),
        },
        "execution_policy": {
            "may_place_order": False,
            "reason": "TradingOrg analyze-run produces research signals only. Broker execution must be handled by a separate deterministic risk-gated app.",
        },
    }

    if args.output_json:
        _write_json(args.output_json, signal)

    print(json.dumps(signal, indent=2, sort_keys=True))
    return signal


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run TradingOrg non-interactively for Hermes/workspace automation."
    )
    parser.add_argument("--ticker", required=True, help="Ticker symbol to analyze, e.g. NVDA or SPY.")
    parser.add_argument("--date", required=True, help="Analysis date in YYYY-MM-DD format.")
    parser.add_argument("--provider", default=DEFAULT_CONFIG["llm_provider"], help="LLM provider, e.g. ollama, openrouter, openai, google.")
    parser.add_argument("--backend-url", default=DEFAULT_CONFIG.get("backend_url"), help="Optional OpenAI-compatible base URL, e.g. http://home-pc:11434/v1.")
    parser.add_argument("--quick-model", default=DEFAULT_CONFIG["quick_think_llm"], help="Model for quick analyst tasks.")
    parser.add_argument("--deep-model", default=DEFAULT_CONFIG["deep_think_llm"], help="Model for deeper manager/portfolio tasks.")
    parser.add_argument("--analysts", type=_parse_analysts, default=["market", "news", "fundamentals"], help="Comma-separated analysts: market,social,news,fundamentals.")
    parser.add_argument("--depth", type=int, default=1, choices=[1, 3, 5], help="Research depth / debate rounds: 1, 3, or 5.")
    parser.add_argument("--output-language", default=DEFAULT_CONFIG.get("output_language", "English"), help="Report language.")
    parser.add_argument("--checkpoint", action="store_true", help="Enable LangGraph checkpoint resume.")
    parser.add_argument("--output-json", type=Path, help="Where to write the machine-readable signal JSON.")
    parser.add_argument("--results-dir", type=Path, help="Override results directory.")
    parser.add_argument("--cache-dir", type=Path, help="Override cache/checkpoint directory.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
