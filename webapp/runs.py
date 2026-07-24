"""Run management for the web frontend.

Each analysis executes in a worker thread around ``TradingAgentsGraph``.
Progress is captured by wrapping the compiled LangGraph in a proxy whose
``stream`` forwards every state chunk to a callback, so the browser can poll
agent-by-agent status without changing the core framework.
"""

import json
import logging
import re
import threading
import traceback
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from webapp.llm_endpoint import probe_llm_endpoint
from webapp.llm_launch import ensure_local_llm
from webapp.llm_verify import smoke_tool_call
from webapp.model_resolution import resolve_model

from tradingagents.agents.utils.structured_reports import (
    build_bull_bear_comparison,
    structure_reports,
)
from tradingagents.agents.utils.tool_efficiency import RunMetricsCollector
from tradingagents.graph.analyst_execution import ANALYST_NODE_SPECS

logger = logging.getLogger(__name__)

# Pipeline display order. Analyst entries are filtered by selection at run
# start; downstream agents are always present.
ANALYST_AGENTS = {
    "market": "Market Analyst",
    "social": "Sentiment Analyst",
    "news": "News Analyst",
    "fundamentals": "Fundamentals Analyst",
}
DOWNSTREAM_AGENTS = [
    "Bull Researcher",
    "Bear Researcher",
    "Research Manager",
    "Trader",
    "Risk Analysts",
    "Portfolio Manager",
]
OPENROUTER_FREE_QUICK_MAX_RECUR_LIMIT = 360

DEEP_ROUTE_AGENTS = {"Research Manager", "Portfolio Manager"}
QUICK_ROUTE_AGENTS = {
    *ANALYST_AGENTS.values(),
    "Bull Researcher",
    "Bear Researcher",
    "Trader",
    "Risk Analysts",
    "Aggressive Analyst",
    "Neutral Analyst",
    "Conservative Analyst",
}


def _route_provider(params: dict, role: str) -> str | None:
    role_provider = params.get(f"{role}_provider")
    provider = params.get("provider")
    if role_provider:
        return role_provider
    if provider and str(provider).lower() != "hybrid":
        return provider
    return None


def _llm_routes_from_params(params: dict) -> dict[str, dict[str, Any]]:
    routes = params.get("llm_routes")
    if isinstance(routes, dict):
        return routes
    return {
        "quick": {
            "provider": _route_provider(params, "quick"),
            "model": params.get("quick_model"),
            "backend_url": params.get("quick_backend_url") or params.get("backend_url"),
        },
        "deep": {
            "provider": _route_provider(params, "deep"),
            "model": params.get("deep_model"),
            "backend_url": params.get("deep_backend_url") or params.get("backend_url"),
        },
    }


def _format_route_summary(routes: dict[str, dict[str, Any]]) -> str:
    parts = []
    for role in ("quick", "deep"):
        route = routes.get(role) or {}
        provider = route.get("provider") or "default"
        model = route.get("model") or "default"
        backend = route.get("backend_url")
        suffix = f" @ {backend}" if backend else ""
        parts.append(f"{role} {provider} {model}{suffix}")
    return "; ".join(parts)


def _route_for_agent(current_agent: str | None) -> str | None:
    if current_agent in DEEP_ROUTE_AGENTS:
        return "deep"
    if current_agent in QUICK_ROUTE_AGENTS:
        return "quick"
    return None


def is_model_route_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    if "model" in text and "not found" in text:
        return True
    if "model_not_found" in text:
        return True
    if "no endpoints found that support tool use" in text:
        return True
    if "connection refused" in text or "failed to establish a new connection" in text:
        return True
    if "model" in text and "does not exist" in text:
        return True
    if "endpoint" in text and ("not found" in text or "does not exist" in text):
        return True
    if "model" in text and "404" in text:
        return True
    return False


def role_for_agent(agent: str | None) -> str | None:
    if not agent:
        return None
    if agent in DEEP_ROUTE_AGENTS:
        return "deep"
    if agent in QUICK_ROUTE_AGENTS:
        return "quick"
    return "quick"


def _provider_display(provider: str | None) -> str:
    key = (provider or "").lower()
    if key == "openrouter":
        return "OpenRouter"
    if key == "openai_compatible":
        return "local OpenAI-compatible"
    return provider or "configured"


