"""Mid-run LLM route failure detection and auto-recovery."""

from __future__ import annotations

from unittest import mock

import pytest

from webapp.runs import is_model_route_error, role_for_agent


@pytest.mark.unit
def test_detects_model_not_found():
    assert is_model_route_error(RuntimeError("model 'x' not found"))
    assert is_model_route_error(RuntimeError("No endpoints found that support tool use"))
    assert is_model_route_error(ConnectionError("Connection refused"))
    assert not is_model_route_error(ValueError("invalid ticker"))


@pytest.mark.unit
def test_role_for_agent():
    assert role_for_agent("Market Analyst") == "quick"
    assert role_for_agent("Research Manager") == "deep"
    assert role_for_agent("Portfolio Manager") == "deep"


@pytest.mark.unit
def test_execute_attempts_model_recovery_and_resumes(tmp_path):
    from webapp.runs import RunManager

    manager = RunManager(tmp_path)
    manager._runs["run123"] = {
        "id": "run123",
        "ticker": "NVDA",
        "trade_date": "2026-07-02",
        "asset_type": "stock",
        "analysts": ["market"],
        "provider": "hybrid",
        "quick_provider": "openai_compatible",
        "quick_model": "bad-model",
        "quick_backend_url": "http://127.0.0.1:1234/v1",
        "deep_provider": "openrouter",
        "deep_model": "openrouter/deep",
        "llm_routes": {
            "quick": {
                "provider": "openai_compatible",
                "model": "bad-model",
                "backend_url": "http://127.0.0.1:1234/v1",
            },
            "deep": {
                "provider": "openrouter",
                "model": "openrouter/deep",
                "backend_url": None,
            },
        },
        "status": "running",
        "reports": {"market_report": "partial"},
        "structured_reports": {},
        "agent_status": [{"agent": "Market Analyst", "status": "in_progress"}],
        "recovery_events": [],
        "recovery_roles_tried": [],
        "recovery_bad_models": [],
    }

    def fail_with_model_error(run_id, params):
        raise RuntimeError("model 'bad-model' not found")

    manager._run_analysis = fail_with_model_error  # type: ignore[method-assign]

    fake_probe = {
        "reachable": True,
        "models": ["good-model", "bad-model"],
        "backend_url": "http://127.0.0.1:1234/v1",
    }
    resume_calls: list[tuple[str, dict | None]] = []

    def fake_resume(run_id, overrides=None):
        resume_calls.append((run_id, overrides))
        return {"id": "child-recovery", "status": "queued"}

    with (
        mock.patch("webapp.runs.probe_llm_endpoint", return_value=fake_probe),
        mock.patch("webapp.runs.ensure_local_llm") as launch,
        mock.patch(
            "webapp.runs.smoke_tool_call",
            return_value={"ok": True, "error": None},
        ),
        mock.patch.object(manager, "resume_run", side_effect=fake_resume),
    ):
        manager._execute(
            "run123",
            {
                "ticker": "NVDA",
                "trade_date": "2026-07-02",
                "analysts": ["market"],
                "quick_provider": "openai_compatible",
                "quick_model": "bad-model",
                "quick_backend_url": "http://127.0.0.1:1234/v1",
            },
        )

    launch.assert_called_once()
    assert len(resume_calls) == 1
    assert resume_calls[0][0] == "run123"
    assert resume_calls[0][1]["quick_model"] == "good-model"

    parent = manager.get_run("run123")
    assert parent is not None
    assert parent["status"] == "paused"
    assert parent["resume_available"] is True
    assert parent["recovery_roles_tried"] == ["quick"]
    assert len(parent["recovery_events"]) == 1
    assert parent["recovery_events"][0]["role"] == "quick"
    assert parent["recovery_events"][0]["from"] == "bad-model"
    assert parent["recovery_events"][0]["to"] == "good-model"


@pytest.mark.unit
def test_recovery_skips_second_attempt_for_same_role(tmp_path):
    from webapp.runs import RunManager

    manager = RunManager(tmp_path)
    manager._runs["run456"] = {
        "id": "run456",
        "ticker": "NVDA",
        "trade_date": "2026-07-02",
        "analysts": ["market"],
        "llm_routes": {
            "quick": {
                "provider": "openrouter",
                "model": "gone:free",
                "backend_url": None,
            },
        },
        "status": "running",
        "reports": {},
        "agent_status": [{"agent": "Market Analyst", "status": "in_progress"}],
        "recovery_events": [],
        "recovery_roles_tried": ["quick"],
        "recovery_bad_models": [],
    }

    manager._run_analysis = lambda run_id, params: (_ for _ in ()).throw(  # type: ignore[method-assign, return-value]
        RuntimeError("model not found")
    )

    with mock.patch.object(manager, "resume_run") as resume_mock:
        manager._execute("run456", {"ticker": "NVDA", "trade_date": "2026-07-02", "analysts": ["market"]})

    resume_mock.assert_not_called()
    run = manager.get_run("run456")
    assert run is not None
    assert run["status"] == "failed"
