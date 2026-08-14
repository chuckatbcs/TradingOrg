"""Alpaca REST client (sync httpx via requests) — paper default."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from firm.config import FIRM_CONFIG

logger = logging.getLogger(__name__)


class AlpacaOrderRejectedError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message
        super().__init__(f"Alpaca {status_code}: {message}")


def _headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": FIRM_CONFIG.alpaca_api_key,
        "APCA-API-SECRET-KEY": FIRM_CONFIG.alpaca_secret_key,
        "Content-Type": "application/json",
    }


def _request(method: str, url: str, **kwargs) -> Any:
    resp = requests.request(method, url, headers=_headers(), timeout=kwargs.pop("timeout", 15), **kwargs)
    if not resp.ok:
        detail = resp.text[:500]
        raise AlpacaOrderRejectedError(resp.status_code, detail or resp.reason)
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def get_account() -> dict:
    return _request("GET", f"{FIRM_CONFIG.alpaca_base_url}/v2/account")


def get_positions() -> list[dict]:
    data = _request("GET", f"{FIRM_CONFIG.alpaca_base_url}/v2/positions")
    return data if isinstance(data, list) else []


def get_position(symbol: str) -> dict | None:
    sym = symbol.strip().upper()
    try:
        return _request("GET", f"{FIRM_CONFIG.alpaca_base_url}/v2/positions/{sym}")
    except AlpacaOrderRejectedError as exc:
        if exc.status_code == 404:
            return None
        raise


def get_latest_quote(symbol: str) -> dict:
    data = _request(
        "GET",
        f"{FIRM_CONFIG.alpaca_data_url}/v2/stocks/{symbol}/quotes/latest",
        params={"feed": "iex"},
    )
    return data.get("quote", data) if isinstance(data, dict) else {}


def submit_order(
    symbol: str,
    qty: float,
    side: str,
    order_type: str = "market",
    time_in_force: str = "day",
) -> dict:
    if FIRM_CONFIG.trading_mode == "live" and not FIRM_CONFIG.allow_live_auto_execute:
        raise ValueError("SAFETY: live execution blocked — set FIRM_ALLOW_LIVE_AUTO_EXECUTE=true")

    payload = {
        "symbol": symbol.upper(),
        "qty": str(qty),
        "side": side.lower(),
        "type": order_type,
        "time_in_force": time_in_force,
    }
    logger.info("Submitting %s %s %s [%s]", side, qty, symbol, FIRM_CONFIG.trading_mode)
    return _request(
        "POST",
        f"{FIRM_CONFIG.alpaca_base_url}/v2/orders",
        json=payload,
    )


def close_position(symbol: str) -> dict:
    return _request(
        "DELETE",
        f"{FIRM_CONFIG.alpaca_base_url}/v2/positions/{symbol.upper()}",
    )


def cancel_all_orders() -> dict:
    return _request("DELETE", f"{FIRM_CONFIG.alpaca_base_url}/v2/orders")


def get_most_actives(*, top: int = 10, by: str = "volume") -> dict:
    """Top most-active US stocks by volume or trade count (Alpaca Screener API)."""
    return _request(
        "GET",
        f"{FIRM_CONFIG.alpaca_data_url}/v1beta1/screener/stocks/most-actives",
        params={"top": top, "by": by},
    )


def get_market_movers(*, market_type: str = "stocks", top: int = 10) -> dict:
    """Top gainers and losers (Alpaca Screener API)."""
    return _request(
        "GET",
        f"{FIRM_CONFIG.alpaca_data_url}/v1beta1/screener/{market_type}/movers",
        params={"top": top},
    )


def get_active_us_equities() -> list[dict]:
    """Tradable active US equity assets (Trading API — coarse universe fallback)."""
    data = _request(
        "GET",
        f"{FIRM_CONFIG.alpaca_base_url}/v2/assets",
        params={"status": "active", "asset_class": "us_equity"},
        timeout=30,
    )
    return data if isinstance(data, list) else []


def get_bars(symbol: str, days: int = 400) -> list[dict]:
    start = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    data = _request(
        "GET",
        f"{FIRM_CONFIG.alpaca_data_url}/v2/stocks/{symbol}/bars",
        params={
            "timeframe": "1Day",
            "limit": days,
            "adjustment": "split",
            "feed": "iex",
            "start": start,
        },
        timeout=30,
    )
    bars = data.get("bars") if isinstance(data, dict) else None
    return bars if isinstance(bars, list) else []
