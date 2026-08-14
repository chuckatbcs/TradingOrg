from langchain_core.messages import HumanMessage, ToolMessage

from tradingagents.agents.analysts.market_analyst import create_market_analyst


class ExplodingLLM:
    def bind_tools(self, tools):
        raise AssertionError("LLM should not be called after the tool budget is spent")


def test_market_budget_returns_partial_report_without_more_llm_calls():
    node = create_market_analyst(ExplodingLLM())
    messages = [HumanMessage(content="MU")]
    for i in range(8):
        messages.append(
            ToolMessage(
                content=f"# Market tool output {i}\nClose and indicator context.",
                name="get_indicators" if i else "get_stock_data",
                tool_call_id=f"call-{i}",
            )
        )

    result = node(
        {
            "messages": messages,
            "trade_date": "2026-07-04",
            "company_of_interest": "MU",
            "asset_type": "stock",
            "instrument_context": "Instrument under analysis: MU.",
        }
    )

    assert "Partial Market Report" in result["market_report"]
    assert "get_stock_data" in result["market_report"]
    assert "get_indicators" in result["market_report"]
    assert result["messages"][-1].tool_calls == []
