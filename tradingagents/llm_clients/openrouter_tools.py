"""OpenRouter model tool-calling support for TradingAgents.

TradingAgents requires function/tool calling. Many OpenRouter models — especially
free *reasoning* variants — do not advertise ``tools`` in ``supported_parameters``
and return 404 ("No endpoints found that support tool use") at runtime.
"""

from __future__ import annotations

import re
from typing import Any

import requests as http_requests

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Verified free models that support tools even if the catalog lags.
KNOWN_TOOL_CAPABLE_FREE: frozenset[str] = frozenset(
    {
        "meta-llama/llama-3.3-70b-instruct:free",
        "google/gemma-4-26b-a4b-it:free",
        "qwen/qwen3-coder:free",
        "openai/gpt-oss-20b:free",
        "openrouter/free",
    }
)

# Models that are known to fail tool use — used for docs and UI heuristics.
KNOWN_NO_TOOLS: frozenset[str] = frozenset(
    {
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
        "nvidia/nemotron-3-nano-30b-a3b-reasoning",
    }
)

_REASONING_ID_RE = re.compile(r"reasoning", re.IGNORECASE)


def entry_supports_tools(entry: dict[str, Any]) -> bool:
    """True when an OpenRouter /models entry supports tool calling."""
    model_id = entry.get("id") or ""
    if model_id in KNOWN_NO_TOOLS:
        return False
    if model_id in KNOWN_TOOL_CAPABLE_FREE:
        return True
    params = entry.get("supported_parameters") or []
    return "tools" in params


def model_id_supports_tools(model_id: str, catalog: list[dict[str, Any]]) -> bool:
    """Resolve tool support for a model ID against a fetched catalog."""
    if not model_id:
        return False
    if model_id in KNOWN_NO_TOOLS:
        return False
    if model_id in KNOWN_TOOL_CAPABLE_FREE:
        return True
    for entry in catalog:
        if entry.get("id") == model_id:
            return entry_supports_tools(entry)
    # Unknown ID not in catalog — allow only if not a known-bad pattern.
    return not looks_like_reasoning_without_tools(model_id)


def looks_like_reasoning_without_tools(model_id: str) -> bool:
    """Heuristic: reasoning-suffixed models often lack tool endpoints on OpenRouter."""
    if model_id in KNOWN_TOOL_CAPABLE_FREE:
        return False
    if model_id in KNOWN_NO_TOOLS:
        return True
    return bool(_REASONING_ID_RE.search(model_id))


def fetch_openrouter_catalog(
    *,
    api_key: str | None = None,
    timeout: float = 10,
) -> list[dict[str, Any]]:
    """GET OpenRouter /models; returns the ``data`` array."""
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    resp = http_requests.get(OPENROUTER_MODELS_URL, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.json().get("data", [])


def filter_openrouter_tool_models(
    catalog: list[dict[str, Any]],
    *,
    free_only: bool = True,
) -> tuple[list[str], list[str]]:
    """Return (included_ids, excluded_ids) for chat models with tool support."""
    included: list[str] = []
    excluded: list[str] = []
    for entry in catalog:
        model_id = entry.get("id") or ""
        if not model_id or "embed" in model_id.lower():
            continue
        if free_only and not model_id.endswith(":free"):
            continue
        if entry_supports_tools(entry):
            included.append(model_id)
        else:
            excluded.append(model_id)
    return included, excluded


def validate_openrouter_model_for_agents(
    model_id: str,
    *,
    catalog: list[dict[str, Any]] | None = None,
    api_key: str | None = None,
    timeout: float = 8,
) -> str | None:
    """Return a user-facing error string if ``model_id`` cannot run agent tools."""
    if not model_id:
        return "OpenRouter model ID is required for analysis."
    if model_id in KNOWN_NO_TOOLS:
        return (
            f"Model '{model_id}' does not support tool/function calling on OpenRouter. "
            "TradingAgents requires tools (get_stock_data, etc.). "
            "Use meta-llama/llama-3.3-70b-instruct:free or see docs/MODELS.md."
        )
    if model_id in KNOWN_TOOL_CAPABLE_FREE:
        return None
    if catalog is None:
        try:
            catalog = fetch_openrouter_catalog(api_key=api_key, timeout=timeout)
        except Exception as exc:
            if looks_like_reasoning_without_tools(model_id):
                return (
                    f"Model '{model_id}' appears to be a reasoning-only model without "
                    "tool support. TradingAgents requires tool-capable models — try "
                    "meta-llama/llama-3.3-70b-instruct:free."
                )
            # Catalog unreachable — don't block known-good defaults; warn on reasoning pattern only.
            return None
    if model_id_supports_tools(model_id, catalog):
        return None
    return (
        f"Model '{model_id}' does not support tool/function calling on OpenRouter "
        "(supported_parameters lacks 'tools'). TradingAgents requires tools. "
        "Recommended: meta-llama/llama-3.3-70b-instruct:free — see docs/MODELS.md."
    )
