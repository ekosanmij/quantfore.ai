from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from quantfore_research.shadow.rehearsal import synthetic_executable_lock


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import rehearse_model_v2_shadow_ledger as rehearsal_pipeline  # noqa: E402


FIXTURE_PATH = REPOSITORY_ROOT / rehearsal_pipeline.DEFAULT_FIXTURE
REPORT_PATH = REPOSITORY_ROOT / rehearsal_pipeline.DEFAULT_REPORT
MARKDOWN_PATH = REPOSITORY_ROOT / rehearsal_pipeline.DEFAULT_MARKDOWN


@pytest.fixture(scope="module")
def stored_rehearsal():
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    return fixture, report


def test_synthetic_executable_lock_is_unambiguously_fixture_only():
    lock = synthetic_executable_lock()

    assert lock["fixture_only"] is True
    assert lock["claims_eligible"] is False
    assert lock["universe"]["universe_id"] == "test-shadow-universe"
    assert lock["lock_version"].startswith("synthetic-shadow-rehearsal-")


def test_rehearsal_passes_every_mechanical_control_without_authorizing_shadow(
    stored_rehearsal,
):
    _, report = stored_rehearsal

    assert report["decision"] == "PASS_SYNTHETIC_REHEARSAL_ONLY"
    assert report["claims_eligible"] is False
    assert report["fixture_only"] is True
    assert report["real_shadow_authorized"] is False
    assert report["outcomes_accessed"] is False
    assert all(control["passed"] for control in report["controls"].values())
    assert (
        report["blocked_pre_shadow_lock"]["status"]
        == "BLOCKED_COVERAGE_GATES_FAILED"
    )
    assert (
        report["blocked_pre_shadow_lock"]["executable_for_shadow_predictions"]
        is False
    )


def test_fixture_has_one_scored_one_excluded_no_labels_and_no_outcomes(
    stored_rehearsal,
):
    fixture, report = stored_rehearsal

    assert fixture["fixture_only"] is True
    assert fixture["real_shadow_authorized"] is False
    assert fixture["batch"]["expected_member_count"] == 2
    assert fixture["batch"]["scored_count"] == 1
    assert fixture["batch"]["excluded_count"] == 1
    assert {row["disposition"] for row in fixture["records"]} == {
        "SCORED",
        "EXCLUDED",
    }
    assert all(row["product_label"] is None for row in fixture["records"])
    assert all(
        set(row["outcome_fields"].values()) == {None}
        for row in fixture["records"]
    )
    assert fixture["outcomes"] == []
    assert report["counts"]["product_labels"] == 0
    assert report["counts"]["model_outcomes"] == 0
    assert report["counts"]["shadow_outcomes"] == 0


def test_versioned_fixture_and_report_reproduce_from_the_rehearsal_runner(
    stored_rehearsal,
):
    stored_fixture, stored_report = stored_rehearsal
    generated_at = datetime.fromisoformat(
        stored_report["generated_at"].replace("Z", "+00:00")
    )

    fresh_fixture, fresh_report = rehearsal_pipeline.run_rehearsal(
        repository_root=REPOSITORY_ROOT,
        generated_at=generated_at,
    )

    assert fresh_fixture == stored_fixture
    assert fresh_report == stored_report
    expected_fixture_hash = hashlib.sha256(
        rehearsal_pipeline._json_bytes(stored_fixture)
    ).hexdigest()
    assert stored_report["fixture"]["sha256"] == expected_fixture_hash
    assert MARKDOWN_PATH.read_text(encoding="utf-8") == (
        rehearsal_pipeline.render_markdown(stored_report)
    )
