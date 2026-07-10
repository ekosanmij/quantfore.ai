from datetime import date
from decimal import Decimal

import pytest

from quantfore_research.validation.factor_diagnostics import (
    FamilyEvaluationRow,
    build_dominance_assessment,
    component_reason,
    evaluate_signal,
    summarize_ablations,
    summarize_component_aggregates,
    summarize_families,
)


def test_component_reason_preserves_valid_and_explicit_missing_states():
    assert component_reason(
        directed_value=Decimal("0"),
        missing_reason=None,
        applicability_status="APPLICABLE",
    ) == "VALID"
    assert component_reason(
        directed_value=None,
        missing_reason="SOURCE_MISSING",
        applicability_status="MISSING",
    ) == "SOURCE_MISSING"
    assert component_reason(
        directed_value=None,
        missing_reason=None,
        applicability_status="NOT_APPLICABLE",
    ) == "NOT_APPLICABLE"


def test_component_and_family_missingness_are_reconciled():
    components = summarize_component_aggregates(
        [
            {
                "family": "value",
                "feature_name": "earnings_yield",
                "reason": "VALID",
                "count": 4,
            },
            {
                "family": "value",
                "feature_name": "earnings_yield",
                "reason": "SOURCE_MISSING",
                "count": 6,
            },
            {
                "family": "quality",
                "feature_name": "roic",
                "reason": "NOT_APPLICABLE",
                "count": 10,
            },
        ],
        expected_security_months=10,
    )

    earnings = next(row for row in components if row["feature_name"] == "earnings_yield")
    assert earnings["valid_rate"] == pytest.approx(0.4)
    assert earnings["mostly_not_applicable_or_source_missing"] is True
    assert earnings["dominant_state"] == "SOURCE_MISSING"

    families = summarize_families(
        components=components,
        availability_counts={"value": 3, "quality": 0},
        security_months=10,
    )
    value = next(row for row in families if row["family"] == "value")
    quality = next(row for row in families if row["family"] == "quality")
    assert value["family_availability_rate"] == pytest.approx(0.3)
    assert value["valid_component_rate"] == pytest.approx(0.4)
    assert quality["component_reason_counts"] == {"NOT_APPLICABLE": 10}


def test_component_reconciliation_rejects_silent_row_loss():
    with pytest.raises(ValueError, match="expected 10"):
        summarize_component_aggregates(
            [
                {
                    "family": "growth",
                    "feature_name": "revenue_growth",
                    "reason": "VALID",
                    "count": 9,
                }
            ],
            expected_security_months=10,
        )


def _evaluation_rows() -> tuple[FamilyEvaluationRow, ...]:
    output = []
    for month in (1, 2):
        for index in range(5):
            signal = Decimal(index)
            excess = Decimal(index - 2) / Decimal("100")
            output.append(
                FamilyEvaluationRow(
                    security_id=f"security-{index}",
                    ticker=f"T{index}",
                    prediction_date=date(2020, month, 28),
                    sector="Test",
                    final_score=signal,
                    family_z={
                        "value": signal,
                        "quality": None,
                        "growth": signal / Decimal("2"),
                        "momentum": signal,
                        "risk": -signal,
                    },
                    excess_return=excess,
                    realised_return=Decimal("0.02") + excess,
                    benchmark_return=Decimal("0.02"),
                    max_drawdown=Decimal("-0.05"),
                )
            )
    return tuple(output)


