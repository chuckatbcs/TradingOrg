from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tradingagents.agents.analysts.fundamentals_analyst import (
    _build_partial_fundamentals_report,
)
from tradingagents.agents.utils.context_budget import (
    compact_messages_for_context,
    context_budget_chars,
    truncate_text,
)
from tradingagents.agents.utils.structured_reports import format_source_report_briefs
from webapp.runs import _format_run_error


def test_context_budget_chars_leaves_prompt_headroom():
    assert context_budget_chars(8192) == 18000
    assert context_budget_chars(16384) == 40000


def test_truncate_text_preserves_head_and_tail_with_notice():
    text = "A" * 200 + " middle " + "Z" * 200

    result = truncate_text(text, max_chars=120, label="report")

    assert len(result) <= 120
    assert result.startswith("A" * 20)
    assert result.endswith("Z" * 20)
    assert "[report truncated" in result


def test_compact_messages_truncates_tool_outputs_and_overall_history():
    messages = [
        HumanMessage(content="Analyze MU"),
        ToolMessage(content="tool-one " + ("A" * 5000), name="get_stock_data", tool_call_id="1"),
        AIMessage(content="partial report " + ("B" * 5000)),
        ToolMessage(
            content="tool-two " + ("C" * 5000),
            name="get_fundamentals",
            tool_call_id="2",
        ),
    ]

    compacted = compact_messages_for_context(messages, max_chars=1800, max_tool_chars=500)

    joined = "\n".join(str(getattr(msg, "content", "")) for msg in compacted)
    assert len(joined) <= 1800
    assert "tool-one" in joined
    assert "tool-two" in joined
    assert "[tool output truncated" in joined
    assert compacted[-1].content.endswith("C" * 20)


def test_partial_fundamentals_report_stays_concise_for_8k_budget():
    tool_messages = [
        ToolMessage(content=f"payload-{i} " + ("X" * 2500), name="get_fundamentals", tool_call_id=str(i))
        for i in range(4)
    ]

    report = _build_partial_fundamentals_report(
        {"company_of_interest": "MU", "trade_date": "2026-07-03"},
        tool_messages,
        max_tool_calls=4,
    )

    assert len(report) <= 2400
    assert report.count("[tool output truncated") == 4


def test_source_report_briefs_are_compact_and_structured():
    reports = {
        "market_report": (
            "# Market Report\n\n"
            "Thesis Summary: Momentum is improving but volatility remains elevated.\n\n"
            "Stance: Bullish\n\n"
            "Evidence:\n"
            "- Price reclaimed the 50-day moving average.\n"
            "- Volume expanded on up days.\n\n"
            "Risks:\n"
            "- Macro volatility could reverse the breakout.\n"
            + ("Extra detail. " * 500)
        ),
        "news_report": (
            "# News Report\n\n"
            "Thesis Summary: Recent headlines are constructive.\n\n"
            "Stance: Mildly Bullish\n\n"
            "Catalysts:\n"
            "- New product launch next week.\n"
        ),
    }

    brief = format_source_report_briefs(
        reports,
        ticker="MU",
        trade_date="2026-07-03",
        max_chars=900,
    )

    assert len(brief) <= 900
    assert "Market Analyst" in brief
    assert "News Analyst" in brief
    assert "Price reclaimed the 50-day moving average" in brief
    assert "Full source reports remain available" in brief


def test_context_window_error_names_settings_and_analysts_to_reduce():
    formatted = _format_run_error(
        ValueError(
            "Context window exceeded (Error code: 400 - {'error': "
            "'n_keep: 9750>= n_ctx: 8192'})"
        ),
        current_agent="Fundamentals Analyst",
    )

    assert "TRADINGAGENTS_CONTEXT_WINDOW" in formatted
    assert "max_context_tokens" in formatted
    assert "LM Studio" in formatted
    assert "uncheck Fundamentals" in formatted
    assert "Market+News" in formatted
