"""Tests for firm.config."""

import pytest

from firm.config import FirmConfig


@pytest.mark.unit
def test_firm_config_defaults():
    cfg = FirmConfig()
    assert cfg.trading_mode == "paper"
    assert cfg.allow_live_auto_execute is False
    assert cfg.max_positions == 15
    assert cfg.fusion_entry_threshold == 0.5


@pytest.mark.unit
def test_firm_config_from_env(monkeypatch):
    monkeypatch.setenv("FIRM_TRADING_MODE", "paper")
    monkeypatch.setenv("FIRM_ALLOW_LIVE_AUTO_EXECUTE", "false")
    monkeypatch.setenv("FIRM_FUSION_ENTRY_THRESHOLD", "0.6")
    monkeypatch.setenv("FIRM_SCREENER_VOLUME_RATIO_MIN", "1.0")
    cfg = FirmConfig.from_env()
    assert cfg.trading_mode == "paper"
    assert cfg.allow_live_auto_execute is False
    assert cfg.fusion_entry_threshold == 0.6
    assert cfg.screener_volume_ratio_min == 1.0


@pytest.mark.unit
def test_can_auto_execute_requires_keys(monkeypatch):
    cfg = FirmConfig(trading_mode="paper", alpaca_api_key="", alpaca_secret_key="")
    assert cfg.can_auto_execute() is False

    cfg2 = FirmConfig(
        trading_mode="paper",
        alpaca_api_key="key",
        alpaca_secret_key="secret",
    )
    assert cfg2.can_auto_execute() is True


@pytest.mark.unit
def test_live_blocked_without_flag(monkeypatch):
    cfg = FirmConfig(
        trading_mode="live",
        allow_live_auto_execute=False,
        alpaca_api_key="key",
        alpaca_secret_key="secret",
    )
    assert cfg.can_auto_execute() is False

    cfg.allow_live_auto_execute = True
    assert cfg.can_auto_execute() is True
