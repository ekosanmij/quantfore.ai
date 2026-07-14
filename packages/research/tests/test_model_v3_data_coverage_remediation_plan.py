from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import create_model_v3_data_coverage_remediation_plan as plan_pipeline  # noqa: E402


PLAN_PATH = REPOSITORY_ROOT / plan_pipeline.DEFAULT_OUTPUT


def _plan() -> dict:
    return json.loads(PLAN_PATH.read_text(encoding="utf-8"))


def test_blocked_plan_reproduces_and_cannot_self_authorize():
    plan = plan_pipeline.verify_plan(repository_root=REPOSITORY_ROOT)

    assert plan["status"] == "BLOCKED_STRUCTURAL_FEASIBILITY_INPUT_MISSING"
    assert plan["blocking_gate"]["passed"] is False
    assert plan["claims_eligible"] is False
    assert plan["executable_for_data_acquisition"] is False
    assert plan["executable_for_score_rebuild"] is False
    assert plan["outcomes_accessed"] is False


def test_plan_retains_measured_v2_priorities_without_using_them_as_v3_proof():
    baseline = _plan()["v2_prioritization_baseline"]

    assert baseline["expected_security_months"] == 50600
    assert baseline["scored_security_months"] == 16349
    assert baseline["aggregate_final_score_coverage"] == 0.323102766798419
    assert baseline["complete_family_readiness"]["quality"] == 0.18511857707509882
    assert baseline["sec_normalization_gaps"]["missing_filing_evidence"] == 246986
    assert baseline["existing_price_request_start"] == "2013-01-01"
    assert "cannot prove Model V3" in baseline["baseline_scope_warning"]


def test_work_packages_are_ordered_and_keep_original_readiness_gates():
    work = _plan()["ordered_work_packages"]

    assert [row["id"] for row in work] == [
        "W0",
        "W1",
        "W2",
        "W3",
        "W4",
        "W5",
        "W6",
    ]
    assert work[0]["status"] == "BLOCKING_INPUT_MISSING"
    assert all(row["status"] == "BLOCKED_BY_W0" for row in work[1:])
    final = work[-1]["acceptance"]
    assert final["minimum_overall_score_coverage_every_month"] == 0.90
    assert final["minimum_active_branch_score_coverage_every_month"] == 0.80
    assert final["minimum_eligible_names_per_active_branch_every_month"] == 20
    assert final["identical_clean_rebuilds"] == 2
    assert final["fallback_or_outcome_access_count"] == 0


def test_quality_and_specialist_inputs_are_explicit_priorities():
    work = {row["id"]: row for row in _plan()["ordered_work_packages"]}

    assert work["W4"]["priority_family"] == "quality"
    assert work["W4"]["priority_components"] == [
        "gross_profitability",
        "roic",
        "fcf_conversion",
    ]
    assert set(work["W5"]["required_concepts"]) >= {
        "loans_and_leases_net",
        "customer_deposits",
        "premiums_earned_net",
        "policyholder_benefits_claims_net",
        "real_estate_investment_property_net",
        "diluted_shares",
    }


def test_plan_rejects_an_authorization_mutation(tmp_path):
    tampered = _plan()
    tampered["executable_for_data_acquisition"] = True
    path = tmp_path / "tampered-plan.json"
    path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="cannot self-authorize"):
        plan_pipeline.verify_plan(repository_root=REPOSITORY_ROOT, plan_path=path)
