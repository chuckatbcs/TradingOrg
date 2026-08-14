"""Web run records include structured report summaries."""

import pytest

from webapp.runs import RunManager


@pytest.mark.unit
def test_run_manager_lazily_adds_structured_reports(tmp_path):
    manager = RunManager(tmp_path)
    manager._runs["run123"] = {
        "id": "run123",
        "ticker": "NVDA",
        "trade_date": "2026-07-03",
        "status": "completed",
        "reports": {
            "bull_history": "Bull Analyst:\n\n## Evidence\n- Margins are expanding.",
            "bear_history": "Bear Analyst:\n\n## Risks\n- Demand could normalize.",
            "final_trade_decision": "**Rating**: Buy\n\n**Price Target**: $125",
        },
    }

    run = manager.get_run("run123")

    assert run is not None
    assert run["structured_reports"]["bull_history"]["stance"] == "Bullish"
    assert run["structured_reports"]["bear_history"]["stance"] == "Bearish"
    assert run["structured_reports"]["final_trade_decision"]["rating"] == "Buy"

    comparison = manager.get_run_comparison("run123")
    assert comparison is not None
    assert comparison["available"] is True
    assert any("Stance differs" in item for item in comparison["disagreements"])
