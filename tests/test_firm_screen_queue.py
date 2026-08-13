"""Tests for firm.ops.screen_queue dedup and queue logic."""

from __future__ import annotations

from unittest import mock
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from firm.ops.screen_queue import (
    active_tickers_today,
    queue_screener_finalists,
)


class FakeRunManager:
    def __init__(self, runs: list[dict] | None = None):
        self._runs = list(runs or [])
        self.started: list[dict] = []

    def list_runs(self) -> list[dict]:
        return list(self._runs)

    def start_run(self, params: dict) -> dict:
        record = {
            "id": f"run-{len(self.started)}",
            "ticker": params["ticker"],
            "trade_date": params["trade_date"],
            "status": "queued",
            "source": params.get("source", "manual"),
        }
        self._runs.append(record)
        self.started.append(params)
        return record


@pytest.mark.unit
def test_active_tickers_today_filters_by_date_and_status():
    runs = [
        {"ticker": "AAPL", "trade_date": "2026-07-02", "status": "queued"},
        {"ticker": "MSFT", "trade_date": "2026-07-02", "status": "running"},
        {"ticker": "GOOG", "trade_date": "2026-07-02", "status": "completed"},
        {"ticker": "TSLA", "trade_date": "2026-07-01", "status": "queued"},
    ]
    active = active_tickers_today(runs, "2026-07-02")
    assert active == {"AAPL", "MSFT"}


@pytest.mark.unit
def test_queue_screener_finalists_dedups_and_queues(monkeypatch):
    monkeypatch.setattr(
        "firm.ops.screen_queue.resolve_screening_universe",
        lambda: (["AAA", "BBB", "CCC"], {"mode": "watchlist", "count": 3}),
    )

    class Result:
        def __init__(self, ticker: str):
            self.ticker = ticker
            self.score = 80.0
            self.passed = True
            self.filters = {}
            self.metrics = {}
            self.blockers = []

    monkeypatch.setattr(
        "firm.ops.screen_queue.screen_universe",
        lambda symbols, top_n=None, pass_only=True: [Result("AAA"), Result("BBB"), Result("CCC")][: (top_n or 3)],
    )
    monkeypatch.setattr("firm.ops.screen_queue.notify", MagicMock())
    monkeypatch.setattr(
        "webapp.llm_route_prep.build_default_run_params",
        lambda config=None: {},
    )

    manager = FakeRunManager(
        [
            {"ticker": "BBB", "trade_date": "2026-07-02", "status": "running"},
        ]
    )

    result = queue_screener_finalists(
        manager,
        top_n=3,
        trade_date="2026-07-02",
        notify_discord=False,
        source="manual_screen",
    )

    assert result["candidates"] == [
        {"ticker": "AAA", "score": 80.0, "passed": True, "filters": {}, "metrics": {}, "blockers": []},
        {"ticker": "BBB", "score": 80.0, "passed": True, "filters": {}, "metrics": {}, "blockers": []},
        {"ticker": "CCC", "score": 80.0, "passed": True, "filters": {}, "metrics": {}, "blockers": []},
    ]
    assert result["queued"] == ["AAA", "CCC"]
    assert result["skipped"] == ["BBB"]
    assert result["count"] == 2
    assert len(manager.started) == 2
    assert manager.started[0]["ticker"] == "AAA"
    assert manager.started[0]["source"] == "manual_screen"
    assert manager.started[0]["analysts"] == [
        "market",
        "social",
        "news",
        "fundamentals",
    ]


