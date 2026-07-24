"""FastAPI backend for the TradingAgents web frontend.

Run with:  uvicorn webapp.server:app --host 0.0.0.0 --port 8000
"""

try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
    load_dotenv(find_dotenv(".env.enterprise", usecwd=True), override=False)
except ImportError:
    pass

import logging
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from webapp.llm_endpoint import probe_llm_endpoint
from webapp.llm_launch import ensure_local_llm
from webapp.llm_verify import verify_routes
from webapp.market_dates import latest_sensible_date, validate_market_date
from webapp.model_presets import MODEL_PRESETS
from webapp.model_resolution import resolve_model, route_signature
from webapp.runs import ANALYST_AGENTS, RunManager, _format_route_summary

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_HOME = Path(os.path.expanduser("~")) / ".tradingagents"
_STATIC = Path(__file__).parent / "static"
_DOCS = Path(__file__).resolve().parent.parent / "docs"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        from firm.ops.scheduler import start_scheduler

        start_scheduler()
    except Exception:
        logger.exception("firm scheduler failed to start")
    yield
    try:
        from firm.ops.scheduler import stop_scheduler

        stop_scheduler()
    except Exception:
        pass


app = FastAPI(title="TradingAgents Web", version="0.2.0", lifespan=lifespan)
manager = RunManager(state_dir=_HOME / "webapp")

from firm.ops.screen_queue import bind_run_manager  # noqa: E402

bind_run_manager(manager)

_TICKER_RE = re.compile(r"^[A-Za-z0-9.\-^=]{1,15}$")

_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")


class AnalyzeRequest(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=15)
    trade_date: str
    analysts: list[str] = ["market", "social", "news", "fundamentals"]
    provider: str | None = None
    backend_url: str | None = None
    quick_provider: str | None = None
    quick_backend_url: str | None = None
    deep_provider: str | None = None
    deep_backend_url: str | None = None
    deep_model: str | None = None
    quick_model: str | None = None
    model_preset: str | None = None
    max_debate_rounds: int | None = Field(None, ge=1, le=5)
    max_risk_rounds: int | None = Field(None, ge=1, le=5)
    max_recur_limit: int | None = Field(None, ge=50, le=1000)
    max_context_tokens: int | None = Field(None, ge=4096, le=262144)
    openrouter_free_override: bool = False
    market_date_override: bool = False


class ResumeRunRequest(BaseModel):
    provider: str | None = None
    backend_url: str | None = None
    quick_provider: str | None = None
    quick_backend_url: str | None = None
    deep_provider: str | None = None
    deep_backend_url: str | None = None
    deep_model: str | None = None
    quick_model: str | None = None
    max_context_tokens: int | None = Field(None, ge=4096, le=262144)
    local_only: bool = False


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


class QueueScreenRequest(BaseModel):
    top_n: int | None = Field(None, ge=1, le=100)
    analysts: list[str] | None = None
    provider: str | None = None
    backend_url: str | None = None
    quick_provider: str | None = None
    quick_backend_url: str | None = None
    deep_provider: str | None = None
    deep_backend_url: str | None = None
    deep_model: str | None = None
    quick_model: str | None = None
    model_preset: str | None = None
    max_debate_rounds: int | None = Field(None, ge=1, le=5)
    max_risk_rounds: int | None = Field(None, ge=1, le=5)
    max_recur_limit: int | None = Field(None, ge=50, le=1000)
    max_context_tokens: int | None = Field(None, ge=4096, le=262144)
    openrouter_free_override: bool = False


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def get_config():
    """Current effective defaults so the UI can pre-fill the form."""
    from tradingagents.default_config import DEFAULT_CONFIG

    return {
        "llm_provider": DEFAULT_CONFIG["llm_provider"],
        "backend_url": DEFAULT_CONFIG.get("backend_url"),
        "quick_provider": DEFAULT_CONFIG.get("quick_provider"),
        "quick_backend_url": DEFAULT_CONFIG.get("quick_backend_url"),
        "deep_provider": DEFAULT_CONFIG.get("deep_provider"),
        "deep_backend_url": DEFAULT_CONFIG.get("deep_backend_url"),
        "deep_think_llm": DEFAULT_CONFIG["deep_think_llm"],
        "quick_think_llm": DEFAULT_CONFIG["quick_think_llm"],
        "temperature": DEFAULT_CONFIG.get("temperature"),
        "max_debate_rounds": DEFAULT_CONFIG["max_debate_rounds"],
        "max_risk_rounds": DEFAULT_CONFIG["max_risk_discuss_rounds"],
        "max_context_tokens": DEFAULT_CONFIG.get("max_context_tokens", 8192),
        "analysts": [{"key": k, "label": v} for k, v in ANALYST_AGENTS.items()],
        "model_presets": MODEL_PRESETS,
        "models_doc": "/docs/MODELS.md",
        "today": datetime.now().strftime("%Y-%m-%d"),
        "latest_sensible_date": latest_sensible_date(),
    }


