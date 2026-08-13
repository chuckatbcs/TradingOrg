from unittest import mock

from webapp import llm_verify


def test_smoke_tool_call_success(monkeypatch):
    class FakeResp:
        status_code = 200
        text = ""
        reason = "OK"

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "type": "function",
                                    "function": {"name": "ping", "arguments": "{}"},
                                }
                            ],
                        }
                    }
                ]
            }

    with mock.patch.object(llm_verify.http_requests, "post", return_value=FakeResp()):
        out = llm_verify.smoke_tool_call("openai_compatible", "http://127.0.0.1:1234/v1", "m1")
    assert out["ok"] is True


def test_smoke_tool_call_retries_string_tool_choice_for_lm_studio():
    """LM Studio rejects OpenAI object tool_choice; accept string 'required'."""
    posts: list[object] = []

    class BadToolChoice:
        status_code = 400
        text = "Invalid tool_choice type: 'object'. Supported string values: none, auto, required"
        reason = "Bad Request"

        def json(self):
            return {"error": self.text}

    class OkResp:
        status_code = 200
        text = ""
        reason = "OK"

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "tool_calls": [
                                {
                                    "id": "1",
                                    "type": "function",
                                    "function": {"name": "ping", "arguments": "{}"},
                                }
                            ]
                        }
                    }
                ]
            }

    def _post(url, **kwargs):
        posts.append(kwargs["json"]["tool_choice"])
        if kwargs["json"]["tool_choice"] == "required":
            return OkResp()
        return BadToolChoice()

    with mock.patch.object(llm_verify.http_requests, "post", side_effect=_post):
        out = llm_verify.smoke_tool_call("openai_compatible", "http://127.0.0.1:1235/v1", "m1")
    assert out["ok"] is True
    assert posts[0] == "required"


def test_verify_routes_remaps_then_smokes():
    with mock.patch.object(llm_verify, "smoke_tool_call", return_value={"ok": True, "error": None}):
        result = llm_verify.verify_routes(
            [
                {
                    "role": "quick",
                    "provider": "openrouter",
                    "backend_url": None,
                    "requested_model": "meta-llama/missing:free",
                    "catalog": ["meta-llama/llama-3.3-70b-instruct:free"],
                }
            ]
        )
    assert result["ok"] is True
    assert result["routes"][0]["remapped"] is True
    assert result["routes"][0]["resolved"] == "meta-llama/llama-3.3-70b-instruct:free"


def test_verify_routes_retries_when_listed_model_smoke_fails():
    """OpenRouter may still list :free IDs that 404 at completion time."""
    calls: list[str] = []

    def _smoke(_provider, _backend, model, **_kwargs):
        calls.append(model)
        if model.endswith("instruct:free"):
            return {"ok": False, "error": "This model is unavailable for free"}
        return {"ok": True, "error": None}

    with mock.patch.object(llm_verify, "smoke_tool_call", side_effect=_smoke):
        result = llm_verify.verify_routes(
            [
                {
                    "role": "quick",
                    "provider": "openrouter",
                    "backend_url": None,
                    "requested_model": "meta-llama/llama-3.3-70b-instruct:free",
                    "catalog": [
                        "meta-llama/llama-3.3-70b-instruct:free",
                        "google/gemma-4-26b-a4b-it:free",
                    ],
                }
            ]
        )
    assert result["ok"] is True
    assert result["routes"][0]["resolved"] == "google/gemma-4-26b-a4b-it:free"
    assert result["routes"][0]["remapped"] is True
    assert calls == [
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-4-26b-a4b-it:free",
    ]
