"""Guardrails for OpenRouter free-tier web runs."""

from contextlib import contextmanager
from unittest import mock

from fastapi.testclient import TestClient


@contextmanager
def _verify_ok(models=None):
    catalog = models or ["meta-llama/llama-3.3-70b-instruct:free"]
    fake_probe = {"reachable": True, "models": catalog, "error": None}
    with mock.patch("webapp.llm_route_prep.probe_llm_endpoint", return_value=fake_probe), \
         mock.patch("webapp.llm_route_prep.ensure_local_llm") as launch, \
         mock.patch("webapp.llm_verify.smoke_tool_call", return_value={"ok": True, "error": None}):
        launch.return_value = mock.Mock(attempted=False, reached=True, error=None, detail=None)
        yield


def _openrouter_body(**overrides):
    body = {
        "ticker": "MU",
        "trade_date": "2026-07-03",
        "analysts": ["market", "social", "news"],
        "provider": "openrouter",
        "deep_model": "meta-llama/llama-3.3-70b-instruct:free",
        "quick_model": "meta-llama/llama-3.3-70b-instruct:free",
        "max_debate_rounds": 1,
        "max_risk_rounds": 1,
        "market_date_override": True,
    }
    body.update(overrides)
    return body


def test_openrouter_free_rejects_three_analysts_without_override():
    from webapp.server import app

    client = TestClient(app)
    with _verify_ok():
        resp = client.post("/api/analyze", json=_openrouter_body())

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "OpenRouter free" in detail
    assert "Market analyst only" in detail
    assert "override" in detail


def test_openrouter_free_rejects_fundamentals_without_override():
    from webapp.server import app

    client = TestClient(app)
    with _verify_ok():
        resp = client.post(
            "/api/analyze",
            json=_openrouter_body(analysts=["market", "fundamentals"]),
        )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Fundamentals" in detail
    assert "Hybrid" in detail
    assert "override" in detail


