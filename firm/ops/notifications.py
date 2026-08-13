"""Optional Discord webhook notifications."""

from __future__ import annotations

import logging

import requests

from firm.config import FIRM_CONFIG

logger = logging.getLogger(__name__)


def notify(title: str, body: str, *, color: int = 0x2F81F7) -> bool:
    """Post a Discord embed if DISCORD_WEBHOOK_URL is set."""
    url = FIRM_CONFIG.discord_webhook_url
    if not url:
        return False
    payload = {
        "embeds": [{
            "title": title,
            "description": body[:4000],
            "color": color,
        }],
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception:
        logger.exception("discord notify failed")
        return False


def notify_fused_signal(ticker: str, fused_pass: bool, score: float, blockers: list[str]) -> bool:
    status = "PASS" if fused_pass else "BLOCKED"
    color = 0x3FB950 if fused_pass else 0xF85149
    body = f"**{ticker}** fused_score={score:.3f}\n"
    if blockers:
        body += "Blockers: " + "; ".join(blockers[:5])
    return notify(f"Firm Signal {status}", body, color=color)


def notify_execution(ticker: str, side: str, qty: float, status: str, reason: str = "") -> bool:
    color = 0x3FB950 if status == "placed" else 0xD29922
    body = f"{side.upper()} {qty} {ticker} — {status}"
    if reason:
        body += f"\n{reason}"
    return notify("Firm Execution", body, color=color)
