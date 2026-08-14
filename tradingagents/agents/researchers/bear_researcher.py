from tradingagents.agents.utils.agent_utils import (
    get_instrument_context_from_state,
    get_language_instruction,
    get_structured_report_instruction,
)
from tradingagents.agents.utils.context_budget import (
    history_section_budget,
    max_context_tokens_from_config,
    report_section_budget,
    short_section_budget,
    truncate_text,
)
from tradingagents.agents.utils.structured_reports import format_source_report_briefs


def create_bear_researcher(llm):
    def bear_node(state) -> dict:
        investment_debate_state = state["investment_debate_state"]
        max_context_tokens = max_context_tokens_from_config()
        report_budget = report_section_budget(max_context_tokens)
        history_budget = history_section_budget(max_context_tokens)
        short_budget = short_section_budget(max_context_tokens)
        history = truncate_text(
            investment_debate_state.get("history", ""),
            history_budget,
            "debate history",
        )
        bear_history = truncate_text(
            investment_debate_state.get("bear_history", ""),
            history_budget,
            "bear history",
        )

        current_response = truncate_text(
            investment_debate_state.get("current_response", ""),
            short_budget,
            "last bull argument",
        )
        source_report_briefs = format_source_report_briefs(
            {
                "market_report": state["market_report"],
                "sentiment_report": state["sentiment_report"],
                "news_report": state["news_report"],
                "fundamentals_report": state["fundamentals_report"],
            },
            ticker=state.get("company_of_interest"),
            trade_date=state.get("trade_date"),
            max_chars=max(report_budget * 2, 3000),
        )
        instrument_context = get_instrument_context_from_state(state)
        asset_type = state.get("asset_type", "stock")
        target_label = "stock" if asset_type == "stock" else "asset"

        prompt = f"""You are a Bear Analyst making the case against investing in the {target_label}. Your goal is to present a well-reasoned argument emphasizing risks, challenges, and negative indicators. Leverage the provided research and data to highlight potential downsides and counter bullish arguments effectively.

Key points to focus on:

- Risks and Challenges: Highlight factors like market saturation, financial instability, or macroeconomic threats that could hinder the stock's performance.
- Competitive Weaknesses: Emphasize vulnerabilities such as weaker market positioning, declining innovation, or threats from competitors.
- Negative Indicators: Use evidence from financial data, market trends, or recent adverse news to support your position.
- Bull Counterpoints: Critically analyze the bull argument with specific data and sound reasoning, exposing weaknesses or over-optimistic assumptions.
- Engagement: Present your argument in a conversational style, directly engaging with the bull analyst's points and debating effectively rather than simply listing facts.

Resources available:

{instrument_context}
Source report briefs:
{source_report_briefs}
Conversation history of the debate: {history}
Last bull argument: {current_response}
Use only these source briefs and existing debate context. Do not ask for or invent new source data, exact figures, competitors, catalysts, dates, or price levels not present in the briefs. If a useful fact is missing, say it is not in the source briefs. Deliver a compact bear argument with headings: Thesis Summary, Evidence, Bull Counterpoints, Risks/Uncertainty, Recommended Action.
{get_structured_report_instruction()}
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bear Analyst: {response.content}"

        new_investment_debate_state = {
            "history": truncate_text(
                history + "\n" + argument,
                history_budget,
                "debate history",
            ),
            "bear_history": truncate_text(
                bear_history + "\n" + argument,
                history_budget,
                "bear history",
            ),
            "bull_history": investment_debate_state.get("bull_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bear_node