@app.get("/api/market-date/validate")
def market_date_validate(ticker: str, date: str):
    symbol = ticker.strip().upper()
    if not _TICKER_RE.match(symbol):
        raise HTTPException(400, "invalid ticker symbol")
    return validate_market_date(symbol, date)


@app.get("/api/models")
def list_models(
    backend_url: str | None = None,
    provider: str | None = None,
    quick_provider: str | None = None,
    quick_backend_url: str | None = None,
    deep_provider: str | None = None,
    deep_backend_url: str | None = None,
    quick_model: str | None = None,
    deep_model: str | None = None,
):
    """Proxy the OpenAI-compatible /models endpoint (LM Studio, OpenRouter, ...)."""
    from tradingagents.default_config import DEFAULT_CONFIG

    if _is_hybrid_probe(provider, quick_provider, deep_provider, DEFAULT_CONFIG):
        payload = _hybrid_probe_payload(
            probe_llm_endpoint,
            DEFAULT_CONFIG,
            provider=provider,
            backend_url=backend_url,
            quick_provider=quick_provider,
            quick_backend_url=quick_backend_url,
            deep_provider=deep_provider,
            deep_backend_url=deep_backend_url,
            timeout=5,
        )
    else:
        resolved_provider = provider or DEFAULT_CONFIG.get("llm_provider")
        url = _resolve_health_backend_url(backend_url, provider, DEFAULT_CONFIG)
        result = probe_llm_endpoint(resolved_provider, url, timeout=5)
        if not result["reachable"]:
            return {
                "models": [],
                "backend_url": result.get("backend_url"),
                "error": result.get("error") or result.get("hint"),
            }
        payload = {
            "models": result.get("models") or [],
            "backend_url": result.get("backend_url"),
            "error": result.get("error"),
        }
        if result.get("models_meta"):
            payload["models_meta"] = result["models_meta"]

    _enrich_model_resolutions(
        payload,
        quick_model=quick_model,
        deep_model=deep_model,
        provider=provider,
        quick_provider=quick_provider,
        deep_provider=deep_provider,
        config=DEFAULT_CONFIG,
    )
    return payload


@app.get("/api/llm-health")
def llm_health(
    backend_url: str | None = None,
    provider: str | None = None,
    quick_provider: str | None = None,
    quick_backend_url: str | None = None,
    deep_provider: str | None = None,
    deep_backend_url: str | None = None,
):
    """Probe whether the configured LLM endpoint is reachable from this process."""
    from tradingagents.default_config import DEFAULT_CONFIG
    from webapp.llm_endpoint import probe_llm_endpoint

    if _is_hybrid_probe(provider, quick_provider, deep_provider, DEFAULT_CONFIG):
        return _hybrid_probe_payload(
            probe_llm_endpoint,
            DEFAULT_CONFIG,
            provider=provider,
            backend_url=backend_url,
            quick_provider=quick_provider,
            quick_backend_url=quick_backend_url,
            deep_provider=deep_provider,
            deep_backend_url=deep_backend_url,
            timeout=5,
        )

    resolved_provider = provider or DEFAULT_CONFIG.get("llm_provider")
    url = _resolve_health_backend_url(backend_url, provider, DEFAULT_CONFIG)
    return probe_llm_endpoint(resolved_provider, url, timeout=5)


def _resolve_health_backend_url(
    backend_url: str | None,
    provider: str | None,
    config: dict,
) -> str | None:
    """Pick backend URL for health/model probes.

    When the caller explicitly passes ``provider`` (e.g. UI preset for
    OpenRouter), do not inherit a local ``backend_url`` from config — cloud
    providers resolve their own default endpoint.
    """
    if backend_url is not None:
        return backend_url or None
    if provider:
        return None
    return config.get("backend_url")