def test_standalone_signal_evaluation_handles_present_and_absent_families():
    rows = _evaluation_rows()
    value = evaluate_signal(rows, score=lambda row: row.family_z["value"])
    quality = evaluate_signal(rows, score=lambda row: row.family_z["quality"])

    assert value["evaluable"] is True
    assert value["observations"] == 10
    assert value["calculable_rank_ic_months"] == 2
    assert value["mean_rank_ic"] == pytest.approx(1.0)
    assert value["positive_rank_ic_month_rate"] == pytest.approx(1.0)
    assert quality == {
        "evaluable": False,
        "reason": "NO_AVAILABLE_SCORES_IN_EVALUATED_COHORT",
        "observations": 0,
        "prediction_months": 0,
        "calculable_rank_ic_months": 0,
        "mean_rank_ic": None,
        "median_rank_ic": None,
        "positive_rank_ic_month_rate": None,
        "top_bucket_gross_excess_return": None,
        "top_bucket_net_excess_return_25_bps": None,
        "top_minus_bottom_spread": None,
        "non_overlapping_rank_ic_periods": 0,
        "non_overlapping_rank_ic_t_statistic": None,
    }


def _comparison() -> dict:
    return {
        "comparison": {
            "models": {
                "sprint8_multifactor": {"mean_rank_ic": 0.5},
            },
            "family_ablations": {
                f"without_{family}": {
                    "design": {
                        "eligible_observations": 10,
                        "excluded_observations": 0,
                        "retuned": False,
                    },
                    "evaluation": {
                        "mean_rank_ic": 0.5 if family == "quality" else 0.4,
                        "transaction_costs": {
                            "25_bps": {"average_net_excess_return": -0.01}
                        },
                        "non_overlapping_rank_ic_periods": 1,
                        "non_overlapping_rank_ic_t_statistic": None,
                    },
                }
                for family in ("value", "quality", "growth", "momentum", "risk")
            },
        }
    }


def test_ablations_distinguish_absent_family_from_positive_marginal_effect():
    ablations = summarize_ablations(
        _comparison(),
        evaluated_family_counts={
            "value": 10,
            "quality": 0,
            "growth": 10,
            "momentum": 10,
            "risk": 10,
        },
    )
    by_family = {row["family"]: row for row in ablations}

    assert by_family["quality"]["rank_ic_loss_when_removed"] == 0
    assert by_family["quality"]["interpretation"] == "NO_EFFECT_FAMILY_ABSENT"
    assert by_family["growth"]["rank_ic_loss_when_removed"] == pytest.approx(0.1)
    assert (
        by_family["growth"]["interpretation"]
        == "NARROW_POSITIVE_MARGINAL_CONTRIBUTION"
    )


def test_dominance_requires_the_three_diagnostics_to_agree():
    standalone = {
        "value": {"evaluable": True, "mean_rank_ic": 0.1},
        "quality": {"evaluable": False, "mean_rank_ic": None},
        "growth": {"evaluable": True, "mean_rank_ic": 0.2},
        "momentum": {"evaluable": True, "mean_rank_ic": 0.6},
        "risk": {"evaluable": True, "mean_rank_ic": 0.3},
    }
    ablations = [
        {"family": family, "rank_ic_loss_when_removed": loss}
        for family, loss in {
            "value": -0.1,
            "quality": 0,
            "growth": 0.4,
            "momentum": 0.2,
            "risk": 0.3,
        }.items()
    ]
    contributions = [
        {"family": family, "absolute_contribution_share": share}
        for family, share in {
            "value": 0.2,
            "quality": 0,
            "growth": 0.3,
            "momentum": 0.25,
            "risk": 0.25,
        }.items()
    ]
    grouped = {
        "fundamentals_value_quality_growth": {"mean_rank_ic": 0.05},
        "price_risk_momentum_risk": {"mean_rank_ic": 0.45},
    }

    result = build_dominance_assessment(
        standalone=standalone,
        ablations=ablations,
        contribution_attribution=contributions,
        grouped=grouped,
    )

    assert result["strongest_standalone_rank_ic_family"] == "momentum"
    assert result["largest_rank_ic_loss_when_removed_family"] == "growth"
    assert result["largest_absolute_score_contribution_family"] == "growth"
    assert result["consistent_single_family_dominance"] is False
    assert result["price_risk_group_rank_ic_exceeds_fundamentals"] is True
