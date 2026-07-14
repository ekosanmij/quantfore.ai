from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from quantfore_research.shadow.ledger import (
    LOCKED_SHADOW_DATES,
    _validate_executable_lock,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import decide_model_v2_first_shadow_batch as decision_pipeline  # noqa: E402


REPORT_PATH = REPOSITORY_ROOT / decision_pipeline.DEFAULT_REPORT
MARKDOWN_PATH = REPOSITORY_ROOT / decision_pipeline.DEFAULT_MARKDOWN


def _report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def test_first_real_shadow_batch_is_a_pre_target_fail_closed_no_go():
    report = _report()

    assert report["decision"] == "NO_GO_COVERAGE_GATES_FAILED"
    assert report["batch_status"] == "NOT_CREATED_BLOCKED_PRE_TARGET"
    assert report["claims_eligible"] is False
    assert report["real_shadow_authorized"] is False
    assert report["real_shadow_batch_created"] is False
    assert report["batch_id"] is None
    assert report["batch_hash"] is None
    assert report["target"]["recorded_before_prediction_timestamp"] is True
    assert report["target"]["target_changed"] is False


def test_no_go_record_proves_no_prediction_product_or_outcome_writes():
    report = _report()
    audit = report["write_audit"]

    assert audit == {
        "database_writes": 0,
        "outcome_records_created": 0,
        "outcomes_accessed": False,
        "prediction_records_created": 0,
        "product_labels_emitted": 0,
        "real_prediction_inputs_accessed": False,
        "return_metrics_accessed": False,
        "shadow_cli_invoked": False,
    }
    assert report["anti_backfill_controls"]["backfill_allowed"] is False
    assert (
        report["anti_backfill_controls"]["target_date_may_move_silently"]
        is False
    )


def test_target_remains_the_first_exact_locked_schedule_date():
    report = _report()

    assert report["target"]["prediction_date"] == LOCKED_SHADOW_DATES[0]
    assert report["target"]["prediction_timestamp"] == "2026-07-31T20:00:00Z"
    assert report["target"]["schedule_position"] == 1
    assert report["target"]["schedule_size"] == 24
    assert report["activation_conditions"]["readiness_gates_pass"] is False
    assert report["activation_conditions"]["executable_lock_status"] is False
    assert (
        report["activation_conditions"]["shadow_prediction_authorized"] is False
    )
    assert report["activation_conditions"]["synthetic_rehearsal_passed"] is True


def test_blocked_lock_remains_rejected_by_the_real_shadow_runtime():
    lock = json.loads(
        (REPOSITORY_ROOT / decision_pipeline.PRE_SHADOW_LOCK).read_text(
            encoding="utf-8"
        )
    )

    with pytest.raises(ValueError, match="status=EXECUTABLE_LOCKED"):
        _validate_executable_lock(
            lock,
            prediction_date=date(2026, 7, 31),
            universe_id="sp500-pit-v1",
            normalization_version="multifactor-v2-branch-normalization-v1",
            code_commit=lock["implementation"]["code_commit"],
        )


def test_missed_target_is_recorded_without_backfill_or_date_movement():
    after_target = decision_pipeline.build_decision(
        repository_root=REPOSITORY_ROOT,
        evaluated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )

    assert after_target["batch_status"] == "MISSED_NOT_BACKFILLED"
    assert after_target["anti_backfill_controls"]["missed_batch"] is True
    assert after_target["anti_backfill_controls"]["backfill_allowed"] is False
    assert after_target["target"]["prediction_date"] == "2026-07-31"
    assert after_target["target"]["target_changed"] is False
    assert after_target["real_shadow_batch_created"] is False


def test_versioned_decision_and_markdown_reproduce_exactly():
    stored = _report()
    evaluated_at = datetime.fromisoformat(
        stored["evaluated_at"].replace("Z", "+00:00")
    )
    fresh = decision_pipeline.build_decision(
        repository_root=REPOSITORY_ROOT,
        evaluated_at=evaluated_at,
    )

    assert fresh == stored
    assert MARKDOWN_PATH.read_text(encoding="utf-8") == (
        decision_pipeline.render_markdown(stored)
    )
    for binding in stored["evidence"].values():
        path = REPOSITORY_ROOT / binding["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding["sha256"]
