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
        out_routes.append(
            {
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
            }
        )
    return {"ok": all_ok, "routes": out_routes, "notes": notes}
