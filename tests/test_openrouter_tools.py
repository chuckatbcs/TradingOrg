"""OpenRouter tool-capability filtering and validation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tradingagents.llm_clients.openrouter_tools import (
    KNOWN_NO_TOOLS,
    KNOWN_TOOL_CAPABLE_FREE,
    entry_supports_tools,
    filter_openrouter_tool_models,
    looks_like_reasoning_without_tools,
    model_id_supports_tools,
    validate_openrouter_model_for_agents,
)


@pytest.mark.unit
def test_entry_supports_tools_from_parameters():
    assert entry_supports_tools(
        {"id": "foo/bar:free", "supported_parameters": ["tools", "temperature"]}
    )
    assert not entry_supports_tools(
        {"id": "foo/bar:free", "supported_parameters": ["temperature"]}
    )


@pytest.mark.unit
def test_known_lists_override_catalog():
    nemotron = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    assert nemotron in KNOWN_NO_TOOLS
    assert not entry_supports_tools(
        {"id": nemotron, "supported_parameters": ["tools"]}
    )
    llama = "meta-llama/llama-3.3-70b-instruct:free"
    assert llama in KNOWN_TOOL_CAPABLE_FREE
    assert entry_supports_tools({"id": llama, "supported_parameters": []})


@pytest.mark.unit
def test_filter_openrouter_tool_models_free_only():
    catalog = [
        {"id": "meta-llama/llama-3.3-70b-instruct:free", "supported_parameters": ["tools"]},
        {"id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", "supported_parameters": []},
        {"id": "anthropic/claude-sonnet-4", "supported_parameters": ["tools"]},
        {"id": "openai/text-embedding-3-small", "supported_parameters": []},
    ]
    included, excluded = filter_openrouter_tool_models(catalog, free_only=True)
    assert "meta-llama/llama-3.3-70b-instruct:free" in included
    assert "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free" in excluded
    assert "anthropic/claude-sonnet-4" not in included
    assert all(m.endswith(":free") for m in included + excluded if "embed" not in m)


@pytest.mark.unit
def test_validate_rejects_nemotron_without_network():
    err = validate_openrouter_model_for_agents(
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        catalog=[],
    )
    assert err is not None
    assert "tool" in err.lower()


@pytest.mark.unit
def test_validate_accepts_llama_without_network():
    assert (
        validate_openrouter_model_for_agents(
            "meta-llama/llama-3.3-70b-instruct:free",
            catalog=[],
        )
        is None
    )


@pytest.mark.unit
def test_reasoning_heuristic():
    assert looks_like_reasoning_without_tools(
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"
    )
    assert not looks_like_reasoning_without_tools(
        "meta-llama/llama-3.3-70b-instruct:free"
    )


@pytest.mark.unit
def test_model_id_supports_tools_against_catalog():
    catalog = [
        {"id": "google/gemma-4-26b-a4b-it:free", "supported_parameters": ["tools"]},
    ]
    assert model_id_supports_tools("google/gemma-4-26b-a4b-it:free", catalog)


@pytest.mark.unit
def test_analyze_rejects_nemotron_before_run(monkeypatch):
    from unittest import mock

    from webapp.server import app

    fake_probe = {
        "reachable": True,
        "models": ["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"],
        "error": None,
    }
    monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "openrouter")
    client = TestClient(app)
    with mock.patch("webapp.server.probe_llm_endpoint", return_value=fake_probe), \
         mock.patch("webapp.server.ensure_local_llm") as launch, \
         mock.patch("webapp.llm_verify.smoke_tool_call", return_value={"ok": True, "error": None}):
        launch.return_value = mock.Mock(attempted=False, reached=True, error=None, detail=None)
        resp = client.post(
            "/api/analyze",
            json={
                "ticker": "MU",
                "trade_date": "2026-07-03",
                "analysts": ["market"],
                "provider": "openrouter",
                "deep_model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                "quick_model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
                "market_date_override": True,
            },
        )
    assert resp.status_code == 400
    assert "tool" in resp.json()["detail"].lower()
