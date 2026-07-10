import hashlib
import json
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DECISION_PATH = (
    REPOSITORY_ROOT / "reports" / "reproducibility" / "sprint9-decision-v1.json"
)
REPORT_PATH = DECISION_PATH.with_suffix(".md")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _decision() -> dict:
    return _json(DECISION_PATH)


def test_decision_is_hash_bound_to_human_report_and_every_evidence_input():
    decision = _decision()

    assert decision["human_report"]["path"] == str(
        REPORT_PATH.relative_to(REPOSITORY_ROOT)
    )
    assert decision["human_report"]["sha256"] == hashlib.sha256(
        REPORT_PATH.read_bytes()
    ).hexdigest()
    assert len(decision["evidence_inputs"]) >= 13
    for source in decision["evidence_inputs"]:
        path = REPOSITORY_ROOT / source["path"]
        assert path.is_file(), source["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]


def test_decision_matches_the_coverage_and_factor_evidence():
    decision = _decision()
    evidence = decision["key_evidence"]
    funnel = _json(
        REPOSITORY_ROOT
        / "reports"
        / "data-audits"
        / "sprint9-cohort-funnel-v1.json"
    )["audit"]
    factor = _json(
        REPOSITORY_ROOT
        / "reports"
        / "research"
        / "sprint9-factor-diagnostics-v1.json"
    )["diagnostic"]

    totals = funnel["funnel_totals"]
    assert evidence["universe_stock_months"] == totals["universe_members"]
    assert evidence["eligible_final_score_stock_months"] == totals[
        "eligible_final_scores"
    ]
    assert evidence["aggregate_final_score_coverage"] == pytest.approx(
        totals["final_score_coverage"], abs=5e-7
    )
    assert evidence["monthly_cohorts"] == funnel["scope"]["monthly_cohorts"]
    assert evidence["months_with_no_final_score"] == funnel[
        "diagnoses"
    ]["monthly_breadth"]["months_with_zero_eligible_scores"]
    assert evidence["months_at_or_above_90pct_coverage"] == funnel[
        "breadth_assessment"
    ]["months_at_or_above_required_coverage"]
    assert evidence["unique_universe_securities"] == funnel[
        "unique_security_counts"
    ]["universe_members"]
    assert evidence["unique_evaluated_securities"] == factor["scope"][
        "eligible_evaluated_unique_securities"
    ]
    quality = next(
        row
        for row in factor["family_missingness_all_security_months"]
        if row["family"] == "quality"
    )
    quality_evaluated = next(
        row
        for row in factor["family_missingness_evaluated_rows"]
        if row["family"] == "quality"
    )
    assert evidence["quality_available_universe_rows"] == quality[
        "family_available_security_months"
    ]
    assert evidence["quality_available_evaluated_rows"] == quality_evaluated[
        "family_available_security_months"
    ]


def test_decision_matches_signal_investability_and_sector_evidence():
    decision = _decision()
    evidence = decision["key_evidence"]
    readout = _json(
        REPOSITORY_ROOT
        / "reports"
        / "research"
        / "sprint9-evidence-readout-v1.json"
    )
    investability = _json(
        REPOSITORY_ROOT
        / "reports"
        / "backtests"
        / "sprint9-investability-diagnostic-v1.json"
    )["diagnostic"]
    sector = _json(
        REPOSITORY_ROOT
        / "docs"
        / "research"
        / "sector-specific-factor-treatment-v1.json"
    )

    performance = readout["model_performance"]
    assert evidence["rank_ic_mean"] == pytest.approx(
        performance["mean_rank_ic"], abs=5e-5
    )
    assert evidence["rank_ic_calculable_months"] == performance[
        "calculable_rank_ic_months"
    ]
    assert evidence["rank_ic_calculable_holdout_months"] == readout["holdout"][
        "represented_rank_ic_months"
    ]
    assert evidence["rank_ic_nonoverlap_tstat"] is None
    cost = investability["root_cause_assessment"]["cost_drag"]
    selection = investability["root_cause_assessment"]["model_selection"]
    assert evidence["gross_selected_excess_return"] == pytest.approx(
        cost["gross_excess_return"], abs=5e-7
    )
    assert evidence["net_selected_excess_return_25bps"] == pytest.approx(
        cost["net_excess_return_25_bps"], abs=5e-7
    )
    assert evidence["selected_minus_eligible_equal_weight"] == pytest.approx(
        selection["selected_minus_eligible_equal_weight_excess"], abs=5e-5
    )
    assert evidence[
        "selected_minus_equal_weight_multiname_months"
    ] == pytest.approx(selection["multi_name_month_selection_lift"], abs=5e-5)
    assert evidence["maximum_single_name_weight"] == investability[
        "concentration"
    ]["single_name"]["maximum_period_name_weight"]
    assert evidence["maximum_sector_weight"] == investability["concentration"][
        "sector"
    ]["maximum_period_sector_weight"]
    assert evidence["reit_sic_6798_evaluated_rows"] == sector["evidence"][
        "eligible_observations_reit_sic_6798"
    ]
    assert evidence["insurer_sic_6331_evaluated_rows"] == sector["evidence"][
        "eligible_observations_insurer_sic_6331"
    ]


def test_sprint_closes_without_authorizing_shadow_model_or_product_use():
    decision = _decision()

    assert decision["decision"] == (
        "EXPAND_DATA_AND_IMPLEMENT_MODEL_V2_BEFORE_SHADOW_TESTING"
    )
    assert decision["claims_eligible"] is False
    assert decision["sprint9_status"] == "COMPLETE"
    assert decision["sprint8_assessment"]["engineering_reproducibility"] == "PASS"
    assert decision["sprint8_assessment"]["engineering_promotion_gates"] == "FAIL"
    assert decision["sprint8_assessment"]["model_performance_promotion_gates"] == "FAIL"
    assert decision["path_decisions"]["implement_locked_model_v2"] == (
        "GO_CONDITIONAL_ENGINEERING_ONLY"
    )
    assert decision["path_decisions"]["start_shadow_testing_now"] == (
        "NO_GO_NOT_READY"
    )
    assert decision["path_decisions"]["use_quant_scores_in_product"] == "NO_GO"
    assert decision["first_shadow_batch_policy"]["authorized_now"] is False
    assert decision["first_shadow_batch_policy"][
        "backfill_after_outcome_availability_allowed"
    ] is False
    assert all(
        item["status"] == "COMPLETE"
        for item in decision["sprint9_deliverables"].values()
    )


def test_sprint10_has_explicit_entry_gates_and_stop_conditions():
    decision = _decision()

    assert decision["sprint10"]["theme"] == "MODEL_V2_PRE_SHADOW_READINESS"
    assert len(decision["sprint10"]["work_packages"]) == 6
    assert "FINAL_SCORE_COVERAGE_GTE_90PCT_EVERY_MONTH" in decision[
        "shadow_entry_gates"
    ]
    assert "ALL_FIVE_FIXED_WEIGHT_FAMILIES_PRESENT_IN_EVERY_SCORED_ROW" in decision[
        "shadow_entry_gates"
    ]
    assert "LOCK_ONLY_COMMIT_IS_ONLY_CHANGE_AFTER_IMPLEMENTATION_COMMIT" in decision[
        "shadow_entry_gates"
    ]
    assert (
        "FINAL_SCORE_COVERAGE_CANNOT_REACH_90PCT_WITHOUT_RULE_RELAXATION"
        in decision["stop_conditions"]
    )
    assert (
        "RETURN_DRIVEN_FORMULA_WEIGHT_THRESHOLD_OR_ELIGIBILITY_CHANGE_REQUIRED"
        in decision["stop_conditions"]
    )


def test_human_report_states_the_decision_scope_and_claims_boundary():
    report = REPORT_PATH.read_text(encoding="utf-8")

    for heading in (
        "## Answer to the Sprint 9 question",
        "## Evidence that determines the decision",
        "## Decision by possible path",
        "## What Sprint 9 completed",
        "## Sprint 10 definition",
        "## Entry gate for shadow testing",
        "## Stop conditions",
        "## Claims and product boundary",
    ):
        assert heading in report
    assert "not broad, reliable, or investable enough" in report
    assert "implementation_ready_not_prediction_authorized" in report
    assert "claims_eligible=false" in report
