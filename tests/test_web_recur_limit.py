"""Web run recursion budget scales with analyst count."""

from webapp.runs import compute_web_recur_limit


def test_override_wins():
    assert compute_web_recur_limit(["market"], override=450) == 450


def test_openrouter_free_quick_caps_override():
    assert (
        compute_web_recur_limit(
            ["market", "social", "news", "fundamentals"],
            override=1000,
            openrouter_free_quick=True,
        )
        == 360
    )


def test_openrouter_free_quick_caps_computed_limit():
    assert (
        compute_web_recur_limit(
            ["market", "social", "news", "fundamentals"],
            openrouter_free_quick=True,
        )
        == 360
    )


def test_scales_with_analysts():
    one = compute_web_recur_limit(["market"])
    four = compute_web_recur_limit(["market", "social", "news", "fundamentals"])
    assert four > one
    assert four == 580


def test_scales_with_debate_and_risk():
    base = compute_web_recur_limit(["market"])
    more = compute_web_recur_limit(["market"], max_debate_rounds=3, max_risk_rounds=3)
    assert more > base
