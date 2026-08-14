"""Shared tool memoization and run diagnostics for token/cost control."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from langchain_core.messages import BaseMessage
from langchain_core.tools import BaseTool, StructuredTool

from tradingagents.agents.utils.context_budget import truncate_text

CACHED_RESULT_MARKER = "[cached tool result reused]"

DEFAULT_TOOL_OUTPUT_CHARS = {
    "get_stock_data": 1400,
    "get_indicators": 1200,
    "get_verified_market_snapshot": 1200,
    "get_news": 1600,
    "get_global_news": 1600,
    "get_insider_transactions": 1200,
    "get_macro_indicators": 1400,
    "get_prediction_markets": 1400,
    "get_fundamentals": 1800,
    "get_balance_sheet": 1400,
    "get_cashflow": 1400,
    "get_income_statement": 1400,
}

REPORT_AGENT_BY_SECTION = {
    "market_report": "Market Analyst",
    "sentiment_report": "Sentiment Analyst",
    "news_report": "News Analyst",
    "fundamentals_report": "Fundamentals Analyst",
    "bull_history": "Bull Researcher",
    "bear_history": "Bear Researcher",
    "research_judge": "Research Manager",
    "trader_investment_plan": "Trader",
    "risk_history": "Risk Analysts",
    "risk_judge": "Portfolio Manager",
    "final_trade_decision": "Portfolio Manager",
}


def tool_output_budget(tool_name: str, configured: int | None = None) -> int:
    """Return the compact output budget for a tool result shown to the LLM."""

    if configured:
        return int(configured)
    return DEFAULT_TOOL_OUTPUT_CHARS.get(tool_name, 1400)


def compact_tool_output(tool_name: str, output: Any, max_chars: int | None = None) -> str:
    """Compact verbose raw tool output before it enters graph messages."""

    budget = tool_output_budget(tool_name, max_chars)
    return truncate_text(output, budget, f"{tool_name} output")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def tool_signature(tool_name: str, args: Mapping[str, Any] | None) -> str:
    """Build a stable signature for duplicate/cached tool calls."""

    return json.dumps(
        {"tool": tool_name, "args": _jsonable(args or {})},
        sort_keys=True,
        separators=(",", ":"),
    )


def _empty_agent_metrics() -> dict[str, int]:
    return {
        "tool_calls": 0,
        "tool_results": 0,
        "tool_result_chars": 0,
        "truncated_tool_results": 0,
        "cached_tool_results": 0,
        "duplicate_tool_calls": 0,
        "report_chars": 0,
    }


class ToolEfficiencyTracker:
    """Per-run cache for deterministic data tool calls."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self.tool_calls: defaultdict[str, int] = defaultdict(int)
        self.cached_tool_calls = 0
        self.truncated_tool_results = 0
        self.raw_chars = 0
        self.returned_chars = 0

    def call(
        self,
        tool_name: str,
        args: Mapping[str, Any] | None,
        fn: Callable[[], Any],
        *,
        max_chars: int | None = None,
    ) -> str:
        """Execute once per unique tool+args and return compact cached output."""

        signature = tool_signature(tool_name, args)
        if signature in self._cache:
            self.cached_tool_calls += 1
            return self._cache[signature]

        self.tool_calls[tool_name] += 1
        raw = "" if (result := fn()) is None else str(result)
        compacted = compact_tool_output(tool_name, raw, max_chars)
        if len(compacted) < len(raw):
            self.truncated_tool_results += 1
        self.raw_chars += len(raw)
        self.returned_chars += len(compacted)
        self._cache[signature] = compacted
        return compacted

    def snapshot(self) -> dict[str, Any]:
        return {
            "tool_calls": dict(sorted(self.tool_calls.items())),
            "cached_tool_calls": self.cached_tool_calls,
            "truncated_tool_results": self.truncated_tool_results,
            "raw_tool_chars": self.raw_chars,
            "returned_tool_chars": self.returned_chars,
            "estimated_saved_tool_chars": max(0, self.raw_chars - self.returned_chars),
        }


def memoize_tool(
    base_tool: BaseTool,
    *,
    tracker: ToolEfficiencyTracker,
    max_chars: int | None = None,
) -> BaseTool:
    """Wrap a LangChain tool with per-run cache and compact returned content."""

    def _run(**kwargs: Any) -> str:
        return tracker.call(
            base_tool.name,
            kwargs,
            lambda: base_tool.invoke(kwargs),
            max_chars=max_chars,
        )

    return StructuredTool.from_function(
        func=_run,
        name=base_tool.name,
        description=base_tool.description,
        args_schema=base_tool.args_schema,
        infer_schema=False,
        return_direct=getattr(base_tool, "return_direct", False),
    )


class RunMetricsCollector:
    """Accumulates lightweight per-agent tool and report diagnostics."""

    def __init__(self) -> None:
        self._agents: defaultdict[str, dict[str, int]] = defaultdict(_empty_agent_metrics)
        self._seen_tool_call_ids: set[str] = set()
        self._seen_tool_result_ids: set[str] = set()
        self._seen_signatures: dict[str, str] = {}
        self._repeated_tool_calls: list[dict[str, Any]] = []

    def observe_messages(self, messages: Iterable[BaseMessage], *, agent: str | None) -> None:
        agent_name = agent or "Unknown Agent"
        metrics = self._agents[agent_name]
        for message in messages or []:
            for call in getattr(message, "tool_calls", []) or []:
                call_id = str(call.get("id") or tool_signature(call.get("name", "tool"), call.get("args")))
                if call_id in self._seen_tool_call_ids:
                    continue
                self._seen_tool_call_ids.add(call_id)
                metrics["tool_calls"] += 1

                name = str(call.get("name") or "tool")
                args = call.get("args") or {}
                signature = tool_signature(name, args)
                if signature in self._seen_signatures:
                    metrics["duplicate_tool_calls"] += 1
                    self._repeated_tool_calls.append(
                        {
                            "tool": name,
                            "agent": agent_name,
                            "first_agent": self._seen_signatures[signature],
                            "args": _jsonable(args),
                        }
                    )
                else:
                    self._seen_signatures[signature] = agent_name

            if getattr(message, "type", None) != "tool":
                continue
            result_id = str(getattr(message, "tool_call_id", "") or id(message))
            if result_id in self._seen_tool_result_ids:
                continue
            self._seen_tool_result_ids.add(result_id)
            content = str(getattr(message, "content", "") or "")
            metrics["tool_results"] += 1
            metrics["tool_result_chars"] += len(content)
            if "truncated from" in content:
                metrics["truncated_tool_results"] += 1
            if CACHED_RESULT_MARKER in content:
                metrics["cached_tool_results"] += 1

    def observe_reports(self, reports: Mapping[str, Any]) -> None:
        for section, content in (reports or {}).items():
            agent = REPORT_AGENT_BY_SECTION.get(section)
            if not agent:
                continue
            self._agents[agent]["report_chars"] = len(str(content or ""))

    def snapshot(self) -> dict[str, Any]:
        return {
            "agents": {agent: dict(metrics) for agent, metrics in sorted(self._agents.items())},
            "repeated_tool_calls": self._repeated_tool_calls[:20],
        }
