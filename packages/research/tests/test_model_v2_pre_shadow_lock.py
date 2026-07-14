from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from quantfore_research.shadow.ledger import (
    LOCKED_SHADOW_DATES,
    SHADOW_HORIZONS,
    _validate_executable_lock,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import create_model_v2_pre_shadow_lock as lock_pipeline  # noqa: E402


SHA256 = "a" * 64
COMMIT = "b" * 40


def _hash_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _binding(name: str) -> dict[str, str]:
    return {"path": name, "sha256": SHA256}


def _failed_readiness() -> dict:
    return {
        "decision": "FAIL_NOT_READY_FOR_EXECUTABLE_LOCK",
        "criteria": {
            "final_score_coverage_every_month": {
                "passed": False,
                "minimum_observed": 0.0,
            },
            "known_branch_or_subtype_every_month": {
                "passed": True,
                "minimum_observed": 0.981,
            },
            "represented_active_branches_every_month": {
                "passed": False,
                "minimum_observed": 0,
            },
            "represented_sectors_every_month": {
                "passed": False,
                "minimum_observed": 0,
            },
        },
        "reconciliation": {
            "expected_stock_months": 100,
            "scored_stock_months": 30,
            "excluded_stock_months": 70,
            "final_disposition_fraction": 1.0,
        },
        "coverage": {"aggregate_final_score_coverage": 0.3},
    }


def _lock() -> dict:
    source = {"source": _binding("source.json")}
    score_manifest = {
        "model_version": "multifactor-v2-branch-aware-equal-weight-v1",
        "feature_version": "multifactor-v2-branch-aware-v1",
        "formula_version": "multifactor-v2-branch-formulas-v1",
        "normalization_version": "multifactor-v2-branch-normalization-v1",
        "minimum_branch_cross_section": 20,
    }
    design_lock = {
        "model": {"classification_version": "sec-sic-financial-subtype-v2"},
        "universe": {"universe_id": "sp500-pit-v1"},
        "portfolio_protocol": {
            "one_way_cost_scenarios_bps": [0, 10, 25, 50],
            "primary_one_way_cost_bps": 25,
            "stress_one_way_cost_bps": 50,
        },
    }
    return lock_pipeline._assemble_lock(
        implementation_commit=COMMIT,
        locked_at=datetime(2026, 7, 14, 12, tzinfo=timezone.utc),
        design_lock=design_lock,
        design_lock_binding=_binding("design.json"),
        readiness=_failed_readiness(),
        readiness_binding=_binding("readiness.json"),
        score_manifest=score_manifest,
        formula_ledger={"version": "formula-v1", "sha256": SHA256},
        classification_ledger=_binding("classification.jsonl.gz"),
        source_manifests=source,
        implementation_sources=source,
        evaluation_sources=source,
        report_artifacts=source,
        shadow_sources=source,
        reproducible_local_ledgers={
            "scores": {
                "relative_path": "scores.jsonl.gz",
                "sha256": SHA256,
                "rows": 100,
                "storage": "LOCAL_REPRODUCIBLE_NOT_IN_GIT",
            }
        },
    )


def test_failed_readiness_creates_a_hash_bound_non_executable_lock():
    lock = _lock()

    assert lock["status"] == "BLOCKED_COVERAGE_GATES_FAILED"
    assert lock["activation_decision"] == "DO_NOT_START_SHADOW"
    assert lock["claims_eligible"] is False
    assert lock["executable_for_shadow_predictions"] is False
    assert lock["executable_for_outcome_evaluation"] is False
    assert lock["shadow_start_authorized"] is False
    assert lock["implementation"]["code_commit"] == COMMIT
    assert lock["implementation"]["portfolio_notional_usd"] is None
    assert lock["implementation"]["source_manifest_sha256"] == _hash_json(
        lock["source_manifests"]
    )
    assert "FAILED_CRITERION:final_score_coverage_every_month" in lock[
        "blocked_reasons"
    ]


def test_lock_binds_the_exact_schedule_costs_formulas_and_shadow_rules():
    lock = _lock()

    assert tuple(lock["prediction_schedule"]["dates"]) == LOCKED_SHADOW_DATES
    assert lock["prediction_schedule"]["sha256"] == _hash_json(
        list(LOCKED_SHADOW_DATES)
    )
    assert lock["model"]["required_horizons"] == list(SHADOW_HORIZONS)
    assert set(lock["model"]["family_weights"].values()) == {0.2}
    assert lock["costs"]["sha256"] == _hash_json(lock["costs"]["protocol"])
    assert lock["formula_ledger"]["sha256"] == SHA256
    assert lock["shadow_ledger"]["rules_sha256"] == _hash_json(
        lock["shadow_ledger"]["rules"]
    )


def test_shadow_runtime_rejects_the_blocked_lock():
    lock = _lock()

    with pytest.raises(ValueError, match="status=EXECUTABLE_LOCKED"):
        _validate_executable_lock(
            lock,
            prediction_date=datetime(2026, 7, 31).date(),
            universe_id="sp500-pit-v1",
            normalization_version="multifactor-v2-branch-normalization-v1",
            code_commit=COMMIT,
        )


def test_lock_builder_refuses_to_misstate_a_passing_readiness_report():
    readiness = _failed_readiness()
    readiness["decision"] = "PASS_READY_FOR_EXECUTABLE_LOCK"

    with pytest.raises(ValueError, match="requires FAIL_NOT_READY"):
        lock_pipeline._failed_criteria(readiness)
