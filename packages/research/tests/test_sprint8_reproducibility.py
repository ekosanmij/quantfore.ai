import hashlib
import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from pipelines.close_multifactor_sprint import (
    Sprint8RebuildArtifacts,
    _audit_source_snapshot_ids,
    build_sprint8_closure_document,
    render_markdown,
    validate_rebuild_program,
    validate_sprint7_prerequisite,
)
from quantfore_research.validation.sprint8_reproducibility import (
    Sprint8RebuildFingerprint,
    _require_exact_document,
    compare_sprint8_rebuilds,
)


def fingerprint():
    return Sprint8RebuildFingerprint(
        fundamental_fact_hash="1" * 64,
        availability_revision_hash="2" * 64,
        feature_count=190,
        feature_value_hash="3" * 64,
        monthly_eligible_universe_hash="4" * 64,
        prediction_count=40,
        outcome_count=40,
        prediction_outcome_hash="5" * 64,
        backtest_metrics_hash="6" * 64,
        canonical_report_hashes={
            "fundamental_audit": "7" * 64,
            "multifactor_backtest": "8" * 64,
            "price_vs_multifactor": "9" * 64,
        },
    )


def artifacts(value=None):
    return Sprint8RebuildArtifacts(
        fingerprint=value or fingerprint(),
        audit={"decision": "pass"},
        backtest={"report_id": "pit_multifactor_baseline_v1"},
        comparison={"report_id": "price-vs-multifactor-v1"},
    )


def test_every_sprint8_rebuild_invariant_must_match():
    first = fingerprint()
    identical = compare_sprint8_rebuilds(first, first)
    changed = compare_sprint8_rebuilds(
        first, replace(first, feature_value_hash="0" * 64)
    )

    assert identical["all_matched"] is True
    assert set(identical["checks"]) == {
        "fundamental_fact_hash",
        "availability_revision_hash",
        "feature_count",
        "feature_value_hash",
        "monthly_eligible_universe_hash",
        "prediction_count",
        "outcome_count",
        "prediction_outcome_hash",
        "backtest_metrics_hash",
        "canonical_report_hashes",
    }
    assert changed["all_matched"] is False
    assert changed["checks"]["feature_value_hash"]["matched"] is False


def test_supplied_report_must_equal_database_calculation():
    calculated = {"evaluation": {"mean_rank_ic": 0.04}}

    _require_exact_document(calculated, calculated, report_name="backtest")
    with pytest.raises(ValueError, match="does not reproduce from closure database"):
        _require_exact_document(
            {"evaluation": {"mean_rank_ic": 0.40}},
            calculated,
            report_name="backtest",
        )


def test_closure_requires_two_identical_rebuilds_and_records_prerequisite():
    document = build_sprint8_closure_document(
        first=artifacts(),
        second=artifacts(),
        git_commit="abc123",
        bundle_manifest_sha256="a" * 64,
        sprint7_closure_sha256="b" * 64,
        rebuild_program_sha256="c" * 64,
        generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )

    assert document["closure_decision"] == "pass"
    assert document["claims_eligible"] is False
    assert document["configuration"]["rebuild_count"] == 2
    assert document["reproducibility"]["all_matched"] is True
    assert document["sprint7_closure_sha256"] == "b" * 64
    assert document["rebuild_program_sha256"] == "c" * 64
    assert "Two fresh databases" in document["definition_of_done"]
    assert "Sprint 8 Reproducibility" in render_markdown(document)

    with pytest.raises(ValueError, match="differ"):
        build_sprint8_closure_document(
            first=artifacts(),
            second=artifacts(replace(fingerprint(), outcome_count=39)),
            git_commit="abc123",
            bundle_manifest_sha256="a" * 64,
            sprint7_closure_sha256="b" * 64,
            rebuild_program_sha256="c" * 64,
            generated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )


def test_sprint7_closure_is_a_hash_bound_hard_prerequisite(tmp_path):
    document = {
        "schema_version": "sprint7_reproducibility_closure_v1",
        "closure_decision": "pass",
        "clean_worktree_verified": True,
        "claims_eligible": False,
    }
    body = (json.dumps(document, sort_keys=True) + "\n").encode()
    path = tmp_path / "sprint7.json"
    path.write_bytes(body)

    assert validate_sprint7_prerequisite(
        path, expected_sha256=hashlib.sha256(body).hexdigest()
    ) == document
    with pytest.raises(ValueError, match="SHA-256"):
        validate_sprint7_prerequisite(path, expected_sha256="0" * 64)


def test_rebuild_program_is_hash_bound_before_execution(tmp_path):
    program = tmp_path / "rebuild.py"
    body = b"#!/usr/bin/env python3\n"
    program.write_bytes(body)
    expected = hashlib.sha256(body).hexdigest()

    assert validate_rebuild_program(program, expected_sha256=expected) == expected
    with pytest.raises(ValueError, match="rebuild program SHA-256"):
        validate_rebuild_program(program, expected_sha256="0" * 64)


def test_closure_discovers_each_rebuilds_hash_bound_source_snapshot():
    assert _audit_source_snapshot_ids(
        {
            "source_snapshot_hashes": {
                "snapshot-b": "b" * 64,
                "snapshot-a": "a" * 64,
            }
        }
    ) == ("snapshot-a", "snapshot-b")

    with pytest.raises(ValueError, match="source snapshot bindings"):
        _audit_source_snapshot_ids({"source_snapshot_hashes": {}})
