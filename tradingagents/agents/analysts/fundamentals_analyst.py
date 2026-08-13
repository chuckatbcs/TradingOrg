from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_balance_sheet,
    get_cashflow,
    get_fundamentals,
    get_income_statement,
    get_instrument_context_from_state,
    get_language_instruction,
    get_structured_report_instruction,
)
from tradingagents.agents.utils.context_budget import (
    compact_messages_for_context,
    context_budget_chars,
    max_context_tokens_from_config,
    truncate_text,
)

FUNDAMENTALS_TOOL_NAMES = {
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
}


def _fundamentals_tool_messages(messages):
    return [
        msg
        for msg in messages
        if getattr(msg, "type", None) == "tool"
        and getattr(msg, "name", None) in FUNDAMENTALS_TOOL_NAMES
    ]


def _build_partial_fundamentals_report(state, tool_messages, max_tool_calls: int) -> str:
    ticker = state.get("company_of_interest", "the company")
    trade_date = state.get("trade_date", "the analysis date")
    parts = [
        "# Partial Fundamentals Report",
        "",
        (
            f"Fundamentals analysis for {ticker} on {trade_date} stopped after "
            f"the configured tool-call budget of {max_tool_calls} was reached."
        ),
    ]
    if not tool_messages:
        parts.extend(
            [
                "",
                "No fundamentals tool output was available before the budget was reached.",
            ]
        )
        return "\n".join(parts)

    parts.extend(["", "## Tool Outputs Used"])
    for msg in tool_messages:
        content = str(getattr(msg, "content", "")).strip()
        content = truncate_text(content, 420, "tool output")
        parts.extend(
            [
                "",
                f"### {getattr(msg, 'name', 'fundamentals_tool')}",
                content or "(tool returned no content)",
            ]
        )
    parts.extend(
        [
            "",
            "## Limitation",
            (
                "This report is intentionally partial to prevent an unbounded "
                "LLM/tool loop. Re-run with Hybrid/local routing for a deeper "
                "fundamentals pass if needed."
            ),
        ]
    )
    return "\n".join(parts)


def create_fundamentals_analyst(llm, max_tool_calls: int = 4):
    def fundamentals_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)
        max_context_tokens = max_context_tokens_from_config()
        completed_tool_messages = _fundamentals_tool_messages(state["messages"])
        if len(completed_tool_messages) >= max_tool_calls:
            report = _build_partial_fundamentals_report(
                state,
                completed_tool_messages,
                max_tool_calls,
            )
            return {
                "messages": [AIMessage(content=report)],
                "fundamentals_report": report,
            }

        tools = [
            get_fundamentals,
            get_balance_sheet,
            get_cashflow,
            get_income_statement,
        ]

        system_message = (
            "You are a fundamentals researcher. Build a concise trading brief from the available company profile, fundamentals, and financial-statement data. Do not imply complete coverage when statements are partial, stale, unavailable, or TTM-only; name the missing data and explain how it limits conviction."
            + " Use headings: Thesis Summary, Stance, Evidence, Risks, Data Gaps, Recommended Action. Append a compact Markdown table with metric/source, read-through, and limitation."
            + " Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements."
            + f" Hard limit: request no more than {max_tool_calls} fundamentals tool calls total, and call each fundamentals tool at most once. After the tool outputs are available, stop calling tools and write the final report from the data you have."
            + get_structured_report_instruction()
            + get_language_instruction(),
        )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " Use the provided tools to progress towards answering the question."
                    " If you are unable to fully answer, that's OK; another assistant with different tools"
                    " will help where you left off. Execute what you can to make progress."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    " You have access to the following tools: {tool_names}."
                    " Today's date is {current_date}; treat it as 'now' for all analysis and tool-call date ranges. {instrument_context}\n"
                    "{system_message}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(tool_names=", ".join([tool.name for tool in tools]))
        prompt = prompt.partial(current_date=current_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        chain = prompt | llm.bind_tools(tools)

        messages = compact_messages_for_context(
            state["messages"],
            max_chars=context_budget_chars(max_context_tokens),
            max_tool_chars=1200 if max_context_tokens <= 8192 else 2500,
        )
        result = chain.invoke(messages)

        report = ""

        tool_calls = getattr(result, "tool_calls", []) or []
        if len(tool_calls) == 0:
            report = result.content
        elif len(completed_tool_messages) + len(tool_calls) > max_tool_calls:
            report = _build_partial_fundamentals_report(
                state,
                completed_tool_messages,
                max_tool_calls,
            )
            result = AIMessage(content=report)

        return {
            "messages": [result],
            "fundamentals_report": report,
        }

    return fundamentals_analyst_node
