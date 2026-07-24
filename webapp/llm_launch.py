"""Auto-start a local OpenAI-compatible LLM server when unreachable."""

from __future__ import annotations

from dataclasses import dataclass
import os
import shlex
import subprocess
import time
from typing import Any, Callable

from webapp.llm_endpoint import probe_llm_endpoint

LOCAL_PROVIDERS = {"openai_compatible", "ollama"}


@dataclass(frozen=True)
class LaunchResult:
    attempted: bool
    reached: bool
    error: str | None
    detail: str | None


def _is_local_provider(provider: str | None) -> bool:
    return (provider or "").lower() in LOCAL_PROVIDERS


def _timeout_sec() -> float:
    raw = os.environ.get("TRADINGAGENTS_LOCAL_LLM_LAUNCH_TIMEOUT_SEC", "90")
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 90.0


def _run_cmd(cmd: str) -> subprocess.CompletedProcess[str]:
    # Windows-friendly: shell=True when not easily split; prefer list when possible
    timeout = _timeout_sec()
    if os.name == "nt":
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=timeout)


def ensure_local_llm(
    provider: str | None,
    backend_url: str | None,
    *,
    model: str | None = None,
    probe: Callable[..., dict[str, Any]] = probe_llm_endpoint,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> LaunchResult:
    if not _is_local_provider(provider):
        return LaunchResult(False, True, None, "non-local provider; launch skipped")

    first = probe(provider, backend_url)
    if first.get("reachable") and first.get("models"):
        return LaunchResult(False, True, None, "already reachable")

    launch_cmd = (os.environ.get("TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD") or "").strip()
    if not launch_cmd:
        return LaunchResult(
            False,
            False,
            first.get("error") or "local LLM unreachable",
            "TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD not set",
        )

    try:
        completed = _run_cmd(launch_cmd)
    except Exception as exc:
        return LaunchResult(True, False, str(exc), "launch command failed")

    load_cmd = (os.environ.get("TRADINGAGENTS_LOCAL_LLM_LOAD_CMD") or "").strip()
    if load_cmd and model:
        try:
            _run_cmd(load_cmd.replace("{model}", model))
        except Exception as exc:
            return LaunchResult(True, False, str(exc), "load command failed")

    deadline = time.monotonic() + _timeout_sec()
    last_err = first.get("error")
    while time.monotonic() < deadline:
        health = probe(provider, backend_url)
        if health.get("reachable") and health.get("models"):
            detail = f"launch_rc={completed.returncode}"
            return LaunchResult(True, True, None, detail)
        last_err = health.get("error") or last_err
        sleep_fn(1.0)

    return LaunchResult(
        True,
        False,
        last_err or "timed out waiting for local LLM",
        f"launch_rc={completed.returncode}; stdout={completed.stdout[-400:]}",
    )
