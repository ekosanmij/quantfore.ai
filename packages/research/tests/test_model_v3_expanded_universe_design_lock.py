from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import create_model_v3_expanded_universe_design_lock as design_pipeline  # noqa: E402


LOCK_PATH = REPOSITORY_ROOT / design_pipeline.DEFAULT_OUTPUT


def _lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def test_design_lock_reproduces_and_is_strictly_non_executable():
    lock = design_pipeline.verify_design_lock(repository_root=REPOSITORY_ROOT)

    assert lock["status"] == "DESIGN_LOCK_PRE_FEASIBILITY"
    assert lock["decision"] == (
        "PROCEED_TO_OUTCOME_BLIND_UNIVERSE_FEASIBILITY_ONLY"
    )
    assert lock["claims_eligible"] is False
    assert lock["executable_for_data_acquisition"] is False
    assert lock["executable_for_score_rebuild"] is False
    assert lock["executable_for_shadow_predictions"] is False
    assert lock["executable_for_outcome_evaluation"] is False


def test_parent_failure_freeze_is_hash_bound_without_mutation():
    parent = _lock()["parent_failure_freeze"]
    path = REPOSITORY_ROOT / parent["path"]

    assert parent["status"] == "FROZEN_FAILED_NOT_SHADOW_READY"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == parent["sha256"]
    assert parent["frozen_baseline_commit"] == (
        "e18303e686f1946f83a5451e868a12cd1aa45375"
    )


def test_branch_feasibility_floor_is_derived_from_unchanged_v2_gates():
    feasibility = _lock()["structural_feasibility"]

    assert feasibility["minimum_eligible_names"] == 20
    assert feasibility["minimum_branch_score_coverage"] == 0.80
    assert feasibility["minimum_expected_names_per_active_branch"] == math.ceil(
        20 / 0.80
    )
    assert feasibility["gates"]["F1"]["threshold"] == 25
    assert feasibility["gates"]["F2"]["threshold"] == 20
    assert "may_not_be_deactivated" in feasibility["branch_activation_rule"]


def test_structural_denominator_cannot_shrink_with_data_or_outcomes():
    universe = _lock()["universe"]

    assert universe["universe_id"] == "us-listed-common-equity-pit-v1"
    assert universe["status"] == "SPECIFIED_NOT_ACQUIRED_OR_AUDITED"
    assert universe["point_in_time_membership_required"] is True
    assert universe["survivorship_free_required"] is True
    assert universe["delisted_history_preserved"] is True
    assert set(universe["prohibited_denominator_filters"]) >= {
        "price_availability",
        "filing_availability",
        "feature_completeness",
        "score_eligibility",
        "returns_or_outcomes",
    }


def test_v2_formula_schema_weights_and_engineering_thresholds_are_inherited():
    lock = _lock()
    model = lock["model"]
    gates = lock["inherited_engineering_gates"]

    assert model["formula_inheritance"]["formula_version"] == (
        "multifactor-v2-branch-formulas-v1"
    )
    assert model["formula_inheritance"]["branch_schema_sha256"] == (
        "e9dffef82107724149bd7fb6ddd64671ac0d39ca8c7753de915c65abf980a595"
    )
    assert set(model["family_weights"].values()) == {0.2}
    assert model["all_five_families_required"] is True
    assert model["cross_branch_fallback"] is False
    assert gates["E5"]["threshold"] == 0.9
    assert gates["E6"]["threshold"] == 0.8
    assert gates["E7"]["conditions"][0]["threshold"] == 20


def test_no_shadow_date_or_july_backfill_is_authorized():
    lock = _lock()
    schedule = lock["evaluation_protocol"]["forward_shadow_window"]

    assert schedule["status"] == "PENDING_POST_FEASIBILITY_EXECUTABLE_LOCK"
    assert schedule["dates"] == []
    assert schedule["july_2026_backfill_allowed"] is False
    assert "july_2026_shadow_backfill" in lock["prohibited_changes"]


def test_verifier_rejects_a_relaxed_structural_floor(tmp_path):
    tampered = _lock()
    tampered["structural_feasibility"][
        "minimum_expected_names_per_active_branch"
    ] = 20
    path = tmp_path / "tampered-v3-design-lock.json"
    path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="no longer reproduces"):
        design_pipeline.verify_design_lock(
            repository_root=REPOSITORY_ROOT, lock_path=path
        )


def test_contract_limits_next_action_to_structural_feasibility():
    contract = (
        REPOSITORY_ROOT
        / "experiments"
        / "multifactor-v3-expanded-universe-hypothesis-contract.md"
    ).read_text(encoding="utf-8")

    assert "Proceed only to an outcome-blind structural feasibility audit" in contract
    assert "ceil(20 minimum eligible names / 0.80 minimum branch coverage) = 25" in contract
    assert "July 2026 as a V3 shadow batch" in contract
    assert "deliberately non-executable" in contract
