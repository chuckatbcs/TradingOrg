"""Pydantic-style dataclasses for hybrid signal fusion."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TradeCandidate:
    ticker: str
    trade_date: str
    run_id: str | None = None
    side: str = "buy"
    entry_price: float | None = None
    atr: float | None = None


@dataclass
class FusedSignal:
    ticker: str
    trade_date: str
    run_id: str | None = None
    quant_pass: bool = False
    quant_score: float = 0.0
    llm_pass: bool = False
    llm_rating: str = "Hold"
    llm_score: float = 0.0
    regime: str = "choppy"
    regime_multiplier: float = 0.5
    fused_score: float = 0.0
    fused_pass: bool = False
    blockers: list[str] = field(default_factory=list)
    side: str = "buy"

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "trade_date": self.trade_date,
            "run_id": self.run_id,
            "quant_pass": self.quant_pass,
            "quant_score": self.quant_score,
            "llm_pass": self.llm_pass,
            "llm_rating": self.llm_rating,
            "llm_score": self.llm_score,
            "regime": self.regime,
            "regime_multiplier": self.regime_multiplier,
            "fused_score": self.fused_score,
            "fused_pass": self.fused_pass,
            "blockers": self.blockers,
            "side": self.side,
        }