def _format_connection_error(
    name: str,
    msg: str,
    *,
    current_agent: str | None,
    llm_routes: dict[str, dict[str, Any]] | None,
) -> str:
    routes = llm_routes or {}
    failed_role = _route_for_agent(current_agent)
    candidate_routes = [failed_role] if failed_role else ["quick", "deep"]
    providers = {
        (routes.get(role) or {}).get("provider", "").lower()
        for role in candidate_routes
        if routes.get(role)
    }
    if not providers:
        providers = {
            (route or {}).get("provider", "").lower()
            for route in routes.values()
            if isinstance(route, dict)
        }
    route_summary = _format_route_summary(routes) if routes else ""
    agent_text = f" Current agent: {current_agent}." if current_agent else ""

    if "openrouter" in providers:
        route_text = "OpenRouter route"
        if failed_role:
            route_text = f"{failed_role} OpenRouter route"
        summary_text = f" Routes: {route_summary}." if route_summary else ""
        return (
            f"{name}: Cannot reach the {route_text} ({msg}).{agent_text}{summary_text} "
            "Check network/DNS/TLS access to openrouter.ai, confirm OPENROUTER_API_KEY is set "
            "inside Docker, keep TRADINGAGENTS_LLM_PROVIDER/openrouter route settings aligned, "
            "and retry. OpenRouter free routes can also have transient upstream/provider failures."
        )

    if "openai_compatible" in providers or not providers:
        route_text = "local LLM route"
        if failed_role and routes.get(failed_role):
            route = routes[failed_role]
            route_text = f"{failed_role} {_provider_display(route.get('provider'))} route"
        summary_text = f" Routes: {route_summary}." if route_summary else ""
        return (
            f"{name}: Cannot reach the {route_text} ({msg}).{agent_text}{summary_text} "
            "Start LM Studio (or your OpenAI-compatible server), load a chat model, "
            "and ensure the configured backend URL is reachable from Docker "
            "(default: http://host.docker.internal:1234/v1)."
        )

    summary_text = f" Routes: {route_summary}." if route_summary else ""
    return (
        f"{name}: Cannot reach the configured LLM provider ({msg}).{agent_text}{summary_text} "
        "Check the provider endpoint, API key, and Docker network access."
    )

# Maps a report section key -> (state extractor, section title)
def compute_web_recur_limit(
    analysts: list[str],
    max_debate_rounds: int = 1,
    max_risk_rounds: int = 1,
    override: int | None = None,
    openrouter_free_quick: bool = False,
) -> int:
    """Budget LangGraph steps from analyst count and debate depth.

  Each analyst alternates LLM <-> tools until the model stops calling tools;
  four analysts with thorough tool use can exceed a flat limit of 300.
    """
    cap = OPENROUTER_FREE_QUICK_MAX_RECUR_LIMIT if openrouter_free_quick else 1000
    if override is not None:
        return min(int(override), cap)
    n = max(len(analysts), 1)
    debate = max(int(max_debate_rounds or 1), 1)
    risk = max(int(max_risk_rounds or 1), 1)
    limit = 120 + (n * 100) + (debate * 25) + (risk * 35)
    return min(max(limit, 200), cap)


def _format_run_error(
    exc: Exception,
    current_agent: str | None = None,
    llm_routes: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Turn raw exceptions into actionable UI messages."""
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()
    if name == "GraphRecursionError":
        agent_text = f" Current agent: {current_agent}." if current_agent else ""
        return (
            f"{name}: Agent pipeline exceeded its step budget.{agent_text} "
            "This usually means the active analyst kept requesting tools without producing a report. "
            "For Fundamentals loops, use Hybrid/local routing, uncheck Fundamentals on OpenRouter free, "
            "or lower the analyst set and retry. "
            f"({msg})"
        )
    if any(
        x in low
        for x in (
            "connection refused",
            "connect error",
            "failed to establish",
            "name or service not known",
            "connection error",
        )
    ):
        return _format_connection_error(
            name,
            msg,
            current_agent=current_agent,
            llm_routes=llm_routes,
        )
    if (
        "429" in low
        or "rate limit" in low
        or "too many requests" in low
        or "quota" in low
    ):
        return (
            f"{name}: LLM provider rate limit or quota exceeded ({msg}). "
            "For OpenRouter free models, start with Market analyst only, keep "
            "debate/risk rounds at 1, queue fewer tickers, or add OpenRouter credits "
            "for a higher daily quota. Use local LM Studio for bulk or all-analyst runs."
        )
    if "timeout" in low or "timed out" in low:
        return (
            f"{name}: LLM request timed out ({msg}). "
            "The model may be overloaded, too slow, or not loaded."
        )
    if "context length" in low or "maximum context" in low or "context window" in low:
        agent_text = f" Current agent: {current_agent}." if current_agent else ""
        return (
            f"{name}: Context window exceeded.{agent_text} ({msg}). "
            "If you are using LM Studio, increase the loaded model's Context Length "
            "to 16384 or 32768 and set TRADINGAGENTS_CONTEXT_WINDOW to the same value "
            "(or pass max_context_tokens from the web UI). For 8192-token local runs, "
            "use Hybrid budget mode / Market+News, uncheck Fundamentals, and keep "
            "debate/risk rounds at 1."
        )
    return f"{name}: {msg}"


REPORT_SECTIONS = [
    ("market_report", "Market Analyst"),
    ("sentiment_report", "Sentiment Analyst"),
    ("news_report", "News Analyst"),
    ("fundamentals_report", "Fundamentals Analyst"),
    ("bull_history", "Bull Researcher"),
    ("bear_history", "Bear Researcher"),
    ("research_judge", "Research Manager"),
    ("trader_investment_plan", "Trader"),
    ("risk_judge", "Portfolio Manager"),
    ("final_trade_decision", "Final Decision"),
]

ANALYST_REPORT_BY_KEY = {
    key: spec.report_key for key, spec in ANALYST_NODE_SPECS.items()
}
ANALYST_NODE_BY_KEY = {
    key: spec.agent_node for key, spec in ANALYST_NODE_SPECS.items()
}


def _is_rate_limit_error(exc: Exception) -> bool:
    """Return True for common 429/quota/rate-limit exception shapes."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(
        marker in text
        for marker in (
            "429",
            "rate limit",
            "ratelimit",
            "too many requests",
            "quota",
        )
    )


