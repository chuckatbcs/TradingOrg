# Dynamic Model Picker, Verify, and Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make LLM model selection resilient to catalog churn by resolving live closest matches, auto-launching the local runner when down, tool-smoke-testing before scans, and remapping+resuming on mid-run model/tool failures — without changing quick/deep agent routing or preset analyst patterns.

**Architecture:** Add three focused webapp modules (`model_resolution`, `llm_launch`, `llm_verify`) as the shared source of truth. Wire them into `/api/models`, new `/api/llm-verify`, analyze/queue entrypoints, and `RunManager._execute` recovery. Keep `tradingagents/graph/setup.py` and `resolve_llm_config` unchanged for agent→role bindings.

**Tech Stack:** Python 3 / FastAPI webapp, `requests` for LLM HTTP, existing `probe_llm_endpoint`, OpenRouter tool filters, vanilla JS UI, pytest.

## Global Constraints

- Do **not** change which agents bind to quick vs deep in `tradingagents/graph/setup.py`.
- Preserve preset → analyst checkbox patterns and OpenRouter free-tier guardrails.
- Closest-match never invents IDs outside the live catalog.
- Auto-launch is non-interactive (web process execs env command; no user modal).
- One automatic recovery chain per role per run.
- Spec: `docs/superpowers/specs/2026-07-24-dynamic-model-picker-design.md`.
- Prefer TDD; commit after each task when the user has authorized commits (otherwise stop after tests green and report).

---

## File structure

| File | Responsibility |
|------|----------------|
| `webapp/model_resolution.py` | Closest-match resolver + route signature helpers |
| `webapp/llm_launch.py` | Ensure local backend up via launch/load commands + poll |
| `webapp/llm_verify.py` | Tool-capable smoke test per route |
| `webapp/server.py` | `/api/llm-verify`; resolve/launch on models/analyze/queue |
| `webapp/runs.py` | Classify model failures; remap; resume; `recovery_events` |
| `webapp/static/app.js` | Verify gate, remap notes, invalidate cache |
| `webapp/static/index.html` | Verify button + status elements |
| `.env.example` | Launch/load/timeout env vars |
| `docs/MODELS.md` | Behavior notes |
| `tests/test_model_resolution.py` | Resolver unit tests |
| `tests/test_llm_launch.py` | Launch/poll unit tests |
| `tests/test_llm_verify.py` | Smoke-test unit tests |
| `tests/test_llm_recovery.py` | Mid-run remap+resume classification/helpers |

---

### Task 1: Model resolution module

**Files:**
- Create: `webapp/model_resolution.py`
- Test: `tests/test_model_resolution.py`

**Interfaces:**
- Produces:
  - `ModelResolution(requested: str | None, resolved: str | None, remapped: bool, reason: str, catalog_fingerprint: str)`
  - `resolve_model(requested: str | None, catalog: list[str], *, provider: str | None = None, exclude: set[str] | None = None) -> ModelResolution`
  - `score_candidate(requested: str, candidate: str, *, provider: str | None = None) -> int`
  - `catalog_fingerprint(catalog: list[str]) -> str`
  - `route_signature(provider: str | None, backend_url: str | None, model: str | None, *, quick_provider=None, quick_backend_url=None, deep_provider=None, deep_backend_url=None, quick_model=None, deep_model=None) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_model_resolution.py
from webapp.model_resolution import resolve_model, score_candidate, catalog_fingerprint


def test_exact_match_not_remapped():
    catalog = ["meta-llama/llama-3.3-70b-instruct:free", "qwen/qwen3-coder:free"]
    result = resolve_model("meta-llama/llama-3.3-70b-instruct:free", catalog)
    assert result.resolved == "meta-llama/llama-3.3-70b-instruct:free"
    assert result.remapped is False


def test_missing_prefers_same_family_and_free_tier():
    catalog = [
        "google/gemma-4-26b-a4b-it:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "qwen/qwen3-coder:free",
    ]
    result = resolve_model("meta-llama/llama-4-missing:free", catalog, provider="openrouter")
    assert result.resolved == "meta-llama/llama-3.3-70b-instruct:free"
    assert result.remapped is True
    assert "family" in result.reason or "closest" in result.reason


def test_exclude_skips_failed_id():
    catalog = ["a/model-a:free", "a/model-b:free"]
    result = resolve_model("a/gone:free", catalog, exclude={"a/model-a:free"})
    assert result.resolved == "a/model-b:free"


def test_empty_catalog_fails_soft():
    result = resolve_model("x", [])
    assert result.resolved is None
    assert result.remapped is False
    assert "empty" in result.reason.lower() or "no" in result.reason.lower()


def test_fingerprint_stable():
    assert catalog_fingerprint(["b", "a"]) == catalog_fingerprint(["a", "b"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_model_resolution.py -v`  
