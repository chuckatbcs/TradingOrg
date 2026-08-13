"""Tests for firm watchlist resolution and metadata."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from firm.universe.watchlist import (
    CURATED_EXTRA,
    TICKER_POOL,
    default_watchlist,
    parse_ticker_symbols,
    resolve_watchlist,
    save_watchlist_extra,
    watchlist_metadata,
)


@pytest.mark.unit
def test_default_watchlist_is_50_unique():
    wl = default_watchlist()
    assert len(wl) == len(TICKER_POOL) + len(CURATED_EXTRA)
    assert len(wl) == 50
    assert len(set(wl)) == 50


@pytest.mark.unit
def test_parse_ticker_symbols():
    assert parse_ticker_symbols("pltr, sofi\nCOIN") == ["PLTR", "SOFI", "COIN"]
    assert parse_ticker_symbols(["nvda", "NVDA"]) == ["NVDA"]
    with pytest.raises(ValueError, match="invalid ticker"):
        parse_ticker_symbols("BAD TICKER!")


@pytest.mark.unit
def test_resolve_watchlist_merges_user_extra(tmp_path):
    save_watchlist_extra(tmp_path, ["PLTR", "SOFI"])
    wl = resolve_watchlist(tmp_path)
    assert "PLTR" in wl
    assert "SOFI" in wl
    assert len(wl) == 52


@pytest.mark.unit
def test_watchlist_metadata(tmp_path):
    save_watchlist_extra(tmp_path, ["PLTR"])
    meta = watchlist_metadata(tmp_path)
    assert meta["count"] == 51
    assert meta["sources"]["gemini_core"]["count"] == 30
    assert meta["sources"]["curated_extra"]["count"] == 20
    assert meta["sources"]["user_extra"]["symbols"] == ["PLTR"]


@pytest.mark.unit
def test_firm_watchlist_api(tmp_path, monkeypatch):
    from firm.config import reload_firm_config
    from webapp.server import app

    monkeypatch.setenv("FIRM_DATA_DIR", str(tmp_path))
    reload_firm_config()
    client = TestClient(app)
    resp = client.get("/api/firm/watchlist")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 50
    assert "sources" in body
    assert body["sources"]["gemini_core"]["count"] == 30


@pytest.mark.unit
def test_firm_settings_watchlist_extra_patch(tmp_path, monkeypatch):
    from firm.config import reload_firm_config
    from webapp.server import app

    monkeypatch.setenv("FIRM_DATA_DIR", str(tmp_path))
    reload_firm_config()
    client = TestClient(app)
    resp = client.patch(
        "/api/firm/settings",
        json={"watchlist_extra": "PLTR, SOFI"},
    )
    assert resp.status_code == 200
    assert resp.json()["watchlist"]["user_extra"] == ["PLTR", "SOFI"]
    on_disk = json.loads((tmp_path / "user_settings.json").read_text(encoding="utf-8"))
    assert on_disk["watchlist_extra"] == ["PLTR", "SOFI"]
