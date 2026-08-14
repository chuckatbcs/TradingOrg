"""Tests for Alpaca-backed market screener (stage 1)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from firm.execution.alpaca import AlpacaOrderRejectedError
from firm.universe.market_screener import resolve_screening_universe, scan_market


def _default_scan_config(**overrides):
    base = dict(
        market_screener_max=200,
        market_screener_actives_top=100,
        market_screener_min_price=5.0,
        market_screener_movers_min_pct=2.0,
        market_screener_include_actives=True,
        market_screener_include_actives_by_trades=False,
        market_screener_include_movers=True,
        market_screener_include_watchlist=False,
        market_screener_include_spy_holdings=False,
        market_screener_spy_holdings_top=15,
    )
    base.update(overrides)
    return MagicMock(**base)


@pytest.mark.unit
def test_scan_market_merges_actives_and_movers(monkeypatch):
    monkeypatch.setattr(
        "firm.universe.market_screener.get_most_actives",
        lambda **kw: {
            "most_actives": [
                {"symbol": "AAPL", "volume": 100, "trade_count": 10},
                {"symbol": "MSFT", "volume": 90, "trade_count": 9},
            ],
            "last_updated": "2026-07-02T12:00:00Z",
        },
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.get_market_movers",
        lambda **kw: {
            "gainers": [{"symbol": "NVDA", "price": 120.0, "percent_change": 5.0, "change": 6.0}],
            "losers": [
                {"symbol": "PENNY", "price": 1.5, "percent_change": -10.0, "change": -0.15},
                {"symbol": "WEAK", "price": 20.0, "percent_change": -1.0, "change": -0.2},
            ],
            "last_updated": "2026-07-02T12:00:00Z",
        },
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.FIRM_CONFIG",
        _default_scan_config(),
    )

    result = scan_market()

    assert result.source == "alpaca_screener"
    assert result.symbols == ["AAPL", "MSFT", "NVDA"]
    assert "PENNY" not in result.symbols
    assert "WEAK" not in result.symbols
    assert result.stage1["scans"]["actives_volume"]["count"] == 2
    assert result.stage1["scans"]["movers"]["count"] == 1


@pytest.mark.unit
def test_scan_market_includes_trades_scan_and_watchlist(monkeypatch):
    calls: list[str] = []

    def fake_actives(**kw):
        calls.append(kw.get("by", "volume"))
        if kw.get("by") == "trades":
            return {
                "most_actives": [{"symbol": "COIN", "volume": 1, "trade_count": 99}],
                "last_updated": "2026-07-02T12:00:00Z",
            }
        return {
            "most_actives": [{"symbol": "AAPL", "volume": 100, "trade_count": 10}],
            "last_updated": "2026-07-02T12:00:00Z",
        }

    monkeypatch.setattr("firm.universe.market_screener.get_most_actives", fake_actives)
    monkeypatch.setattr(
        "firm.universe.market_screener.get_market_movers",
        lambda **kw: {"gainers": [], "losers": [], "last_updated": "2026-07-02T12:00:00Z"},
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.resolve_watchlist",
        lambda data_dir=None: ["PLTR"],
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.FIRM_CONFIG",
        _default_scan_config(
            market_screener_include_actives_by_trades=True,
            market_screener_include_movers=False,
            market_screener_include_watchlist=True,
        ),
    )

    result = scan_market()

    assert calls == ["volume", "trades"]
    assert result.symbols == ["AAPL", "COIN", "PLTR"]
    assert result.stage1["scans"]["watchlist"]["count"] == 1


@pytest.mark.unit
def test_scan_market_respects_max_cap(monkeypatch):
    monkeypatch.setattr(
        "firm.universe.market_screener.get_most_actives",
        lambda **kw: {
            "most_actives": [{"symbol": f"T{i}", "volume": i, "trade_count": i} for i in range(10)],
            "last_updated": "2026-07-02T12:00:00Z",
        },
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.get_market_movers",
        lambda **kw: {"gainers": [], "losers": [], "last_updated": "2026-07-02T12:00:00Z"},
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.FIRM_CONFIG",
        _default_scan_config(
            market_screener_max=3,
            market_screener_actives_top=10,
            market_screener_include_movers=False,
        ),
    )

    result = scan_market()

    assert len(result.symbols) == 3
    assert result.symbols == ["T0", "T1", "T2"]


@pytest.mark.unit
def test_resolve_screening_universe_watchlist_mode(monkeypatch):
    monkeypatch.setattr(
        "firm.universe.market_screener.FIRM_CONFIG",
        MagicMock(universe_mode="watchlist"),
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.resolve_watchlist",
        lambda data_dir=None: ["AAPL", "MSFT"],
    )

    symbols, meta = resolve_screening_universe()

    assert symbols == ["AAPL", "MSFT"]
    assert meta["mode"] == "watchlist"
    assert meta["fallback"] is False


@pytest.mark.unit
def test_resolve_screening_universe_market_mode(monkeypatch):
    monkeypatch.setattr(
        "firm.universe.market_screener.FIRM_CONFIG",
        MagicMock(
            universe_mode="market",
            alpaca_api_key="key",
            alpaca_secret_key="secret",
        ),
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.scan_market",
        lambda data_dir=None: MagicMock(
            symbols=["AAPL", "NVDA"],
            source="alpaca_screener",
            fallback=False,
            stage1={"final_count": 2},
        ),
    )

    symbols, meta = resolve_screening_universe()

    assert symbols == ["AAPL", "NVDA"]
    assert meta["mode"] == "market"
    assert meta["source"] == "alpaca_screener"


@pytest.mark.unit
def test_resolve_screening_universe_falls_back_on_alpaca_error(monkeypatch):
    monkeypatch.setattr(
        "firm.universe.market_screener.FIRM_CONFIG",
        MagicMock(
            universe_mode="market",
            alpaca_api_key="key",
            alpaca_secret_key="secret",
        ),
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.scan_market",
        lambda data_dir=None: (_ for _ in ()).throw(AlpacaOrderRejectedError(403, "forbidden")),
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.resolve_watchlist",
        lambda data_dir=None: ["SPY"],
    )

    symbols, meta = resolve_screening_universe()

    assert symbols == ["SPY"]
    assert meta["mode"] == "watchlist"
    assert meta["fallback"] is True
    assert meta["source"] == "watchlist_fallback"


@pytest.mark.unit
def test_resolve_screening_universe_missing_keys_falls_back(monkeypatch):
    monkeypatch.setattr(
        "firm.universe.market_screener.FIRM_CONFIG",
        MagicMock(
            universe_mode="market",
            alpaca_api_key="",
            alpaca_secret_key="",
        ),
    )
    monkeypatch.setattr(
        "firm.universe.market_screener.resolve_watchlist",
        lambda data_dir=None: ["QQQ"],
    )

    symbols, meta = resolve_screening_universe()

    assert symbols == ["QQQ"]
    assert meta["reason"] == "missing_alpaca_keys"