Expected: FAIL with `ModuleNotFoundError` or import error for `webapp.model_resolution`

- [ ] **Step 3: Implement `webapp/model_resolution.py`**

```python
"""Resolve preferred model IDs against a live catalog with closest-match fallback."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Iterable


@dataclass(frozen=True)
class ModelResolution:
    requested: str | None
    resolved: str | None
    remapped: bool
    reason: str
    catalog_fingerprint: str


def catalog_fingerprint(catalog: list[str]) -> str:
    joined = "\n".join(sorted({c.strip() for c in catalog if c}))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _family_prefix(model_id: str) -> str:
    # org/name → org; also keep first path segment before variant noise
    return model_id.split("/", 1)[0].lower() if model_id else ""


def _base_name(model_id: str) -> str:
    name = model_id.split("/", 1)[-1].lower()
    return re.sub(r"(:free|:beta|:extended).*$", "", name)


def score_candidate(requested: str, candidate: str, *, provider: str | None = None) -> int:
    if not requested or not candidate:
        return -10_000
    score = 0
    req_free = requested.endswith(":free")
    cand_free = candidate.endswith(":free")
    if req_free == cand_free:
        score += 50
    elif req_free and not cand_free:
        score -= 40
    if _family_prefix(requested) and _family_prefix(requested) == _family_prefix(candidate):
        score += 100
    req_base, cand_base = _base_name(requested), _base_name(candidate)
    if req_base == cand_base:
        score += 80
    elif req_base and cand_base.startswith(req_base[:6]):
        score += 40
    # Prefer instruct/chat over coder/tiny when otherwise close
    if "instruct" in cand_base or "chat" in cand_base:
        score += 10
    if "coder" in cand_base or "tiny" in cand_base or "nano" in cand_base:
        score -= 5
    if provider and provider.lower() == "openrouter" and candidate.endswith(":free"):
        score += 5
    return score


def resolve_model(
    requested: str | None,
    catalog: list[str],
    *,
    provider: str | None = None,
    exclude: set[str] | None = None,
) -> ModelResolution:
    fp = catalog_fingerprint(catalog)
    exclude = exclude or set()
    usable = [m for m in catalog if m and m not in exclude]
    if not usable:
        return ModelResolution(
            requested=requested,
            resolved=None,
            remapped=False,
            reason="catalog empty or all candidates excluded",
            catalog_fingerprint=fp,
        )
    if requested and requested in usable:
        return ModelResolution(
            requested=requested,
            resolved=requested,
            remapped=False,
            reason="exact match",
            catalog_fingerprint=fp,
        )
    if not requested:
        pick = usable[0]
        return ModelResolution(
            requested=requested,
            resolved=pick,
            remapped=True,
            reason="no preferred model; using first catalog entry",
            catalog_fingerprint=fp,
        )
    ranked = sorted(
        usable,
        key=lambda c: (-score_candidate(requested, c, provider=provider), c),
    )
    pick = ranked[0]
    return ModelResolution(
        requested=requested,
        resolved=pick,
        remapped=True,
        reason=f"closest match (family/tier/name score; preferred missing)",
        catalog_fingerprint=fp,
    )


def route_signature(
    provider: str | None,
    backend_url: str | None,
    model: str | None,
    *,
    quick_provider: str | None = None,
    quick_backend_url: str | None = None,
    deep_provider: str | None = None,
    deep_backend_url: str | None = None,
    quick_model: str | None = None,
    deep_model: str | None = None,
) -> str:
    parts = [
        provider or "",
        backend_url or "",
        model or "",
        quick_provider or "",
        quick_backend_url or "",
        deep_provider or "",
        deep_backend_url or "",
        quick_model or "",
        deep_model or "",
    ]
    return "|".join(parts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_model_resolution.py -v`  
