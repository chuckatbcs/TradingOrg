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


def create_bull_researcher(llm):
    def bull_node(state) -> dict:
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
        bull_history = truncate_text(
            investment_debate_state.get("bull_history", ""),
            history_budget,
            "bull history",
        )

        current_response = truncate_text(
            investment_debate_state.get("current_response", ""),
            short_budget,
            "last bear argument",
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

        prompt = f"""You are a Bull Analyst advocating for investing in the {target_label}. Your task is to build a strong, evidence-based case emphasizing growth potential, competitive advantages, and positive market indicators. Leverage the provided research and data to address concerns and counter bearish arguments effectively.

Key points to focus on:
- Growth Potential: Highlight the company's market opportunities, revenue projections, and scalability.
- Competitive Advantages: Emphasize factors like unique products, strong branding, or dominant market positioning.
- Positive Indicators: Use financial health, industry trends, and recent positive news as evidence.
- Bear Counterpoints: Critically analyze the bear argument with specific data and sound reasoning, addressing concerns thoroughly and showing why the bull perspective holds stronger merit.
- Engagement: Present your argument in a conversational style, engaging directly with the bear analyst's points and debating effectively rather than just listing data.

Resources available:
{instrument_context}
Source report briefs:
{source_report_briefs}
Conversation history of the debate: {history}
Last bear argument: {current_response}
Use only these source briefs and existing debate context. Do not ask for or invent new source data, exact figures, competitors, catalysts, dates, or price levels not present in the briefs. If a useful fact is missing, say it is not in the source briefs. Deliver a compact bull argument with headings: Thesis Summary, Evidence, Bear Counterpoints, Risks/Uncertainty, Recommended Action.
{get_structured_report_instruction()}
""" + get_language_instruction()

        response = llm.invoke(prompt)

        argument = f"Bull Analyst: {response.content}"

        new_investment_debate_state = {
            "history": truncate_text(
                history + "\n" + argument,
                history_budget,
                "debate history",
            ),
            "bull_history": truncate_text(
                bull_history + "\n" + argument,
                history_budget,
                "bull history",
            ),
            "bear_history": investment_debate_state.get("bear_history", ""),
            "current_response": argument,
            "count": investment_debate_state["count"] + 1,
        }

        return {"investment_debate_state": new_investment_debate_state}

    return bull_node
