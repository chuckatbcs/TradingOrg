"""Auto-start a local OpenAI-compatible LLM server when unreachable."""

from __future__ import annotations

from dataclasses import dataclass
import os
import shlex
import subprocess
import time
from typing import Any, Callable

import requests as http_requests

from webapp.llm_endpoint import probe_llm_endpoint

LOCAL_PROVIDERS = {"openai_compatible", "ollama"}
# Avoid Windows Hyper-V excluded ports (e.g. 8765/8787); use 18787 for the host agent.
DEFAULT_HOST_AGENT_URL = "http://host.docker.internal:18787"
DEFAULT_LAUNCH_CMD = "lms server start"
# -y: non-interactive; --ttl: unload when idle so verify remaps do not pack VRAM.
DEFAULT_LOAD_CMD = "lms load {model} -y --ttl 3600"


@dataclass(frozen=True)
class LaunchResult:
    attempted: bool
    reached: bool
    error: str | None
    detail: str | None
    backend_url: str | None = None


def _is_local_provider(provider: str | None) -> bool:
    return (provider or "").lower() in LOCAL_PROVIDERS


def _timeout_sec() -> float:
    raw = os.environ.get("TRADINGAGENTS_LOCAL_LLM_LAUNCH_TIMEOUT_SEC", "120")
    try:
        return max(5.0, float(raw))
    except ValueError:
        return 120.0


def _host_agent_url() -> str | None:
    """URL of the Windows host agent that can run ``lms`` for Docker web."""
    raw = os.environ.get("TRADINGAGENTS_LOCAL_LLM_HOST_AGENT_URL")
    if raw is not None:
        value = raw.strip()
        if value in ("", "0", "false", "off", "disabled"):
            return None
        return value
    return DEFAULT_HOST_AGENT_URL


def _launch_cmd() -> str:
    return (os.environ.get("TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD") or DEFAULT_LAUNCH_CMD).strip()


def _load_cmd() -> str:
    return (os.environ.get("TRADINGAGENTS_LOCAL_LLM_LOAD_CMD") or DEFAULT_LOAD_CMD).strip()


def _run_cmd(cmd: str) -> subprocess.CompletedProcess[str]:
    timeout = _timeout_sec()
    if os.name == "nt":
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=timeout)


def _start_via_host_agent(model: str | None) -> tuple[bool, str | None, str | None, str | None]:
    """POST /start on the host agent. Returns (ok, error, detail, backend_url)."""
    base = _host_agent_url()
    if not base:
        return False, "host agent disabled", None, None
    url = f"{base.rstrip('/')}/start"
    try:
        resp = http_requests.post(
            url,
            json={"model": model},
            timeout=max(_timeout_sec(), 60.0),
        )
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("ok") is False:
            err = data.get("error") or resp.text[:300] or f"HTTP {resp.status_code}"
            return False, str(err), url, None
        backend_url = data.get("backend_url")
        if not backend_url and data.get("port"):
            backend_url = f"http://host.docker.internal:{data['port']}/v1"
        detail = f"host_agent={url}; port={data.get('port')}; status={data.get('status') or 'started'}"
        return True, None, detail, backend_url
    except Exception as exc:
        return False, str(exc), url, None


def cleanup_local_models(keep_model: str | None = None) -> str | None:
    """Ask the host agent to unload extra LM Studio models (keep at most one)."""
    base = _host_agent_url()
    if not base:
        return None
    url = f"{base.rstrip('/')}/cleanup"
    payload: dict[str, str] = {}
    if keep_model:
        payload["keep"] = keep_model
    try:
        resp = http_requests.post(url, json=payload, timeout=max(_timeout_sec(), 60.0))
        data = resp.json() if resp.content else {}
        if resp.status_code >= 400 or data.get("ok") is False:
            return data.get("error") or f"cleanup HTTP {resp.status_code}"
        notes = data.get("notes") or []
        if isinstance(notes, list):
            return "; ".join(str(n) for n in notes if n)
        return str(notes) if notes else "cleanup ok"
    except Exception as exc:
        return str(exc)


def ensure_local_llm(
    provider: str | None,
    backend_url: str | None,
    *,
    model: str | None = None,
    probe: Callable[..., dict[str, Any]] = probe_llm_endpoint,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> LaunchResult:
    if not _is_local_provider(provider):
        return LaunchResult(False, True, None, "non-local provider; launch skipped", backend_url)

    effective_backend = backend_url
    first = probe(provider, effective_backend)
    if first.get("reachable") and first.get("models"):
        return LaunchResult(False, True, None, "already reachable", effective_backend)

    notes: list[str] = []
    launch_ok = False
    resolved_backend: str | None = None

    # 1) Prefer host agent (Docker web → Windows LM Studio via host.docker.internal).
    agent_ok, agent_err, agent_detail, agent_backend = _start_via_host_agent(model)
    if agent_ok:
        launch_ok = True
        resolved_backend = agent_backend or effective_backend
        if agent_detail:
            notes.append(agent_detail)
    else:
        notes.append(f"host agent: {agent_err}")

    # 2) Fall back to local shell commands (works when web runs on the host).
    if not launch_ok:
        launch_cmd = _launch_cmd()
        try:
            completed = _run_cmd(launch_cmd)
            notes.append(f"launch_cmd rc={completed.returncode}")
            launch_ok = completed.returncode == 0
            if completed.stdout:
                notes.append(completed.stdout[-200:])
            if completed.stderr:
                notes.append(completed.stderr[-200:])
        except Exception as exc:
            notes.append(f"launch command failed: {exc}")

        load_cmd = _load_cmd()
        if load_cmd and model:
            try:
                loaded = _run_cmd(load_cmd.replace("{model}", model))
                notes.append(f"load_cmd rc={loaded.returncode}")
            except Exception as exc:
                notes.append(f"load command failed: {exc}")
        resolved_backend = effective_backend

    if not launch_ok:
        return LaunchResult(
            True,
            False,
            first.get("error") or agent_err or "local LLM unreachable",
            "; ".join(notes) if notes else "launch failed",
            effective_backend,
        )

    probe_url = resolved_backend or effective_backend
    deadline = time.monotonic() + _timeout_sec()
    last_err = first.get("error")
    while time.monotonic() < deadline:
        health = probe(provider, probe_url)
        if health.get("reachable") and health.get("models"):
            return LaunchResult(
                True,
                True,
                None,
                "; ".join(notes) or "local LLM started",
                probe_url,
            )
        last_err = health.get("error") or last_err
        sleep_fn(1.0)

    return LaunchResult(
        True,
        False,
        last_err or "timed out waiting for local LLM",
        "; ".join(notes) if notes else "timed out after launch",
        probe_url,
    )