Expected: PASS

- [ ] **Step 5: Commit (only if user authorized commits)**

```bash
git add webapp/model_resolution.py tests/test_model_resolution.py
git commit -m "$(cat <<'EOF'
feat: add live catalog model resolver with closest-match fallback

EOF
)"
```

---

### Task 2: Local LLM auto-launch

**Files:**
- Create: `webapp/llm_launch.py`
- Modify: `.env.example` (append launch vars near LM Studio section)
- Test: `tests/test_llm_launch.py`

**Interfaces:**
- Consumes: `probe_llm_endpoint` from `webapp.llm_endpoint`
- Produces:
  - `LaunchResult(attempted: bool, reached: bool, error: str | None, detail: str | None)`
  - `ensure_local_llm(provider: str | None, backend_url: str | None, *, model: str | None = None, probe=probe_llm_endpoint) -> LaunchResult`
  - Env: `TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD`, `TRADINGAGENTS_LOCAL_LLM_LOAD_CMD`, `TRADINGAGENTS_LOCAL_LLM_LAUNCH_TIMEOUT_SEC` (default `90`)

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_launch.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_llm_launch.py -v`  
Expected: FAIL import error

- [ ] **Step 3: Implement `webapp/llm_launch.py`**

```python
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
    if os.name == "nt":
        return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=120)
    return subprocess.run(shlex.split(cmd), capture_output=True, text=True, timeout=120)


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
```

- [ ] **Step 4: Append to `.env.example` after the LM Studio section**

```bash
# Auto-start local LLM when the web service finds the backend down (Windows LM Studio CLI example):
#TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD=lms server start
#TRADINGAGENTS_LOCAL_LLM_LOAD_CMD=lms load {model}
#TRADINGAGENTS_LOCAL_LLM_LAUNCH_TIMEOUT_SEC=90
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_llm_launch.py -v`  
Expected: PASS

- [ ] **Step 6: Commit (if authorized)**

```bash
git add webapp/llm_launch.py tests/test_llm_launch.py .env.example
git commit -m "$(cat <<'EOF'
feat: auto-launch local LLM when web health probe fails

EOF
)"
```

---

### Task 3: Tool-capable smoke verify

**Files:**
- Create: `webapp/llm_verify.py`
- Test: `tests/test_llm_verify.py`

**Interfaces:**
- Consumes: `resolve_llm_base_url`, `llm_auth_headers` from `webapp.llm_endpoint`; `resolve_model` from `webapp.model_resolution`
- Produces:
  - `smoke_tool_call(provider, backend_url, model, *, timeout=30) -> dict` with keys `ok`, `error`
  - `verify_routes(routes: list[dict]) -> dict` where each route is `{role, provider, backend_url, requested_model, catalog}` and result includes remaps + smoke results

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_llm_verify.py
from unittest import mock
from webapp import llm_verify


def test_smoke_tool_call_success(monkeypatch):
    class FakeResp:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "1",
                            "type": "function",
                            "function": {"name": "ping", "arguments": "{}"},
                        }],
                    }
                }]
            }

    with mock.patch.object(llm_verify.http_requests, "post", return_value=FakeResp()):
        out = llm_verify.smoke_tool_call("openai_compatible", "http://127.0.0.1:1234/v1", "m1")
    assert out["ok"] is True


def test_verify_routes_remaps_then_smokes():
    with mock.patch.object(llm_verify, "smoke_tool_call", return_value={"ok": True, "error": None}):
        result = llm_verify.verify_routes([{
            "role": "quick",
            "provider": "openrouter",
            "backend_url": None,
            "requested_model": "meta-llama/missing:free",
            "catalog": ["meta-llama/llama-3.3-70b-instruct:free"],
        }])
    assert result["ok"] is True
    assert result["routes"][0]["remapped"] is True
    assert result["routes"][0]["resolved"] == "meta-llama/llama-3.3-70b-instruct:free"
```

