from unittest import mock
from webapp import llm_launch


def test_ensure_skips_when_already_reachable(monkeypatch):
    monkeypatch.delenv("TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD", raising=False)
    probe = mock.Mock(return_value={"reachable": True, "models": ["m1"]})
    result = llm_launch.ensure_local_llm("openai_compatible", "http://127.0.0.1:1234/v1", probe=probe)
    assert result.attempted is False
    assert result.reached is True
    probe.assert_called_once()


def test_ensure_runs_launch_cmd_then_reprobes(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD", "echo launch")
    monkeypatch.setenv("TRADINGAGENTS_LOCAL_LLM_LAUNCH_TIMEOUT_SEC", "2")
    probes = iter([
        {"reachable": False, "models": [], "error": "connection refused"},
        {"reachable": True, "models": ["qwen/qwen3-4b-2507"]},
    ])
    probe = mock.Mock(side_effect=lambda *a, **k: next(probes))
    with mock.patch.object(llm_launch.subprocess, "run") as run:
        run.return_value = mock.Mock(returncode=0, stdout="ok", stderr="")
        result = llm_launch.ensure_local_llm(
            "openai_compatible",
            "http://127.0.0.1:1234/v1",
            model="qwen/qwen3-4b-2507",
            probe=probe,
            sleep_fn=lambda _s: None,
        )
    assert result.attempted is True
    assert result.reached is True
    assert run.called


def test_ensure_noop_for_openrouter(monkeypatch):
    monkeypatch.setenv("TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD", "echo launch")
    probe = mock.Mock(return_value={"reachable": False})
    result = llm_launch.ensure_local_llm("openrouter", None, probe=probe)
    assert result.attempted is False
