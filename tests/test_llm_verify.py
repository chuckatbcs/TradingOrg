from unittest import mock

from webapp import llm_verify


def test_smoke_tool_call_success(monkeypatch):
    class FakeResp:
        status_code = 200

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
