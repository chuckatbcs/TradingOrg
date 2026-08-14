from langchain_core.messages import HumanMessage, ToolMessage

from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst


class ExplodingLLM:
    def bind_tools(self, tools):
        raise AssertionError("LLM should not be called after the tool budget is spent")


def test_fundamentals_budget_returns_partial_report_without_more_llm_calls():
    node = create_fundamentals_analyst(ExplodingLLM(), max_tool_calls=2)

    result = node(
        {
            "messages": [
                HumanMessage(content="MU"),
                ToolMessage(
                    content="# Company Fundamentals for MU\nRevenue improved.",
                    name="get_fundamentals",
                    tool_call_id="call-1",
                ),
                ToolMessage(
                    content="# Income Statement data for MU\nNet income improved.",
                    name="get_income_statement",
                    tool_call_id="call-2",
                ),
            ],
            "trade_date": "2026-07-03",
            "company_of_interest": "MU",
            "asset_type": "stock",
            "instrument_context": "Instrument under analysis: MU.",
        }
    )

    assert "Partial Fundamentals Report" in result["fundamentals_report"]
    assert "get_fundamentals" in result["fundamentals_report"]
    assert "get_income_statement" in result["fundamentals_report"]
    assert result["messages"][-1].tool_calls == []
