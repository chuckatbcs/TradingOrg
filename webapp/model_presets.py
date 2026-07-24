"""Model presets for the web UI (local LM Studio and cloud OpenRouter)."""

from __future__ import annotations

LOCAL_LM_STUDIO_BACKEND_URL = "http://host.docker.internal:1234/v1"

MODEL_PRESETS: list[dict] = [
    {
        "id": "openrouter_free_budget",
        "label": "OpenRouter Free Budget - Market only",
        "llm_provider": "openrouter",
        "deep_think_llm": "google/gemma-4-26b-a4b-it:free",
        "quick_think_llm": "google/gemma-4-26b-a4b-it:free",
        "hint": (
            "Safest free-tier mode: Market analyst only, debate/risk rounds 1, "
            "and a smaller step budget. Avoid all analysts unless you add credits. "
            "Verify remaps automatically if a free model becomes unavailable."
        ),
        "analysts": ["market"],
        "max_debate_rounds": 1,
        "max_risk_rounds": 1,
        "max_recur_limit": 260,
        "max_context_tokens": 8192,
    },
    {
        "id": "openrouter_free_llama",
        "label": "OpenRouter (free) - Gemma 4 26B (was Llama)",
        "llm_provider": "openrouter",
        "deep_think_llm": "google/gemma-4-26b-a4b-it:free",
        "quick_think_llm": "google/gemma-4-26b-a4b-it:free",
        "hint": (
            "Cloud free tier (~50 req/day, 20/min). Set OPENROUTER_API_KEY in .env. "
            "Llama 3.3 70B :free was removed by OpenRouter; Gemma is the default free pick. "
            "Start with Market analyst; avoid Fundamentals unless using Hybrid/local."
        ),
        "analysts": ["market"],
        "max_context_tokens": 8192,
    },
    {
        "id": "openrouter_free_gemma",
        "label": "OpenRouter (free) - Gemma 4 26B",
        "llm_provider": "openrouter",
        "deep_think_llm": "google/gemma-4-26b-a4b-it:free",
        "quick_think_llm": "google/gemma-4-26b-a4b-it:free",
        "hint": (
            "Strong tool calling on OpenRouter free tier. Same rate limits as other "
            ":free models; avoid Fundamentals unless using Hybrid/local."
        ),
        "analysts": ["market", "news"],
        "max_context_tokens": 8192,
    },
    {
        "id": "openrouter_free_qwen",
        "label": "OpenRouter (free) - Qwen3 Coder",
        "llm_provider": "openrouter",
        "deep_think_llm": "qwen/qwen3-coder:free",
        "quick_think_llm": "qwen/qwen3-coder:free",
        "hint": "Code-oriented Qwen3; good tool JSON. Watch the daily free quota on long agent runs.",
        "analysts": ["market"],
        "max_context_tokens": 8192,
    },
    {
        "id": "hybrid_budget_mode",
        "label": "Hybrid budget mode (8K) - Market + News",
        "llm_provider": "hybrid",
        "quick_provider": "openai_compatible",
        "quick_backend_url": LOCAL_LM_STUDIO_BACKEND_URL,
        "quick_think_llm": "qwen/qwen3-4b-2507",
        "deep_provider": "openrouter",
        "deep_backend_url": None,
        "deep_think_llm": "google/gemma-4-26b-a4b-it:free",
        "hint": (
            "Conservative 8K mode: local LM Studio handles Market+News only, "
            "Fundamentals is excluded by default, and prompt snippets are budgeted. "
            "Use when LM Studio Context Length is 8192."
        ),
        "analysts": ["market", "news"],
        "max_debate_rounds": 1,
        "max_risk_rounds": 1,
        "max_recur_limit": 420,
        "max_context_tokens": 8192,
    },
    {
        "id": "hybrid_local_quick_openrouter_deep",
        "label": "Hybrid: Local quick + OpenRouter deep",
        "llm_provider": "hybrid",
        "quick_provider": "openai_compatible",
        "quick_backend_url": LOCAL_LM_STUDIO_BACKEND_URL,
        "quick_think_llm": "qwen/qwen3-4b-2507",
        "deep_provider": "openrouter",
        "deep_backend_url": None,
        "deep_think_llm": "google/gemma-4-26b-a4b-it:free",
        "hint": (
            "Hybrid default: high-call analysts/tool loops use local LM Studio; "
            "research and portfolio manager synthesis use OpenRouter. Requires both "
            "LM Studio and OPENROUTER_API_KEY to be healthy. Set LM Studio Context "
            "Length to 16K preferred; 8K may still fail on Fundamentals/full analysts."
        ),
        "analysts": ["market", "social", "news", "fundamentals"],
        "max_debate_rounds": 1,
        "max_risk_rounds": 1,
        "max_recur_limit": 620,
        "max_context_tokens": 16384,
    },
    {
        "id": "fast_local",
        "label": "Fast (local 8GB) - Qwen3-4B Instruct",
        "llm_provider": "openai_compatible",
        "backend_url": LOCAL_LM_STUDIO_BACKEND_URL,
        "deep_think_llm": "qwen/qwen3-4b-2507",
        "quick_think_llm": "qwen/qwen3-4b-2507",
        "hint": "Non-thinking instruct model. Use Market analyst first; 8K context in LM Studio.",
        "analysts": ["market"],
        "max_context_tokens": 8192,
    },
    {
        "id": "balanced_gemma",
        "label": "Balanced (local) - Gemma 4 E4B",
        "llm_provider": "openai_compatible",
        "backend_url": LOCAL_LM_STUDIO_BACKEND_URL,
        "deep_think_llm": "google/gemma-4-e4b",
        "quick_think_llm": "google/gemma-4-e4b",
        "hint": "Native tool calling; ~5 GB VRAM. Keep context at 8K on 8 GB cards.",
        "analysts": ["market", "news"],
        "max_context_tokens": 8192,
    },
    {
        "id": "custom",
        "label": "Custom (manual)",
        "llm_provider": None,
        "deep_think_llm": None,
        "quick_think_llm": None,
        "hint": "Pick models from the dropdowns. Match your provider (LM Studio or OpenRouter).",
        "analysts": None,
    },
]
