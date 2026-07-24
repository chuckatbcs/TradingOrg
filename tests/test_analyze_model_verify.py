"""Analyze and queue-screen gate on server-side LLM verify."""

from unittest import mock

from fastapi.testclient import TestClient


def test_analyze_resolves_missing_model(monkeypatch):
    from webapp.server import app, manager

    client = TestClient(app)
    fake_probe = {
        "reachable": True,
        "models": ["meta-llama/llama-3.3-70b-instruct:free"],
        "error": None,
    }
    with mock.patch("webapp.llm_route_prep.probe_llm_endpoint", return_value=fake_probe), \
         mock.patch("webapp.llm_route_prep.ensure_local_llm") as launch, \
         mock.patch("webapp.llm_verify.smoke_tool_call", return_value={"ok": True, "error": None}), \
         mock.patch.object(manager, "start_run", return_value={"id": "abc", "status": "queued"}) as start:
        launch.return_value = mock.Mock(attempted=False, reached=True, error=None, detail=None)
        res = client.post("/api/analyze", json={
            "ticker": "NVDA",
            "trade_date": "2026-07-23",
            "analysts": ["market"],
            "provider": "openrouter",
            "deep_model": "meta-llama/missing:free",
            "quick_model": "meta-llama/missing:free",
            "openrouter_free_override": True,
            "market_date_override": True,
        })
    assert res.status_code == 200
    params = start.call_args.args[0]
    assert params["quick_model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert params["deep_model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert params["llm_routes"]["quick"]["model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert params["model_resolution"]["quick"]["remapped"] is True


def test_start_run_persists_model_resolution(tmp_path):
    from webapp.runs import RunManager

    manager = RunManager(tmp_path)
    resolution = {
        "quick": {
            "requested": "meta-llama/missing:free",
            "resolved": "meta-llama/llama-3.3-70b-instruct:free",
            "remapped": True,
        }
    }
    with mock.patch.object(manager, "_execute"):
        record = manager.start_run(
            {
                "ticker": "NVDA",
                "trade_date": "2026-07-02",
                "analysts": ["market"],
                "model_resolution": resolution,
            }
        )
    assert record["model_resolution"] == resolution
    stored = manager.get_run(record["id"])
    assert stored is not None
    assert stored["model_resolution"] == resolution


def test_analyze_rejects_failed_verify(monkeypatch):
    from webapp.server import app, manager

    client = TestClient(app)
    fake_probe = {
        "reachable": True,
        "models": ["meta-llama/llama-3.3-70b-instruct:free"],
        "error": None,
    }
    with mock.patch("webapp.llm_route_prep.probe_llm_endpoint", return_value=fake_probe), \
         mock.patch("webapp.llm_route_prep.ensure_local_llm") as launch, \
         mock.patch("webapp.llm_verify.smoke_tool_call", return_value={"ok": False, "error": "no tools"}), \
         mock.patch.object(manager, "start_run") as start:
        launch.return_value = mock.Mock(attempted=False, reached=True, error=None, detail=None)
        res = client.post("/api/analyze", json={
            "ticker": "NVDA",
            "trade_date": "2026-07-23",
            "analysts": ["market"],
            "provider": "openrouter",
            "deep_model": "meta-llama/llama-3.3-70b-instruct:free",
            "quick_model": "meta-llama/llama-3.3-70b-instruct:free",
            "openrouter_free_override": True,
            "market_date_override": True,
        })
    assert res.status_code == 400
    start.assert_not_called()
