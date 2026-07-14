import pytest

from pipelines.audit_model_v2_coverage_readiness import render_markdown
from quantfore_research.validation.model_v2_coverage import (
    audit_model_v2_coverage,
    compare_clean_rebuilds,
)


FAMILIES = ("value", "quality", "growth", "momentum", "risk")


def _classification(prediction_date, security_id, branch, sector, *, known=True):
    return {
        "prediction_date": prediction_date,
        "security_id": security_id,
        "sector_branch": branch,
        "known_subtype": known,
        "base_classification": {"sector": sector},
        "explicit_classification_evidence": {"sector": sector},
    }


def _score(prediction_date, security_id, branch, *, eligible):
    return {
        "prediction_date": prediction_date,
        "security_id": security_id,
        "sector_branch": branch,
        "eligible": eligible,
        "final_score": "50" if eligible else None,
        "exclusion_reason_codes": [] if eligible else ["BRANCH_REQUIRED_FEATURE_MISSING"],
        "family_available": {family: eligible for family in FAMILIES},
        "family_weights": {family: "0.20" for family in FAMILIES},
        "components": [
            {
                "normalization_scope": "BRANCH" if eligible else "NONE",
                "normalization_group": branch if eligible else None,
            }
        ],
    }


def _rebuild_evidence():
    return {
        "run_count": 2,
        "clean_rebuilds": True,
        "all_rebuild_artifacts_matched": True,
        "canonical_ledgers_reproduced": True,
        "artifacts": {
            "feature_input_ledger": {
                "first_sha256": "a" * 64,
                "second_sha256": "a" * 64,
                "matched": True,
            }
        },
    }


def _audit(classifications, scores):
    eligible = sum(row["eligible"] for row in scores)
    return audit_model_v2_coverage(
        classification_rows=classifications,
        score_rows=scores,
        score_manifest={
            "outcomes_accessed": False,
            "counts": {
                "rows": len(scores),
                "eligible_rows": eligible,
                "cross_branch_fallback_count": 0,
            },
        },
        input_manifest={"outcomes_accessed": False},
        rebuild_evidence=_rebuild_evidence(),
    )


def test_audit_reconciles_every_row_and_fails_locked_breadth_without_tuning():
    classifications = [
        _classification("2025-05-31", "a", "INDUSTRIAL_GENERAL", "Industrials"),
        _classification("2025-05-31", "b", "BANK", "Financials"),
        _classification("2025-06-30", "a", "INDUSTRIAL_GENERAL", "Industrials"),
        _classification("2025-06-30", "b", "BANK", "Financials"),
    ]
    scores = [
        _score("2025-05-31", "a", "INDUSTRIAL_GENERAL", eligible=True),
        _score("2025-05-31", "b", "BANK", eligible=False),
        _score("2025-06-30", "a", "INDUSTRIAL_GENERAL", eligible=False),
        _score("2025-06-30", "b", "BANK", eligible=False),
    ]
    report = _audit(classifications, scores)

    assert report["decision"] == "FAIL_NOT_READY_FOR_EXECUTABLE_LOCK"
    assert report["reconciliation"]["expected_stock_months"] == 4
    assert report["reconciliation"]["scored_stock_months"] == 1
    assert report["reconciliation"]["final_disposition_fraction"] == 1.0
    assert report["coverage"]["aggregate_final_score_coverage"] == 0.25
    assert report["criteria"]["two_clean_rebuilds_match_exactly"]["passed"] is True
    assert report["criteria"]["final_score_coverage_every_month"]["passed"] is False
    assert report["criteria"]["represented_active_branches_every_month"]["threshold"] == 5
    assert report["thresholds_changed_after_failure"] is False
    assert report["outcomes_accessed"] is False


def test_audit_rejects_missing_duplicate_or_unstable_dispositions():
    classifications = [
        _classification("2025-06-30", "a", "INDUSTRIAL_GENERAL", "Industrials")
    ]
    score = _score("2025-06-30", "a", "INDUSTRIAL_GENERAL", eligible=False)

    with pytest.raises(ValueError, match="lack final disposition"):
        _audit(classifications, [])
    with pytest.raises(ValueError, match="duplicate score disposition"):
        _audit(classifications, [score, score])
    unstable = dict(score, exclusion_reason_codes=[])
    with pytest.raises(ValueError, match="lacks stable disposition"):
        _audit(classifications, [unstable])


def test_audit_rejects_outcomes_and_cross_branch_normalization():
    classification = _classification(
        "2025-06-30", "a", "INDUSTRIAL_GENERAL", "Industrials"
    )
    score = _score("2025-06-30", "a", "INDUSTRIAL_GENERAL", eligible=True)
    score["forward_return"] = "0.50"
    with pytest.raises(ValueError, match="outcome field is prohibited"):
        _audit([classification], [score])

    score.pop("forward_return")
    score["components"][0]["normalization_group"] = "BANK"
    report = _audit([classification], [score])
    assert report["criteria"]["cross_branch_fallback_count"]["passed"] is False
    assert report["controls"]["cross_branch_fallback_count"] == 1


def test_rebuild_comparison_requires_every_artifact_and_canonical_ledger_to_match():
    fingerprint = {
        "feature_input_ledger": "a" * 64,
        "feature_input_manifest": "b" * 64,
        "score_ledger": "c" * 64,
        "score_manifest": "d" * 64,
    }
    matched = compare_clean_rebuilds(
        first=fingerprint,
        second=fingerprint,
        canonical={
            "feature_input_ledger": "a" * 64,
            "score_ledger": "c" * 64,
        },
    )
    changed = compare_clean_rebuilds(
        first=fingerprint,
        second=dict(fingerprint, score_ledger="0" * 64),
        canonical={
            "feature_input_ledger": "a" * 64,
            "score_ledger": "c" * 64,
        },
    )
    assert matched["all_rebuild_artifacts_matched"] is True
    assert matched["canonical_ledgers_reproduced"] is True
    assert changed["all_rebuild_artifacts_matched"] is False
    assert changed["artifacts"]["score_ledger"]["matched"] is False


def test_human_report_states_failure_rebuild_proof_and_claims_boundary():
    report = _audit(
        [_classification("2025-06-30", "a", "INDUSTRIAL_GENERAL", "Industrials")],
        [_score("2025-06-30", "a", "INDUSTRIAL_GENERAL", eligible=True)],
    )
    markdown = render_markdown(report)
    assert "not ready for the executable pre-shadow lock" in markdown
    assert "Two clean rebuilds match" in markdown
    assert "No return metrics used" in markdown
    assert "## Claims boundary" in markdown
