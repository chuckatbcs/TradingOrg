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


def create_conservative_debator(llm):
    def conservative_node(state) -> dict:
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
        conservative_history = truncate_text(
            risk_debate_state.get("conservative_history", ""),
            history_budget,
            "conservative history",
        )

        current_aggressive_response = truncate_text(
            risk_debate_state.get("current_aggressive_response", ""),
            short_budget,
            "last aggressive argument",
        )
        current_neutral_response = truncate_text(
            risk_debate_state.get("current_neutral_response", ""),
            short_budget,
            "last neutral argument",
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

        prompt = f"""As the Conservative Risk Analyst, your primary objective is to protect assets, minimize volatility, and ensure steady, reliable growth. You prioritize stability, security, and risk mitigation, carefully assessing potential losses, economic downturns, and market volatility. When evaluating the trader's decision or plan, critically examine high-risk elements, pointing out where the decision may expose the firm to undue risk and where more cautious alternatives could secure long-term gains. Here is the trader's decision:

{trader_decision}

Your task is to actively counter the arguments of the Aggressive and Neutral Analysts, highlighting where their views may overlook potential threats or fail to prioritize sustainability. Respond directly to their points, drawing from the following data sources to build a convincing case for a low-risk approach adjustment to the trader's decision:

{instrument_context}
Source report briefs:
{source_report_briefs}
Here is the current conversation history: {history} Here is the last response from the aggressive analyst: {current_aggressive_response} Here is the last response from the neutral analyst: {current_neutral_response}. If there are no responses from the other viewpoints yet, present your own argument based on the available data.

Engage by questioning optimism and emphasizing overlooked downside. Use only the trader proposal, source briefs, and debate context; do not invent fresh facts, exact levels, or catalysts. State your risk posture, evidence, uncertainty/blockers, and recommended adjustment with compact markdown headings. Keep the stance/action consistent with your own recommendation.
{get_structured_report_instruction()}""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Conservative Analyst: {response.content}"

        new_risk_debate_state = {
            "history": truncate_text(
                history + "\n" + argument,
                history_budget,
                "risk debate history",
            ),
            "aggressive_history": risk_debate_state.get("aggressive_history", ""),
            "conservative_history": truncate_text(
                conservative_history + "\n" + argument,
                history_budget,
                "conservative history",
            ),
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Conservative",
            "current_aggressive_response": risk_debate_state.get(
                "current_aggressive_response", ""
            ),
            "current_conservative_response": argument,
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return conservative_node