- [ ] **Step 2: Run tests — expect FAIL import**

Run: `pytest tests/test_llm_verify.py -v`

- [ ] **Step 3: Implement `webapp/llm_verify.py`**

```python
"""Tool-capable smoke tests for resolved LLM routes."""

from __future__ import annotations

from typing import Any

import requests as http_requests

from webapp.llm_endpoint import llm_auth_headers, resolve_llm_base_url
from webapp.model_resolution import resolve_model


PING_TOOL = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Health check tool; return ok=true.",
        "parameters": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    },
}


def smoke_tool_call(
    provider: str | None,
    backend_url: str | None,
    model: str,
    *,
    timeout: float = 30,
) -> dict[str, Any]:
    base = resolve_llm_base_url(provider, backend_url)
    if not base or not model:
        return {"ok": False, "error": "missing base_url or model"}
    url = f"{base.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Call the ping tool once."},
            {"role": "user", "content": "ping"},
        ],
        "tools": [PING_TOOL],
        "tool_choice": {"type": "function", "function": {"name": "ping"}},
        "max_tokens": 64,
    }
    try:
        resp = http_requests.post(
            url,
            headers={**llm_auth_headers(provider), "Content-Type": "application/json"},
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            return {"ok": False, "error": "model returned no tool_calls"}
        return {"ok": True, "error": None}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def verify_routes(routes: list[dict[str, Any]]) -> dict[str, Any]:
    out_routes = []
    notes: list[str] = []
    all_ok = True
    for route in routes:
        resolution = resolve_model(
            route.get("requested_model"),
            list(route.get("catalog") or []),
            provider=route.get("provider"),
            exclude=set(route.get("exclude") or []),
        )
        smoke = {"ok": False, "error": "unresolved model"}
        if resolution.resolved:
            smoke = smoke_tool_call(
                route.get("provider"),
                route.get("backend_url"),
                resolution.resolved,
            )
        ok = bool(resolution.resolved and smoke.get("ok"))
        all_ok = all_ok and ok
        if resolution.remapped:
            notes.append(
                f"{route.get('role')}: remapped {resolution.requested!r} → {resolution.resolved!r}"
            )
        if not smoke.get("ok"):
            notes.append(f"{route.get('role')}: smoke failed: {smoke.get('error')}")
        out_routes.append({
            "role": route.get("role"),
            "provider": route.get("provider"),
            "backend_url": route.get("backend_url"),
            "requested": resolution.requested,
            "resolved": resolution.resolved,
            "remapped": resolution.remapped,
            "reason": resolution.reason,
            "smoke_ok": bool(smoke.get("ok")),
            "error": None if ok else (smoke.get("error") or resolution.reason),
            "catalog_fingerprint": resolution.catalog_fingerprint,
        })
    return {"ok": all_ok, "routes": out_routes, "notes": notes}
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_verify.py -v`  
Expected: PASS

- [ ] **Step 5: Commit (if authorized)**

```bash
git add webapp/llm_verify.py tests/test_llm_verify.py
git commit -m "$(cat <<'EOF'
feat: add tool-capable LLM smoke verification

EOF
)"
```

---

### Task 4: Server API — `/api/llm-verify` + resolve on model listing

**Files:**
- Modify: `webapp/server.py` (add request model + endpoint; optionally enrich `/api/models` response with `resolved` fields)
- Test: extend `tests/test_llm_endpoint.py` or add `tests/test_llm_verify_api.py` using FastAPI `TestClient` if the project already uses it; otherwise unit-test a thin helper `build_verify_payload(...)` extracted in server or a small `webapp/llm_route_prep.py`.

**Interfaces:**
- Produces: `POST /api/llm-verify` → body fields aligned with analyze (`provider`, `backend_url`, `quick_*`, `deep_*`, `deep_model`, `quick_model`)
- Behavior: for each needed role → `ensure_local_llm` if local → `probe_llm_endpoint` → `verify_routes`

