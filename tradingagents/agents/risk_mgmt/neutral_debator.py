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


def create_neutral_debator(llm):
    def neutral_node(state) -> dict:
        risk_debate_state = state["risk_debate_state"]
        max_context_tokens = max_context_tokens_from_config()
        report_budget = report_section_budget(max_context_tokens)
        history_budget = history_section_budget(max_context_tokens)
        short_budget = short_section_budget(max_context_tokens)
        history = truncate_text(
            risk_debate_state.get("history", ""),
            history_budget,
            "risk debate history",
        )
        neutral_history = truncate_text(
            risk_debate_state.get("neutral_history", ""),
            history_budget,
            "neutral history",
        )

        current_aggressive_response = truncate_text(
            risk_debate_state.get("current_aggressive_response", ""),
            short_budget,
            "last aggressive argument",
        )
        current_conservative_response = truncate_text(
            risk_debate_state.get("current_conservative_response", ""),
            short_budget,
            "last conservative argument",
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

        trader_decision = truncate_text(
            state["trader_investment_plan"],
            report_budget,
            "trader proposal",
        )

        prompt = f"""As the Neutral Risk Analyst, your role is to provide a balanced perspective, weighing both the potential benefits and risks of the trader's decision or plan. You prioritize a well-rounded approach, evaluating the upsides and downsides while factoring in broader market trends, potential economic shifts, and diversification strategies.Here is the trader's decision:

{trader_decision}

Your task is to challenge both the Aggressive and Conservative Analysts, pointing out where each perspective may be overly optimistic or overly cautious. Use insights from the following data sources to support a moderate, sustainable strategy to adjust the trader's decision:

{instrument_context}
Source report briefs:
{source_report_briefs}
Here is the current conversation history: {history} Here is the last response from the aggressive analyst: {current_aggressive_response} Here is the last response from the conservative analyst: {current_conservative_response}. If there are no responses from the other viewpoints yet, present your own argument based on the available data.

Engage actively by analyzing both sides critically and addressing weaknesses in aggressive and conservative arguments. Use only the trader proposal, source briefs, and debate context; do not invent fresh facts, exact levels, or catalysts. State your risk posture, evidence, uncertainty/blockers, and recommended adjustment with compact markdown headings. Keep the stance/action consistent with your own recommendation.
{get_structured_report_instruction()}""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Neutral Analyst: {response.content}"

        new_risk_debate_state = {
            "history": truncate_text(
                history + "\n" + argument,
                history_budget,
                "risk debate history",
            ),
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": truncate_text(
                neutral_history + "\n" + argument,
                history_budget,
                "neutral history",
            ),
            "latest_speaker": "Neutral",
            "current_aggressive_response": risk_debate_state.get(
                "current_aggressive_response", ""
            ),
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),
            "current_neutral_response": argument,
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return neutral_node
