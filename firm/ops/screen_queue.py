"""Queue screener finalists as TradingAgents analysis runs."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

from firm.config import FIRM_CONFIG
from firm.ops.notifications import notify
from firm.universe.market_screener import resolve_screening_universe
from firm.universe.screener import screen_universe

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

DEFAULT_ANALYSTS = ["market", "social", "news", "fundamentals"]
_CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")


class RunManagerProtocol(Protocol):
    def list_runs(self) -> list[dict]: ...

    def start_run(self, params: dict) -> dict: ...


_run_manager: RunManagerProtocol | None = None


def bind_run_manager(manager: RunManagerProtocol | None) -> None:
    """Register the webapp RunManager (called once at server startup)."""
    global _run_manager
    _run_manager = manager


def get_run_manager() -> RunManagerProtocol | None:
    return _run_manager


def active_tickers_today(runs: list[dict], trade_date: str) -> set[str]:
    """Tickers that already have a queued or running run for trade_date."""
    active: set[str] = set()
    for run in runs:
        if run.get("trade_date") != trade_date:
            continue
        if run.get("status") not in ("queued", "running"):
            continue
        ticker = (run.get("ticker") or "").strip().upper()
        if ticker:
            active.add(ticker)
    return active


def _resolve_analysts(ticker: str, analysts: list[str] | None) -> list[str]:
    selected = list(analysts or DEFAULT_ANALYSTS)
    if ticker.endswith(_CRYPTO_SUFFIXES):
        selected = [a for a in selected if a != "fundamentals"] or ["market"]
    return selected


def _candidate_payload(result) -> dict:
    payload = {
        "ticker": result.ticker,
        "score": result.score,
        "passed": result.passed,
        "filters": result.filters,
        "metrics": result.metrics,
        "blockers": result.blockers,
    }
    advisory = getattr(result, "advisory", None)
    if advisory:
        payload["advisory"] = advisory
    return payload


def _resolve_model_params(run_params: dict[str, Any] | None) -> dict[str, Any]:
    """Apply verify/resolve when scheduler queues without UI run_params."""
    if run_params:
        return dict(run_params)
    # firm imports webapp.llm_route_prep (not webapp.server) to avoid circular imports.
    from webapp.llm_route_prep import LlmVerifyError, build_default_run_params

    try:
        return build_default_run_params()
    except LlmVerifyError as exc:
        logger.error("scheduler LLM verify failed: %s", exc)
        raise RuntimeError(str(exc)) from exc


def queue_screener_finalists(
    run_manager: RunManagerProtocol | None = None,
    *,
    top_n: int | None = None,
    trade_date: str | None = None,
    analysts: list[str] | None = None,
    notify_discord: bool = True,
    source: str = "premarket_screen",
    run_params: dict[str, Any] | None = None,
) -> dict:
    """Screen universe (market or watchlist) and queue top finalists for TradingAgents analysis."""
    manager = run_manager or _run_manager
    if manager is None:
        raise RuntimeError("RunManager not bound — start the web server first")

    n = top_n if top_n is not None else FIRM_CONFIG.premarket_screen_top_n
    date = trade_date or datetime.now().strftime("%Y-%m-%d")

    symbols, universe_meta = resolve_screening_universe()
    finalists = screen_universe(symbols, top_n=n, pass_only=True)
    tickers = [r.ticker for r in finalists]

    existing = active_tickers_today(manager.list_runs(), date)
    queued: list[str] = []
    queued_runs: list[dict[str, str]] = []
    skipped: list[str] = []
    model_params = _resolve_model_params(run_params)

    for ticker in tickers:
        upper = ticker.strip().upper()
        if upper in existing:
            skipped.append(upper)
            continue
        asset_type = "crypto" if upper.endswith(_CRYPTO_SUFFIXES) else "stock"
        selected_analysts = _resolve_analysts(upper, analysts)
        start_params = {
            **model_params,
            "ticker": upper,
            "trade_date": date,
            "asset_type": asset_type,
            "analysts": selected_analysts,
            "source": source,
        }
        record = manager.start_run(
            start_params
        )
        queued.append(record["ticker"])
        queued_runs.append({"ticker": record["ticker"], "run_id": record["id"]})
        existing.add(upper)

    result = {
        "trade_date": date,
        "universe": universe_meta,
        "candidates": [_candidate_payload(r) for r in finalists],
        "screener_mode": FIRM_CONFIG.screener_mode,
        "queued": queued,
        "queued_runs": queued_runs,
        "skipped": skipped,
        "count": len(queued),
        "model_preset": model_params.get("model_preset"),
        "route_summary": model_params.get("route_summary"),
        "llm_routes": model_params.get("llm_routes"),
        "model_resolution": model_params.get("model_resolution"),
    }

    if notify_discord and queued:
        notify(
            "Screener Queued",
            f"Queued {len(queued)} analysis run(s): {', '.join(queued)}",
            color=0x2F81F7,
        )
    elif notify_discord and tickers and not queued:
        notify(
            "Screener Queue Skipped",
            f"All {len(tickers)} finalist(s) already queued/running today.",
            color=0xD29922,
        )

    logger.info(
        "screen queue: %d queued, %d skipped (date=%s)",
        len(queued),
        len(skipped),
        date,
    )
    return result