- [ ] **Step 1: Write API test with TestClient**

```python
# tests/test_llm_verify_api.py
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
```

- [ ] **Step 2: Run — expect FAIL (404 or missing symbol)**

Run: `pytest tests/test_llm_verify_api.py -v`

- [ ] **Step 3: Implement endpoint in `webapp/server.py`**

Add imports for `ensure_local_llm`, `verify_routes`, `route_signature`.

Add pydantic model:

```python
class LlmVerifyRequest(BaseModel):
    provider: str | None = None
    backend_url: str | None = None
    quick_provider: str | None = None
    quick_backend_url: str | None = None
    deep_provider: str | None = None
    deep_backend_url: str | None = None
    deep_model: str | None = None
    quick_model: str | None = None
    model_preset: str | None = None
```

Add helper `_prepare_role_routes(req) -> tuple[list[dict], list[str], bool]` that:
1. Uses existing `_request_llm_routes` / `_resolve_role_provider` patterns already in `server.py`
2. Calls `ensure_local_llm` for local roles
3. Probes catalog per role
4. Builds route dicts for `verify_routes`

```python
@app.post("/api/llm-verify")
def llm_verify(req: LlmVerifyRequest):
    routes, launch_notes, launch_attempted = _prepare_verify_routes(req)
    result = verify_routes(routes)
    result["launch_attempted"] = launch_attempted
    result["notes"] = launch_notes + list(result.get("notes") or [])
    result["route_signature"] = route_signature(
        req.provider,
        req.backend_url,
        None,
        quick_provider=req.quick_provider,
        quick_backend_url=req.quick_backend_url,
        deep_provider=req.deep_provider,
        deep_backend_url=req.deep_backend_url,
        quick_model=req.quick_model,
        deep_model=req.deep_model,
    )
    return result
```

Also, in analyze/queue prep (Task 5), reuse `_prepare_verify_routes` so resolution is not duplicated.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_llm_verify_api.py tests/test_llm_endpoint.py -v`  
Expected: PASS

- [ ] **Step 5: Commit (if authorized)**

```bash
git add webapp/server.py tests/test_llm_verify_api.py
git commit -m "$(cat <<'EOF'
feat: add /api/llm-verify with launch+resolve+smoke pipeline

EOF
)"
```

---

### Task 5: Gate analyze and Screen & Queue on verify; apply resolved models

**Files:**
- Modify: `webapp/server.py` (`/api/analyze`, `/api/firm/queue-screen`)
- Modify: `webapp/static/app.js`, `webapp/static/index.html`

**Interfaces:**
- Consumes: `/api/llm-verify`, `currentModelRunOptions()`
- Server: before starting a run, resolve models (and optionally require `verified_signature` matching current routes, or re-run verify inline)
- Prefer **server-side re-verify** on analyze/queue so the gate cannot be bypassed by the client alone; UI still disables buttons for UX.

- [ ] **Step 1: Write server test that analyze remaps missing model into run params**

```python
def test_analyze_resolves_missing_model(monkeypatch):
    from fastapi.testclient import TestClient
    from webapp.server import app, manager
    client = TestClient(app)

    fake_probe = {
        "reachable": True,
        "models": ["meta-llama/llama-3.3-70b-instruct:free"],
        "error": None,
    }
    with mock.patch("webapp.server.probe_llm_endpoint", return_value=fake_probe), \
         mock.patch("webapp.server.ensure_local_llm") as launch, \
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
        })
    assert res.status_code == 200
    params = start.call_args.args[0]
    assert params["quick_model"] == "meta-llama/llama-3.3-70b-instruct:free"
    assert params["deep_model"] == "meta-llama/llama-3.3-70b-instruct:free"
```

(Adjust field names to match the real `AnalyzeRequest` / `start_run` params in `server.py`.)

- [ ] **Step 2: Run — expect FAIL until wiring exists**

- [ ] **Step 3: Wire server analyze/queue**

Before `manager.start_run` / `queue_screener_finalists`:
1. Build verify routes + run `verify_routes` (after launch).
2. If not `ok` → `HTTPException(400, detail=notes)`.
3. Replace request quick/deep model fields with `resolved` values; attach `model_resolution` dict onto params for the run record.

- [ ] **Step 4: UI changes**

In `index.html` inside Model settings (near `#model-warning`):

