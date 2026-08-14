"""Resumable web runs after rate limits or partial failures."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.mark.unit
def test_rate_limit_failure_with_reports_marks_run_paused(tmp_path):
    from webapp.runs import RunManager

    manager = RunManager(tmp_path)
    manager._runs["run123"] = {
        "id": "run123",
        "ticker": "NVDA",
        "trade_date": "2026-07-02",
        "analysts": ["market"],
        "status": "running",
        "reports": {"market_report": "Market report complete."},
        "structured_reports": {},
        "agent_status": [],
        "llm_routes": {"deep": {"provider": "openrouter"}},
    }

    def fail_with_rate_limit(run_id, params):
        raise RuntimeError("Error code: 429 - retry after 60 seconds")

    manager._run_analysis = fail_with_rate_limit  # type: ignore[method-assign]

    manager._execute("run123", {"ticker": "NVDA", "trade_date": "2026-07-02"})

    run = manager.get_run("run123")
    assert run is not None
    assert run["status"] == "paused"
    assert run["resume_available"] is True
    assert "rate limit" in run["resume_reason"].lower()
    assert run["retry_after_seconds"] == 60
    assert run["reports"]["market_report"] == "Market report complete."


@pytest.mark.unit
def test_list_runs_keeps_resume_fields_for_screen_queue_runs(tmp_path):
    from webapp.runs import RunManager

    manager = RunManager(tmp_path)
    manager._runs["screen-run"] = {
        "id": "screen-run",
        "ticker": "NVDA",
        "trade_date": "2026-07-02",
        "source": "manual_screen",
        "status": "paused",
        "created_at": "2026-07-02T09:30:00",
        "reports": {"market_report": "Market report complete."},
        "structured_reports": {},
        "resume_available": True,
        "resume_reason": "Paused after a provider rate limit.",
        "retry_after_seconds": 60,
        "resumed_from": None,
    }

    [listed] = manager.list_runs()

    assert listed["source"] == "manual_screen"
    assert listed["resume_available"] is True
    assert listed["resume_reason"] == "Paused after a provider rate limit."
    assert listed["retry_after_seconds"] == 60
    assert "reports" not in listed


@pytest.mark.unit
def test_first_resume_node_skips_completed_analysts_and_research():
    from webapp.runs import _first_resume_node

    reports = {
        "market_report": "Market report",
        "sentiment_report": "Sentiment report",
        "news_report": "News report",
        "fundamentals_report": "Fundamentals report",
        "bull_history": "Bull case",
        "bear_history": "Bear case",
    }

    assert _first_resume_node(reports, ["market", "social", "news", "fundamentals"]) == (
        "Research Manager",
        "continue_downstream",
    )
    assert _first_resume_node({"market_report": "done"}, ["market", "news"]) == (
        "News Analyst",
        "continue_from_partial",
    )
    assert _first_resume_node({}, ["market"]) == (None, "restart")


@pytest.mark.unit
def test_resume_run_creates_child_with_overrides_and_audit(tmp_path):
    from webapp.runs import RunManager

    manager = RunManager(tmp_path)
    manager._runs["parent"] = {
        "id": "parent",
        "ticker": "NVDA",
        "trade_date": "2026-07-02",
        "asset_type": "stock",
        "analysts": ["market"],
        "provider": "hybrid",
        "quick_provider": "openai_compatible",
        "deep_provider": "openrouter",
        "quick_model": "local-a",
        "deep_model": "openrouter/free",
        "llm_routes": {
            "quick": {"provider": "openai_compatible", "model": "local-a", "backend_url": None},
            "deep": {"provider": "openrouter", "model": "openrouter/free", "backend_url": None},
        },
        "route_summary": "old",
        "status": "paused",
        "reports": {"market_report": "Market report complete."},
        "structured_reports": {},
        "resume_available": True,
        "resume_attempts": [],
        "source": "manual",
    }

    captured = {}

    def fake_execute(run_id, params):
        captured["run_id"] = run_id
        captured["params"] = params

    manager._execute = fake_execute  # type: ignore[method-assign]

    child = manager.resume_run(
        "parent",
        {
            "deep_provider": "openai_compatible",
            "deep_model": "local-deep",
            "deep_backend_url": "http://host.docker.internal:1234/v1",
        },
    )

    parent = manager.get_run("parent")
    assert parent is not None
    assert parent["resume_attempts"][0]["child_run_id"] == child["id"]
    assert child["resumed_from"] == "parent"
    assert child["resume_mode"] == "continue_downstream"
    assert child["reports"]["market_report"] == "Market report complete."
    assert child["deep_provider"] == "openai_compatible"
    assert child["deep_model"] == "local-deep"
    assert captured["run_id"] == child["id"]
    assert captured["params"]["resume_from_run_id"] == "parent"
    assert captured["params"]["resume_start_node"] == "Bull Researcher"


@pytest.mark.unit
def test_resume_api_accepts_model_overrides(monkeypatch):
    from webapp import server

    def fake_resume_run(run_id, overrides):
        assert run_id == "run123"
        assert overrides["deep_provider"] == "openai_compatible"
        assert overrides["deep_model"] == "local-deep"
        return {"id": "child123", "status": "queued"}

    monkeypatch.setattr(server.manager, "resume_run", fake_resume_run)
    client = TestClient(server.app)

    resp = client.post(
        "/api/runs/run123/resume",
        json={"deep_provider": "openai_compatible", "deep_model": "local-deep"},
    )

    assert resp.status_code == 200
    assert resp.json() == {"run_id": "child123", "status": "queued"}
