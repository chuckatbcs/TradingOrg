"""Alpaca position sync into local SQLite state."""

from __future__ import annotations

import logging

from firm.config import FIRM_CONFIG
from firm.execution import alpaca
from firm.storage.db import FirmDB
from firm.universe.watchlist import sector_for

logger = logging.getLogger(__name__)


def sync_positions() -> list[dict]:
    """Pull Alpaca positions and persist to firm.db."""
    if not FIRM_CONFIG.can_auto_execute():
        logger.debug("Alpaca not configured — skipping position sync")
        return FirmDB().list_positions()

    try:
        raw = alpaca.get_positions()
    except Exception:
        logger.exception("position sync failed")
        return FirmDB().list_positions()

    positions: list[dict] = []
    for p in raw:
        symbol = str(p.get("symbol", "")).upper()
        if not symbol:
            continue
        positions.append({
            "ticker": symbol,
            "qty": float(p.get("qty", 0) or 0),
            "avg_entry": float(p.get("avg_entry_price", 0) or 0),
            "market_value": float(p.get("market_value", 0) or 0),
            "unrealized_pl": float(p.get("unrealized_pl", 0) or 0),
            "sector": sector_for(symbol),
        })

    FirmDB().upsert_positions(positions)
    return positions
