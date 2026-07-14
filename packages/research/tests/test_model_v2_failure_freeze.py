from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import freeze_model_v2_failure_evidence as freeze_pipeline  # noqa: E402


FREEZE_PATH = REPOSITORY_ROOT / freeze_pipeline.DEFAULT_OUTPUT


def _freeze() -> dict:
    return json.loads(FREEZE_PATH.read_text(encoding="utf-8"))


def test_frozen_failure_chain_verifies_against_the_sprint_10_merge_commit():
    verified = freeze_pipeline.verify_failure_freeze(
        repository_root=REPOSITORY_ROOT
    )

    assert verified["status"] == "FROZEN_FAILED_NOT_SHADOW_READY"
    assert verified["frozen_baseline_commit"] == (
        "e18303e686f1946f83a5451e868a12cd1aa45375"
    )
    assert verified["claims_eligible"] is False
    assert verified["shadow_authorized"] is False


def test_every_frozen_file_matches_its_hash_and_baseline_bytes():
    manifest = _freeze()
    baseline = manifest["frozen_baseline_commit"]

    for binding in manifest["evidence_bindings"]:
        path = REPOSITORY_ROOT / binding["path"]
        current = path.read_bytes()
        baseline_bytes = subprocess.check_output(
            ["git", "show", f"{baseline}:{binding['path']}"],
            cwd=REPOSITORY_ROOT,
        )
        assert current == baseline_bytes
        assert hashlib.sha256(current).hexdigest() == binding["sha256"]


def test_model_thresholds_and_failure_metrics_are_snapshotted_exactly():
    manifest = _freeze()
    contract = manifest["locked_design_contract"]["payload"]
    model = contract["model"]
    engineering = contract["engineering_gates"]
    failure = manifest["observed_failure"]["payload"]

    assert model["all_five_families_required"] is True
    assert model["minimum_component_coverage"] == 0.8
    assert model["minimum_required_components_per_family"] == 0.6
    assert model["minimum_branch_cross_section"] == 20
    assert model["cross_branch_fallback"] is False
    assert engineering["E4"]["threshold"] == 0.98
    assert engineering["E5"]["threshold"] == 0.9
    assert engineering["E6"]["threshold"] == 0.8
    assert engineering["E7"]["conditions"][0]["threshold"] == 20
    assert failure["decision"] == "FAIL_NOT_READY_FOR_EXECUTABLE_LOCK"
    assert failure["aggregate_final_score_coverage"] == 0.323102766798419
    assert failure["reconciliation"]["expected_stock_months"] == 50600
    assert failure["reconciliation"]["scored_stock_months"] == 16349


def test_closure_chain_remains_blocked_and_contains_no_real_batch():
    closure = _freeze()["closure_chain"]

    assert closure == {
        "first_batch_decision": "NO_GO_COVERAGE_GATES_FAILED",
        "pre_shadow_lock_executable": False,
        "pre_shadow_lock_status": "BLOCKED_COVERAGE_GATES_FAILED",
        "readiness_decision": "FAIL_NOT_READY_FOR_EXECUTABLE_LOCK",
        "real_shadow_batch_created": False,
        "rehearsal_decision": "PASS_SYNTHETIC_REHEARSAL_ONLY",
        "rehearsal_real_shadow_authorized": False,
    }


def test_verifier_rejects_a_rebound_evidence_hash(tmp_path):
    tampered = _freeze()
    tampered["evidence_bindings"][0]["sha256"] = "0" * 64
    manifest_path = tmp_path / "tampered-freeze.json"
    manifest_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="bindings no longer reproduce"):
        freeze_pipeline.verify_failure_freeze(
            repository_root=REPOSITORY_ROOT,
            manifest_path=manifest_path,
        )


def test_freeze_contract_requires_append_only_versioned_remediation():
    contract = (
        REPOSITORY_ROOT
        / "docs"
        / "research"
        / "model-v2-failure-evidence-freeze-v1.md"
    ).read_text(encoding="utf-8")

    assert "Frozen paths are immutable" in contract
    assert "Thresholds may not be relaxed in place" in contract
    assert "new model version and a new design lock" in contract
    assert "July 2026 batch remains non-backfillable" in contract