def _retry_after_seconds(exc: Exception) -> int | None:
    """Best-effort extraction of retry-after from provider exceptions/messages."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        try:
            value = headers.get("retry-after") or headers.get("Retry-After")
        except Exception:
            value = None
        if value:
            try:
                return max(int(float(value)), 0)
            except ValueError:
                pass

    text = str(exc)
    patterns = (
        r"retry[- ]?after\D{0,12}(\d+)",
        r"try again in\D{0,12}(\d+)",
        r"wait\D{0,12}(\d+)\s*(?:s|sec|second)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return max(int(match.group(1)), 0)
    return None


def _has_useful_reports(reports: dict | None) -> bool:
    return any(bool(value) for value in (reports or {}).values())


def _first_resume_node(reports: dict, selected_analysts: list[str]) -> tuple[str | None, str]:
    """Choose the first graph node that still needs work.

    Returns ``(node, mode)`` where mode is one of:
    - ``restart`` when no saved report can be reused
    - ``continue_from_partial`` when some analyst work is reused but at least
      one analyst still needs to run
    - ``continue_downstream`` when analyst reports are complete and only
      synthesis/debate/risk stages need to run
    - ``completed`` when there is already a final decision
    """
    selected = selected_analysts or ["market"]
    for analyst_key in selected:
        report_key = ANALYST_REPORT_BY_KEY.get(analyst_key)
        if not report_key:
            continue
        if not reports.get(report_key):
            if _has_useful_reports(reports):
                return ANALYST_NODE_BY_KEY[analyst_key], "continue_from_partial"
            return None, "restart"

    if not reports.get("bull_history"):
        return "Bull Researcher", "continue_downstream"
    if not reports.get("bear_history"):
        return "Bear Researcher", "continue_downstream"
    if not (reports.get("research_judge") or reports.get("investment_plan")):
        return "Research Manager", "continue_downstream"
    if not reports.get("trader_investment_plan"):
        return "Trader", "continue_downstream"
    if not reports.get("risk_history"):
        return "Aggressive Analyst", "continue_downstream"
    if not reports.get("final_trade_decision"):
        return "Portfolio Manager", "continue_downstream"
    return None, "completed"


def _state_overrides_from_reports(reports: dict) -> dict[str, Any]:
    """Build LangGraph state overrides from persisted web report sections."""
    overrides: dict[str, Any] = {}
    for key in (
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
    ):
        if reports.get(key):
            overrides[key] = reports[key]

    bull = reports.get("bull_history") or ""
    bear = reports.get("bear_history") or ""
    research_judge = reports.get("research_judge") or reports.get("investment_plan") or ""
    if bull or bear or research_judge:
        history = "\n".join(part for part in (bull, bear) if part)
        overrides["investment_debate_state"] = {
            "bull_history": bull,
            "bear_history": bear,
            "history": history,
            "current_response": research_judge or bear or bull,
            "judge_decision": research_judge,
            "count": int(bool(bull)) + int(bool(bear)),
        }
        if research_judge and not overrides.get("investment_plan"):
            overrides["investment_plan"] = research_judge

    risk_history = reports.get("risk_history") or ""
    risk_judge = reports.get("risk_judge") or reports.get("final_trade_decision") or ""
    if risk_history or risk_judge:
        overrides["risk_debate_state"] = {
            "aggressive_history": reports.get("aggressive_history", ""),
            "conservative_history": reports.get("conservative_history", ""),
            "neutral_history": reports.get("neutral_history", ""),
            "history": risk_history,
            "latest_speaker": "Neutral" if risk_history else "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": risk_judge,
            "count": 3 if risk_history else 0,
        }
    return overrides


class _StreamProxy:
    """Wraps a compiled LangGraph so ``stream`` reports chunks to a callback."""

    def __init__(self, graph, callback):
        self._graph = graph
        self._callback = callback

    def stream(self, *args, **kwargs):
        for chunk in self._graph.stream(*args, **kwargs):
            try:
                self._callback(chunk)
            except Exception:  # progress must never kill the run
                logger.exception("progress callback failed")
            yield chunk

    def __getattr__(self, name):
        return getattr(self._graph, name)


def _extract_reports(state: dict) -> dict:
    """Pull the report sections out of a (possibly partial) graph state."""
    reports = {}
    for key in (
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "trader_investment_plan",
        "investment_plan",
        "final_trade_decision",
    ):
        value = state.get(key)
        if value:
            reports[key] = value
    debate = state.get("investment_debate_state") or {}
    if debate.get("bull_history"):
        reports["bull_history"] = debate["bull_history"]
    if debate.get("bear_history"):
        reports["bear_history"] = debate["bear_history"]
    if debate.get("judge_decision"):
        reports["research_judge"] = debate["judge_decision"]
    risk = state.get("risk_debate_state") or {}
    if risk.get("history"):
        reports["risk_history"] = risk["history"]
    if risk.get("judge_decision"):
        reports["risk_judge"] = risk["judge_decision"]
    return reports


def _derive_agent_status(reports: dict, selected_analysts: list[str], finished: bool) -> list[dict]:
    """Compute per-agent status from which report sections exist so far."""
    steps = []
    for key in selected_analysts:
        label = ANALYST_AGENTS[key]
        report_key = {
            "market": "market_report",
            "social": "sentiment_report",
            "news": "news_report",
            "fundamentals": "fundamentals_report",
        }[key]
        steps.append((label, report_key in reports))
    steps.append(("Bull Researcher", "bull_history" in reports))
    steps.append(("Bear Researcher", "bear_history" in reports))
    steps.append(("Research Manager", "research_judge" in reports))
    steps.append(("Trader", "trader_investment_plan" in reports))
    steps.append(("Risk Analysts", "risk_history" in reports))
    steps.append(("Portfolio Manager", "final_trade_decision" in reports))

    result = []
    in_progress_assigned = False
    for label, done in steps:
        if done:
            status = "completed"
        elif not in_progress_assigned and not finished:
            status = "in_progress"
            in_progress_assigned = True
        else:
            status = "pending"
        result.append({"agent": label, "status": status})
    return result


def _in_progress_agent(agent_status: list[dict]) -> str | None:
    for item in agent_status:
        if item.get("status") == "in_progress":
            return item.get("agent")
    return None


class RunManager:
    """Owns run records and executes analyses one at a time."""

    def __init__(self, state_dir: Path):
        self._runs: dict[str, dict] = {}
        self._lock = threading.Lock()
        # Local LLM servers handle one heavy workload at a time; serialize runs.
        self._exec_lock = threading.Lock()
        self._state_dir = state_dir
        self._index_path = state_dir / "runs.json"
        self._load_index()

    # ---------- persistence ----------

    def _load_index(self):
        try:
            if self._index_path.exists():
                for rec in json.loads(self._index_path.read_text(encoding="utf-8")):
                    # Anything persisted mid-run in a previous process is dead.
                    if rec.get("status") in ("queued", "running"):
                        rec["status"] = "failed"
                        rec["error"] = "interrupted (server restarted)"
                    self._runs[rec["id"]] = rec
        except Exception:
            logger.exception("could not load runs index")

    def _save_index(self):
        try:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            with self._lock:
                records = list(self._runs.values())
            self._index_path.write_text(
                json.dumps(records, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            logger.exception("could not save runs index")

    # ---------- public API ----------

    def list_runs(self) -> list[dict]:
        with self._lock:
            runs = sorted(self._runs.values(), key=lambda r: r.get("created_at", ""), reverse=True)
            # History list stays light: strip report bodies.
            return [
                {k: v for k, v in r.items() if k not in ("reports", "structured_reports")}
                for r in runs
            ]

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            run = self._runs.get(run_id)
            result = dict(run) if run else None
        if result:
            self._ensure_structured_reports(result)
        return result

    def get_run_comparison(self, run_id: str) -> dict | None:
        run = self.get_run(run_id)
        if run is None:
            return None
        return build_bull_bear_comparison(
            run.get("structured_reports") or {},
            ticker=run.get("ticker"),
            trade_date=run.get("trade_date"),
        )

    def start_run(self, params: dict) -> dict:
        run_id = uuid.uuid4().hex[:12]
        llm_routes = _llm_routes_from_params(params)
        quick_route = llm_routes.get("quick") or {}
        deep_route = llm_routes.get("deep") or {}
        route_summary = params.get("route_summary") or _format_route_summary(llm_routes)
        record = {
            "id": run_id,
            "ticker": params["ticker"],
            "trade_date": params["trade_date"],
            "asset_type": params.get("asset_type", "stock"),
            "analysts": params["analysts"],
            "provider": params.get("provider", ""),
            "quick_provider": quick_route.get("provider") or "",
            "quick_backend_url": quick_route.get("backend_url") or "",
            "deep_provider": deep_route.get("provider") or "",
            "deep_backend_url": deep_route.get("backend_url") or "",
            "deep_model": deep_route.get("model") or "",
            "quick_model": quick_route.get("model") or "",
            "llm_routes": llm_routes,
            "route_summary": route_summary,
            "model_preset": params.get("model_preset"),
            "model_resolution": params.get("model_resolution"),
            "max_context_tokens": params.get("max_context_tokens"),
            "status": "queued",
            "source": params.get("source", "manual"),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "finished_at": None,
            "agent_status": _derive_agent_status({}, params["analysts"], False),
            "reports": {},
            "structured_reports": {},
            "run_metrics": {"agents": {}, "repeated_tool_calls": []},
            "decision": None,
            "error": None,
            "warning": params.get("openrouter_free_warning"),
            "report_path": None,
            "resume_available": False,
            "resume_reason": None,
            "retry_after_seconds": None,
            "resumed_from": params.get("resumed_from"),
            "resume_mode": params.get("resume_mode"),
            "resume_attempt": params.get("resume_attempt"),
            "resume_attempts": [],
            "recovery_events": [],
            "recovery_roles_tried": [],
            "recovery_bad_models": [],
        }
        with self._lock:
            self._runs[run_id] = record
        thread = threading.Thread(target=self._execute, args=(run_id, params), daemon=True)
        thread.start()
        return dict(record)

    def resume_run(self, run_id: str, overrides: dict | None = None) -> dict:
        """Create a child run that reuses completed report sections when possible."""
        overrides = {k: v for k, v in (overrides or {}).items() if v not in ("", None)}
        with self._lock:
            parent = deepcopy(self._runs.get(run_id))
        if parent is None:
            raise KeyError("run not found")
        if parent.get("status") == "completed":
            raise ValueError("run is already completed")

        reports = deepcopy(parent.get("reports") or {})
        selected = list(parent.get("analysts") or ["market"])
        start_node, resume_mode = _first_resume_node(reports, selected)
        if resume_mode == "completed":
            raise ValueError("run already has a final decision")

        params = self._params_from_run(parent)
        params.update(overrides)
        if overrides.get("local_only"):
            self._apply_local_only_resume(params)
        params["llm_routes"] = _llm_routes_from_params(params)
        params["route_summary"] = _format_route_summary(params["llm_routes"])
        params["resume_from_run_id"] = run_id
        params["resume_start_node"] = start_node
        params["resume_mode"] = resume_mode
        params["resume_reports"] = reports
        params["resume_initial_state"] = _state_overrides_from_reports(reports) if reports else None
        params["resumed_from"] = run_id
        params["recovery_roles_tried"] = list(parent.get("recovery_roles_tried") or [])
        params["recovery_bad_models"] = list(parent.get("recovery_bad_models") or [])

        child_id = uuid.uuid4().hex[:12]
        attempts = list(parent.get("resume_attempts") or [])
        attempt_number = len(attempts) + 1
        params["resume_attempt"] = attempt_number

        child_routes = params["llm_routes"]
        quick_route = child_routes.get("quick") or {}
        deep_route = child_routes.get("deep") or {}
        now = datetime.now().isoformat(timespec="seconds")
        resume_reason = (
            "No completed reports were available, so this resume restarts the run."
            if resume_mode == "restart"
            else f"Reusing {len(reports)} completed report section(s); continuing at {start_node}."
        )
        child = {
            "id": child_id,
            "ticker": params["ticker"],
            "trade_date": params["trade_date"],
            "asset_type": params.get("asset_type", "stock"),
            "analysts": selected,
            "provider": params.get("provider", ""),
            "quick_provider": quick_route.get("provider") or "",
            "quick_backend_url": quick_route.get("backend_url") or "",
            "deep_provider": deep_route.get("provider") or "",
            "deep_backend_url": deep_route.get("backend_url") or "",
            "deep_model": deep_route.get("model") or "",
            "quick_model": quick_route.get("model") or "",
            "llm_routes": child_routes,
            "route_summary": params["route_summary"],
            "model_preset": params.get("model_preset"),
            "model_resolution": params.get("model_resolution") or parent.get("model_resolution"),
            "max_context_tokens": params.get("max_context_tokens"),
            "status": "queued",
            "source": "resume",
            "created_at": now,
            "finished_at": None,
            "agent_status": _derive_agent_status(reports, selected, False),
            "reports": reports,
            "structured_reports": structure_reports(
                reports,
                ticker=params["ticker"],
                trade_date=params["trade_date"],
            )
            if reports
            else {},
            "run_metrics": {"agents": {}, "repeated_tool_calls": []},
            "decision": None,
            "error": None,
            "warning": parent.get("warning"),
            "report_path": None,
            "resume_available": False,
            "resume_reason": resume_reason,
            "retry_after_seconds": None,
            "resumed_from": run_id,
            "resume_mode": resume_mode,
            "resume_attempt": attempt_number,
            "resume_attempts": [],
            "recovery_events": list(parent.get("recovery_events") or []),
            "recovery_roles_tried": list(parent.get("recovery_roles_tried") or []),
            "recovery_bad_models": list(parent.get("recovery_bad_models") or []),
        }
        attempt = {
            "child_run_id": child_id,
            "created_at": now,
            "resume_mode": resume_mode,
            "start_node": start_node,
            "overrides": overrides,
        }

        with self._lock:
            parent_live = self._runs.get(run_id)
            if parent_live is not None:
                parent_live.setdefault("resume_attempts", []).append(attempt)
                parent_live["resume_available"] = True
            self._runs[child_id] = child
        self._save_index()

        thread = threading.Thread(target=self._execute, args=(child_id, params), daemon=True)
        thread.start()
        return dict(child)

    def _params_from_run(self, run: dict) -> dict:
        return {
            "ticker": run["ticker"],
            "trade_date": run["trade_date"],
            "asset_type": run.get("asset_type", "stock"),
            "analysts": list(run.get("analysts") or ["market"]),
            "provider": run.get("provider") or None,
            "quick_provider": run.get("quick_provider") or None,
            "quick_backend_url": run.get("quick_backend_url") or None,
            "deep_provider": run.get("deep_provider") or None,
            "deep_backend_url": run.get("deep_backend_url") or None,
            "deep_model": run.get("deep_model") or None,
            "quick_model": run.get("quick_model") or None,
            "model_preset": run.get("model_preset"),
            "model_resolution": run.get("model_resolution"),
            "max_context_tokens": run.get("max_context_tokens"),
            "source": run.get("source", "manual"),
        }

    def _apply_local_only_resume(self, params: dict) -> None:
        backend = (
            params.get("quick_backend_url")
            or params.get("deep_backend_url")
            or params.get("backend_url")
            or "http://host.docker.internal:1234/v1"
        )
        quick_model = params.get("quick_model")
        deep_model = params.get("deep_model") or quick_model
        params.update(
            {
                "provider": "hybrid",
                "quick_provider": "openai_compatible",
                "deep_provider": "openai_compatible",
                "quick_backend_url": backend,
                "deep_backend_url": backend,
                "deep_model": deep_model,
                "quick_model": quick_model or deep_model,
            }
        )

    # ---------- execution ----------

    def _update(self, run_id: str, **fields):
        with self._lock:
            run = self._runs.get(run_id)
            if run:
                run.update(fields)

    def _current_agent(self, run_id: str) -> str | None:
        with self._lock:
            run = self._runs.get(run_id) or {}
            for item in run.get("agent_status") or []:
                if item.get("status") == "in_progress":
                    return item.get("agent")
        return None

    def _ensure_structured_reports(self, run: dict) -> None:
        reports = run.get("reports") or {}
        structured = run.get("structured_reports") or {}
        if not reports:
            run.setdefault("structured_reports", {})
            return
        if set(reports) - set(structured):
            run["structured_reports"] = structure_reports(
                reports,
                ticker=run.get("ticker"),
                trade_date=run.get("trade_date"),
            )

    def _execute(self, run_id: str, params: dict):
        with self._exec_lock:
            self._update(run_id, status="running")
            try:
                self._run_analysis(run_id, params)
            except Exception as exc:
                logger.exception("run %s failed", run_id)
                current_agent = self._current_agent(run_id)
                with self._lock:
                    run = self._runs.get(run_id) or {}
                    llm_routes = run.get("llm_routes")
                    reports = dict(run.get("reports") or {})
                    analysts = list(run.get("analysts") or [])
                formatted_error = _format_run_error(
                    exc,
                    current_agent=current_agent,
                    llm_routes=llm_routes,
                )
                stopped_at = datetime.now().isoformat(timespec="seconds")
                if _is_rate_limit_error(exc) and _has_useful_reports(reports):
                    retry_after = _retry_after_seconds(exc)
                    retry_text = (
                        f" Retry after about {retry_after} seconds or switch models."
                        if retry_after is not None
                        else " Retry after the provider limit resets or switch models."
                    )
                    self._update(
                        run_id,
                        status="paused",
                        error=formatted_error,
                        finished_at=stopped_at,
                        paused_at=stopped_at,
                        resume_available=True,
                        resume_reason=(
                            "Paused after a provider rate limit with completed report sections."
                            + retry_text
                        ),
                        retry_after_seconds=retry_after,
                        agent_status=_derive_agent_status(reports, analysts, False),
                        traceback=traceback.format_exc()[-4000:],
                    )
                elif is_model_route_error(exc):
                    recovered = self._attempt_model_recovery(
                        run_id, params, exc, current_agent, reports, analysts
                    )
                    if recovered:
                        return
                    self._update(
                        run_id,
                        status="failed",
                        error=formatted_error,
                        finished_at=stopped_at,
                        resume_available=False,
                        resume_reason=None,
                        retry_after_seconds=None,
                        traceback=traceback.format_exc()[-4000:],
                    )
                else:
                    self._update(
                        run_id,
                        status="failed",
                        error=formatted_error,
                        finished_at=stopped_at,
                        resume_available=False,
                        resume_reason=None,
                        retry_after_seconds=None,
                        traceback=traceback.format_exc()[-4000:],
                    )
            finally:
                self._save_index()

    def _attempt_model_recovery(
        self,
        run_id: str,
        params: dict,
        exc: Exception,
        current_agent: str | None,
        reports: dict,
        analysts: list[str],
    ) -> bool:
        role = role_for_agent(current_agent)
        if not role:
            return False

        with self._lock:
            run = deepcopy(self._runs.get(run_id) or {})
        roles_tried = set(run.get("recovery_roles_tried") or [])
        if role in roles_tried:
            return False

        llm_routes = run.get("llm_routes") or _llm_routes_from_params(params)
        route = llm_routes.get(role) or {}
        provider = route.get("provider") or params.get(f"{role}_provider")
        backend_url = route.get("backend_url") or params.get(f"{role}_backend_url")
        current_model = route.get("model") or params.get(f"{role}_model")

        known_bad = set(run.get("recovery_bad_models") or [])
        if current_model:
            known_bad.add(current_model)

        provider_key = (provider or "").lower()
        if provider_key in ("openai_compatible", "ollama"):
            ensure_local_llm(provider, backend_url, model=current_model)

        probe = probe_llm_endpoint(provider, backend_url, timeout=5)
        catalog = list(probe.get("models") or [])
        if not catalog:
            return False

        exclude = set(known_bad)
        new_model: str | None = None
        resolution_reason = ""
        for _ in range(3):
            resolution = resolve_model(
                current_model,
                catalog,
                provider=provider,
                exclude=exclude,
            )
            candidate = resolution.resolved
            if not candidate:
                break
            smoke = smoke_tool_call(provider, backend_url, candidate, timeout=15)
            if smoke.get("ok"):
                new_model = candidate
                resolution_reason = resolution.reason
                break
            exclude.add(candidate)
            known_bad.add(candidate)

        if not new_model:
            self._update(run_id, recovery_bad_models=list(known_bad))
            return False

        _, resume_mode = _first_resume_node(reports, analysts)
        stopped_at = datetime.now().isoformat(timespec="seconds")
        event = {
            "at": stopped_at,
            "role": role,
            "from": current_model,
            "to": new_model,
            "reason": resolution_reason or str(exc),
            "resume_mode": resume_mode,
        }
        events = list(run.get("recovery_events") or [])
        events.append(event)
        roles_tried.add(role)

        self._update(
            run_id,
            status="paused",
            error=_format_run_error(
                exc,
                current_agent=current_agent,
                llm_routes=llm_routes,
            ),
            finished_at=stopped_at,
            paused_at=stopped_at,
            resume_available=True,
            resume_reason=(
                f"Auto-recovered {role} route: remapped {current_model} → {new_model} "
                f"and resumed ({resume_mode})."
            ),
            agent_status=_derive_agent_status(reports, analysts, False),
            recovery_events=events,
            recovery_roles_tried=sorted(roles_tried),
            recovery_bad_models=sorted(known_bad),
            traceback=traceback.format_exc()[-4000:],
        )
        self._save_index()

        overrides: dict[str, Any] = {f"{role}_model": new_model}
        if provider:
            overrides[f"{role}_provider"] = provider
        if backend_url:
            overrides[f"{role}_backend_url"] = backend_url
        self.resume_run(run_id, overrides)
        return True

    def _run_analysis(self, run_id: str, params: dict):
        # Import here so server startup stays fast and config env is settled.
        from tradingagents.default_config import DEFAULT_CONFIG
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        config = DEFAULT_CONFIG.copy()
        config["checkpoint_enabled"] = False  # incompatible with stream proxy
        if params.get("provider"):
            config["llm_provider"] = params["provider"]
        if params.get("backend_url"):
            config["backend_url"] = params["backend_url"]
        if params.get("quick_provider"):
            config["quick_provider"] = params["quick_provider"]
        if params.get("quick_backend_url"):
            config["quick_backend_url"] = params["quick_backend_url"]
        if params.get("deep_provider"):
            config["deep_provider"] = params["deep_provider"]
        if params.get("deep_backend_url"):
            config["deep_backend_url"] = params["deep_backend_url"]
        if params.get("deep_model"):
            config["deep_think_llm"] = params["deep_model"]
        if params.get("quick_model"):
            config["quick_think_llm"] = params["quick_model"]
        if params.get("max_debate_rounds"):
            config["max_debate_rounds"] = int(params["max_debate_rounds"])
        if params.get("max_risk_rounds"):
            config["max_risk_discuss_rounds"] = int(params["max_risk_rounds"])
        if params.get("max_context_tokens"):
            config["max_context_tokens"] = int(params["max_context_tokens"])
        config["max_recur_limit"] = compute_web_recur_limit(
            selected := params["analysts"],
            max_debate_rounds=params.get("max_debate_rounds") or config["max_debate_rounds"],
            max_risk_rounds=params.get("max_risk_rounds") or config["max_risk_discuss_rounds"],
            override=params.get("max_recur_limit"),
            openrouter_free_quick=bool(params.get("openrouter_free_quick")),
        )

        ta = TradingAgentsGraph(
            selected_analysts=selected,
            debug=True,
            config=config,
            start_at=params.get("resume_start_node"),
        )
        metrics = RunMetricsCollector()

        def on_chunk(chunk: dict):
            chunk_reports = _extract_reports(chunk)
            with self._lock:
                existing_reports = dict((self._runs.get(run_id) or {}).get("reports") or {})
            reports = {**existing_reports, **chunk_reports}
            structured_reports = structure_reports(
                reports,
                ticker=params["ticker"],
                trade_date=params["trade_date"],
            )
            agent_status = _derive_agent_status(reports, selected, False)
            current_agent = _in_progress_agent(agent_status)
            metrics.observe_messages(chunk.get("messages") or [], agent=current_agent)
            metrics.observe_reports(reports)
            run_metrics = metrics.snapshot()
            run_metrics["tool_cache"] = ta.tool_efficiency_tracker.snapshot()
            self._update(
                run_id,
                reports=reports,
                structured_reports=structured_reports,
                agent_status=agent_status,
                run_metrics=run_metrics,
                resume_available=False,
            )
            self._save_index()

        # debug=True makes propagate() consume graph.stream(); the proxy relays
        # every state chunk to on_chunk for live progress.
        ta.graph = _StreamProxy(ta.graph, on_chunk)

        final_state, decision = ta.propagate(
            params["ticker"],
            params["trade_date"],
            asset_type=params.get("asset_type", "stock"),
            initial_state_overrides=params.get("resume_initial_state"),
        )

        report_path = ta.save_reports(final_state, params["ticker"])
        reports = _extract_reports(final_state)
        metrics.observe_reports(reports)
        structured_reports = structure_reports(
            reports,
            ticker=params["ticker"],
            trade_date=params["trade_date"],
        )
        run_metrics = metrics.snapshot()
        run_metrics["tool_cache"] = ta.tool_efficiency_tracker.snapshot()
        fused_signal = self._fuse_completed_run(
            {
                "id": run_id,
                "ticker": params["ticker"],
                "trade_date": params["trade_date"],
                "reports": reports,
                "decision": decision,
            }
        )
        self._update(
            run_id,
            status="completed",
            reports=reports,
            structured_reports=structured_reports,
            agent_status=_derive_agent_status(reports, selected, True),
            run_metrics=run_metrics,
            decision=decision,
            fused_signal=fused_signal,
            report_path=str(report_path),
            resume_available=False,
            resume_reason=None,
            retry_after_seconds=None,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )

    def _fuse_completed_run(self, run: dict) -> dict:
        """Run hybrid fusion and persist to firm audit log."""
        try:
            from firm.service import evaluate_and_store_fusion

            return evaluate_and_store_fusion(run)
        except Exception:
            logger.exception("fusion failed for run %s", run.get("id"))
            return {
                "ticker": run.get("ticker"),
                "trade_date": run.get("trade_date"),
                "run_id": run.get("id"),
                "fused_pass": False,
                "blockers": ["fusion_error"],
            }


def section_title(key: str) -> str:
    for k, title in REPORT_SECTIONS:
        if k == key:
            return title
    return key