def test_openrouter_free_override_allows_run_and_returns_warning(monkeypatch):
    from webapp import server

    def fake_start_run(params):
        assert params["analysts"] == ["market", "social", "news"]
        assert params["openrouter_free_warning"]
        assert params["llm_routes"]["quick"]["provider"] == "openrouter"
        assert params["llm_routes"]["deep"]["provider"] == "openrouter"
        assert "quick openrouter" in params["route_summary"]
        return {"id": "run123", "status": "queued"}

    monkeypatch.setattr(server.manager, "start_run", fake_start_run)
    client = TestClient(server.app)

    with _verify_ok():
        resp = client.post(
            "/api/analyze",
            json=_openrouter_body(openrouter_free_override=True),
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["run_id"] == "run123"
    assert "OpenRouter free" in payload["warning"]


def test_openrouter_paid_or_local_models_are_not_limited(monkeypatch):
    from webapp import server

    def fake_start_run(params):
        return {"id": "run456", "status": "queued"}

    monkeypatch.setattr(server.manager, "start_run", fake_start_run)
    client = TestClient(server.app)

    with _verify_ok(models=["anthropic/claude-sonnet-4"]):
        resp = client.post(
            "/api/analyze",
            json=_openrouter_body(
                deep_model="anthropic/claude-sonnet-4",
                quick_model="anthropic/claude-sonnet-4",
            ),
        )

    assert resp.status_code == 200
    assert "OpenRouter free" not in resp.json().get("warning", "")


def test_openai_compatible_without_backend_url_rejected_before_run(monkeypatch):
    from webapp import server

    def fail_start_run(params):
        raise AssertionError("run should not start without a local backend URL")

    monkeypatch.setattr(server.manager, "start_run", fail_start_run)
    client = TestClient(server.app)

    resp = client.post(
        "/api/analyze",
        json={
            "ticker": "MU",
            "trade_date": "2026-07-03",
            "analysts": ["market"],
            "provider": "openai_compatible",
            "deep_model": "google/gemma-4-e4b",
            "quick_model": "google/gemma-4-e4b",
            "market_date_override": True,
        },
    )

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "openai_compatible" in detail
    assert "backend_url" in detail
    assert "host.docker.internal:1234/v1" in detail


def test_openai_compatible_with_backend_url_starts_run(monkeypatch):
    from webapp import server

    def fake_start_run(params):
        assert params["provider"] == "openai_compatible"
        assert params["backend_url"] == "http://host.docker.internal:1234/v1"
        assert params["llm_routes"]["quick"]["backend_url"] == "http://host.docker.internal:1234/v1"
        assert params["llm_routes"]["deep"]["backend_url"] == "http://host.docker.internal:1234/v1"
        return {"id": "run-local", "status": "queued"}

    monkeypatch.setattr(server.manager, "start_run", fake_start_run)
    client = TestClient(server.app)

    with _verify_ok(models=["google/gemma-4-e4b"]):
        resp = client.post(
            "/api/analyze",
            json={
                "ticker": "MU",
                "trade_date": "2026-07-03",
                "analysts": ["market"],
                "provider": "openai_compatible",
                "backend_url": "http://host.docker.internal:1234/v1",
                "deep_model": "google/gemma-4-e4b",
                "quick_model": "google/gemma-4-e4b",
                "market_date_override": True,
            },
        )

    assert resp.status_code == 200
    assert resp.json()["run_id"] == "run-local"


def test_hybrid_local_quick_openrouter_deep_warns_without_blocking(monkeypatch):
    from webapp import server

    def fake_start_run(params):
        assert params["provider"] == "hybrid"
        assert params["quick_provider"] == "openai_compatible"
        assert params["deep_provider"] == "openrouter"
        assert params["quick_backend_url"] == "http://host.docker.internal:1234/v1"
        assert params["openrouter_free_warning"]
        assert params["llm_routes"]["quick"]["provider"] == "openai_compatible"
        assert params["llm_routes"]["deep"]["provider"] == "openrouter"
        assert "quick openai_compatible" in params["route_summary"]
        assert "deep openrouter" in params["route_summary"]
        return {"id": "run789", "status": "queued"}

    monkeypatch.setattr(server.manager, "start_run", fake_start_run)
    client = TestClient(server.app)

    with _verify_ok(
        models=["qwen/qwen3-4b-2507", "meta-llama/llama-3.3-70b-instruct:free"]
    ):
        resp = client.post(
            "/api/analyze",
            json={
                "ticker": "MU",
                "trade_date": "2026-07-03",
                "analysts": ["market", "social", "news", "fundamentals"],
                "provider": "hybrid",
                "quick_provider": "openai_compatible",
                "quick_backend_url": "http://host.docker.internal:1234/v1",
                "quick_model": "qwen/qwen3-4b-2507",
                "deep_provider": "openrouter",
                "deep_model": "meta-llama/llama-3.3-70b-instruct:free",
                "max_debate_rounds": 1,
                "max_risk_rounds": 1,
                "market_date_override": True,
            },
        )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["run_id"] == "run789"
    assert "final synthesis" in payload["warning"]


def test_rate_limit_errors_explain_openrouter_quota():
    from webapp.runs import _format_run_error

    message = _format_run_error(
        RuntimeError('Error code: 429 - {"error":{"message":"Rate limit exceeded"}}')
    )

    assert "rate limit" in message.lower()
    assert "OpenRouter free" in message
    assert "Market analyst only" in message


def test_recursion_errors_include_current_agent_guidance():
    from langgraph.errors import GraphRecursionError

    from webapp.runs import _format_run_error

    message = _format_run_error(
        GraphRecursionError("Recursion limit of 620 reached"),
        current_agent="Fundamentals Analyst",
    )

    assert "Fundamentals Analyst" in message
    assert "kept requesting tools" in message
    assert "Hybrid/local" in message


def test_hybrid_deep_connection_error_names_openrouter_route():
    from openai import APIConnectionError
    from openai._models import FinalRequestOptions

    from webapp.runs import _format_run_error

    request = FinalRequestOptions.construct(method="post", url="/chat/completions")
    message = _format_run_error(
        APIConnectionError(request=request),
        current_agent="Research Manager",
        llm_routes={
            "quick": {
                "provider": "openai_compatible",
                "model": "qwen/qwen3-4b-2507",
                "backend_url": "http://host.docker.internal:1234/v1",
            },
            "deep": {
                "provider": "openrouter",
                "model": "meta-llama/llama-3.3-70b-instruct:free",
                "backend_url": None,
            },
        },
    )

    assert "deep OpenRouter route" in message
    assert "Research Manager" in message
    assert "OPENROUTER_API_KEY" in message
    assert "openrouter.ai" in message
    assert "Start LM Studio" not in message


def test_openrouter_connection_error_does_not_lead_with_lm_studio():
    from openai import APIConnectionError
    from openai._models import FinalRequestOptions

    from webapp.runs import _format_run_error

    request = FinalRequestOptions.construct(method="post", url="/chat/completions")
    message = _format_run_error(
        APIConnectionError(request=request),
        llm_routes={
            "quick": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
            "deep": {"provider": "openrouter", "model": "meta-llama/llama-3.3-70b-instruct:free"},
        },
    )

    assert "OpenRouter" in message
    assert "OPENROUTER_API_KEY" in message
    assert "Start LM Studio" not in message
