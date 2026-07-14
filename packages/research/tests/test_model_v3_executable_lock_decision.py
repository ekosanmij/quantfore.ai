from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import decide_model_v3_executable_lock as lock_pipeline  # noqa: E402


DECISION_PATH = REPOSITORY_ROOT / lock_pipeline.DEFAULT_JSON_OUTPUT


def _decision() -> dict:
    return json.loads(DECISION_PATH.read_text(encoding="utf-8"))


def test_executable_lock_gate_closes_as_no_go():
    decision = lock_pipeline.verify_decision(repository_root=REPOSITORY_ROOT)

    assert decision["decision"] == "NO_GO_EXECUTABLE_LOCK_PREREQUISITES_FAILED"
    assert decision["status"] == "BLOCKED_NO_EXECUTABLE_LOCK_OR_SHADOW_DATE"
    assert decision["executable_lock_created"] is False
    assert decision["shadow_date_selected"] is False
    assert decision["prediction_schedule"] == []
    assert decision["real_shadow_batch_created"] is False
    assert decision["outcomes_accessed"] is False


def test_structural_rebuild_and_coverage_prerequisites_fail():
    prerequisites = _decision()["prerequisites"]

    assert prerequisites["L1"]["passed"] is True
    assert prerequisites["L2"]["passed"] is False
    assert prerequisites["L3"]["passed"] is False
    assert prerequisites["L4"]["passed"] is False
    assert prerequisites["L5"]["passed"] is False
    assert prerequisites["L6"]["passed"] is True
    assert prerequisites["L7"]["passed"] is True


def test_required_executable_bindings_are_incomplete():
    decision = _decision()
    bindings = decision["required_executable_lock_bindings"]

    assert decision["all_required_bindings_complete"] is False
    assert bindings["formula_and_branch_schema_sha256"] == (
        "e9dffef82107724149bd7fb6ddd64671ac0d39ca8c7753de915c65abf980a595"
    )
    assert bindings["implementation_code_commit"] is None
    assert bindings["expanded_universe_manifest_sha256"] is None
    assert bindings["two_rebuild_fingerprint_sha256"] is None
    assert bindings["prediction_schedule_sha256"] is None
    assert bindings["portfolio_notional_usd"] is None


def test_schedule_is_strictly_prospective_and_july_is_not_backfillable():
    rule = _decision()["prospective_schedule_rule"]
    authorization = _decision()["authorization"]

    assert rule["schedule_may_be_selected_now"] is False
    assert rule["scheduled_monthly_cohorts_after_go"] == 24
    assert rule["first_boundary_strictly_after_executable_lock_commit"] is True
    assert rule["first_boundary_must_be_operationally_reachable"] is True
    assert rule["july_2026_backfill_allowed"] is False
    assert authorization["create_executable_lock_authorized"] is False
    assert authorization["select_shadow_schedule_authorized"] is False
    assert authorization["create_real_shadow_prediction_authorized"] is False
    assert authorization["evaluate_outcomes_authorized"] is False
    assert authorization["july_2026_backfill_allowed"] is False


def test_no_executable_lock_schedule_or_batch_artifact_exists():
    outputs = _decision()["output_inventory"]

    assert all(item["exists"] is False for item in outputs.values())


def test_verifier_rejects_a_retroactive_shadow_date(tmp_path):
    tampered = _decision()
    tampered["shadow_date_selected"] = True
    tampered["prediction_schedule"] = ["2026-07-31"]
    tampered["authorization"]["july_2026_backfill_allowed"] = True
    path = tmp_path / "tampered-executable-lock-decision.json"
    path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="no longer reproduces"):
        lock_pipeline.verify_decision(
            repository_root=REPOSITORY_ROOT,
            json_path=path,
            markdown_path=lock_pipeline.DEFAULT_MARKDOWN_OUTPUT,
        )
