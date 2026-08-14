"""Tests for webapp LLM endpoint resolution and health probing."""

from __future__ import annotations

from unittest import mock

import pytest


def test_resolve_openrouter_default_url():
    from webapp.llm_endpoint import resolve_llm_base_url

    assert resolve_llm_base_url("openrouter", None) == "https://openrouter.ai/api/v1"
    assert (
        resolve_llm_base_url("openrouter", "https://custom.example/v1")
        == "https://custom.example/v1"
    )


def test_llm_auth_headers_openrouter(monkeypatch):
    from webapp.llm_endpoint import llm_auth_headers

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert llm_auth_headers("openrouter") == {}
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert llm_auth_headers("openrouter") == {"Authorization": "Bearer sk-or-test"}


def test_api_key_configured_openrouter(monkeypatch):
    from webapp.llm_endpoint import api_key_configured

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert api_key_configured("openrouter") is False
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert api_key_configured("openrouter") is True


def test_probe_openrouter_success(monkeypatch):
    from webapp import llm_endpoint

    payload = {
        "data": [
            {
                "id": "meta-llama/llama-3.3-70b-instruct:free",
                "supported_parameters": ["tools"],
            },
            {
                "id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                "supported_parameters": [],
            },
            {"id": "openai/text-embedding-3-small"},
            {"id": "anthropic/claude-sonnet-4", "supported_parameters": ["tools"]},
        ]
    }

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    with mock.patch.object(llm_endpoint.http_requests, "get", return_value=FakeResp()) as get:
        result = llm_endpoint.probe_llm_endpoint("openrouter", None)

    assert result["reachable"] is True
    assert result["backend_url"] == "https://openrouter.ai/api/v1"
    assert result["models"] == ["meta-llama/llama-3.3-70b-instruct:free"]
    assert result["models_meta"]["tool_capable_only"] is True
    assert result["models_meta"]["excluded_count"] == 1
    assert "nemotron" in result["models_meta"]["excluded_examples"][0]
    assert result["tool_capable_models_count"] == 1
    assert "50" in result["rate_limits"]["daily_free_requests"]
    assert "20 RPM" in result["rate_limits"]["requests_per_minute"]
    assert result["api_key_set"] is True
    get.assert_called_once()
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-or-test"


def test_probe_local_requires_backend_url():
    from webapp.llm_endpoint import probe_llm_endpoint

    result = probe_llm_endpoint("openai_compatible", None)
    assert result["reachable"] is False
    assert "no backend_url" in result["error"]


def test_model_presets_include_openrouter():
    from webapp.model_presets import MODEL_PRESETS

    ids = {p["id"] for p in MODEL_PRESETS}
    assert "openrouter_free_llama" in ids
    preset = next(p for p in MODEL_PRESETS if p["id"] == "openrouter_free_llama")
    assert preset["llm_provider"] == "openrouter"
    assert preset["deep_think_llm"].endswith(":free")


def test_local_model_presets_include_docker_backend_url():
    from webapp.model_presets import MODEL_PRESETS

    for preset_id in ("fast_local", "balanced_gemma"):
        preset = next(p for p in MODEL_PRESETS if p["id"] == preset_id)
        assert preset["llm_provider"] == "openai_compatible"
        assert preset["backend_url"] == "http://host.docker.internal:1234/v1"


def test_model_presets_include_hybrid_local_quick_openrouter_deep():
    from webapp.model_presets import MODEL_PRESETS

    preset = next(p for p in MODEL_PRESETS if p["id"] == "hybrid_local_quick_openrouter_deep")
    assert preset["llm_provider"] == "hybrid"
    assert preset["quick_provider"] == "openai_compatible"
    assert preset["quick_backend_url"] == "http://host.docker.internal:1234/v1"
    assert preset["quick_think_llm"] == "qwen/qwen3-4b-2507"
    assert preset["deep_provider"] == "openrouter"
    assert preset["deep_backend_url"] is None
    assert preset["deep_think_llm"] == "meta-llama/llama-3.3-70b-instruct:free"


def test_resolve_health_backend_url_explicit_provider():
    from webapp.server import _resolve_health_backend_url

    cfg = {"backend_url": "http://host.docker.internal:1234/v1"}
    # Explicit provider (UI preset) must not inherit local backend_url
    assert _resolve_health_backend_url(None, "openrouter", cfg) is None
    # Config default when no provider override
    assert (
        _resolve_health_backend_url(None, None, cfg)
        == "http://host.docker.internal:1234/v1"
    )
    # Explicit backend_url always wins
    assert (
        _resolve_health_backend_url("https://custom/v1", "openrouter", cfg)
        == "https://custom/v1"
    )


def test_llm_health_checks_quick_and_deep_routes(monkeypatch):
    from webapp import server

    calls = []

    def fake_probe(provider, backend_url=None, *, timeout=5):
        calls.append((provider, backend_url, timeout))
        return {
            "reachable": True,
            "provider": provider,
            "backend_url": backend_url or "https://openrouter.ai/api/v1",
            "models": ["m"],
            "api_key_set": True,
            "error": None,
            "hint": None,
        }

    monkeypatch.setattr("webapp.llm_endpoint.probe_llm_endpoint", fake_probe)

    result = server.llm_health(
        provider="hybrid",
        quick_provider="openai_compatible",
        quick_backend_url="http://host.docker.internal:1234/v1",
        deep_provider="openrouter",
    )

    assert result["reachable"] is True
    assert result["mode"] == "hybrid"
    assert result["quick"]["provider"] == "openai_compatible"
    assert result["deep"]["provider"] == "openrouter"
    assert calls == [
        ("openai_compatible", "http://host.docker.internal:1234/v1", 5),
        ("openrouter", None, 5),
    ]
