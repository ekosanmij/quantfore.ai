from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import decide_model_v3_outcome_blind_rebuild as rebuild_pipeline  # noqa: E402


DECISION_PATH = REPOSITORY_ROOT / rebuild_pipeline.DEFAULT_JSON_OUTPUT


def _decision() -> dict:
    return json.loads(DECISION_PATH.read_text(encoding="utf-8"))


def test_rebuild_is_formally_blocked_before_start():
    decision = rebuild_pipeline.verify_decision(repository_root=REPOSITORY_ROOT)

    assert decision["decision"] == "NO_GO_REBUILD_PREREQUISITES_FAILED"
    assert decision["status"] == "BLOCKED_BEFORE_REBUILD_START"
    assert decision["rebuild_authorized"] is False
    assert decision["rebuild_started"] is False
    assert decision["canonical_outputs_created"] is False
    assert decision["outcomes_accessed"] is False


def test_structural_data_and_lock_prerequisites_fail_closed():
    prerequisites = _decision()["prerequisites"]

    assert prerequisites["P1"]["passed"] is False
    assert prerequisites["P2"]["passed"] is False
    assert prerequisites["P3"]["passed"] is False
    assert prerequisites["P4"]["passed"] is False
    assert prerequisites["P5"]["passed"] is True
    assert all(
        item["exists"] is False for item in _decision()["required_inputs"].values()
    )


def test_original_coverage_and_breadth_gates_are_locked():
    gates = _decision()["locked_acceptance_gates"]

    assert gates["R1"]["threshold"] == 0.90
    assert gates["R2"]["threshold"] == 0.80
    assert gates["R3"]["threshold"] == 20
    assert gates["R4"]["conditions"][0]["threshold"] == 5
    assert gates["R4"]["conditions"][1]["threshold"] == 5
    assert gates["R5"]["threshold"] == 0.98
    assert gates["R6"]["threshold"] == 1.0
    assert gates["R7"]["threshold"] == 2
    assert gates["R8"]["threshold"] == 0
    assert gates["R9"]["threshold"] == 0


def test_protocol_requires_two_clean_rebuilds_without_fallback_or_outcomes():
    protocol = _decision()["rebuild_protocol"]
    authorization = _decision()["authorization"]

    assert protocol["clean_rebuild_count"] == 2
    assert protocol["expected_member_denominator_may_shrink"] is False
    assert protocol["populated_branch_deactivation_allowed"] is False
    assert protocol["cross_branch_normalization_or_fallback_allowed"] is False
    assert protocol["family_weight_renormalization_allowed"] is False
    assert protocol["outcome_or_return_columns_allowed"] is False
    assert authorization["feature_or_score_rebuild_authorized"] is False
    assert authorization["outcome_evaluation_authorized"] is False
    assert authorization["shadow_prediction_authorized"] is False
    assert authorization["july_2026_backfill_allowed"] is False


def test_no_canonical_v3_rebuild_outputs_exist():
    outputs = _decision()["canonical_output_inventory"]

    assert outputs
    assert all(item["exists"] is False for item in outputs.values())


def test_verifier_rejects_an_unauthorized_go_mutation(tmp_path):
    tampered = _decision()
    tampered["rebuild_authorized"] = True
    tampered["authorization"]["feature_or_score_rebuild_authorized"] = True
    path = tmp_path / "tampered-rebuild-decision.json"
    path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="no longer reproduces"):
        rebuild_pipeline.verify_decision(
            repository_root=REPOSITORY_ROOT,
            json_path=path,
            markdown_path=rebuild_pipeline.DEFAULT_MARKDOWN_OUTPUT,
        )
