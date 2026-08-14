"""Tests for firm user settings persistence and API merge."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from firm.config import FirmConfig, reload_firm_config
from firm.user_settings import (
    TUNABLE_ENV,
    load_user_settings,
    reset_user_settings,
    save_user_settings,
    user_settings_path,
    validate_patch,
    validate_setting,
)


@pytest.mark.unit
def test_validate_setting_ranges():
    assert validate_setting("quant_min_score", 55) == 55
    with pytest.raises(ValueError, match="between"):
        validate_setting("quant_min_score", 150)
    with pytest.raises(ValueError, match="screener_rsi_low"):
        validate_patch(
            {"screener_rsi_low": 80, "screener_rsi_high": 70},
            {"screener_rsi_low": 80, "screener_rsi_high": 70},
        )


@pytest.mark.unit
def test_validate_universe_mode():
    assert validate_setting("universe_mode", "market") == "market"
    with pytest.raises(ValueError, match="invalid value"):
        validate_setting("universe_mode", "invalid")


@pytest.mark.unit
def test_validate_screener_mode():
    assert validate_setting("screener_mode", "scoring") == "scoring"
    with pytest.raises(ValueError, match="invalid value"):
        validate_setting("screener_mode", "loose")


@pytest.mark.unit
def test_build_settings_view_includes_scan_toggles():
    from firm.config import FirmConfig
    from firm.user_settings import build_settings_view

    view = build_settings_view(FirmConfig())
    assert view["settings"]["market_screener_include_actives"]["type"] == "bool"
    assert view["settings"]["screener_mode"]["options"] == ["strict", "scoring"]


@pytest.mark.unit
def test_build_settings_view_includes_select_options():
    from firm.config import FirmConfig
    from firm.user_settings import build_settings_view

    universe_mode = build_settings_view(FirmConfig())["settings"]["universe_mode"]
    assert universe_mode["type"] == "select"
    assert universe_mode["options"] == ["watchlist", "market"]


@pytest.mark.unit
def test_user_settings_save_load(tmp_path, monkeypatch):
    monkeypatch.setenv("FIRM_DATA_DIR", str(tmp_path))
    save_user_settings(tmp_path, {"quant_min_score": 55, "fusion_entry_threshold": 0.6})
    loaded = load_user_settings(tmp_path)
    assert loaded["quant_min_score"] == 55
    assert loaded["fusion_entry_threshold"] == 0.6
    assert "alpaca_api_key" not in loaded

    path = user_settings_path(tmp_path)
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert set(on_disk.keys()) <= {
        "quant_min_score",
        "screener_adx_min",
        "screener_rsi_low",
        "screener_rsi_high",
        "screener_volume_ratio_min",
        "fusion_entry_threshold",
        "premarket_screen_top_n",
        "max_positions",
        "max_position_pct",
        "daily_loss_limit_pct",
        "risk_per_trade",
        "universe_mode",
        "market_screener_max",
        "market_screener_min_price",
        "market_screener_actives_top",
        "market_screener_include_actives",
        "market_screener_include_actives_by_trades",
        "market_screener_include_movers",
        "market_screener_movers_min_pct",
        "market_screener_include_watchlist",
        "market_screener_include_spy_holdings",
        "market_screener_spy_holdings_top",
        "screener_mode",
    }


@pytest.mark.unit
def test_merged_config_user_file_overrides_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("FIRM_QUANT_MIN_SCORE", raising=False)
    monkeypatch.delenv("FIRM_FUSION_ENTRY_THRESHOLD", raising=False)
    monkeypatch.setenv("FIRM_DATA_DIR", str(tmp_path))
    save_user_settings(tmp_path, {"quant_min_score": 52, "fusion_entry_threshold": 0.45})
    cfg = FirmConfig.from_merged()
    assert cfg.quant_min_score == 52
    assert cfg.fusion_entry_threshold == 0.45


@pytest.mark.unit
def test_merged_config_env_overrides_user_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FIRM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("FIRM_QUANT_MIN_SCORE", "48")
    save_user_settings(tmp_path, {"quant_min_score": 52})
    cfg = FirmConfig.from_merged()
    assert cfg.quant_min_score == 48


@pytest.mark.unit
def test_reload_firm_config_mutates_singleton(tmp_path, monkeypatch):
    monkeypatch.setenv("FIRM_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("FIRM_QUANT_MIN_SCORE", raising=False)
    from firm.config import FIRM_CONFIG

    before = FIRM_CONFIG.quant_min_score
    save_user_settings(tmp_path, {"quant_min_score": before - 5})
    reload_firm_config()
    assert FIRM_CONFIG.quant_min_score == before - 5
    reset_user_settings(tmp_path)
    reload_firm_config()


@pytest.mark.unit
def test_firm_settings_api_get_and_patch(tmp_path, monkeypatch):
    from webapp.server import app

    monkeypatch.setenv("FIRM_DATA_DIR", str(tmp_path))
    for env_key in TUNABLE_ENV.values():
        monkeypatch.delenv(env_key, raising=False)
    reload_firm_config()

    client = TestClient(app)
    resp = client.get("/api/firm/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert "quant_min_score" in body["settings"]
    assert body["settings"]["quant_min_score"]["value"] == 60

    patch = client.patch(
        "/api/firm/settings",
        json={"settings": {"quant_min_score": 58, "screener_adx_min": 18}},
    )
    assert patch.status_code == 200
    updated = patch.json()
    assert updated["settings"]["quant_min_score"]["value"] == 58
    assert updated["settings"]["quant_min_score"]["source"] == "user_file"

    bad = client.patch("/api/firm/settings", json={"settings": {"quant_min_score": 999}})
    assert bad.status_code == 400

    reset = client.patch("/api/firm/settings", json={"reset": True})
    assert reset.status_code == 200
    assert reset.json()["settings"]["quant_min_score"]["source"] == "default"
    assert not user_settings_path(tmp_path).exists()
    reload_firm_config()
