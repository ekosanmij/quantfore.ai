from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import decide_model_v3_structural_feasibility_gate as gate_pipeline  # noqa: E402


DECISION_PATH = REPOSITORY_ROOT / gate_pipeline.DEFAULT_JSON_OUTPUT


def _decision() -> dict:
    return json.loads(DECISION_PATH.read_text(encoding="utf-8"))


def test_gate_decision_reproduces_as_a_no_go_before_rebuild():
    decision = gate_pipeline.verify_decision(repository_root=REPOSITORY_ROOT)

    assert decision["decision"] == "NO_GO_MISSING_EXPANDED_UNIVERSE_EVIDENCE"
    assert decision["status"] == "CLOSED_NO_GO_BEFORE_REBUILD"
    assert decision["claims_eligible"] is False
    assert decision["outcomes_accessed"] is False
    assert decision["audit_result"]["evaluated_months"] == 0
    assert decision["audit_result"]["failed_criteria"] == [
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
    ]


def test_gate_preserves_the_derived_25_name_structural_floor():
    rule = _decision()["structural_rule"]

    assert rule["minimum_eligible_names_per_active_branch"] == 20
    assert rule["minimum_active_branch_coverage"] == 0.80
    assert rule["minimum_expected_names_formula"] == "ceil(20 / 0.80)"
    assert rule["minimum_expected_names_per_populated_branch"] == 25
    assert rule["populated_branches_may_be_deactivated"] is False
    assert rule["denominator_may_shrink_for_missing_data"] is False


def test_no_go_blocks_every_expensive_or_shadow_action():
    authorization = _decision()["authorization"]

    assert authorization["accounting_or_price_acquisition_authorized"] is False
    assert authorization["feature_or_score_rebuild_authorized"] is False
    assert authorization["executable_lock_authorized"] is False
    assert authorization["shadow_prediction_authorized"] is False
    assert authorization["outcome_evaluation_authorized"] is False
    assert authorization["july_2026_backfill_allowed"] is False
    assert authorization[
        "structural_evidence_acquisition_requires_separate_authorization"
    ] is True


def test_next_action_is_only_the_expanded_universe_denominator():
    next_action = _decision()["next_required_action"]

    assert next_action["id"] == "W0_EXPANDED_UNIVERSE_DENOMINATOR"
    assert next_action["required_artifact"] == (
        "data/raw/model-v3/us-listed-common-equity-pit-v1/manifest.json"
    )
    assert next_action["required_decision_to_continue"] == (
        "PASS_STRUCTURALLY_FEASIBLE"
    )
    assert next_action["threshold_changes_allowed"] is False
    assert next_action["return_or_outcome_access_allowed"] is False


def test_gate_rejects_a_score_rebuild_authorization_mutation(tmp_path):
    tampered = _decision()
    tampered["authorization"]["feature_or_score_rebuild_authorized"] = True
    path = tmp_path / "tampered-gate.json"
    path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="no longer reproduces"):
        gate_pipeline.verify_decision(
            repository_root=REPOSITORY_ROOT,
            json_path=path,
            markdown_path=gate_pipeline.DEFAULT_MARKDOWN_OUTPUT,
        )
