"""Resolve LLM HTTP endpoints and auth headers for the web UI."""

from __future__ import annotations

import os
from typing import Any

import requests as http_requests

from cli.utils import resolve_backend_url
from tradingagents.llm_clients.api_key_env import get_api_key_env
from tradingagents.llm_clients.openai_client import OPENAI_COMPATIBLE_PROVIDERS


def resolve_llm_base_url(provider: str | None, backend_url: str | None = None) -> str | None:
    """Resolve the OpenAI-compatible base URL for ``provider``."""
    if not provider:
        return backend_url
    return resolve_backend_url(provider.lower(), None, backend_url)


def llm_auth_headers(provider: str | None) -> dict[str, str]:
    """Bearer token headers when the provider's API key env var is set."""
    if not provider:
        return {}
    env_var = get_api_key_env(provider.lower())
    if not env_var:
        return {}
    key = os.environ.get(env_var)
    if not key:
        return {}
    return {"Authorization": f"Bearer {key}"}


def api_key_configured(provider: str | None) -> bool | None:
    """True/False when the provider uses a key env var; None if not applicable."""
    if not provider:
        return None
    env_var = get_api_key_env(provider.lower())
    if not env_var:
        return None
    spec = OPENAI_COMPATIBLE_PROVIDERS.get(provider.lower())
    if spec is not None and spec.key_optional and not os.environ.get(env_var):
        return None  # optional — absence is fine
    return bool(os.environ.get(env_var))


def _filter_chat_models(provider: str | None, models: list[str]) -> list[str]:
    """Drop embedding routes; for OpenRouter, prefer free tool-capable chat models."""
    models = [m for m in models if "embed" not in m.lower()]
    if provider and provider.lower() == "openrouter":
        free = [m for m in models if m.endswith(":free")]
        return free or models
    return models


def _openrouter_tool_filter(
    catalog: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Filter OpenRouter catalog to free tool-capable model IDs."""
    from tradingagents.llm_clients.openrouter_tools import filter_openrouter_tool_models

    return filter_openrouter_tool_models(catalog, free_only=True)


def _connection_hint(provider: str | None, error: str) -> str | None:
    low = error.lower()
    if provider and provider.lower() == "openrouter":
        if "401" in low or "unauthorized" in low:
            return "Set OPENROUTER_API_KEY in .env (create a key at openrouter.ai/keys)."
        return (
            "Check OPENROUTER_API_KEY in .env and network access to openrouter.ai. "
            "Free models use IDs ending in :free (see docs/MODELS.md)."
        )
    if provider and provider.lower() in ("groq", "mistral", "kimi", "deepseek", "xai"):
        env = get_api_key_env(provider.lower()) or "API key env var"
        return f"Set {env} in .env and ensure the provider endpoint is reachable."
    if any(x in low for x in ("connection refused", "failed to establish", "connect")):
        return (
            "LM Studio must be running on the host with a model loaded. "
            "In Docker, use TRADINGAGENTS_LLM_BACKEND_URL=http://host.docker.internal:1234/v1"
        )
    return None


def _openrouter_rate_limits() -> dict[str, str]:
    return {
        "daily_free_requests": "50 requests/day without credits; 1,000/day after $10+ lifetime credits",
        "requests_per_minute": "20 RPM",
        "strategy": "Use Market-only runs first; use local LM Studio for bulk or all-analyst runs.",
    }


def probe_llm_endpoint(
    provider: str | None,
    backend_url: str | None = None,
    *,
    timeout: float = 8,
) -> dict[str, Any]:
    """GET /models on the resolved endpoint; return a health payload for the UI."""
    base_url = resolve_llm_base_url(provider, backend_url)
    key_set = api_key_configured(provider)
    provider_key = (provider or "").lower()

    if not base_url:
        hint = (
            "Set TRADINGAGENTS_LLM_BACKEND_URL (e.g. http://host.docker.internal:1234/v1 "
            "for LM Studio, or https://openrouter.ai/api/v1 for OpenRouter via openai_compatible)."
        )
        if provider and provider.lower() == "openrouter":
            hint = (
                "Set TRADINGAGENTS_LLM_PROVIDER=openrouter and OPENROUTER_API_KEY in .env. "
                "No backend_url is required — the default is https://openrouter.ai/api/v1."
            )
        return {
            "reachable": False,
            "provider": provider,
            "backend_url": None,
            "models": [],
            "api_key_set": key_set,
            "error": "no backend_url configured",
            "hint": hint,
        }

    url = f"{base_url.rstrip('/')}/models"
    headers = llm_auth_headers(provider)
    try:
        resp = http_requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        catalog = data.get("data", [])
        models_meta: dict[str, Any] | None = None
        if provider_key == "openrouter":
            included, excluded = _openrouter_tool_filter(catalog)
            models = included
            models_meta = {
                "tool_capable_only": True,
                "included_count": len(included),
                "tool_capable_count": len(included),
                "excluded_count": len(excluded),
                "excluded_examples": excluded[:8],
                "note": "Agent runs require tool-capable models (supported_parameters includes tools).",
            }
        else:
            models = _filter_chat_models(provider, [m["id"] for m in catalog])
        if not models:
            hint = (
                "No chat models listed yet — Verify / analyze will auto-start LM Studio "
                "via the host agent and load a model when needed."
            )
            if provider_key == "openrouter":
                hint = "OpenRouter responded but returned no models — try again or check API status."
            out: dict[str, Any] = {
                "reachable": True,
                "provider": provider,
                "backend_url": base_url,
                "models": [],
                "api_key_set": key_set,
                "error": "server responded but no chat models are listed",
                "hint": hint,
            }
            if models_meta:
                out["models_meta"] = models_meta
                out["tool_capable_models_count"] = models_meta["tool_capable_count"]
            if provider_key == "openrouter":
                out["rate_limits"] = _openrouter_rate_limits()
            return out
        hint = None
        if key_set is False:
            env_var = get_api_key_env((provider or "").lower()) or "API key"
            hint = f"Endpoint is reachable but {env_var} is not set — analyses will fail until you add it to .env."
        out = {
            "reachable": True,
            "provider": provider,
            "backend_url": base_url,
            "models": models,
            "api_key_set": key_set,
            "error": None,
            "hint": hint,
        }
        if models_meta:
            out["models_meta"] = models_meta
            out["tool_capable_models_count"] = models_meta["tool_capable_count"]
        if provider_key == "openrouter":
            out["rate_limits"] = _openrouter_rate_limits()
        return out
    except Exception as exc:
        err = str(exc)
        out = {
            "reachable": False,
            "provider": provider,
            "backend_url": base_url,
            "models": [],
            "api_key_set": key_set,
            "error": err,
            "hint": _connection_hint(provider, err),
        }
        if provider_key == "openrouter":
            out["rate_limits"] = _openrouter_rate_limits()
            out["tool_capable_models_count"] = 0
        return out
