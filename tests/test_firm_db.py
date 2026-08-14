"""Tests for firm SQLite storage."""

import pytest

from firm.storage.db import FirmDB


@pytest.mark.unit
def test_firm_db_roundtrip(tmp_path):
    db = FirmDB(db_path=tmp_path / "firm.db")
    sig_id = db.save_fused_signal({
        "ticker": "NVDA",
        "trade_date": "2026-01-01",
        "run_id": "run1",
        "quant_pass": True,
        "quant_score": 80.0,
        "llm_pass": True,
        "llm_rating": "Buy",
        "llm_score": 1.0,
        "regime": "bull",
        "regime_multiplier": 1.0,
        "fused_score": 0.8,
        "fused_pass": True,
        "blockers": [],
    })
    assert sig_id == 1
    rows = db.list_fused_signals()
    assert len(rows) == 1
    assert rows[0]["ticker"] == "NVDA"
    assert rows[0]["fused_pass"] is True
