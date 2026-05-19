import os

_TRADINGAGENTS_HOME = os.path.join(os.path.expanduser("~"), ".tradingagents")


def _env_or_none(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


_DEFAULT_LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai").strip().lower()
_DEFAULT_BACKEND_URL = _env_or_none("LLM_BACKEND_URL")

# Convenience alias for local/remote Ollama deployments. This lets a Hermes
# worker point TradingOrg at a home-PC Ollama server over Tailscale without
# needing to edit Python code.
if not _DEFAULT_BACKEND_URL and _DEFAULT_LLM_PROVIDER == "ollama":
    _DEFAULT_BACKEND_URL = _env_or_none("OLLAMA_BASE_URL")

DEFAULT_CONFIG = {
    "project_dir": os.path.abspath(os.path.join(os.path.dirname(__file__), ".")),
    "results_dir": os.getenv("TRADINGAGENTS_RESULTS_DIR", os.path.join(_TRADINGAGENTS_HOME, "logs")),
    "data_cache_dir": os.getenv("TRADINGAGENTS_CACHE_DIR", os.path.join(_TRADINGAGENTS_HOME, "cache")),
    "memory_log_path": os.getenv("TRADINGAGENTS_MEMORY_LOG_PATH", os.path.join(_TRADINGAGENTS_HOME, "memory", "trading_memory.md")),
    # Optional cap on the number of resolved memory log entries. When set,
    # the oldest resolved entries are pruned once this limit is exceeded.
    # Pending entries are never pruned. None disables rotation entirely.
    "memory_log_max_entries": None,
    # LLM settings
    "llm_provider": _DEFAULT_LLM_PROVIDER,
    "deep_think_llm": os.getenv("DEEP_THINK_LLM", os.getenv("LOCAL_DEEP_MODEL", "gpt-5.4")),
    "quick_think_llm": os.getenv("QUICK_THINK_LLM", os.getenv("LOCAL_QUICK_MODEL", "gpt-5.4-mini")),
    # When None, each provider's client falls back to its own default endpoint
    # (api.openai.com for OpenAI, generativelanguage.googleapis.com for Gemini, ...).
    # Set LLM_BACKEND_URL or OLLAMA_BASE_URL to route OpenAI-compatible providers
    # to a local/home-PC/free-cloud endpoint.
    "backend_url": _DEFAULT_BACKEND_URL,
    # High-level routing metadata used by Hermes scripts and future service mode.
    "model_routing_mode": os.getenv("MODEL_ROUTING_MODE", "manual"),
    "openrouter_free_quick_model": _env_or_none("OPENROUTER_FREE_QUICK_MODEL"),
    "openrouter_free_deep_model": _env_or_none("OPENROUTER_FREE_DEEP_MODEL"),
    "paid_llm_provider": os.getenv("PAID_LLM_PROVIDER", "openai").strip().lower(),
    "paid_quick_model": os.getenv("PAID_QUICK_MODEL", "gpt-5.4-mini"),
    "paid_deep_model": os.getenv("PAID_DEEP_MODEL", "gpt-5.4"),
    # Provider-specific thinking configuration
    "google_thinking_level": _env_or_none("GOOGLE_THINKING_LEVEL"),      # "high", "minimal", etc.
    "openai_reasoning_effort": _env_or_none("OPENAI_REASONING_EFFORT"),    # "medium", "high", "low"
    "anthropic_effort": _env_or_none("ANTHROPIC_EFFORT"),           # "high", "medium", "low"
    # Checkpoint/resume: when True, LangGraph saves state after each node
    # so a crashed run can resume from the last successful step.
    "checkpoint_enabled": os.getenv("TRADINGAGENTS_CHECKPOINT", "false").strip().lower() in {"1", "true", "yes", "on"},
    # Output language for analyst reports and final decision
    # Internal agent debate stays in English for reasoning quality
    "output_language": os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE", "English"),
    # Debate and discussion settings
    "max_debate_rounds": int(os.getenv("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "1")),
    "max_risk_discuss_rounds": int(os.getenv("TRADINGAGENTS_MAX_RISK_DISCUSS_ROUNDS", "1")),
    "max_recur_limit": 100,
    # Data vendor configuration
    # Category-level configuration (default for all tools in category)
    "data_vendors": {
        "core_stock_apis": os.getenv("TRADINGAGENTS_CORE_STOCK_VENDOR", "yfinance"),       # Options: alpha_vantage, yfinance
        "technical_indicators": os.getenv("TRADINGAGENTS_TECHNICAL_VENDOR", "yfinance"),  # Options: alpha_vantage, yfinance
        "fundamental_data": os.getenv("TRADINGAGENTS_FUNDAMENTAL_VENDOR", "yfinance"),      # Options: alpha_vantage, yfinance
        "news_data": os.getenv("TRADINGAGENTS_NEWS_VENDOR", "yfinance"),             # Options: alpha_vantage, yfinance
    },
    # Tool-level configuration (takes precedence over category-level)
    "tool_vendors": {
        # Example: "get_stock_data": "alpha_vantage",  # Override category default
    },
}