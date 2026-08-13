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


def create_aggressive_debator(llm):
    def aggressive_node(state) -> dict:
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
        aggressive_history = truncate_text(
            risk_debate_state.get("aggressive_history", ""),
            history_budget,
            "aggressive history",
        )

        current_conservative_response = truncate_text(
            risk_debate_state.get("current_conservative_response", ""),
            short_budget,
            "last conservative argument",
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

        prompt = f"""As the Aggressive Risk Analyst, your role is to actively champion high-reward, high-risk opportunities, emphasizing bold strategies and competitive advantages. When evaluating the trader's decision or plan, focus intently on the potential upside, growth potential, and innovative benefits—even when these come with elevated risk. Use the provided market data and sentiment analysis to strengthen your arguments and challenge the opposing views. Specifically, respond directly to each point made by the conservative and neutral analysts, countering with data-driven rebuttals and persuasive reasoning. Highlight where their caution might miss critical opportunities or where their assumptions may be overly conservative. Here is the trader's decision:

{trader_decision}

Your task is to create a compelling case for the trader's decision by questioning and critiquing the conservative and neutral stances to demonstrate why your high-reward perspective offers the best path forward. Incorporate insights from the following sources into your arguments:

{instrument_context}
Source report briefs:
{source_report_briefs}
Here is the current conversation history: {history} Here are the last arguments from the conservative analyst: {current_conservative_response} Here are the last arguments from the neutral analyst: {current_neutral_response}. If there are no responses from the other viewpoints yet, present your own argument based on the available data.

Engage actively by addressing specific concerns raised and refuting weak logic. Use only the trader proposal, source briefs, and debate context; do not invent fresh facts, exact levels, or catalysts. State your risk posture, evidence, uncertainty/blockers, and recommended adjustment with compact markdown headings. Keep the stance/action consistent with your own recommendation.
{get_structured_report_instruction()}""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Aggressive Analyst: {response.content}"

        new_risk_debate_state = {
            "history": truncate_text(
                history + "\n" + argument,
                history_budget,
                "risk debate history",
            ),
            "aggressive_history": truncate_text(
                aggressive_history + "\n" + argument,
                history_budget,
                "aggressive history",
            ),
            "conservative_history": risk_debate_state.get("conservative_history", ""),
            "neutral_history": risk_debate_state.get("neutral_history", ""),
            "latest_speaker": "Aggressive",
            "current_aggressive_response": argument,
            "current_conservative_response": risk_debate_state.get("current_conservative_response", ""),
            "current_neutral_response": risk_debate_state.get(
                "current_neutral_response", ""
            ),
            "count": risk_debate_state["count"] + 1,
        }

        return {"risk_debate_state": new_risk_debate_state}

    return aggressive_node
