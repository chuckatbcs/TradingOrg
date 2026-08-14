"""Deterministic structured summaries for saved agent reports.

The graph still stores each agent's markdown report as the source of truth.
This module adds a comparable envelope around that prose without making
another LLM call, so old and new run records can be summarized the same way.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from tradingagents.agents.utils.context_budget import truncate_text
from tradingagents.agents.utils.rating import parse_rating

ParseStatus = Literal["parsed", "partial", "empty"]

_SECTION_META: dict[str, tuple[str, str]] = {
    "market_report": ("Market Analyst", "analyst"),
    "sentiment_report": ("Sentiment Analyst", "analyst"),
    "news_report": ("News Analyst", "analyst"),
    "fundamentals_report": ("Fundamentals Analyst", "analyst"),
    "bull_history": ("Bull Researcher", "bull"),
    "bear_history": ("Bear Researcher", "bear"),
    "research_judge": ("Research Manager", "manager"),
    "trader_investment_plan": ("Trader", "trader"),
    "risk_history": ("Risk Analysts", "risk"),
    "risk_judge": ("Portfolio Manager", "portfolio"),
    "final_trade_decision": ("Final Decision", "decision"),
}

_RATING_TO_STANCE = {
    "Buy": "Bullish",
    "Overweight": "Bullish",
    "Hold": "Neutral",
    "Underweight": "Bearish",
    "Sell": "Bearish",
}

_CONFIDENCE_WORDS = {
    "very high": 0.9,
    "high": 0.75,
    "medium": 0.5,
    "moderate": 0.5,
    "low": 0.25,
    "very low": 0.1,
}


class StructuredAgentReport(BaseModel):
    """Comparable envelope extracted from one markdown report."""

    agent: str
    role: str
    section_key: str
    ticker: str | None = None
    trade_date: str | None = None
    stance: str | None = None
    rating: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    thesis_summary: str | None = None
    key_points: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    catalysts: list[str] = Field(default_factory=list)
    time_horizon: str | None = None
    price_target: float | None = None
    stop_loss: float | None = None
    recommended_action: str | None = None
    raw_text: str = ""
    parse_status: ParseStatus = "empty"
    parse_warnings: list[str] = Field(default_factory=list)


class BullBearComparison(BaseModel):
    """Side-by-side summary for the investment debate."""

    ticker: str | None = None
    trade_date: str | None = None
    available: bool
    bull: StructuredAgentReport | None = None
    bear: StructuredAgentReport | None = None
    disagreements: list[str] = Field(default_factory=list)
    parse_warnings: list[str] = Field(default_factory=list)


def structure_reports(
    reports: dict[str, str],
    *,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> dict[str, dict]:
    """Extract comparable fields from all available report sections."""

    structured: dict[str, dict] = {}
    for section_key, text in (reports or {}).items():
        structured[section_key] = structure_report(
            section_key,
            text,
            ticker=ticker,
            trade_date=trade_date,
        ).model_dump()
    return structured


def structure_report(
    section_key: str,
    text: str | None,
    *,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> StructuredAgentReport:
    """Extract a structured report envelope from one raw markdown report."""

    agent, role = _SECTION_META.get(section_key, (section_key, "agent"))
    raw_text = str(text or "").strip()
    if not raw_text:
        return StructuredAgentReport(
            agent=agent,
            role=role,
            section_key=section_key,
            ticker=ticker,
            trade_date=trade_date,
            raw_text="",
            parse_status="empty",
            parse_warnings=["empty_report"],
        )

    warnings: list[str] = []
    rating = _extract_rating(raw_text, role)
    stance = _extract_stance(raw_text, section_key, role, rating)
    confidence = _extract_confidence(raw_text)
    thesis_summary = _extract_summary(raw_text)
    key_points = _first_nonempty_items(
        raw_text,
        [
            "key points",
            "summary table",
            "technical picture",
            "market trends",
            "fundamental picture",
            "dominant themes",
        ],
    )
    evidence = _first_nonempty_items(
        raw_text,
        [
            "evidence",
            "supporting evidence",
            "positive indicators",
            "negative indicators",
            "data sources",
            "rationale",
        ],
    )
    risks = _first_nonempty_items(
        raw_text,
        ["risks", "risk", "downsides", "downside", "concerns", "challenges"],
    )
    catalysts = _first_nonempty_items(
        raw_text,
        ["catalysts", "upside", "growth potential", "drivers", "opportunities"],
    )
    recommended_action = _extract_text_value(
        raw_text,
        ["recommended action", "strategic actions", "action", "final transaction proposal"],
    )
    if not recommended_action and rating:
        recommended_action = rating

    time_horizon = _extract_text_value(raw_text, ["time horizon", "holding period", "horizon"])
    price_target = _extract_float_value(raw_text, ["price target", "target price"])
    stop_loss = _extract_float_value(raw_text, ["stop loss", "stop-loss", "risk level"])

    if not key_points:
        key_points = _fallback_bullets(raw_text)
    if not evidence:
        evidence = key_points[:3]
    if not thesis_summary:
        thesis_summary = _first_paragraph(raw_text)

    comparable_count = sum(
        bool(value)
        for value in (
            stance,
            rating,
            confidence,
            thesis_summary,
            key_points,
            evidence,
            risks,
            catalysts,
            recommended_action,
        )
    )
    parse_status: ParseStatus = "parsed" if comparable_count >= 3 else "partial"
    if stance is None and rating is None:
        warnings.append("no_stance_or_rating_found")
    if not key_points and not evidence:
        warnings.append("no_evidence_points_found")

    return StructuredAgentReport(
        agent=agent,
        role=role,
        section_key=section_key,
        ticker=ticker,
        trade_date=trade_date,
        stance=stance,
        rating=rating,
        confidence=confidence,
        thesis_summary=thesis_summary,
        key_points=key_points[:6],
        evidence=evidence[:6],
        risks=risks[:6],
        catalysts=catalysts[:6],
        time_horizon=time_horizon,
        price_target=price_target,
        stop_loss=stop_loss,
        recommended_action=recommended_action,
        raw_text=raw_text,
        parse_status=parse_status,
        parse_warnings=warnings,
    )


def build_bull_bear_comparison(
    structured_reports: dict[str, dict] | None,
    *,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> dict:
    """Build a compact side-by-side bull/bear debate comparison."""

    reports = structured_reports or {}
    bull = _coerce_report(reports.get("bull_history"))
    bear = _coerce_report(reports.get("bear_history"))
    warnings: list[str] = []
    disagreements: list[str] = []

    if not bull:
        warnings.append("missing_bull_report")
    if not bear:
        warnings.append("missing_bear_report")

    if bull and bear:
        if bull.stance and bear.stance and bull.stance != bear.stance:
            disagreements.append(f"Stance differs: bull is {bull.stance}, bear is {bear.stance}.")
        if bull.rating and bear.rating and bull.rating != bear.rating:
            disagreements.append(f"Rating differs: bull is {bull.rating}, bear is {bear.rating}.")
        if bull.price_target is not None and bear.price_target is not None:
            spread = bull.price_target - bear.price_target
            disagreements.append(f"Price targets differ by {spread:.2f}.")
        if bull.evidence:
            disagreements.append(f"Bull emphasizes: {bull.evidence[0]}")
        if bear.risks:
            disagreements.append(f"Bear emphasizes: {bear.risks[0]}")
        elif bear.evidence:
            disagreements.append(f"Bear emphasizes: {bear.evidence[0]}")

    comparison = BullBearComparison(
        ticker=ticker or (bull.ticker if bull else None) or (bear.ticker if bear else None),
        trade_date=trade_date
        or (bull.trade_date if bull else None)
        or (bear.trade_date if bear else None),
        available=bool(bull and bear),
        bull=bull,
        bear=bear,
        disagreements=disagreements[:6],
        parse_warnings=warnings,
    )
    return comparison.model_dump()


def format_report_brief(
    section_key: str,
    text: str | None,
    *,
    ticker: str | None = None,
    trade_date: str | None = None,
) -> str:
    """Render a deterministic compact brief for downstream prompt handoffs."""

    report = structure_report(
        section_key,
        text,
        ticker=ticker,
        trade_date=trade_date,
    )
    lines = [f"### {report.agent}"]
    descriptors = []
    if report.stance:
        descriptors.append(f"Stance: {report.stance}")
    if report.rating:
        descriptors.append(f"Rating: {report.rating}")
    if report.confidence is not None:
        descriptors.append(f"Confidence: {report.confidence:.2f}")
    if report.recommended_action:
        descriptors.append(f"Action: {report.recommended_action}")
    if descriptors:
        lines.append("; ".join(descriptors))
    if report.thesis_summary:
        lines.append(f"Thesis: {report.thesis_summary}")
    _append_items(lines, "Key Points", report.key_points)
    _append_items(lines, "Evidence", report.evidence)
    _append_items(lines, "Risks", report.risks)
    _append_items(lines, "Catalysts", report.catalysts)
    if report.parse_warnings:
        lines.append(f"Parser notes: {', '.join(report.parse_warnings)}")
    return "\n".join(lines)


def format_source_report_briefs(
    reports: dict[str, str],
    *,
    ticker: str | None = None,
    trade_date: str | None = None,
    max_chars: int = 4500,
) -> str:
    """Build compact analyst-source context without another LLM summary pass."""

    ordered_sections = (
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
    )
    parts = [
        (
            "Compact deterministic source briefs for prompt efficiency. "
            "Full source reports remain available in run reports/logs."
        )
    ]
    for section_key in ordered_sections:
        text = reports.get(section_key)
        if not text:
            continue
        parts.append(
            format_report_brief(
                section_key,
                text,
                ticker=ticker,
                trade_date=trade_date,
            )
        )
    return truncate_text("\n\n".join(parts), max_chars, "source report briefs")


def _coerce_report(value: dict | StructuredAgentReport | None) -> StructuredAgentReport | None:
    if value is None:
        return None
    if isinstance(value, StructuredAgentReport):
        return value
    try:
        return StructuredAgentReport(**value)
    except Exception:
        return None


def _append_items(lines: list[str], label: str, items: list[str], *, limit: int = 3) -> None:
    values = [item for item in items if item][:limit]
    if not values:
        return
    lines.append(f"{label}:")
    lines.extend(f"- {item}" for item in values)


def _extract_rating(text: str, role: str) -> str | None:
    explicit = _extract_text_value(
        text,
        ["rating", "recommendation", "action", "final transaction proposal"],
    )
    if explicit:
        parsed = parse_rating(explicit, default="")
        if parsed:
            return parsed
    if role in {"bull", "bear"}:
        return None
    parsed = parse_rating(text, default="")
    return parsed or None


def _extract_stance(
    text: str,
    section_key: str,
    role: str,
    rating: str | None,
) -> str | None:
    explicit = _extract_text_value(
        text,
        ["stance", "outlook", "overall sentiment", "overall band", "sentiment"],
    )
    if explicit:
        stance = _normalize_stance(explicit)
        if stance:
            return stance
    if rating:
        return _RATING_TO_STANCE.get(rating)
    if section_key == "bull_history" or role == "bull":
        return "Bullish"
    if section_key == "bear_history" or role == "bear":
        return "Bearish"
    return _normalize_stance(text[:600])


def _normalize_stance(value: str) -> str | None:
    value_l = value.lower()
    if "bearish" in value_l or "negative" in value_l or "cautious" in value_l:
        return "Bearish"
    if "bullish" in value_l or "positive" in value_l or "constructive" in value_l:
        return "Bullish"
    if "neutral" in value_l or "mixed" in value_l or "balanced" in value_l:
        return "Neutral"
    return None


def _extract_confidence(text: str) -> float | None:
    value = _extract_text_value(text, ["confidence", "conviction"])
    if not value:
        return None
    value_l = value.lower()
    for word, score in _CONFIDENCE_WORDS.items():
        if word in value_l:
            return score
    match = re.search(r"(\d+(?:\.\d+)?)\s*/\s*(10|100)\b", value_l)
    if match:
        number = float(match.group(1))
        scale = float(match.group(2))
        return _clamp(number / scale)
    match = re.search(r"(\d+(?:\.\d+)?)\s*%", value_l)
    if match:
        return _clamp(float(match.group(1)) / 100.0)
    match = re.search(r"\b(0(?:\.\d+)?|1(?:\.0+)?)\b", value_l)
    if match:
        return _clamp(float(match.group(1)))
    return None


def _extract_summary(text: str) -> str | None:
    value = _extract_text_value(
        text,
        [
            "thesis summary",
            "executive summary",
            "summary",
            "rationale",
            "investment thesis",
            "reasoning",
        ],
    )
    return value or None


def _extract_float_value(text: str, labels: list[str]) -> float | None:
    value = _extract_text_value(text, labels)
    if not value:
        return None
    match = re.search(r"[-+]?\$?\s*(\d+(?:,\d{3})*(?:\.\d+)?)", value)
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def _extract_text_value(text: str, labels: list[str]) -> str | None:
    block = _extract_labeled_block(text, labels, max_lines=6)
    if not block:
        return None
    items = _items_from_block(block, max_items=1)
    if items:
        return items[0]
    cleaned = _clean_text(" ".join(block))
    return cleaned or None


def _first_nonempty_items(text: str, labels: list[str]) -> list[str]:
    block = _extract_labeled_block(text, labels, max_lines=12)
    if block:
        items = _items_from_block(block)
        if items:
            return items
    return []


def _extract_labeled_block(text: str, labels: list[str], *, max_lines: int) -> list[str]:
    wanted = {_normalize_label(label) for label in labels}
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        label, inline_value = _line_label_and_value(line)
        if not label:
            continue
        normalized = _normalize_label(label)
        if not any(normalized == item or item in normalized for item in wanted):
            continue
        block: list[str] = []
        if inline_value:
            block.append(inline_value)
        for next_line in lines[idx + 1 : idx + 1 + max_lines]:
            next_label, _ = _line_label_and_value(next_line)
            if next_label and block:
                break
            if _looks_like_heading(next_line) and block:
                break
            if next_line.strip():
                block.append(next_line)
        return block
    return []


def _line_label_and_value(line: str) -> tuple[str | None, str | None]:
    stripped = line.strip()
    if not stripped:
        return None, None
    stripped = stripped.lstrip("#").strip()
    stripped = re.sub(r"^\*\*(.+?)\*\*", r"\1", stripped)
    match = re.match(r"^\*{0,2}([A-Za-z][A-Za-z /\-_&]{1,40})\*{0,2}\s*[:\-]\s*(.*)$", stripped)
    if match:
        return match.group(1).strip(), match.group(2).strip() or None
    heading = re.match(r"^\*{0,2}([A-Za-z][A-Za-z /\-_&]{1,40})\*{0,2}$", stripped)
    if heading and len(heading.group(1).split()) <= 6:
        return heading.group(1).strip(), None
    return None, None


def _looks_like_heading(line: str) -> bool:
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    label, _ = _line_label_and_value(stripped)
    return bool(label)


def _items_from_block(block: list[str], max_items: int = 6) -> list[str]:
    items: list[str] = []
    for raw in block:
        line = raw.strip()
        if not line:
            continue
        if line.startswith("|") and line.count("|") >= 2:
            if set(line.replace("|", "").replace(":", "").replace("-", "").strip()) <= {" "}:
                continue
            cells = [_clean_text(cell) for cell in line.strip("|").split("|")]
            cells = [cell for cell in cells if cell]
            if cells:
                items.append(" - ".join(cells[:3]))
            continue
        bullet = re.match(r"^(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if bullet:
            items.append(_clean_text(bullet.group(1)))
            continue
        cleaned = _clean_text(line)
        if cleaned:
            items.extend(_split_compact_sentences(cleaned))
        if len(items) >= max_items:
            break
    return _dedupe([item for item in items if item])[:max_items]


def _fallback_bullets(text: str) -> list[str]:
    bullets = []
    for line in text.splitlines():
        match = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$", line)
        if match:
            bullets.append(_clean_text(match.group(1)))
    if bullets:
        return _dedupe(bullets)[:6]
    paragraph = _first_paragraph(text)
    return _split_compact_sentences(paragraph)[:3] if paragraph else []


def _first_paragraph(text: str) -> str | None:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    for paragraph in paragraphs:
        if paragraph.startswith("|"):
            continue
        cleaned = _clean_text(" ".join(paragraph.splitlines()))
        if cleaned:
            return cleaned[:500]
    return None


def _split_compact_sentences(text: str) -> list[str]:
    if ";" in text:
        parts = [part.strip() for part in text.split(";")]
    else:
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9$])", text)
    return [_clean_text(part) for part in parts if _clean_text(part)]


def _clean_text(value: str) -> str:
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"[*_`#>]+", "", value)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\s+", " ", value).strip(" -:\t\r\n")
    return value


def _normalize_label(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", label.lower()).strip()


def _dedupe(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
