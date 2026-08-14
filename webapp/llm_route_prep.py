"""Shared LLM route resolution and verify/apply for web API and firm scheduler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from webapp.llm_endpoint import probe_llm_endpoint
from webapp.llm_launch import cleanup_local_models, ensure_local_llm
from webapp.llm_verify import verify_routes
from webapp.model_resolution import resolve_model
from webapp.runs import _format_route_summary

LOCAL_PROVIDERS = {"openai_compatible", "ollama"}


class LlmVerifyError(Exception):
    """Raised when server-side LLM verify fails before queueing a run."""

    def __init__(self, notes: list[str]):
        self.notes = notes
        super().__init__("\n".join(notes) or "LLM verify failed")


@dataclass
class LlmRouteRequest:
    provider: str | None = None
    backend_url: str | None = None
    quick_provider: str | None = None
    quick_backend_url: str | None = None
    deep_provider: str | None = None
    deep_backend_url: str | None = None
    deep_model: str | None = None
    quick_model: str | None = None
    model_preset: str | None = None


def resolve_role_provider(
    role: str,
    provider: str | None,
    role_provider: str | None,
    config: dict,
) -> str | None:
    if role_provider:
        return role_provider
    configured = config.get(f"{role}_provider")
    if configured:
        return configured
    if provider and provider.lower() != "hybrid":
        return provider
    return config.get("llm_provider")


def resolve_role_backend_url(
    role: str,
    *,
    backend_url: str | None,
    provider: str | None,
    role_provider: str | None,
    role_backend_url: str | None,
    config: dict,
) -> str | None:
    if role_backend_url is not None:
        return role_backend_url or None
    if role_provider:
        return None
    configured = config.get(f"{role}_backend_url")
    if configured:
        return configured
    if backend_url is not None:
        return backend_url or None
    if provider and provider.lower() != "hybrid":
        return None
    if not config.get(f"{role}_provider"):
        return config.get("backend_url")
    return None


def request_llm_routes(req: Any, config: dict) -> dict[str, dict[str, str | None]]:
    """Resolve effective quick/deep routes from a web or scheduler request."""
    provider = (getattr(req, "provider", None) or config.get("llm_provider") or "").lower()
    return {
        "quick": {
            "provider": resolve_role_provider(
                "quick", provider, getattr(req, "quick_provider", None), config
            ),
            "backend_url": resolve_role_backend_url(
                "quick",
                backend_url=getattr(req, "backend_url", None),
                provider=provider,
                role_provider=getattr(req, "quick_provider", None),
                role_backend_url=getattr(req, "quick_backend_url", None),
                config=config,
            ),
            "model": getattr(req, "quick_model", None) or config.get("quick_think_llm"),
        },
        "deep": {
            "provider": resolve_role_provider(
                "deep", provider, getattr(req, "deep_provider", None), config
            ),
            "backend_url": resolve_role_backend_url(
                "deep",
                backend_url=getattr(req, "backend_url", None),
                provider=provider,
                role_provider=getattr(req, "deep_provider", None),
                role_backend_url=getattr(req, "deep_backend_url", None),
                config=config,
            ),
            "model": getattr(req, "deep_model", None) or config.get("deep_think_llm"),
        },
    }


def resolution_payload(
    requested: str | None,
    catalog: list[str],
    *,
    provider: str | None,
) -> dict:
    resolution = resolve_model(requested, catalog, provider=provider)
    return {
        "requested": resolution.requested,
        "resolved": resolution.resolved,
        "remapped": resolution.remapped,
        "reason": resolution.reason,
        "catalog_fingerprint": resolution.catalog_fingerprint,
    }


def prepare_verify_routes(
    req: Any,
    config: dict | None = None,
) -> tuple[list[dict], list[str], bool]:
    from tradingagents.default_config import DEFAULT_CONFIG

    config = config or DEFAULT_CONFIG
    routes = request_llm_routes(req, config)
    launch_notes: list[str] = []
    launch_attempted = False
    verify_inputs: list[dict] = []

    for role in ("quick", "deep"):
        route = routes[role]
        provider = route.get("provider")
        backend_url = route.get("backend_url")
        model = route.get("model")

        launch = ensure_local_llm(provider, backend_url, model=model)
        if launch.attempted:
            launch_attempted = True
        if launch.detail:
            launch_notes.append(f"{role}: {launch.detail}")
        if launch.error and not launch.reached:
            launch_notes.append(f"{role}: launch failed: {launch.error}")
        # Host agent may relocate LM Studio off a blocked port (e.g. 1234 → 1235).
        if launch.backend_url:
            backend_url = launch.backend_url
            routes[role]["backend_url"] = backend_url

        probe = probe_llm_endpoint(provider, backend_url, timeout=5)
        catalog = list(probe.get("models") or [])
        if probe.get("backend_url"):
            backend_url = probe.get("backend_url")
            routes[role]["backend_url"] = backend_url

        verify_inputs.append(
            {
                "role": role,
                "provider": provider,
                "backend_url": backend_url,
                "requested_model": model,
                "catalog": catalog,
            }
        )

    return verify_inputs, launch_notes, launch_attempted


def verify_and_apply_models(
    req: Any,
    config: dict,
) -> tuple[dict[str, dict[str, str | None]], dict[str, dict], list[str]]:
    """Server-side LLM verify; apply resolved models to routes."""
    routes = request_llm_routes(req, config)
    verify_inputs, launch_notes, _ = prepare_verify_routes(req, config)
    result = verify_routes(verify_inputs)
    notes = launch_notes + list(result.get("notes") or [])
    if not result.get("ok"):
        raise LlmVerifyError(notes)

    model_resolution: dict[str, dict] = {}
    for route_input, route_result in zip(verify_inputs, result.get("routes") or []):
        role = route_result.get("role") or route_input.get("role")
        if not role:
            continue
        resolved = route_result.get("resolved")
        model_resolution[role] = {
            "requested": route_result.get("requested"),
            "resolved": resolved,
            "remapped": route_result.get("remapped"),
            "reason": route_result.get("reason"),
            "smoke_ok": route_result.get("smoke_ok"),
            "catalog_fingerprint": route_result.get("catalog_fingerprint"),
        }
        if role not in routes:
            continue
        if route_input.get("backend_url"):
            routes[role]["backend_url"] = route_input["backend_url"]
        if resolved:
            routes[role]["model"] = resolved
        provider = (route_result.get("provider") or route_input.get("provider") or "").lower()
        if resolved and provider in LOCAL_PROVIDERS:
            cleanup_note = cleanup_local_models(keep_model=resolved)
            if cleanup_note:
                notes.append(f"{role}: {cleanup_note}")
    return routes, model_resolution, notes


def _is_openrouter_free_model(model_id: str | None) -> bool:
    if not model_id:
        return False
    return model_id == "openrouter/free" or model_id.endswith(":free")


def build_default_run_params(config: dict | None = None) -> dict[str, Any]:
    """Verify/resolve DEFAULT_CONFIG models for scheduler-driven queue runs."""
    from tradingagents.default_config import DEFAULT_CONFIG

    config = config or DEFAULT_CONFIG
    req = LlmRouteRequest(
        provider=config.get("llm_provider"),
        backend_url=config.get("backend_url"),
        quick_provider=config.get("quick_provider"),
        quick_backend_url=config.get("quick_backend_url"),
        deep_provider=config.get("deep_provider"),
        deep_backend_url=config.get("deep_backend_url"),
        quick_model=config.get("quick_think_llm"),
        deep_model=config.get("deep_think_llm"),
    )
    routes, model_resolution, _notes = verify_and_apply_models(req, config)
    quick_model = routes["quick"].get("model")
    deep_model = routes["deep"].get("model")
    quick_openrouter_free = (
        (routes["quick"].get("provider") or "").lower() == "openrouter"
        and _is_openrouter_free_model(quick_model)
    )
    return {
        "provider": config.get("llm_provider"),
        "backend_url": config.get("backend_url"),
        "quick_provider": routes["quick"].get("provider"),
        "quick_backend_url": routes["quick"].get("backend_url"),
        "deep_provider": routes["deep"].get("provider"),
        "deep_backend_url": routes["deep"].get("backend_url"),
        "deep_model": deep_model,
        "quick_model": quick_model,
        "model_resolution": model_resolution,
        "llm_routes": routes,
        "route_summary": _format_route_summary(routes),
        "openrouter_free_quick": quick_openrouter_free,
    }