def _is_hybrid_probe(
    provider: str | None,
    quick_provider: str | None,
    deep_provider: str | None,
    config: dict,
) -> bool:
    return (
        (provider or "").lower() == "hybrid"
        or bool(quick_provider or deep_provider)
        or (config.get("llm_provider") == "hybrid")
    )


def _resolve_role_provider(
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


def _resolve_role_backend_url(
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


def _hybrid_probe_payload(
    probe,
    config: dict,
    *,
    provider: str | None,
    backend_url: str | None,
    quick_provider: str | None,
    quick_backend_url: str | None,
    deep_provider: str | None,
    deep_backend_url: str | None,
    timeout: float,
) -> dict:
    quick = probe(
        _resolve_role_provider("quick", provider, quick_provider, config),
        _resolve_role_backend_url(
            "quick",
            backend_url=backend_url,
            provider=provider,
            role_provider=quick_provider,
            role_backend_url=quick_backend_url,
            config=config,
        ),
        timeout=timeout,
    )
    deep = probe(
        _resolve_role_provider("deep", provider, deep_provider, config),
        _resolve_role_backend_url(
            "deep",
            backend_url=backend_url,
            provider=provider,
            role_provider=deep_provider,
            role_backend_url=deep_backend_url,
            config=config,
        ),
        timeout=timeout,
    )
    return {
        "mode": "hybrid",
        "reachable": bool(quick.get("reachable") and deep.get("reachable")),
        "quick": quick,
        "deep": deep,
        "error": None if quick.get("reachable") and deep.get("reachable") else "one or more LLM routes are unreachable",
    }


def _is_openrouter_free_model(model_id: str | None) -> bool:
    """OpenRouter free routes are quota-constrained even when tool-capable."""
    if not model_id:
        return False
    return model_id == "openrouter/free" or model_id.endswith(":free")


def _openrouter_free_warning(analysts: list[str], model_ids: tuple[str | None, str | None]) -> str | None:
    if not any(_is_openrouter_free_model(model_id) for model_id in model_ids):
        return None
    if "fundamentals" in analysts:
        return (
            "OpenRouter free warning: Fundamentals is tool-heavy and can loop or hit "
            "the free-tier rate/quota limits. Use Hybrid/local routing for Fundamentals, "
            "uncheck Fundamentals, or send openrouter_free_override=true to run anyway."
        )
    if len(analysts) <= 2:
        return None
    return (
        "OpenRouter free models are tightly quota-limited (about 50 requests/day "
        "without credits and 20 RPM). A 3+ analyst TradingAgents run can burn "
        "through that quota or fail with 429 rate limits. Start with Market analyst only, "
        "keep debate/risk rounds at 1, or use local LM Studio for all-analyst runs. "
        "Send openrouter_free_override=true to run anyway."
    )


def _resolution_payload(
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


def _enrich_model_resolutions(
    payload: dict,
    *,
    quick_model: str | None,
    deep_model: str | None,
    provider: str | None,
    quick_provider: str | None,
    deep_provider: str | None,
    config: dict,
) -> None:
    if not quick_model and not deep_model:
        return
    resolved: dict[str, dict] = {}
    if payload.get("mode") == "hybrid":
        if quick_model:
            quick_catalog = list((payload.get("quick") or {}).get("models") or [])
            if quick_catalog:
                role_provider = _resolve_role_provider(
                    "quick", provider, quick_provider, config
                )
                resolved["quick"] = _resolution_payload(
                    quick_model, quick_catalog, provider=role_provider
                )
        if deep_model:
            deep_catalog = list((payload.get("deep") or {}).get("models") or [])
            if deep_catalog:
                role_provider = _resolve_role_provider(
                    "deep", provider, deep_provider, config
                )
                resolved["deep"] = _resolution_payload(
                    deep_model, deep_catalog, provider=role_provider
                )
    else:
        catalog = list(payload.get("models") or [])
        if catalog:
            role_provider = provider or config.get("llm_provider")
            if quick_model:
                resolved["quick"] = _resolution_payload(
                    quick_model, catalog, provider=role_provider
                )
            if deep_model:
                resolved["deep"] = _resolution_payload(
                    deep_model, catalog, provider=role_provider
                )
    if resolved:
        payload["resolved"] = resolved


def _prepare_verify_routes(
    req: LlmVerifyRequest | AnalyzeRequest | QueueScreenRequest,
) -> tuple[list[dict], list[str], bool]:
    from tradingagents.default_config import DEFAULT_CONFIG

    routes = _request_llm_routes(req, DEFAULT_CONFIG)
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

        probe = probe_llm_endpoint(provider, backend_url, timeout=5)
        catalog = list(probe.get("models") or [])
        if probe.get("backend_url"):
            backend_url = probe.get("backend_url")

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


def _request_llm_routes(
    req: LlmVerifyRequest | AnalyzeRequest | QueueScreenRequest,
    config: dict,
) -> dict[str, dict[str, str | None]]:
    """Resolve effective quick/deep routes from a web request."""
    provider = (req.provider or config.get("llm_provider") or "").lower()
    return {
        "quick": {
            "provider": _resolve_role_provider("quick", provider, req.quick_provider, config),
            "backend_url": _resolve_role_backend_url(
                "quick",
                backend_url=req.backend_url,
                provider=provider,
                role_provider=req.quick_provider,
                role_backend_url=req.quick_backend_url,
                config=config,
            ),
            "model": req.quick_model or config.get("quick_think_llm"),
        },
        "deep": {
            "provider": _resolve_role_provider("deep", provider, req.deep_provider, config),
            "backend_url": _resolve_role_backend_url(
                "deep",
                backend_url=req.backend_url,
                provider=provider,
                role_provider=req.deep_provider,
                role_backend_url=req.deep_backend_url,
                config=config,
            ),
            "model": req.deep_model or config.get("deep_think_llm"),
        },
    }


def _validate_openai_compatible_backend_urls(
    routes: dict[str, dict[str, str | None]],
) -> None:
    """Fail before enqueueing if any local OpenAI-compatible route has no URL."""
    missing = [
        role
        for role, route in routes.items()
        if (route.get("provider") or "").lower() == "openai_compatible"
        and not route.get("backend_url")
    ]
    if not missing:
        return
    roles = ", ".join(missing)
    raise HTTPException(
        400,
        (
            f"openai_compatible route(s) missing backend_url: {roles}. "
            "For LM Studio in Docker, use http://host.docker.internal:1234/v1 "
            "or choose an OpenRouter preset/provider for cloud models."
        ),
    )


def _openrouter_free_guardrail(
    analysts: list[str],
    routes: dict[str, dict[str, str | None]],
) -> tuple[str | None, bool]:
    """Return (warning, requires_override) for OpenRouter free-model routes."""
    quick_free = (
        (routes["quick"].get("provider") or "").lower() == "openrouter"
        and _is_openrouter_free_model(routes["quick"].get("model"))
    )
    deep_free = (
        (routes["deep"].get("provider") or "").lower() == "openrouter"
        and _is_openrouter_free_model(routes["deep"].get("model"))
    )
    if quick_free:
        warning = _openrouter_free_warning(
            analysts,
            (routes["deep"].get("model"), routes["quick"].get("model")),
        )
        if warning:
            return warning, True
    if deep_free:
        return (
            "Hybrid OpenRouter free warning: final synthesis uses the deep OpenRouter "
            "model and consumes free quota, but high-call analyst/tool-loop work is "
            "routed to the quick local model.",
            False,
        )
    return None, False


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


@app.post("/api/analyze")
def start_analysis(req: AnalyzeRequest):
    ticker = req.ticker.strip().upper()
    if not _TICKER_RE.match(ticker):
        raise HTTPException(400, "invalid ticker symbol")
    try:
        datetime.strptime(req.trade_date, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(400, "trade_date must be YYYY-MM-DD") from exc
    invalid = [a for a in req.analysts if a not in ANALYST_AGENTS]
    if invalid:
        raise HTTPException(400, f"unknown analysts: {invalid}")
    if not req.analysts:
        raise HTTPException(400, "select at least one analyst")

    market_date_warning: str | None = None
    market_date_validation: dict | None = None
    if req.market_date_override:
        market_date_warning = (
            "Market-date override: run started without yfinance daily-bar validation. "
            "Results may be stale or incomplete."
        )
    else:
        market_date_validation = validate_market_date(ticker, req.trade_date)
        if not market_date_validation["valid"]:
            raise HTTPException(400, market_date_validation["message"])

    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.llm_clients.api_key_env import get_api_key_env
    from tradingagents.llm_clients.openrouter_tools import validate_openrouter_model_for_agents

    routes = _request_llm_routes(req, DEFAULT_CONFIG)
    _validate_openai_compatible_backend_urls(routes)
    warnings: list[str] = []
    quick_openrouter_free = (
        (routes["quick"].get("provider") or "").lower() == "openrouter"
        and _is_openrouter_free_model(routes["quick"].get("model"))
    )
    for route in routes.values():
        if (route.get("provider") or "").lower() != "openrouter":
            continue
        import os

        api_key = os.environ.get(get_api_key_env("openrouter") or "OPENROUTER_API_KEY")
        err = validate_openrouter_model_for_agents(route.get("model"), api_key=api_key)
        if err:
            raise HTTPException(400, err)
    warning, requires_override = _openrouter_free_guardrail(req.analysts, routes)
    if warning:
        warnings.append(warning)
    if warning and requires_override and not req.openrouter_free_override:
        raise HTTPException(400, warning)
    if market_date_warning:
        warnings.insert(0, market_date_warning)

    asset_type = "crypto" if ticker.endswith(_CRYPTO_SUFFIXES) else "stock"
    analysts = req.analysts
    # The crypto pipeline has no fundamentals stage.
    if asset_type == "crypto":
        analysts = [a for a in analysts if a != "fundamentals"] or ["market"]

    record = manager.start_run(
        {
            "ticker": ticker,
            "trade_date": req.trade_date,
            "asset_type": asset_type,
            "analysts": analysts,
            "provider": req.provider,
            "backend_url": req.backend_url,
            "quick_provider": req.quick_provider,
            "quick_backend_url": req.quick_backend_url,
            "deep_provider": req.deep_provider,
            "deep_backend_url": req.deep_backend_url,
            "deep_model": req.deep_model,
            "quick_model": req.quick_model,
            "model_preset": req.model_preset,
            "llm_routes": routes,
            "route_summary": _format_route_summary(routes),
            "max_debate_rounds": req.max_debate_rounds,
            "max_risk_rounds": req.max_risk_rounds,
            "max_recur_limit": req.max_recur_limit,
            "max_context_tokens": req.max_context_tokens,
            "openrouter_free_quick": quick_openrouter_free,
            "openrouter_free_warning": warning,
            "market_date_warning": market_date_warning,
            "market_date_validation": market_date_validation,
        }
    )
    response = {"run_id": record["id"], "status": record["status"]}
    if warnings:
        response["warning"] = "\n".join(warnings)
    return response


@app.get("/api/runs")
def list_runs():
    return {"runs": manager.list_runs()}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    run = manager.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    return run


@app.get("/api/runs/{run_id}/comparison")
def get_run_comparison(run_id: str):
    comparison = manager.get_run_comparison(run_id)
    if comparison is None:
        raise HTTPException(404, "run not found")
    return comparison


@app.post("/api/runs/{run_id}/resume")
def resume_run(run_id: str, req: ResumeRunRequest):
    overrides = (
        req.model_dump(exclude_none=True)
        if hasattr(req, "model_dump")
        else req.dict(exclude_none=True)
    )
    try:
        record = manager.resume_run(run_id, overrides)
    except KeyError as exc:
        raise HTTPException(404, "run not found") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"run_id": record["id"], "status": record["status"]}


@app.get("/api/memory")
def get_memory():
    """The persistent cross-run decision log, if any."""
    from tradingagents.default_config import DEFAULT_CONFIG

    path = Path(DEFAULT_CONFIG["memory_log_path"])
    if not path.exists():
        return {"content": "", "path": str(path)}
    return {"content": path.read_text(encoding="utf-8"), "path": str(path)}


# ---------- Firm API ----------


@app.get("/api/firm/config")
def firm_config():
    from firm.config import FIRM_CONFIG

    return {
        "trading_mode": FIRM_CONFIG.trading_mode,
        "can_execute": FIRM_CONFIG.can_auto_execute(),
        "max_positions": FIRM_CONFIG.max_positions,
        "quant_min_score": FIRM_CONFIG.quant_min_score,
        "fusion_entry_threshold": FIRM_CONFIG.fusion_entry_threshold,
        "scheduler_enabled": FIRM_CONFIG.scheduler_enabled,
        "premarket_screen_top_n": FIRM_CONFIG.premarket_screen_top_n,
        "universe_mode": FIRM_CONFIG.universe_mode,
        "market_screener_max": FIRM_CONFIG.market_screener_max,
        "screener": {
            "mode": FIRM_CONFIG.screener_mode,
            "quant_min_score": FIRM_CONFIG.quant_min_score,
            "adx_min": FIRM_CONFIG.screener_adx_min,
            "rsi_low": FIRM_CONFIG.screener_rsi_low,
            "rsi_high": FIRM_CONFIG.screener_rsi_high,
            "volume_ratio_min": FIRM_CONFIG.screener_volume_ratio_min,
        },
    }


class FirmSettingsPatch(BaseModel):
    reset: bool = False
    settings: dict[str, float | int | str | bool] = Field(default_factory=dict)
    watchlist_extra: list[str] | str | None = None


@app.get("/api/firm/settings")
def firm_settings_get():
    from firm.config import FIRM_CONFIG
    from firm.user_settings import build_settings_view

    return build_settings_view(FIRM_CONFIG)


@app.patch("/api/firm/settings")
def firm_settings_patch(body: FirmSettingsPatch):
    from firm.config import FIRM_CONFIG, reload_firm_config
    from firm.user_settings import (
        TUNABLE_KEYS,
        build_settings_view,
        load_user_settings,
        reset_user_settings,
        save_user_settings,
        validate_patch,
    )

    if body.reset:
        reset_user_settings(FIRM_CONFIG.data_dir)
        reload_firm_config()
        return build_settings_view(FIRM_CONFIG)

    merged = load_user_settings(FIRM_CONFIG.data_dir)
    if body.settings:
        try:
            current_effective = {k: getattr(FIRM_CONFIG, k) for k in TUNABLE_KEYS}
            validated = validate_patch(body.settings, current_effective)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        merged = {**merged, **validated}

    if body.watchlist_extra is not None:
        from firm.universe.watchlist import parse_ticker_symbols

        try:
            merged["watchlist_extra"] = parse_ticker_symbols(body.watchlist_extra)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    if body.settings or body.watchlist_extra is not None:
        save_user_settings(FIRM_CONFIG.data_dir, merged)
        reload_firm_config()
    return build_settings_view(FIRM_CONFIG)


@app.get("/api/firm/watchlist")
def firm_watchlist():
    from firm.config import FIRM_CONFIG
    from firm.universe.watchlist import watchlist_metadata

    return watchlist_metadata(FIRM_CONFIG.data_dir)


@app.get("/api/firm/regime")
def firm_regime():
    from firm.universe.regime import detect_regime

    r = detect_regime()
    return {
        "label": r.label,
        "multiplier": r.multiplier,
        "spy_close": r.spy_close,
        "sma20": r.sma20,
        "detail": r.detail,
    }


@app.get("/api/firm/screen")
def firm_screen(top_n: int = 10, pass_only: bool = False):
    from firm.config import FIRM_CONFIG
    from firm.universe.market_screener import resolve_screening_universe
    from firm.universe.screener import screen_universe
    from firm.universe.watchlist import watchlist_metadata

    meta = watchlist_metadata(FIRM_CONFIG.data_dir)
    symbols, universe_meta = resolve_screening_universe(FIRM_CONFIG.data_dir)
    all_results = screen_universe(symbols, pass_only=False)
    passing_count = sum(1 for r in all_results if r.passed)
    display = [r for r in all_results if r.passed] if pass_only else all_results
    if top_n:
        display = display[:top_n]

    def _row(r):
        return {
            "ticker": r.ticker,
            "score": r.score,
            "passed": r.passed,
            "filters": r.filters,
            "metrics": r.metrics,
            "blockers": r.blockers,
            "advisory": r.advisory,
        }

    return {
        "total_screened": len(symbols),
        "passing_count": passing_count,
        "pass_only": pass_only,
        "screener_mode": FIRM_CONFIG.screener_mode,
        "universe": universe_meta,
        "watchlist": {
            "count": meta["count"],
            "static_seed_count": meta["static_seed_count"],
            "user_extra_count": meta["sources"]["user_extra"]["count"],
            "expansion": meta["expansion"],
        },
        "candidates": [_row(r) for r in display],
    }


@app.post("/api/firm/queue-screen")
def firm_queue_screen(
    req: QueueScreenRequest | None = Body(default=None),
    top_n: int | None = None,
):
    """Screen watchlist and queue top finalists for TradingAgents analysis."""
    from firm.ops.screen_queue import DEFAULT_ANALYSTS, queue_screener_finalists
    from tradingagents.default_config import DEFAULT_CONFIG

    body = req or QueueScreenRequest()
    analysts = body.analysts
    if analysts is not None and not analysts:
        raise HTTPException(400, "select at least one analyst")
    invalid = [a for a in (analysts or []) if a not in ANALYST_AGENTS]
    if invalid:
        raise HTTPException(400, f"unknown analysts: {invalid}")

    routes = _request_llm_routes(body, DEFAULT_CONFIG)
    _validate_openai_compatible_backend_urls(routes)
    warning, requires_override = _openrouter_free_guardrail(
        analysts or DEFAULT_ANALYSTS,
        routes,
    )
    if warning and requires_override and not body.openrouter_free_override:
        raise HTTPException(400, warning)
    quick_openrouter_free = (
        (routes["quick"].get("provider") or "").lower() == "openrouter"
        and _is_openrouter_free_model(routes["quick"].get("model"))
    )
    run_params = {
        "provider": body.provider,
        "backend_url": body.backend_url,
        "quick_provider": body.quick_provider,
        "quick_backend_url": body.quick_backend_url,
        "deep_provider": body.deep_provider,
        "deep_backend_url": body.deep_backend_url,
        "deep_model": body.deep_model,
        "quick_model": body.quick_model,
        "llm_routes": routes,
        "route_summary": _format_route_summary(routes),
        "model_preset": body.model_preset,
        "max_debate_rounds": body.max_debate_rounds,
        "max_risk_rounds": body.max_risk_rounds,
        "max_recur_limit": body.max_recur_limit,
        "max_context_tokens": body.max_context_tokens,
        "openrouter_free_quick": quick_openrouter_free,
        "openrouter_free_warning": warning,
    }

    try:
        result = queue_screener_finalists(
            manager,
            top_n=body.top_n if body.top_n is not None else top_n,
            analysts=analysts,
            notify_discord=True,
            source="manual_screen",
            run_params=run_params,
        )
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc
    if warning:
        result["warning"] = warning
    return result


@app.get("/api/firm/positions")
def firm_positions():
    from firm.portfolio.sync import sync_positions

    return {"positions": sync_positions()}


@app.get("/api/firm/signals")
def firm_signals(limit: int = 50):
    from firm.storage.db import FirmDB

    return {"signals": FirmDB().list_fused_signals(limit=limit)}


@app.get("/api/firm/killswitch")
def firm_killswitch():
    from firm.ops.killswitch import KillSwitch

    return KillSwitch().status()


@app.post("/api/firm/execute/{run_id}")
def firm_execute(run_id: str):
    run = manager.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    if run.get("status") != "completed":
        raise HTTPException(400, "run must be completed before execution")
    from firm.service import execute_fused_run

    return execute_fused_run(run)


@app.post("/api/firm/sync")
def firm_sync():
    from firm.portfolio.sync import sync_positions

    return {"positions": sync_positions()}


class FirmBacktestRequest(BaseModel):
    start_date: str
    end_date: str
    tickers: list[str] | None = None
    mode: str = "compare"
    llm_proxy: str = "momentum"
    frequency: str = "weekly"
    screener_mode: str | None = None


@app.post("/api/firm/backtest")
def firm_backtest(body: FirmBacktestRequest):
    from firm.backtest.hybrid_backtest import run_hybrid_backtest

    try:
        return run_hybrid_backtest(
            body.start_date,
            body.end_date,
            tickers=body.tickers,
            mode=body.mode,  # type: ignore[arg-type]
            llm_proxy=body.llm_proxy,  # type: ignore[arg-type]
            frequency=body.frequency,
            screener_mode=body.screener_mode,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        logger.exception("backtest failed")
        raise HTTPException(500, f"backtest failed: {exc}") from exc


# Static frontend (mounted last so /api keeps priority)
@app.get("/docs/{doc_name}")
def serve_doc(doc_name: str):
    """Serve markdown docs (e.g. MODELS.md) for in-app links."""
    if doc_name != "MODELS.md":
        raise HTTPException(404, "document not found")
    path = _DOCS / doc_name
    if not path.is_file():
        raise HTTPException(404, "document not found")
    return FileResponse(path, media_type="text/markdown; charset=utf-8")


@app.get("/")
def index():
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
