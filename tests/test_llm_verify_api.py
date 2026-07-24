from unittest import mock

from fastapi.testclient import TestClient


def test_llm_verify_endpoint_ok(monkeypatch):
    from webapp.server import app

    client = TestClient(app)
    fake_probe = {
        "reachable": True,
        "models": ["meta-llama/llama-3.3-70b-instruct:free"],
        "error": None,
    }
    with mock.patch("webapp.server.probe_llm_endpoint", return_value=fake_probe), \
         mock.patch("webapp.server.ensure_local_llm") as launch, \
         mock.patch("webapp.llm_verify.smoke_tool_call", return_value={"ok": True, "error": None}):
        launch.return_value = mock.Mock(attempted=False, reached=True, error=None, detail=None)
        res = client.post("/api/llm-verify", json={
            "provider": "openrouter",
            "deep_model": "meta-llama/missing:free",
            "quick_model": "meta-llama/missing:free",
        })
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert any(r.get("remapped") for r in body["routes"])
    assert body.get("route_signature")


def test_list_models_includes_resolved_when_model_params(monkeypatch):
    from webapp.server import app

    client = TestClient(app)
    fake_probe = {
        "reachable": True,
        "models": ["meta-llama/llama-3.3-70b-instruct:free"],
        "backend_url": "https://openrouter.ai/api/v1",
        "error": None,
    }
    with mock.patch("webapp.server.probe_llm_endpoint", return_value=fake_probe):
        res = client.get(
            "/api/models",
            params={
                "provider": "openrouter",
                "quick_model": "meta-llama/missing:free",
            },
        )
    assert res.status_code == 200
    body = res.json()
    assert body["resolved"]["quick"]["resolved"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert body["resolved"]["quick"]["remapped"] is True
