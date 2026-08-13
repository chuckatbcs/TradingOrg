# TradingAgents/graph/conditional_logic.py

from tradingagents.agents.utils.agent_states import AgentState


DEFAULT_ANALYST_TOOL_CALL_LIMITS = {
    "market": 8,
    "social": 4,
    "news": 8,
    "fundamentals": 4,
}


class ConditionalLogic:
    """Handles conditional logic for determining graph flow."""

    def __init__(
        self,
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        analyst_tool_call_limits: dict[str, int] | None = None,
    ):
        """Initialize with configuration parameters."""
        self.max_debate_rounds = max_debate_rounds
        self.max_risk_discuss_rounds = max_risk_discuss_rounds
        self.analyst_tool_call_limits = {
            **DEFAULT_ANALYST_TOOL_CALL_LIMITS,
            **(analyst_tool_call_limits or {}),
        }

    def _tool_call_count(self, state: AgentState) -> int:
        count = 0
        for msg in state["messages"]:
            count += len(getattr(msg, "tool_calls", []) or [])
        return count

    def _should_continue_tools(
        self,
        state: AgentState,
        analyst_key: str,
        tool_node: str,
        clear_node: str,
    ):
        messages = state["messages"]
        last_message = messages[-1]
        if not last_message.tool_calls:
            return clear_node
        limit = self.analyst_tool_call_limits.get(analyst_key)
        if limit is not None and self._tool_call_count(state) > limit:
            return clear_node
        return tool_node

    def should_continue_market(self, state: AgentState):
        """Determine if market analysis should continue."""
        return self._should_continue_tools(
            state,
            "market",
            "tools_market",
            "Msg Clear Market",
        )

    def should_continue_social(self, state: AgentState):
        """Determine if sentiment-analyst tool round should continue.

        Method name keeps the legacy ``social`` suffix to match the
        ``AnalystType.SOCIAL = "social"`` wire value (saved-config
        back-compat); the returned ``clear_node`` label uses the v0.2.5
        rename so it matches the node registered by the execution plan.
        """
        return self._should_continue_tools(
            state,
            "social",
            "tools_social",
            "Msg Clear Sentiment",
        )

    def should_continue_news(self, state: AgentState):
        """Determine if news analysis should continue."""
        return self._should_continue_tools(
            state,
            "news",
            "tools_news",
            "Msg Clear News",
        )

    def should_continue_fundamentals(self, state: AgentState):
        """Determine if fundamentals analysis should continue."""
        return self._should_continue_tools(
            state,
            "fundamentals",
            "tools_fundamentals",
            "Msg Clear Fundamentals",
        )

    def should_continue_debate(self, state: AgentState) -> str:
        """Determine if debate should continue."""

        if (
            state["investment_debate_state"]["count"] >= 2 * self.max_debate_rounds
        ):  # 3 rounds of back-and-forth between 2 agents
            return "Research Manager"
        if state["investment_debate_state"]["current_response"].startswith("Bull"):
            return "Bear Researcher"
        return "Bull Researcher"

    def should_continue_risk_analysis(self, state: AgentState) -> str:
        """Determine if risk analysis should continue."""
        if (
            state["risk_debate_state"]["count"] >= 3 * self.max_risk_discuss_rounds
        ):  # 3 rounds of back-and-forth between 3 agents
            return "Portfolio Manager"
        if state["risk_debate_state"]["latest_speaker"].startswith("Aggressive"):
            return "Conservative Analyst"
        if state["risk_debate_state"]["latest_speaker"].startswith("Conservative"):
            return "Neutral Analyst"
        return "Aggressive Analyst"
