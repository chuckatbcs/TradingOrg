"""Resolve preferred model IDs against a live catalog with closest-match fallback."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Iterable


@dataclass(frozen=True)
class ModelResolution:
    requested: str | None
    resolved: str | None
    remapped: bool
    reason: str
    catalog_fingerprint: str


def catalog_fingerprint(catalog: list[str]) -> str:
    joined = "\n".join(sorted({c.strip() for c in catalog if c}))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _family_prefix(model_id: str) -> str:
    # org/name → org; also keep first path segment before variant noise
    return model_id.split("/", 1)[0].lower() if model_id else ""


def _base_name(model_id: str) -> str:
    name = model_id.split("/", 1)[-1].lower()
    return re.sub(r"(:free|:beta|:extended).*$", "", name)


def score_candidate(requested: str, candidate: str, *, provider: str | None = None) -> int:
    if not requested or not candidate:
        return -10_000
    score = 0
    req_free = requested.endswith(":free")
    cand_free = candidate.endswith(":free")
    if req_free == cand_free:
        score += 50
    elif req_free and not cand_free:
        score -= 40
    if _family_prefix(requested) and _family_prefix(requested) == _family_prefix(candidate):
        score += 100
    req_base, cand_base = _base_name(requested), _base_name(candidate)
    if req_base == cand_base:
        score += 80
    elif req_base and cand_base.startswith(req_base[:6]):
        score += 40
    # Prefer instruct/chat over coder/tiny when otherwise close
    if "instruct" in cand_base or "chat" in cand_base:
        score += 10
    if "coder" in cand_base or "tiny" in cand_base or "nano" in cand_base:
        score -= 5
    if provider and provider.lower() == "openrouter" and candidate.endswith(":free"):
        score += 5
    return score


def resolve_model(
    requested: str | None,
    catalog: list[str],
    *,
    provider: str | None = None,
    exclude: set[str] | None = None,
) -> ModelResolution:
    fp = catalog_fingerprint(catalog)
    exclude = exclude or set()
    usable = [m for m in catalog if m and m not in exclude]
    if not usable:
        return ModelResolution(
            requested=requested,
            resolved=None,
            remapped=False,
            reason="catalog empty or all candidates excluded",
            catalog_fingerprint=fp,
        )
    if requested and requested in usable:
        return ModelResolution(
            requested=requested,
            resolved=requested,
            remapped=False,
            reason="exact match",
            catalog_fingerprint=fp,
        )
    if not requested:
        pick = usable[0]
        return ModelResolution(
            requested=requested,
            resolved=pick,
            remapped=True,
            reason="no preferred model; using first catalog entry",
            catalog_fingerprint=fp,
        )
    ranked = sorted(
        usable,
        key=lambda c: (-score_candidate(requested, c, provider=provider), c),
    )
    pick = ranked[0]
    return ModelResolution(
        requested=requested,
        resolved=pick,
        remapped=True,
        reason=f"closest match (family/tier/name score; preferred missing)",
        catalog_fingerprint=fp,
    )


def route_signature(
    provider: str | None,
    backend_url: str | None,
    model: str | None,
    *,
    quick_provider: str | None = None,
    quick_backend_url: str | None = None,
    deep_provider: str | None = None,
    deep_backend_url: str | None = None,
    quick_model: str | None = None,
    deep_model: str | None = None,
) -> str:
    parts = [
        provider or "",
        backend_url or "",
        model or "",
        quick_provider or "",
        quick_backend_url or "",
        deep_provider or "",
        deep_backend_url or "",
        quick_model or "",
        deep_model or "",
    ]
    return "|".join(parts)
