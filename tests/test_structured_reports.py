"""Structured report extraction from existing markdown reports."""

import pytest

from tradingagents.agents.utils.structured_reports import (
    build_bull_bear_comparison,
    structure_report,
)


@pytest.mark.unit
def test_parse_analyst_report_fields():
    report = structure_report(
        "market_report",
        """
## Thesis Summary
Momentum is improving as price reclaims the 50-day average.

## Stance
Bullish

## Confidence
70%

## Evidence
- Close is above the 50-day moving average.
- Volume expanded on the breakout.

## Risks
- RSI is near overbought territory.

## Catalysts
- Earnings are due next week.
""",
        ticker="NVDA",
        trade_date="2026-07-03",
    )

    assert report.agent == "Market Analyst"
    assert report.stance == "Bullish"
    assert report.confidence == pytest.approx(0.7)
    assert report.evidence[0] == "Close is above the 50-day moving average."
    assert report.risks == ["RSI is near overbought territory."]
    assert report.ticker == "NVDA"


@pytest.mark.unit
def test_parse_bull_and_bear_reports_for_comparison():
    bull = structure_report(
        "bull_history",
        """
Bull Analyst:

## Thesis Summary
The bull case rests on accelerating AI demand and improving margins.

## Evidence
- Datacenter revenue is compounding.
- Gross margin guidance moved higher.

## Risks
- Valuation leaves less room for error.
""",
        ticker="NVDA",
        trade_date="2026-07-03",
    )
    bear = structure_report(
        "bear_history",
        """
Bear Analyst:

## Thesis Summary
The bear case is that expectations already discount flawless execution.

## Evidence
- Customer concentration remains high.

## Risks
- Capex digestion could slow orders.
- Export controls could limit shipments.
""",
        ticker="NVDA",
        trade_date="2026-07-03",
    )

    assert bull.stance == "Bullish"
    assert bear.stance == "Bearish"
    assert bull.rating is None
    assert bear.risks[0] == "Capex digestion could slow orders."

    comparison = build_bull_bear_comparison(
        {
            "bull_history": bull.model_dump(),
            "bear_history": bear.model_dump(),
        },
        ticker="NVDA",
        trade_date="2026-07-03",
    )
    assert comparison["available"] is True
    assert any("Stance differs" in item for item in comparison["disagreements"])
    assert any("Bull emphasizes" in item for item in comparison["disagreements"])
    assert any("Bear emphasizes" in item for item in comparison["disagreements"])


@pytest.mark.unit
def test_parse_final_decision_fields():
    report = structure_report(
        "final_trade_decision",
        """
**Rating**: Buy

**Executive Summary**: Enter gradually while volatility is elevated.

**Investment Thesis**: Demand growth and balance-sheet quality outweigh macro risk.

**Price Target**: $145.50

**Time Horizon**: 3-6 months
""",
    )

    assert report.rating == "Buy"
    assert report.stance == "Bullish"
    assert report.recommended_action == "Buy"
    assert report.price_target == pytest.approx(145.5)
    assert report.time_horizon == "3-6 months"
    assert report.thesis_summary == "Enter gradually while volatility is elevated."
