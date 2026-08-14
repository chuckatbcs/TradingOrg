"""Hybrid quick/deep model routing tests."""

from __future__ import annotations

import importlib


def test_resolve_llm_config_uses_role_specific_route():
    from tradingagents.graph.trading_graph import resolve_llm_config

    config = {
        "llm_provider": "hybrid",
        "backend_url": None,
        "quick_provider": "openai_compatible",
        "quick_backend_url": "http://host.docker.internal:1234/v1",
        "quick_think_llm": "qwen/qwen3-4b-2507",
        "deep_provider": "openrouter",
        "deep_backend_url": None,
        "deep_think_llm": "meta-llama/llama-3.3-70b-instruct:free",
    }

    assert resolve_llm_config(config, "quick") == {
        "provider": "openai_compatible",
        "model": "qwen/qwen3-4b-2507",
        "base_url": "http://host.docker.internal:1234/v1",
    }
    assert resolve_llm_config(config, "deep") == {
        "provider": "openrouter",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "base_url": None,
    }


def test_resolve_llm_config_preserves_single_provider_behavior():
    from tradingagents.graph.trading_graph import resolve_llm_config

    config = {
        "llm_provider": "openai_compatible",
        "backend_url": "http://localhost:1234/v1",
        "quick_think_llm": "local-quick",
        "deep_think_llm": "local-deep",
    }

    assert resolve_llm_config(config, "quick") == {
        "provider": "openai_compatible",
        "model": "local-quick",
        "base_url": "http://localhost:1234/v1",
    }
    assert resolve_llm_config(config, "deep") == {
        "provider": "openai_compatible",
        "model": "local-deep",
        "base_url": "http://localhost:1234/v1",
    }


def test_hybrid_env_overrides(monkeypatch):
    import tradingagents.default_config as default_config_module

    for key in list(default_config_module._ENV_OVERRIDES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "hybrid")
    monkeypatch.setenv("TRADINGAGENTS_QUICK_LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv(
        "TRADINGAGENTS_QUICK_LLM_BACKEND_URL",
        "http://host.docker.internal:1234/v1",
    )
    monkeypatch.setenv("TRADINGAGENTS_QUICK_THINK_LLM", "qwen/qwen3-4b-2507")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("TRADINGAGENTS_DEEP_THINK_LLM", "meta-llama/llama-3.3-70b-instruct:free")

    dc = importlib.reload(default_config_module)
    try:
        assert dc.DEFAULT_CONFIG["llm_provider"] == "hybrid"
        assert dc.DEFAULT_CONFIG["quick_provider"] == "openai_compatible"
        assert dc.DEFAULT_CONFIG["quick_backend_url"] == "http://host.docker.internal:1234/v1"
        assert dc.DEFAULT_CONFIG["quick_think_llm"] == "qwen/qwen3-4b-2507"
        assert dc.DEFAULT_CONFIG["deep_provider"] == "openrouter"
        assert dc.DEFAULT_CONFIG["deep_think_llm"] == "meta-llama/llama-3.3-70b-instruct:free"
    finally:
        for key in list(default_config_module._ENV_OVERRIDES):
            monkeypatch.delenv(key, raising=False)
        importlib.reload(default_config_module)