@pytest.mark.unit
def test_queue_screener_finalists_passes_model_route_params(monkeypatch):
    monkeypatch.setattr(
        "firm.ops.screen_queue.resolve_screening_universe",
        lambda: (["AAA"], {"mode": "watchlist", "count": 1}),
    )

    class Result:
        ticker = "AAA"
        score = 80.0
        passed = True
        filters = {}
        metrics = {}
        blockers = []
        advisory = None

    monkeypatch.setattr(
        "firm.ops.screen_queue.screen_universe",
        lambda symbols, top_n=None, pass_only=True: [Result()],
    )
    monkeypatch.setattr("firm.ops.screen_queue.notify", MagicMock())

    manager = FakeRunManager()
    result = queue_screener_finalists(
        manager,
        top_n=1,
        trade_date="2026-07-02",
        notify_discord=False,
        source="manual_screen",
        run_params={
            "provider": "hybrid",
            "quick_provider": "openai_compatible",
            "quick_backend_url": "http://host.docker.internal:1234/v1",
            "quick_model": "local-quick",
            "deep_provider": "openrouter",
            "deep_model": "openrouter-deep",
            "model_preset": "hybrid_local_quick_openrouter_deep",
            "route_summary": "quick local-quick; deep openrouter-deep",
        },
    )

    assert result["route_summary"] == "quick local-quick; deep openrouter-deep"
    assert result["queued_runs"] == [{"ticker": "AAA", "run_id": "run-0"}]
    assert manager.started[0]["source"] == "manual_screen"
    assert manager.started[0]["quick_model"] == "local-quick"
    assert manager.started[0]["deep_provider"] == "openrouter"
    assert manager.started[0]["model_preset"] == "hybrid_local_quick_openrouter_deep"


@pytest.mark.unit
def test_queue_screener_applies_default_model_verify(monkeypatch):
    from copy import deepcopy

    from tradingagents.default_config import DEFAULT_CONFIG

    test_config = deepcopy(DEFAULT_CONFIG)
    test_config.update(
        {
            "llm_provider": "openai",
            "quick_think_llm": "gpt-5.4-mini",
            "deep_think_llm": "gpt-5.5",
            "quick_provider": None,
            "deep_provider": None,
            "backend_url": None,
            "quick_backend_url": None,
            "deep_backend_url": None,
        }
    )
    monkeypatch.setattr("tradingagents.default_config.DEFAULT_CONFIG", test_config)

    monkeypatch.setattr(
        "firm.ops.screen_queue.resolve_screening_universe",
        lambda: (["AAA"], {"mode": "watchlist", "count": 1}),
    )

    class Result:
        ticker = "AAA"
        score = 80.0
        passed = True
        filters = {}
        metrics = {}
        blockers = []

    monkeypatch.setattr(
        "firm.ops.screen_queue.screen_universe",
        lambda symbols, top_n=None, pass_only=True: [Result()],
    )
    monkeypatch.setattr("firm.ops.screen_queue.notify", MagicMock())

    fake_probe = {
        "reachable": True,
        "models": ["gpt-5.4-mini", "gpt-5.5"],
        "error": None,
    }
    manager = FakeRunManager()
    with (
        mock.patch("webapp.llm_route_prep.probe_llm_endpoint", return_value=fake_probe),
        mock.patch(
            "webapp.llm_route_prep.ensure_local_llm",
            return_value=mock.Mock(attempted=False, reached=True, error=None, detail=None),
        ),
        mock.patch(
            "webapp.llm_verify.smoke_tool_call",
            return_value={"ok": True, "error": None},
        ),
    ):
        result = queue_screener_finalists(
            manager,
            top_n=1,
            trade_date="2026-07-02",
            notify_discord=False,
            source="premarket_screen",
        )

    assert result["count"] == 1
    assert manager.started[0]["quick_model"] == "gpt-5.4-mini"
    assert manager.started[0]["deep_model"] == "gpt-5.5"
    assert manager.started[0]["model_resolution"]["quick"]["resolved"] == "gpt-5.4-mini"
    assert result["model_resolution"]["deep"]["resolved"] == "gpt-5.5"


