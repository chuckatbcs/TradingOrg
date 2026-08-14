"""Stage-1 market-wide screener via Alpaca Screener API (multi-scan union)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from firm.config import FIRM_CONFIG
from firm.execution.alpaca import AlpacaOrderRejectedError, get_market_movers, get_most_actives
from firm.universe.watchlist import resolve_watchlist, spy_top_holdings

logger = logging.getLogger(__name__)


@dataclass
class MarketScanResult:
    symbols: list[str]
    source: str
    fallback: bool = False
    error: str | None = None
    stage1: dict[str, Any] = field(default_factory=dict)


def _dedupe(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sym in symbols:
        upper = sym.strip().upper()
        if upper and upper not in seen:
            seen.add(upper)
            out.append(upper)
    return out


def _symbols_from_actives(resp: dict[str, Any]) -> list[str]:
    actives = resp.get("most_actives") or []
    return [
        a["symbol"]
        for a in actives
        if isinstance(a, dict) and a.get("symbol")
    ]


def _symbols_from_movers(
    resp: dict[str, Any],
    *,
    min_price: float = 0,
    min_pct: float = 0,
) -> list[str]:
    gainers = resp.get("gainers") or []
    losers = resp.get("losers") or []
    rows = [r for r in (*gainers, *losers) if isinstance(r, dict) and r.get("symbol")]
    if min_price > 0:
        rows = [r for r in rows if float(r.get("price") or 0) >= min_price]
    if min_pct > 0:
        rows = [
            r for r in rows
            if abs(float(r.get("percent_change") or 0)) >= min_pct
        ]
    return [r["symbol"] for r in rows]


def scan_market(data_dir=None) -> MarketScanResult:
    """Coarse market scan: union of enabled Alpaca scans + optional watchlist/SPY."""
    cfg = FIRM_CONFIG
    max_n = max(1, cfg.market_screener_max)
    actives_top = max(1, min(cfg.market_screener_actives_top, 100))
    min_price = cfg.market_screener_min_price
    movers_top = max(1, min(50, max_n))

    symbols: list[str] = []
    stage1: dict[str, Any] = {
        "scans": {},
        "actives_top": actives_top,
        "movers_top": movers_top,
        "min_price": min_price,
        "movers_min_pct": cfg.market_screener_movers_min_pct,
        "max": max_n,
    }

    if cfg.market_screener_include_actives:
        actives_resp = get_most_actives(top=actives_top, by="volume")
        active_symbols = _symbols_from_actives(actives_resp)
        symbols.extend(active_symbols)
        stage1["scans"]["actives_volume"] = {
            "enabled": True,
            "count": len(active_symbols),
            "last_updated": actives_resp.get("last_updated"),
        }

    if cfg.market_screener_include_actives_by_trades:
        trades_resp = get_most_actives(top=actives_top, by="trades")
        trade_symbols = _symbols_from_actives(trades_resp)
        symbols.extend(trade_symbols)
        stage1["scans"]["actives_trades"] = {
            "enabled": True,
            "count": len(trade_symbols),
            "last_updated": trades_resp.get("last_updated"),
        }

    if cfg.market_screener_include_movers:
        movers_resp = get_market_movers(market_type="stocks", top=movers_top)
        mover_symbols = _symbols_from_movers(
            movers_resp,
            min_price=min_price,
            min_pct=cfg.market_screener_movers_min_pct,
        )
        symbols.extend(mover_symbols)
        stage1["scans"]["movers"] = {
            "enabled": True,
            "count": len(mover_symbols),
            "min_pct": cfg.market_screener_movers_min_pct,
            "last_updated": movers_resp.get("last_updated"),
        }

    if cfg.market_screener_include_watchlist:
        watchlist_symbols = resolve_watchlist(data_dir)
        symbols.extend(watchlist_symbols)
        stage1["scans"]["watchlist"] = {
            "enabled": True,
            "count": len(watchlist_symbols),
        }

    if cfg.market_screener_include_spy_holdings:
        spy_symbols = spy_top_holdings(cfg.market_screener_spy_holdings_top)
        symbols.extend(spy_symbols)
        stage1["scans"]["spy_holdings"] = {
            "enabled": True,
            "count": len(spy_symbols),
            "top": cfg.market_screener_spy_holdings_top,
        }

    symbols = _dedupe(symbols)[:max_n]
    stage1["union_count"] = len(symbols)
    stage1["final_count"] = len(symbols)

    return MarketScanResult(
        symbols=symbols,
        source="alpaca_screener",
        stage1=stage1,
    )


def resolve_screening_universe(data_dir=None) -> tuple[list[str], dict[str, Any]]:
    """Resolve stage-1 universe: market multi-scan union or static watchlist."""
    cfg = FIRM_CONFIG
    mode = cfg.universe_mode

    if mode == "market":
        if not (cfg.alpaca_api_key and cfg.alpaca_secret_key):
            logger.warning("market universe requested but Alpaca keys missing — using watchlist")
            symbols = resolve_watchlist(data_dir)
            return symbols, {
                "mode": "watchlist",
                "source": "watchlist_fallback",
                "fallback": True,
                "reason": "missing_alpaca_keys",
                "count": len(symbols),
            }
        try:
            scan = scan_market(data_dir)
            return scan.symbols, {
                "mode": "market",
                "source": scan.source,
                "fallback": scan.fallback,
                "count": len(scan.symbols),
                "stage1": scan.stage1,
            }
        except (AlpacaOrderRejectedError, OSError, ValueError) as exc:
            logger.warning("Alpaca market screener failed (%s) — falling back to watchlist", exc)
            symbols = resolve_watchlist(data_dir)
            return symbols, {
                "mode": "watchlist",
                "source": "watchlist_fallback",
                "fallback": True,
                "reason": str(exc)[:200],
                "count": len(symbols),
            }

    symbols = resolve_watchlist(data_dir)
    return symbols, {
        "mode": "watchlist",
        "source": "watchlist",
        "fallback": False,
        "count": len(symbols),
    }
