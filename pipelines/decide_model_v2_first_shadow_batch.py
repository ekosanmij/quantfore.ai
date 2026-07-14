"""Record Sprint 10.8's conditional first-shadow decision without backfilling."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import REPOSITORY_ROOT
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import REPOSITORY_ROOT  # type: ignore

from quantfore_research.shadow.ledger import LOCKED_SHADOW_DATES


DEFAULT_REPORT = Path(
    "reports/reproducibility/model-v2-first-shadow-batch-decision-v1.json"
)
DEFAULT_MARKDOWN = Path(
    "reports/reproducibility/model-v2-first-shadow-batch-decision-v1.md"
)
READINESS_REPORT = Path(
    "reports/data-audits/model-v2-coverage-readiness-v1.json"
)
PRE_SHADOW_LOCK = Path("experiments/model-v2-pre-shadow-lock-v1.json")
REHEARSAL_REPORT = Path(
    "reports/reproducibility/model-v2-shadow-ledger-rehearsal-v1.json"
)
DESIGN_LOCK = Path("experiments/multifactor-v2-hypothesis-lock-v1.json")
TARGET_PREDICTION_TIMESTAMP = datetime(
    2026, 7, 31, 20, 0, tzinfo=timezone.utc
)
RECORDED_REPOSITORY_BASE_COMMIT = "983c6395c0f5688093874aab586518d4dfdddefb"
RECORDED_IMPLEMENTATION_COMMIT = "1962b926839f105a5e04cf5231b44f45c998c562"
SOURCE_PATHS = (
    Path("pipelines/decide_model_v2_first_shadow_batch.py"),
    Path("pipelines/create_shadow_predictions.py"),
    Path("packages/research/quantfore_research/shadow/ledger.py"),
    Path("packages/research/tests/test_model_v2_first_shadow_decision.py"),
)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON document must contain an object: {path}")
    return value


def _binding(root: Path, relative: Path) -> dict[str, str]:
    absolute = root / relative
    if not absolute.is_file():
        raise ValueError(f"required decision evidence is missing: {relative}")
    return {"path": relative.as_posix(), "sha256": _sha256_file(absolute)}


def _assert_recorded_commit(root: Path, commit: str) -> None:
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", commit, "HEAD"],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("recorded first-batch commit is not an ancestor") from exc


def _recorded_base_commit(root: Path) -> str:
    _assert_recorded_commit(root, RECORDED_REPOSITORY_BASE_COMMIT)
    _assert_recorded_commit(root, RECORDED_IMPLEMENTATION_COMMIT)
    return RECORDED_REPOSITORY_BASE_COMMIT


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _source_bindings(root: Path) -> list[dict[str, str]]:
    _assert_recorded_commit(root, RECORDED_IMPLEMENTATION_COMMIT)
    bindings = []
    for path in SOURCE_PATHS:
        try:
            source_bytes = subprocess.check_output(
                [
                    "git",
                    "show",
                    f"{RECORDED_IMPLEMENTATION_COMMIT}:{path.as_posix()}",
                ],
                cwd=root,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                f"recorded first-batch source is missing: {path}"
            ) from exc
        bindings.append(
            {
                "path": path.as_posix(),
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            }
        )
    return bindings


def _failed_criteria(readiness: Mapping[str, Any]) -> list[str]:
    criteria = readiness.get("criteria")
    if not isinstance(criteria, Mapping):
        raise ValueError("readiness report is missing criteria")
    return sorted(
        str(name)
        for name, result in criteria.items()
        if isinstance(result, Mapping) and result.get("passed") is not True
    )


def build_decision(
    *, repository_root: Path, evaluated_at: datetime
) -> dict[str, Any]:
    """Build a no-go or missed-batch record from the frozen readiness chain."""

    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must include a timezone")
    evaluated = evaluated_at.astimezone(timezone.utc)
    root = repository_root.resolve()
    readiness = _load_json(root / READINESS_REPORT)
    pre_shadow_lock = _load_json(root / PRE_SHADOW_LOCK)
    rehearsal = _load_json(root / REHEARSAL_REPORT)

    schedule = pre_shadow_lock.get("prediction_schedule", {}).get("dates")
    if not isinstance(schedule, list) or tuple(schedule) != LOCKED_SHADOW_DATES:
        raise ValueError("pre-shadow lock does not retain the exact shadow schedule")
    if schedule[0] != TARGET_PREDICTION_TIMESTAMP.date().isoformat():
        raise ValueError("Sprint 10.8 target differs from the first locked date")

    readiness_binding = _binding(root, READINESS_REPORT)
    lock_binding = _binding(root, PRE_SHADOW_LOCK)
    rehearsal_binding = _binding(root, REHEARSAL_REPORT)
    if pre_shadow_lock.get("readiness", {}).get("report", {}).get(
        "sha256"
    ) != readiness_binding["sha256"]:
        raise ValueError("pre-shadow lock readiness binding no longer reproduces")
    if rehearsal.get("blocked_pre_shadow_lock", {}).get(
        "sha256"
    ) != lock_binding["sha256"]:
        raise ValueError("rehearsal does not bind the current pre-shadow lock")

    activation_conditions = {
        "readiness_gates_pass": (
            readiness.get("decision") == "PASS_READY_FOR_EXECUTABLE_LOCK"
        ),
        "executable_lock_status": (
            pre_shadow_lock.get("status") == "EXECUTABLE_LOCKED"
        ),
        "shadow_prediction_authorized": (
            pre_shadow_lock.get("executable_for_shadow_predictions") is True
            and pre_shadow_lock.get("shadow_start_authorized") is True
        ),
        "synthetic_rehearsal_passed": (
            rehearsal.get("decision") == "PASS_SYNTHETIC_REHEARSAL_ONLY"
        ),
        "decision_recorded_before_prediction_timestamp": (
            evaluated < TARGET_PREDICTION_TIMESTAMP
        ),
    }
    gates_ready = all(
        activation_conditions[name]
        for name in (
            "readiness_gates_pass",
            "executable_lock_status",
            "shadow_prediction_authorized",
            "synthetic_rehearsal_passed",
        )
    )
    if gates_ready:
        raise RuntimeError(
            "all activation gates pass; use the committed real-batch workflow "
            "instead of creating a no-go record"
        )

    missed = evaluated >= TARGET_PREDICTION_TIMESTAMP
    failed_activation_conditions = sorted(
        name
        for name, passed in activation_conditions.items()
        if name != "decision_recorded_before_prediction_timestamp" and not passed
    )
    failed_coverage_criteria = _failed_criteria(readiness)
    if readiness.get("decision") == "FAIL_NOT_READY_FOR_EXECUTABLE_LOCK":
        decision = "NO_GO_COVERAGE_GATES_FAILED"
    else:
        decision = "NO_GO_ACTIVATION_CONDITIONS_FAILED"

    decision_core = {
        "decision": decision,
        "evaluated_at": _timestamp_text(evaluated),
        "target_prediction_timestamp": _timestamp_text(
            TARGET_PREDICTION_TIMESTAMP
        ),
        "batch_status": (
            "MISSED_NOT_BACKFILLED" if missed else "NOT_CREATED_BLOCKED_PRE_TARGET"
        ),
        "failed_activation_conditions": failed_activation_conditions,
        "failed_coverage_criteria": failed_coverage_criteria,
        "evidence_sha256": {
            "readiness": readiness_binding["sha256"],
            "pre_shadow_lock": lock_binding["sha256"],
            "rehearsal": rehearsal_binding["sha256"],
        },
    }
    report = {
        "schema_version": "model-v2-first-shadow-batch-decision-v1",
        "decision": decision,
        "decision_record_sha256": _hash_json(decision_core),
        "evaluated_at": _timestamp_text(evaluated),
        "claims_eligible": False,
        "real_shadow_authorized": False,
        "real_shadow_batch_created": False,
        "batch_id": None,
        "batch_hash": None,
        "batch_status": decision_core["batch_status"],
        "target": {
            "prediction_date": TARGET_PREDICTION_TIMESTAMP.date().isoformat(),
            "prediction_timestamp": _timestamp_text(
                TARGET_PREDICTION_TIMESTAMP
            ),
            "schedule_position": 1,
            "schedule_size": len(schedule),
            "target_changed": False,
            "recorded_before_prediction_timestamp": not missed,
        },
        "activation_conditions": activation_conditions,
        "failed_activation_conditions": failed_activation_conditions,
        "failed_coverage_criteria": failed_coverage_criteria,
        "evidence": {
            "readiness_report": readiness_binding,
            "pre_shadow_lock": lock_binding,
            "synthetic_rehearsal_report": rehearsal_binding,
            "design_lock": _binding(root, DESIGN_LOCK),
        },
        "evidence_decisions": {
            "readiness": readiness.get("decision"),
            "pre_shadow_lock_status": pre_shadow_lock.get("status"),
            "pre_shadow_lock_activation": pre_shadow_lock.get(
                "activation_decision"
            ),
            "synthetic_rehearsal": rehearsal.get("decision"),
        },
        "write_audit": {
            "shadow_cli_invoked": False,
            "database_writes": 0,
            "prediction_records_created": 0,
            "product_labels_emitted": 0,
            "outcome_records_created": 0,
            "real_prediction_inputs_accessed": False,
            "return_metrics_accessed": False,
            "outcomes_accessed": False,
        },
        "anti_backfill_controls": {
            "backfill_allowed": False,
            "target_date_may_move_silently": False,
            "missed_batch": missed,
            "required_action_if_target_passes": (
                "retain MISSED_NOT_BACKFILLED and select any later cohort "
                "prospectively before its prediction timestamp"
            ),
        },
        "implementation": {
            "repository_base_commit": _recorded_base_commit(root),
            "source_bindings": _source_bindings(root),
        },
        "next_action": (
            "Expand point-in-time accounting and branch coverage, rerun Sprint "
            "10.5 outcome-blind, and create a new executable lock only after every "
            "gate passes. Do not create or backfill the 2026-07-31 batch from this "
            "blocked state."
        ),
        "claims_boundary": (
            "This is a fail-closed operational decision, not a prediction, model "
            "performance claim, recommendation, or product output."
        ),
    }
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    conditions = report["activation_conditions"]
    lines = [
        "# Model V2 First Shadow Batch Decision v1",
        "",
        "`claims_eligible=false`",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Batch status: `{report['batch_status']}`",
        f"- Target: `{report['target']['prediction_timestamp']}`",
        "- Real shadow batch created: `false`",
        "- Real shadow authorized: `false`",
        "",
        "## Decision",
        "",
        (
            "The first real Model V2 shadow batch is a **no-go**. The decision was "
            "recorded before the locked target timestamp because coverage gates "
            "failed and the pre-shadow lock is non-executable."
        ),
        "",
        "## Activation conditions",
        "",
        "| Condition | Result |",
        "| --- | --- |",
    ]
    for name, passed in sorted(conditions.items()):
        lines.append(f"| `{name}` | `{'PASS' if passed else 'FAIL'}` |")
    lines.extend(
        [
            "",
            "## Write audit",
            "",
            "- Shadow CLI invoked: `false`",
            "- Database writes: `0`",
            "- Prediction records: `0`",
            "- Product labels: `0`",
            "- Outcome records: `0`",
            "- Return or outcome access: `false`",
            "",
            "## Backfill policy",
            "",
            (
                "The target remains `2026-07-31`; it was not moved. If that "
                "timestamp passes before every activation gate succeeds, the cohort "
                "must remain `MISSED_NOT_BACKFILLED`. Any later cohort must be "
                "selected prospectively before its own prediction timestamp."
            ),
            "",
            "## Next action",
            "",
            report["next_action"],
            "",
            "## Claims boundary",
            "",
            report["claims_boundary"],
            "",
        ]
    )
    return "\n".join(lines)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("evaluated-at must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("evaluated-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record the conditional first-shadow no-go or missed decision."
    )
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--evaluated-at", type=_timestamp)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        report = build_decision(
            repository_root=REPOSITORY_ROOT,
            evaluated_at=args.evaluated_at or datetime.now(timezone.utc),
        )
        report_path = REPOSITORY_ROOT / args.report
        markdown_path = REPOSITORY_ROOT / args.markdown
        report_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_bytes(_json_bytes(report))
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        print(
            f"first_shadow_decision={report['decision']} "
            f"batch_status={report['batch_status']} "
            "real_shadow_batch_created=false"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"first shadow decision failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
