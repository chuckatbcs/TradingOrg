"""Seed watchlists from Gemini TICKER_POOL + trading-platform curated names."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Gemini-Trading-Firm core 30
TICKER_POOL: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "NFLX", "JPM", "AMD",
    "V", "DIS", "WMT", "PG", "UNH", "HD", "BAC", "MA", "XOM", "JNJ",
    "AVGO", "COST", "MRK", "CRM", "ADBE", "ABT", "NKE", "PEP", "TSM", "ASML",
)

# trading-platform SCREEN_UNIVERSE extras (subset for expansion)
CURATED_EXTRA: tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "DIA", "ORCL", "INTC", "QCOM", "TXN", "LLY", "ABBV",
    "KO", "CVX", "GS", "MS", "CSCO", "IBM", "AMAT", "LRCX", "MU", "PANW",
)

WATCHLIST_EXTRA_KEY = "watchlist_extra"

# Static fallback when yfinance holdings are unavailable (approx. SPY top weights).
_SPY_TOP_HOLDINGS_FALLBACK: tuple[str, ...] = (
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "BRK-B", "AVGO", "TSLA", "JPM",
)

SECTOR_MAP: dict[str, str] = {
    "AAPL": "Technology", "MSFT": "Technology", "NVDA": "Technology",
    "AMZN": "Consumer", "GOOGL": "Technology", "META": "Technology",
    "TSLA": "Consumer", "NFLX": "Communication", "JPM": "Financial",
    "AMD": "Technology", "V": "Financial", "DIS": "Communication",
    "WMT": "Consumer", "PG": "Consumer", "UNH": "Healthcare",
    "HD": "Consumer", "BAC": "Financial", "MA": "Financial",
    "XOM": "Energy", "JNJ": "Healthcare", "AVGO": "Technology",
    "COST": "Consumer", "MRK": "Healthcare", "CRM": "Technology",
    "ADBE": "Technology", "ABT": "Healthcare", "NKE": "Consumer",
    "PEP": "Consumer", "TSM": "Technology", "ASML": "Technology",
}

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,12}$")


def _dedupe(symbols: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for sym in symbols:
        upper = sym.strip().upper()
        if upper and upper not in seen:
            seen.add(upper)
            out.append(upper)
    return out


def parse_ticker_symbols(raw: Any) -> list[str]:
    """Normalize comma/space/newline-separated tickers or a JSON list."""
    if raw is None:
        return []
    if isinstance(raw, list):
        items = [str(x) for x in raw]
    else:
        items = re.split(r"[\s,;]+", str(raw).strip())
    out: list[str] = []
    for item in items:
        sym = item.strip().upper()
        if not sym:
            continue
        if not _TICKER_RE.match(sym):
            raise ValueError(f"invalid ticker symbol: {item!r}")
        out.append(sym)
    return _dedupe(out)


def default_watchlist() -> list[str]:
    """Static seed list only (Gemini core + curated extras)."""
    return _dedupe([*TICKER_POOL, *CURATED_EXTRA])


def _user_settings_path(data_dir: Path) -> Path:
    return data_dir / "user_settings.json"


def load_watchlist_extra(data_dir: Path | None = None) -> list[str]:
    """User-added tickers from user_settings.json (watchlist_extra key)."""
    if data_dir is None:
        from firm.config import FIRM_CONFIG

        data_dir = FIRM_CONFIG.data_dir
    path = _user_settings_path(data_dir)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    try:
        return parse_ticker_symbols(data.get(WATCHLIST_EXTRA_KEY))
    except ValueError:
        return []


def save_watchlist_extra(data_dir: Path, symbols: list[str]) -> None:
    """Persist user extra tickers into user_settings.json."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = _user_settings_path(data_dir)
    current: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                current = raw
        except (json.JSONDecodeError, OSError):
            current = {}
    normalized = parse_ticker_symbols(symbols)
    if normalized:
        current[WATCHLIST_EXTRA_KEY] = normalized
    elif WATCHLIST_EXTRA_KEY in current:
        del current[WATCHLIST_EXTRA_KEY]
    if current:
        path.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


def spy_top_holdings(limit: int = 10) -> list[str]:
    """Universe expansion hook: merge top SPY holdings (live fetch with static fallback)."""
    limit = max(1, min(limit, 50))
    try:
        import yfinance as yf

        ticker = yf.Ticker("SPY")
        holdings = getattr(getattr(ticker, "funds_data", None), "top_holdings", None)
        if holdings is not None and not holdings.empty:
            symbols = [str(s).upper() for s in holdings.index[:limit]]
            return _dedupe(symbols)
    except Exception:
        pass
    return list(_SPY_TOP_HOLDINGS_FALLBACK[:limit])


def resolve_watchlist(
    data_dir: Path | None = None,
    *,
    include_spy_holdings: int = 0,
) -> list[str]:
    """Merged screening universe: seed + user extras + optional SPY top holdings."""
    symbols = default_watchlist()
    user_extra = load_watchlist_extra(data_dir)
    if user_extra:
        symbols = _dedupe([*symbols, *user_extra])
    if include_spy_holdings > 0:
        symbols = _dedupe([*symbols, *spy_top_holdings(include_spy_holdings)])
    return symbols


def watchlist_metadata(
    data_dir: Path | None = None,
    *,
    include_spy_holdings: int = 0,
) -> dict[str, Any]:
    """Structured watchlist info for API/UI."""
    seed = default_watchlist()
    user_extra = load_watchlist_extra(data_dir)
    spy_extra: list[str] = []
    if include_spy_holdings > 0:
        spy_extra = [s for s in spy_top_holdings(include_spy_holdings) if s not in seed]
    symbols = resolve_watchlist(data_dir, include_spy_holdings=include_spy_holdings)
    return {
        "symbols": symbols,
        "count": len(symbols),
        "sources": {
            "gemini_core": {
                "count": len(TICKER_POOL),
                "description": "Gemini-Trading-Firm core 30 (large-cap growth/value mix)",
            },
            "curated_extra": {
                "count": len(CURATED_EXTRA),
                "description": "Trading-platform SCREEN_UNIVERSE extras (ETFs + semis/health/fin)",
            },
            "user_extra": {
                "count": len(user_extra),
                "symbols": user_extra,
                "description": "User-added tickers from Firm Settings (user_settings.json)",
            },
            "spy_holdings": {
                "count": len(spy_extra),
                "symbols": spy_extra,
                "enabled": include_spy_holdings > 0,
                "description": "Optional SPY top-holdings expansion hook (off by default)",
            },
        },
        "static_seed_count": len(seed),
        "expansion": "static only — not Russell-1000 or full Alpaca tradable universe",
        "note": (
            "Screener runs on this curated list before LLM analysis. "
            "Add tickers in Firm Settings or set watchlist_extra in user_settings.json."
        ),
    }


def sector_for(ticker: str) -> str:
    return SECTOR_MAP.get(ticker.upper(), "Other")
