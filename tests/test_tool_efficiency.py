from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import tool

from tradingagents.agents.utils.tool_efficiency import (
    RunMetricsCollector,
    ToolEfficiencyTracker,
    memoize_tool,
)


def test_memoized_tool_reuses_compact_result_for_identical_calls():
    calls = {"count": 0}

    @tool
    def expensive_tool(ticker: str, curr_date: str) -> str:
        """Return an intentionally verbose payload."""
        calls["count"] += 1
        return f"payload for {ticker} on {curr_date}\n" + ("X" * 2000)

    tracker = ToolEfficiencyTracker()
    wrapped = memoize_tool(expensive_tool, tracker=tracker, max_chars=260)

    first = wrapped.invoke({"ticker": "MU", "curr_date": "2026-07-03"})
    second = wrapped.invoke({"ticker": "MU", "curr_date": "2026-07-03"})

    assert calls["count"] == 1
    assert first == second
    assert len(first) <= 260
    assert "[expensive_tool output truncated" in first
    assert tracker.snapshot()["cached_tool_calls"] == 1
    assert tracker.snapshot()["tool_calls"]["expensive_tool"] == 1


def test_run_metrics_collector_counts_tools_and_flags_repeats():
    collector = RunMetricsCollector()
    first_call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-1",
                "name": "get_news",
                "args": {
                    "ticker": "MU",
                    "start_date": "2026-06-26",
                    "end_date": "2026-07-03",
                },
            }
        ],
    )
    repeat_call = AIMessage(
        content="",
        tool_calls=[
            {
                "id": "call-2",
                "name": "get_news",
                "args": {
                    "ticker": "MU",
                    "start_date": "2026-06-26",
                    "end_date": "2026-07-03",
                },
            }
        ],
    )
    tool_result = ToolMessage(
        content="headline\n[cached tool result reused]\n[tool output truncated from 1000 to 200 chars]",
        name="get_news",
        tool_call_id="call-2",
    )

    collector.observe_messages([first_call], agent="News Analyst")
    collector.observe_messages([repeat_call, tool_result], agent="News Analyst")

    snapshot = collector.snapshot()
    news_metrics = snapshot["agents"]["News Analyst"]
    assert news_metrics["tool_calls"] == 2
    assert news_metrics["duplicate_tool_calls"] == 1
    assert news_metrics["cached_tool_results"] == 1
    assert news_metrics["truncated_tool_results"] == 1
    assert snapshot["repeated_tool_calls"][0]["tool"] == "get_news"
