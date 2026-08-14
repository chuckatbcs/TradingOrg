from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

from tradingagents.agents.utils.agent_utils import (
    get_indicators,
    get_instrument_context_from_state,
    get_language_instruction,
    get_stock_data,
    get_structured_report_instruction,
    get_verified_market_snapshot,
)
from tradingagents.agents.utils.context_budget import (
    compact_messages_for_context,
    context_budget_chars,
    max_context_tokens_from_config,
    truncate_text,
)

MARKET_TOOL_NAMES = {
    "get_stock_data",
    "get_indicators",
    "get_verified_market_snapshot",
}


def _market_tool_messages(messages):
    return [
        msg
        for msg in messages
        if getattr(msg, "type", None) == "tool"
        and getattr(msg, "name", None) in MARKET_TOOL_NAMES
    ]


def _build_partial_market_report(state, tool_messages, max_tool_calls: int) -> str:
    ticker = state.get("company_of_interest", "the company")
    trade_date = state.get("trade_date", "the analysis date")
    parts = [
        "# Partial Market Report",
        "",
        (
            f"Market analysis for {ticker} on {trade_date} stopped after "
            f"the configured tool-call budget of {max_tool_calls} was reached."
        ),
    ]
    if not tool_messages:
        parts.extend(["", "No market tool output was available before the budget was reached."])
        return "\n".join(parts)

    parts.extend(["", "## Tool Outputs Used"])
    for msg in tool_messages:
        content = str(getattr(msg, "content", "")).strip()
        content = truncate_text(content, 520, "tool output")
        parts.extend(
            [
                "",
                f"### {getattr(msg, 'name', 'market_tool')}",
                content or "(tool returned no content)",
            ]
        )
    parts.extend(
        [
            "",
            "## Limitation",
            (
                "This report is intentionally partial to prevent an unbounded "
                "market-data tool loop. Re-run with fewer indicators, a larger "
                "context window, or a more reliable quick model for deeper analysis."
            ),
        ]
    )
    return "\n".join(parts)


def create_market_analyst(llm, max_tool_calls: int = 8):

    def market_analyst_node(state):
        current_date = state["trade_date"]
        instrument_context = get_instrument_context_from_state(state)
        max_context_tokens = max_context_tokens_from_config()
        completed_tool_messages = _market_tool_messages(state["messages"])
        if len(completed_tool_messages) >= max_tool_calls:
            report = _build_partial_market_report(
                state,
                completed_tool_messages,
                max_tool_calls,
            )
            return {
                "messages": [AIMessage(content=report)],
                "market_report": report,
            }

        tools = [
            get_stock_data,
            get_indicators,
            get_verified_market_snapshot,
        ]

        system_message = (
            """You are a trading assistant tasked with analyzing financial markets. Select only the **most relevant indicators** for the current setup. The goal is to choose up to **4 indicators** that provide complementary insights without redundancy. Categories and each category's indicators are:

Moving Averages:
- close_50_sma: 50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.
- close_200_sma: 200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.
- close_10_ema: 10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.

MACD Related:
- macd: MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.
- macds: MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.
- macdh: MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.

Momentum Indicators:
- rsi: RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.

Volatility Indicators:
- boll: Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.
- boll_ub: Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.
- boll_lb: Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.
- atr: ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.

Volume-Based Indicators:
- vwma: VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.

- Select indicators that provide diverse and complementary information. Avoid redundancy. Use exact indicator names or the tool call will fail. For speed, call get_stock_data once, call get_indicators for no more than 4 indicators total, then call get_verified_market_snapshot once. If those outputs are present, stop calling tools and write the final report from available data.

Before writing the final report, call get_verified_market_snapshot for this ticker and the current date, and treat it as the source of truth for any exact OHLCV, price-level, or indicator-value claim. If another tool's output conflicts with the verified snapshot, flag the discrepancy rather than inventing a reconciled number. Do not claim historical validation, support/resistance bounces, or exact percentage moves unless they are directly supported by tool output with concrete dates and prices.

Call each exact tool/argument combination at most once. If a relevant tool result is already present in the message history, use that context instead of requesting the same data again.

Write a concise final report with these headings: Thesis Summary, Stance, Evidence, Risks, Price/Trend Levels, Data Gaps, Recommended Action. Do not keep querying once the bounded tool evidence is available."""
            + """ Append a compact Markdown table with the key evidence, risk, and action."""
            + get_structured_report_instruction()
            + get_language_instruction()
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
            max_tool_chars=1000 if max_context_tokens <= 8192 else 2200,
        )
        result = chain.invoke(messages)

        report = ""

        tool_calls = getattr(result, "tool_calls", []) or []
        if len(tool_calls) == 0:
            report = result.content
        elif len(completed_tool_messages) + len(tool_calls) > max_tool_calls:
            report = _build_partial_market_report(
                state,
                completed_tool_messages,
                max_tool_calls,
            )
            result = AIMessage(content=report)

        return {
            "messages": [result],
            "market_report": report,
        }

    return market_analyst_node
