from decimal import Decimal

from quantfore_research.scoring import (
    BASELINE_MODEL_VERSION,
    REQUIRED_FEATURE_NAMES,
    BaselineScore,
    ScoreDriver,
    calculate_baseline_score,
)


def test_baseline_scoring_package_exposes_score_contract():
    driver = ScoreDriver(
        driver_name="momentum_6_1",
        contribution=Decimal("12.4"),
        evidence_uri="feature:momentum_6_1",
    )
    score = BaselineScore(
        score=Decimal("82"),
        confidence=Decimal("0.71"),
        action_label="watch_positive",
        drivers=(driver,),
    )

    assert BASELINE_MODEL_VERSION == "baseline_v0.1"
    assert REQUIRED_FEATURE_NAMES == (
        "momentum_6_1",
        "momentum_12_1",
        "return_21d",
        "volatility_126d",
    )
    assert score.score == Decimal("82")
    assert score.confidence == Decimal("0.71")
    assert score.action_label == "watch_positive"
    assert score.drivers == (driver,)
    assert callable(calculate_baseline_score)


def test_calculate_baseline_score_applies_v0_heuristic():
    score = calculate_baseline_score(
        {
            "momentum_6_1": Decimal("0.10"),
            "momentum_12_1": Decimal("0.20"),
            "return_21d": Decimal("0.05"),
            "volatility_126d": Decimal("0.02"),
        }
    )

    assert score.score == Decimal("57.000000")
    assert score.confidence == Decimal("0.556000")
    assert score.action_label == "neutral"
    assert score.drivers == (
        ScoreDriver("momentum_6_1", Decimal("4.000000"), "feature:momentum_6_1"),
        ScoreDriver("momentum_12_1", Decimal("6.000000"), "feature:momentum_12_1"),
        ScoreDriver("return_21d", Decimal("1.000000"), "feature:return_21d"),
        ScoreDriver("volatility_126d", Decimal("-4.000000"), "feature:volatility_126d"),
    )


def test_calculate_baseline_score_clamps_to_zero_and_100():
    high_score = calculate_baseline_score(
        {
            "momentum_6_1": Decimal("10"),
            "momentum_12_1": Decimal("10"),
            "return_21d": Decimal("10"),
            "volatility_126d": Decimal("0"),
        }
    )
    low_score = calculate_baseline_score(
        {
            "momentum_6_1": Decimal("-10"),
            "momentum_12_1": Decimal("-10"),
            "return_21d": Decimal("-10"),
            "volatility_126d": Decimal("1"),
        }
    )

    assert high_score.score == Decimal("100.000000")
    assert high_score.confidence == Decimal("0.900000")
    assert high_score.action_label == "watch_positive"
    assert low_score.score == Decimal("0.000000")
    assert low_score.confidence == Decimal("0.900000")
    assert low_score.action_label == "thesis_risk_review"


def test_calculate_baseline_score_requires_all_baseline_features():
    try:
        calculate_baseline_score(
            {
                "momentum_6_1": Decimal("0.10"),
                "momentum_12_1": Decimal("0.20"),
                "return_21d": Decimal("0.05"),
            }
        )
    except ValueError as exc:
        assert str(exc) == "missing baseline score features: volatility_126d"
    else:
        raise AssertionError("missing feature did not fail")
