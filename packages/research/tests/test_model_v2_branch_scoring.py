from datetime import date
from decimal import Decimal

from quantfore_research.features.model_v2 import (
    APPLICABLE,
    MISSING,
    BRANCH_FEATURE_DEFINITIONS,
    ModelV2FeatureBatch,
    RawModelV2Feature,
)
from quantfore_research.scoring.model_v2 import (
    ALL_FIVE_FAMILIES_REQUIRED,
    BRANCH_NORMALIZATION_COHORT_TOO_SMALL,
    FAMILY_COVERAGE_BELOW_MINIMUM,
    FAMILY_WEIGHTS,
    normalize_model_v2_cohort,
)


PREDICTION_DATE = date(2025, 6, 30)


def _batch(branch, index, *, missing=()):
    components = []
    for position, definition in enumerate(BRANCH_FEATURE_DEFINITIONS[branch]):
        if definition.name in missing:
            components.append(
                RawModelV2Feature(
                    definition,
                    None,
                    MISSING,
                    "SOURCE_MISSING",
                    f"missing {definition.name}",
                    (),
                )
            )
        else:
            components.append(
                RawModelV2Feature(
                    definition,
                    Decimal(index * 10 + position + 1),
                    APPLICABLE,
                    None,
                    None,
                    (f"lineage-{branch}-{index}-{definition.name}",),
                )
            )
    return ModelV2FeatureBatch(
        security_id=f"{branch}-{index:02d}",
        prediction_date=PREDICTION_DATE,
        sector_branch=branch,
        classification_eligible=True,
        classification_reason_codes=(),
        classification_id=f"classification-{branch}-{index}",
        components=tuple(components),
    )


def test_every_scored_row_has_all_five_families_and_fixed_weights():
    result = normalize_model_v2_cohort([_batch("BANK", index) for index in range(25)])

    assert len(result.scores) == 25
    assert all(score.eligible for score in result.scores)
    assert all(all(score.family_available.values()) for score in result.scores)
    assert all(score.family_weights == FAMILY_WEIGHTS for score in result.scores)
    assert all(not score.exclusion_reason_codes for score in result.scores)
    assert min(score.final_score for score in result.scores) == Decimal("0")
    assert max(score.final_score for score in result.scores) == Decimal("100")


def test_missing_one_component_can_pass_but_a_missing_family_never_renormalizes():
    rows = [_batch("BANK", index) for index in range(25)]
    one_growth_missing = _batch("BANK", 0, missing=("loan_growth",))
    rows[0] = one_growth_missing
    score = normalize_model_v2_cohort(rows).by_security()[one_growth_missing.security_id]
    assert score.eligible is True
    assert score.valid_component_count == score.required_component_count - 1
    assert score.family_weights == FAMILY_WEIGHTS

    value_missing = _batch("BANK", 0, missing=("earnings_yield",))
    rows[0] = value_missing
    score = normalize_model_v2_cohort(rows).by_security()[value_missing.security_id]
    assert score.eligible is False
    assert score.final_score is None
    assert score.family_available["value"] is False
    assert ALL_FIVE_FAMILIES_REQUIRED in score.exclusion_reason_codes
    assert FAMILY_COVERAGE_BELOW_MINIMUM in score.exclusion_reason_codes
    assert score.family_weights == FAMILY_WEIGHTS
    assert set(score.family_weights.values()) == {Decimal("0.20")}


def test_small_branches_are_excluded_instead_of_using_another_branch():
    result = normalize_model_v2_cohort(
        [_batch("BROKER_DEALER", index) for index in range(19)]
    )
    assert all(not score.eligible for score in result.scores)
    assert all(score.final_score is None for score in result.scores)
    assert all(
        BRANCH_NORMALIZATION_COHORT_TOO_SMALL in score.exclusion_reason_codes
        for score in result.scores
    )
    assert all(
        component.normalization_scope == "NONE"
        for score in result.scores
        for component in score.components
    )


def test_normalization_is_strictly_branch_local_without_universe_fallback():
    batches = [_batch("BANK", index) for index in range(25)] + [
        _batch("INDUSTRIAL_GENERAL", index) for index in range(25)
    ]
    result = normalize_model_v2_cohort(batches)
    assert all(score.eligible for score in result.scores)
    for score in result.scores:
        assert all(
            component.normalization_scope == "BRANCH"
            and component.normalization_group == score.sector_branch
            for component in score.components
        )
        assert not any(
            component.normalization_group in {"UNIVERSE", "INDUSTRIAL_GENERAL"}
            and score.sector_branch != "INDUSTRIAL_GENERAL"
            for component in score.components
        )


def test_classification_exclusion_has_a_stable_reason_and_no_components():
    excluded = ModelV2FeatureBatch(
        security_id="unknown-security",
        prediction_date=PREDICTION_DATE,
        sector_branch="UNKNOWN",
        classification_eligible=False,
        classification_reason_codes=("FINANCIAL_SUBTYPE_UNKNOWN",),
        classification_id="classification-unknown",
        components=(),
    )
    result = normalize_model_v2_cohort(
        [excluded] + [_batch("BANK", index) for index in range(20)]
    )
    score = result.by_security()["unknown-security"]
    assert score.eligible is False
    assert score.exclusion_reason_codes == ("FINANCIAL_SUBTYPE_UNKNOWN",)
    assert score.components == ()
