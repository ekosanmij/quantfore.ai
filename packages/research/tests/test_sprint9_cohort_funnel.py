import hashlib
import gzip
import json
from datetime import date
from decimal import Decimal

import pytest

from pipelines.audit_sprint9_cohort_funnel import (
    _find_explanations,
    _write_explanations,
    main as pipeline_main,
)
from quantfore_research.validation.cohort_funnel import (
    BELOW_COVERAGE,
    BELOW_FAMILIES,
    ELIGIBILITY_MISMATCH,
    INCLUDED,
    INCOMPLETE_FEATURES,
    OUTCOME_MISSING,
    PREDICTION_MISSING,
    REQUIRED_HORIZONS,
    _aggregate_records,
    diagnostic_reason_codes,
    primary_reason_code,
    quintile_one_diagnosis,
)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"complete_feature_set": False}, INCOMPLETE_FEATURES),
        ({"available_family_count": 3}, BELOW_FAMILIES),
        ({"component_coverage": Decimal("0.69")}, BELOW_COVERAGE),
        ({"eligible": False}, ELIGIBILITY_MISMATCH),
        ({"prediction_horizons": ("126d",)}, PREDICTION_MISSING),
        ({"evaluated_126d": False}, OUTCOME_MISSING),
        ({}, INCLUDED),
    ],
)
def test_primary_reason_code_is_exclusive_and_ordered(overrides, expected):
    values = {
        "complete_feature_set": True,
        "available_family_count": 4,
        "component_coverage": Decimal("0.70"),
        "eligible": True,
        "prediction_horizons": REQUIRED_HORIZONS,
        "evaluated_126d": True,
    }
    values.update(overrides)

    assert primary_reason_code(**values) == expected


def test_family_failure_precedes_coverage_failure():
    assert primary_reason_code(
        complete_feature_set=True,
        available_family_count=2,
        component_coverage=Decimal("0.10"),
        eligible=False,
        prediction_horizons=(),
        evaluated_126d=False,
    ) == BELOW_FAMILIES


def test_diagnostic_codes_exclude_valid_and_retain_root_causes():
    assert diagnostic_reason_codes(
        exact_prediction_price=False,
        model_available_fundamental_fact=False,
        valid_price_feature_count=0,
        valid_fundamental_feature_count=0,
        sector="SECTOR_UNKNOWN",
        component_reasons=("VALID", "SOURCE_MISSING"),
    ) == (
        "NO_MODEL_AVAILABLE_FUNDAMENTAL_FACT",
        "NO_USABLE_FUNDAMENTAL_FEATURE",
        "NO_USABLE_PRICE_FEATURE",
        "PRICE_MISSING_AT_PREDICTION",
        "SECTOR_UNKNOWN",
        "SOURCE_MISSING",
    )


def test_quintile_diagnosis_explains_small_cohorts():
    result = quintile_one_diagnosis((0, 1, 2, 3, 4))

    assert result["quintile_1_possible"] is False
    assert result["maximum_monthly_eligible_scores"] == 4
    assert result["months_with_at_least_five_scores"] == 0
    assert result["reason_code"] == "COHORT_TOO_SMALL_FOR_BOTTOM_QUINTILE"

    assert quintile_one_diagnosis((5,))["quintile_1_possible"] is True


def _record(
    *,
    prediction_date: str,
    security_id: str,
    ticker: str,
    included: bool,
    primary_reason: str,
    available_families: list[str],
) -> dict:
    return {
        "prediction_date": prediction_date,
        "security_id": security_id,
        "ticker": ticker,
        "sector": "Financials" if included else "Industrials",
        "industry": "6798" if included else "3500",
        "classification_system": "TEST_CLASSIFICATION_V1",
        "included_in_final_evaluation": included,
        "primary_reason_code": primary_reason,
        "reason_codes": [primary_reason],
        "diagnostic_reason_codes": [] if included else ["SOURCE_MISSING"],
        "available_families": available_families,
        "family_available": {},
        "component_reason_counts": (
            {"VALID": 10, "NOT_APPLICABLE": 9}
            if included
            else {"VALID": 6, "SOURCE_MISSING": 13}
        ),
        "missing_components": {},
        "stages": {
            "universe_member": True,
            "exact_prediction_date_price": True,
            "model_available_fundamental_fact": True,
            "raw_feature_count": 19,
            "complete_raw_feature_set": True,
            "normalized_feature_count": 19,
            "complete_normalized_feature_set": True,
            "valid_price_feature_count": 6,
            "valid_fundamental_feature_count": 4 if included else 0,
            "applicable_component_count": 10 if included else 19,
            "valid_component_count": 10 if included else 6,
            "component_coverage": "1" if included else "0.315789",
            "available_family_count": 4 if included else 2,
            "eligible_final_score": included,
            "final_score": "50" if included else None,
            "prediction_horizons": list(REQUIRED_HORIZONS) if included else [],
            "prediction_record_count": 4 if included else 0,
            "mature_outcome_horizons": list(REQUIRED_HORIZONS) if included else [],
            "mature_outcome_126d": included,
            "evaluated_126d": included,
        },
    }


def test_aggregate_records_reconciles_months_and_primary_reasons():
    runs = [
        {"asof_date": date(2020, 1, 31)},
        {"asof_date": date(2020, 2, 28)},
    ]
    records = [
        _record(
            prediction_date="2020-01-31",
            security_id="included-security",
            ticker="INC",
            included=True,
            primary_reason=INCLUDED,
            available_families=["growth", "momentum", "risk", "value"],
        ),
        _record(
            prediction_date="2020-02-28",
            security_id="excluded-security",
            ticker="EXC",
            included=False,
            primary_reason=BELOW_FAMILIES,
            available_families=["momentum", "risk"],
        ),
    ]

    summary, monthly = _aggregate_records(records, runs)

    assert summary["funnel_totals"]["universe_members"] == 2
    assert summary["funnel_totals"]["eligible_final_scores"] == 1
    assert summary["funnel_totals"]["prediction_records"] == 4
    assert summary["primary_reason_counts"] == {
        BELOW_FAMILIES: 1,
        INCLUDED: 1,
    }
    assert monthly[0]["final_score_coverage"] == 1.0
    assert monthly[1]["final_score_coverage"] == 0.0


def test_explanation_jsonl_is_hash_bound_and_lookupable(tmp_path):
    rows = [
        _record(
            prediction_date="2020-01-31",
            security_id="security-1",
            ticker="ABC",
            included=True,
            primary_reason=INCLUDED,
            available_families=["growth", "momentum", "risk", "value"],
        )
    ]
    output = tmp_path / "explanations.jsonl.gz"

    digest, count = _write_explanations(output, rows)

    assert count == 1
    assert digest == hashlib.sha256(output.read_bytes()).hexdigest()
    with gzip.open(output, "rt", encoding="utf-8") as handle:
        assert json.loads(handle.read().strip())["ticker"] == "ABC"
    assert _find_explanations(rows, "abc", date(2020, 1, 31)) == rows
    assert _find_explanations(rows, "SECURITY-1", date(2020, 1, 31)) == rows


def test_explain_only_requires_a_lookup(capsys):
    assert pipeline_main(["--explain-only"]) == 2
    assert "--explain-only requires" in capsys.readouterr().err