@pytest.mark.unit
def test_queue_screen_api_passes_selected_model_routes(monkeypatch):
    from contextlib import contextmanager
    from unittest import mock

    from webapp import server

    @contextmanager
    def verify_ok(models):
        fake_probe = {"reachable": True, "models": models, "error": None}
        with mock.patch("webapp.llm_route_prep.probe_llm_endpoint", return_value=fake_probe), \
             mock.patch("webapp.llm_route_prep.ensure_local_llm") as launch, \
             mock.patch("webapp.llm_verify.smoke_tool_call", return_value={"ok": True, "error": None}):
            launch.return_value = mock.Mock(attempted=False, reached=True, error=None, detail=None)
            yield

    captured = {}

    def fake_queue_screener_finalists(
        run_manager,
        *,
        top_n=None,
        trade_date=None,
        analysts=None,
        notify_discord=True,
        source="premarket_screen",
        run_params=None,
    ):
        captured["run_manager"] = run_manager
        captured["top_n"] = top_n
        captured["analysts"] = analysts
        captured["source"] = source
        captured["run_params"] = run_params
        return {
            "queued": ["AAA"],
            "queued_runs": [{"ticker": "AAA", "run_id": "run-0"}],
            "skipped": [],
            "candidates": [],
            "count": 1,
            "route_summary": run_params["route_summary"],
            "model_preset": run_params["model_preset"],
        }

    monkeypatch.setattr(
        "firm.ops.screen_queue.queue_screener_finalists",
        fake_queue_screener_finalists,
    )
    client = TestClient(server.app)

    with verify_ok(["local-quick", "local-deep"]):
        resp = client.post(
            "/api/firm/queue-screen",
            json={
                "top_n": 2,
                "analysts": ["market"],
                "provider": "openai_compatible",
                "backend_url": "http://host.docker.internal:1234/v1",
                "quick_model": "local-quick",
                "deep_model": "local-deep",
                "model_preset": "fast_local",
                "max_context_tokens": 8192,
            },
        )

    assert resp.status_code == 200
    assert captured["top_n"] == 2
    assert captured["analysts"] == ["market"]
    assert captured["source"] == "manual_screen"
    assert captured["run_params"]["provider"] == "openai_compatible"
    assert captured["run_params"]["llm_routes"]["quick"]["model"] == "local-quick"
    assert captured["run_params"]["llm_routes"]["deep"]["backend_url"] == (
        "http://host.docker.internal:1234/v1"
    )
    assert captured["run_params"]["model_preset"] == "fast_local"
    assert "quick openai_compatible local-quick" in resp.json()["route_summary"]


@pytest.mark.unit
def test_queue_screener_skips_all_when_already_active(monkeypatch):
    monkeypatch.setattr(
        "firm.ops.screen_queue.resolve_screening_universe",
        lambda: (["AAA"], {"mode": "watchlist", "count": 1}),
    )

    class Result:
        ticker = "AAA"
        score = 80.0
        passed = True
        filters = {}
        metrics = {}
        blockers = []

    monkeypatch.setattr(
        "firm.ops.screen_queue.screen_universe",
        lambda symbols, top_n=None, pass_only=True: [Result()],
    )
    mock_notify = MagicMock()
    monkeypatch.setattr("firm.ops.screen_queue.notify", mock_notify)
    monkeypatch.setattr(
        "webapp.llm_route_prep.build_default_run_params",
        lambda config=None: {},
    )

    manager = FakeRunManager(
        [
            {"ticker": "AAA", "trade_date": "2026-07-02", "status": "queued"},
        ]
    )

    result = queue_screener_finalists(
        manager,
        top_n=1,
        trade_date="2026-07-02",
        notify_discord=True,
    )

    assert result["queued"] == []
    assert result["skipped"] == ["AAA"]
    assert manager.started == []
    mock_notify.assert_called_once()
    assert "already queued" in mock_notify.call_args[0][1].lower()


@pytest.mark.unit
def test_queue_requires_run_manager(monkeypatch):
    from firm.ops import screen_queue

    monkeypatch.setattr(screen_queue, "_run_manager", None)
    with pytest.raises(RuntimeError, match="RunManager not bound"):
        queue_screener_finalists(None, notify_discord=False)