```html
<button type="button" id="verify-models-btn">Verify models</button>
<p id="verify-status" class="help-hint" aria-live="polite"></p>
```

In `app.js`:
- `let lastVerify = { signature: null, ok: false };`
- `async function verifyModels({ silent=false } = {})` → POST `/api/llm-verify` with `currentModelRunOptions()` fields; update selects to resolved IDs via `selectOption`; set `lastVerify`; enable/disable `#run-btn` and `#firm-queue-screen-btn`.
- Call `verifyModels({ silent: true })` after `loadModels` / preset apply when health was down or remaps occurred.
- On analyze / queue click: if `!lastVerify.ok`, await `verifyModels()` first; abort if still not ok.
- Show remap notes in `#verify-status`.

- [ ] **Step 5: Manual check**

Run web (docker or uvicorn), open UI, change to a missing model id if possible, click Verify — expect remap + green smoke.

- [ ] **Step 6: Automated tests**

Run: `pytest tests/test_llm_verify_api.py tests/test_model_resolution.py -v` plus the new analyze test file.

- [ ] **Step 7: Commit (if authorized)**

```bash
git add webapp/server.py webapp/static/app.js webapp/static/index.html tests/
git commit -m "$(cat <<'EOF'
feat: gate analyze and queue on LLM verify with resolved models

EOF
)"
```

---

### Task 6: Mid-run remap + resume recovery

**Files:**
- Modify: `webapp/runs.py`
- Test: `tests/test_llm_recovery.py`

**Interfaces:**
- Produces:
  - `is_model_route_error(exc: BaseException) -> bool`
  - `role_for_agent(agent: str | None) -> literal['quick','deep'] | None`
  - In `_execute`: on model-route error with useful reports (or even without, restart mode), attempt recovery once per role

- [ ] **Step 1: Write failing unit tests**

```python
# tests/test_llm_recovery.py
from webapp.runs import is_model_route_error, role_for_agent


def test_detects_model_not_found():
    assert is_model_route_error(RuntimeError("model 'x' not found"))
    assert is_model_route_error(RuntimeError("No endpoints found that support tool use"))
    assert is_model_route_error(ConnectionError("Connection refused"))
    assert not is_model_route_error(ValueError("invalid ticker"))


def test_role_for_agent():
    assert role_for_agent("Market Analyst") == "quick"
    assert role_for_agent("Research Manager") == "deep"
    assert role_for_agent("Portfolio Manager") == "deep"
```

- [ ] **Step 2: Run — expect FAIL until helpers exist**

- [ ] **Step 3: Implement helpers + recovery branch in `_execute`**

```python
def is_model_route_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    needles = (
        "model not found",
        "does not exist",
        "no endpoints found that support tool use",
        "tool use",
        "connection refused",
        "failed to establish a new connection",
        "404",
        "model_not_found",
    )
    return any(n in text for n in needles)


def role_for_agent(agent: str | None) -> str | None:
    if not agent:
        return None
    if agent in DEEP_ROUTE_AGENTS:
        return "deep"
    if agent in QUICK_ROUTE_AGENTS:
        return "quick"
    return "quick"  # default tool-loop agents to quick
```

In `_execute` `except` block, **before** marking failed (and after rate-limit pause handling):

```python
if is_model_route_error(exc):
    recovered = self._attempt_model_recovery(run_id, params, exc, current_agent)
    if recovered:
        return
```

Implement `_attempt_model_recovery`:
1. Read run record; track `recovery_roles_tried: set` on params/run (max one chain per role).
2. Determine `role = role_for_agent(current_agent)`.
3. If role already recovered → return False.
4. If local role → `ensure_local_llm(...)`.
5. Probe catalog; `resolve_model(current, catalog, exclude={current} | known_bad)`.
6. `smoke_tool_call` on candidate; loop up to 3 candidates.
7. On success: append `recovery_events`, call `self.resume_run(run_id, {f"{role}_model": new_id, ...})` **or** inline start child with resume params; mark parent `status="paused"` / `resume_available` with reason describing auto-recovery, and ensure child starts automatically (preferred: call internal resume that starts the thread, same as `resume_run`).
8. Return True if child started.

