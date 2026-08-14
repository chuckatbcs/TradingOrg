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
    # LM Studio / some local servers only accept string tool_choice
    # (none|auto|required). OpenRouter accepts the OpenAI object form too.
    tool_choices: list[Any] = ["required", {"type": "function", "function": {"name": "ping"}}]
    last_err: str | None = None
    for tool_choice in tool_choices:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "Call the ping tool once."},
                {"role": "user", "content": "ping"},
            ],
            "tools": [PING_TOOL],
            "tool_choice": tool_choice,
            "max_tokens": 64,
        }
        try:
            resp = http_requests.post(
                url,
                headers={**llm_auth_headers(provider), "Content-Type": "application/json"},
                json=payload,
                timeout=timeout,
            )
            if resp.status_code >= 400:
                body = (resp.text or "")[:240]
                last_err = f"HTTP {resp.status_code}: {body or resp.reason}"
                # Retry with the other tool_choice shape when the server rejects ours.
                if "tool_choice" in body.lower() or resp.status_code == 400:
                    continue
                return {"ok": False, "error": last_err}
            data = resp.json()
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                last_err = "model returned no tool_calls"
                continue
            return {"ok": True, "error": None}
        except Exception as exc:
            last_err = str(exc)
            continue
    return {"ok": False, "error": last_err or "smoke tool call failed"}


def verify_routes(routes: list[dict[str, Any]], *, max_candidates: int = 3) -> dict[str, Any]:
    """Resolve and smoke-test each route; retry closest matches when smoke fails."""
    out_routes = []
    notes: list[str] = []
    all_ok = True
    for route in routes:
        catalog = list(route.get("catalog") or [])
        exclude = set(route.get("exclude") or [])
        requested = route.get("requested_model")
        provider = route.get("provider")
        backend_url = route.get("backend_url")
        role = route.get("role")

        resolution = resolve_model(
            requested, catalog, provider=provider, exclude=exclude
        )
        smoke: dict[str, Any] = {"ok": False, "error": "unresolved model"}
        attempts = 0
        while resolution.resolved and attempts < max_candidates:
            attempts += 1
            smoke = smoke_tool_call(provider, backend_url, resolution.resolved)
            if smoke.get("ok"):
                break
            notes.append(
                f"{role}: smoke failed for {resolution.resolved!r}: {smoke.get('error')}"
            )
            exclude.add(resolution.resolved)
            next_resolution = resolve_model(
                requested, catalog, provider=provider, exclude=exclude
            )
            if not next_resolution.resolved or next_resolution.resolved == resolution.resolved:
                break
            notes.append(
                f"{role}: trying next candidate {next_resolution.resolved!r}"
            )
            resolution = next_resolution

        ok = bool(resolution.resolved and smoke.get("ok"))
        all_ok = all_ok and ok
        remapped = bool(
            resolution.resolved
            and requested
            and resolution.resolved != requested
        )
        if remapped:
            notes.append(f"{role}: remapped {requested!r} → {resolution.resolved!r}")
        elif not smoke.get("ok"):
            notes.append(f"{role}: smoke failed: {smoke.get('error')}")
        out_routes.append(
            {
                "role": role,
                "provider": provider,
                "backend_url": backend_url,
                "requested": requested,
                "resolved": resolution.resolved,
                "remapped": remapped or resolution.remapped,
                "reason": resolution.reason,
                "smoke_ok": bool(smoke.get("ok")),
                "error": None if ok else (smoke.get("error") or resolution.reason),
                "catalog_fingerprint": resolution.catalog_fingerprint,
            }
        )
    return {"ok": all_ok, "routes": out_routes, "notes": notes}