Also initialize `"recovery_events": []` in `start_run` records.

- [ ] **Step 4: Add an integration-style unit test with mocks**

Mock `probe_llm_endpoint`, `ensure_local_llm`, `smoke_tool_call`, and `resume_run` to assert recovery calls resume once with remapped model.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_llm_recovery.py tests/test_web_run_resume.py -v`  
Expected: PASS (no regressions on existing resume tests)

- [ ] **Step 6: Commit (if authorized)**

```bash
git add webapp/runs.py tests/test_llm_recovery.py
git commit -m "$(cat <<'EOF'
feat: remap LLM role and resume runs after model/tool failures

EOF
)"
```

---

### Task 7: Docs polish + full verification

**Files:**
- Modify: `docs/MODELS.md` (short section: dynamic resolve, verify, auto-launch, recovery)
- Modify: `.env.example` if anything missing from Task 2

- [ ] **Step 1: Add MODELS.md section**

```markdown
## Dynamic model resolve, verify, and recovery

The web UI no longer depends on a preset model ID remaining forever:

1. **Resolve** — preferred quick/deep IDs are matched against the live `/models` catalog; if missing, the closest tool-capable match in the same role is selected.
2. **Auto-launch** — if a local (`openai_compatible` / Ollama) backend is down, the web service runs `TRADINGAGENTS_LOCAL_LLM_LAUNCH_CMD` (and optional `TRADINGAGENTS_LOCAL_LLM_LOAD_CMD`) then polls until ready.
3. **Verify** — `POST /api/llm-verify` performs a tool-capable smoke test; Run Analysis and Screen & Queue stay gated until verify passes.
4. **Recover** — if a run fails with model-not-found / no-tool-endpoints / local connection errors, that role is remapped and the run resumes from the last completed agent (one automatic chain per role).

Quick vs deep agent routing is unchanged: analysts and tool-heavy agents use quick; Research Manager and Portfolio Manager use deep.
```

- [ ] **Step 2: Run full related suite**

```bash
pytest tests/test_model_resolution.py tests/test_llm_launch.py tests/test_llm_verify.py tests/test_llm_verify_api.py tests/test_llm_recovery.py tests/test_llm_endpoint.py tests/test_hybrid_model_routing.py tests/test_openrouter_free_guardrails.py tests/test_web_run_resume.py -v
```

Expected: PASS

- [ ] **Step 3: Commit docs (if authorized)**

```bash
git add docs/MODELS.md docs/superpowers/plans/2026-07-24-dynamic-model-picker.md docs/superpowers/specs/2026-07-24-dynamic-model-picker-design.md
git commit -m "$(cat <<'EOF'
docs: document dynamic model resolve, verify, and recovery

EOF
)"
```

---

## Spec coverage checklist

| Spec requirement | Task |
|------------------|------|
| Shared closest-match resolver | Task 1 |
| Auto-start local runner via web | Task 2 |
| Tool-capable smoke verify API | Tasks 3–4 |
| Gate Run / Screen & Queue | Task 5 |
| Apply resolved models to runs | Task 5 |
| Mid-run remap + resume | Task 6 |
| Preserve quick/deep + presets/guardrails | Global Constraints + no `setup.py` edits |
| Env + MODELS docs | Tasks 2, 7 |
| `recovery_events` | Task 6 |

## Placeholder / consistency self-review

- No TBD/TODO left in task steps.
- Names aligned: `ModelResolution`, `resolve_model`, `ensure_local_llm`, `LaunchResult`, `smoke_tool_call`, `verify_routes`, `is_model_route_error`, `role_for_agent`.
- Analyze request field names must be double-checked against live `AnalyzeRequest` during Task 5 (implementer reads `webapp/server.py` and adjusts the test JSON keys if they differ slightly).
