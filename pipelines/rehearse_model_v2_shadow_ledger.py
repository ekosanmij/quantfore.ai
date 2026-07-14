"""Run the Sprint 10.7 synthetic-only shadow ledger rehearsal."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, select

try:
    import _bootstrap  # noqa: F401
    from _common import REPOSITORY_ROOT
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import REPOSITORY_ROOT  # type: ignore

from quantfore_research.models import (
    ModelOutcome,
    ShadowOutcomeRecord,
    ShadowPredictionBatch,
    ShadowPredictionRecord,
)
from quantfore_research.shadow.rehearsal import (
    create_fixture_batch,
    hash_json,
    seed_shadow_rehearsal_database,
)


DEFAULT_FIXTURE = Path(
    "experiments/model-v2-shadow-ledger-rehearsal-fixture-v1.json"
)
DEFAULT_REPORT = Path(
    "reports/reproducibility/model-v2-shadow-ledger-rehearsal-v1.json"
)
DEFAULT_MARKDOWN = Path(
    "reports/reproducibility/model-v2-shadow-ledger-rehearsal-v1.md"
)
BLOCKED_LOCK = Path("experiments/model-v2-pre-shadow-lock-v1.json")
RECORDED_REPOSITORY_BASE_COMMIT = "983c6395c0f5688093874aab586518d4dfdddefb"
RECORDED_IMPLEMENTATION_COMMIT = "1962b926839f105a5e04cf5231b44f45c998c562"
SOURCE_PATHS = (
    Path("packages/research/quantfore_research/shadow/ledger.py"),
    Path("packages/research/quantfore_research/shadow/rehearsal.py"),
    Path("pipelines/create_shadow_predictions.py"),
    Path("pipelines/rehearse_model_v2_shadow_ledger.py"),
    Path("packages/research/tests/test_model_v2_shadow_rehearsal.py"),
    Path("packages/research/tests/test_shadow_prediction_ledger.py"),
)


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


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
        raise RuntimeError("recorded rehearsal commit is not an ancestor") from exc


def _recorded_base_commit(root: Path) -> str:
    _assert_recorded_commit(root, RECORDED_REPOSITORY_BASE_COMMIT)
    _assert_recorded_commit(root, RECORDED_IMPLEMENTATION_COMMIT)
    return RECORDED_REPOSITORY_BASE_COMMIT


def _source_bindings(root: Path) -> list[dict[str, str]]:
    _assert_recorded_commit(root, RECORDED_IMPLEMENTATION_COMMIT)
    bindings = []
    for relative in SOURCE_PATHS:
        try:
            source_bytes = subprocess.check_output(
                [
                    "git",
                    "show",
                    f"{RECORDED_IMPLEMENTATION_COMMIT}:{relative.as_posix()}",
                ],
                cwd=root,
                stderr=subprocess.PIPE,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                f"recorded rehearsal source is missing: {relative}"
            ) from exc
        bindings.append(
            {
                "path": relative.as_posix(),
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
            }
        )
    return bindings


def _control(passed: bool, evidence: Any) -> dict[str, Any]:
    return {"passed": passed, "evidence": evidence}


def _datetime_text(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def run_rehearsal(
    *, repository_root: Path, generated_at: datetime
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Exercise the live ledger code with a deterministic synthetic cohort."""

    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    root = repository_root.resolve()
    blocked_lock_path = root / BLOCKED_LOCK
    blocked_lock = _load_json(blocked_lock_path)
    blocked_lock_hash = _sha256_file(blocked_lock_path)
    seeded = seed_shadow_rehearsal_database()

    first = create_fixture_batch(seeded)
    second = create_fixture_batch(seeded)

    unsafe_overwrite_error = None
    try:
        create_fixture_batch(
            seeded,
            executable_lock_uri="experiments/tampered-synthetic-lock.json",
        )
    except ValueError as exc:
        unsafe_overwrite_error = str(exc)

    blocked_lock_error = None
    try:
        create_fixture_batch(
            seeded,
            executable_lock=dict(blocked_lock),
            executable_lock_hash=blocked_lock_hash,
        )
    except ValueError as exc:
        blocked_lock_error = str(exc)

    append_only_error = None
    session = seeded.session_factory()
    try:
        stored = session.scalar(select(ShadowPredictionRecord).limit(1))
        if stored is None:
            raise RuntimeError("fixture rehearsal did not store a member row")
        stored.classification_branch = "TAMPERED"
        try:
            session.commit()
        except RuntimeError as exc:
            append_only_error = str(exc)
            session.rollback()
    finally:
        session.close()

    with seeded.session_factory() as read_session:
        batch = read_session.get(ShadowPredictionBatch, first.batch_id)
        if batch is None:
            raise RuntimeError("sealed fixture batch is missing")
        records = read_session.scalars(
            select(ShadowPredictionRecord)
            .where(ShadowPredictionRecord.batch_id == first.batch_id)
            .order_by(ShadowPredictionRecord.security_id)
        ).all()
        batch_count = read_session.scalar(
            select(func.count()).select_from(ShadowPredictionBatch)
        )
        outcome_count = read_session.scalar(
            select(func.count()).select_from(ModelOutcome)
        )
        shadow_outcome_count = read_session.scalar(
            select(func.count()).select_from(ShadowOutcomeRecord)
        )

        fixture_records = []
        for record in records:
            fixture_records.append(
                {
                    "shadow_prediction_id": record.shadow_prediction_id,
                    "security_id": record.security_id,
                    "ticker": record.ticker,
                    "classification_branch": record.classification_branch,
                    "disposition": record.disposition,
                    "research_score": (
                        str(record.research_score)
                        if record.research_score is not None
                        else None
                    ),
                    "research_label": record.research_label,
                    "product_label": record.product_label,
                    "product_label_status": record.product_label_status,
                    "exclusions": record.exclusions_json,
                    "driver_count": len(record.drivers_json),
                    "prediction_ids": record.prediction_ids_json,
                    "outcome_fields": {
                        "model_outcome_id": None,
                        "shadow_outcome_id": None,
                    },
                    "record_hash": record.record_hash,
                }
            )

    fixture_core = {
        "batch": {
            "batch_id": first.batch_id,
            "batch_hash": first.batch_hash,
            "model_version": batch.model_version,
            "universe_id": batch.universe_id,
            "prediction_date": batch.prediction_date.isoformat(),
            "prediction_timestamp": _datetime_text(batch.prediction_timestamp),
            "recorded_at": _datetime_text(batch.recorded_at),
            "expected_member_count": batch.expected_member_count,
            "scored_count": batch.scored_count,
            "excluded_count": batch.excluded_count,
            "claims_eligible": batch.claims_eligible,
            "product_label_policy": batch.product_label_policy,
        },
        "records": fixture_records,
        "outcomes": [],
    }
    fixture = {
        "fixture_version": "model-v2-shadow-ledger-rehearsal-fixture-v1",
        "fixture_only": True,
        "synthetic_data_only": True,
        "real_shadow_authorized": False,
        **fixture_core,
        "fixture_payload_sha256": hash_json(fixture_core),
    }
    fixture_sha256 = hashlib.sha256(_json_bytes(fixture)).hexdigest()

    product_labels_null = all(row["product_label"] is None for row in fixture_records)
    controls = {
        "immutable_batch_sealed": _control(
            first.created is True
            and len(first.batch_hash) == 64
            and batch_count == 1,
            {
                "first_created": first.created,
                "batch_count": batch_count,
                "batch_hash": first.batch_hash,
            },
        ),
        "complete_cohort_reconciliation": _control(
            first.expected_member_count
            == first.scored_count + first.excluded_count
            == len(fixture_records),
            {
                "expected": first.expected_member_count,
                "scored": first.scored_count,
                "excluded": first.excluded_count,
                "stored_records": len(fixture_records),
            },
        ),
        "product_labels_withheld": _control(
            product_labels_null
            and batch.product_label_policy == "WITHHELD_RESEARCH_ONLY",
            {
                "null_product_labels": sum(
                    row["product_label"] is None for row in fixture_records
                ),
                "record_count": len(fixture_records),
                "policy": batch.product_label_policy,
            },
        ),
        "future_outcomes_empty": _control(
            outcome_count == 0 and shadow_outcome_count == 0,
            {
                "model_outcome_rows": outcome_count,
                "shadow_outcome_rows": shadow_outcome_count,
            },
        ),
        "identical_rerun_is_noop": _control(
            second.created is False and second.batch_hash == first.batch_hash,
            {
                "second_created": second.created,
                "first_hash": first.batch_hash,
                "second_hash": second.batch_hash,
            },
        ),
        "unsafe_overwrite_rejected": _control(
            unsafe_overwrite_error is not None
            and "different sealed inputs" in unsafe_overwrite_error,
            {"error": unsafe_overwrite_error},
        ),
        "append_only_update_rejected": _control(
            append_only_error is not None and "append-only" in append_only_error,
            {"error": append_only_error},
        ),
        "blocked_real_lock_rejected": _control(
            blocked_lock_error is not None
            and "status=EXECUTABLE_LOCKED" in blocked_lock_error,
            {
                "error": blocked_lock_error,
                "blocked_lock_status": blocked_lock.get("status"),
            },
        ),
    }
    all_passed = all(control["passed"] for control in controls.values())
    report = {
        "schema_version": "model-v2-shadow-ledger-rehearsal-v1",
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "decision": (
            "PASS_SYNTHETIC_REHEARSAL_ONLY"
            if all_passed
            else "FAIL_SYNTHETIC_REHEARSAL"
        ),
        "claims_eligible": False,
        "fixture_only": True,
        "real_shadow_authorized": False,
        "outcomes_accessed": False,
        "scope": (
            "Synthetic fixture data exercising production shadow-ledger mechanics; "
            "no real security cohort or forward outcome is created."
        ),
        "fixture": {
            "path": DEFAULT_FIXTURE.as_posix(),
            "sha256": fixture_sha256,
            "payload_sha256": fixture["fixture_payload_sha256"],
        },
        "blocked_pre_shadow_lock": {
            "path": BLOCKED_LOCK.as_posix(),
            "sha256": blocked_lock_hash,
            "status": blocked_lock.get("status"),
            "activation_decision": blocked_lock.get("activation_decision"),
            "executable_for_shadow_predictions": blocked_lock.get(
                "executable_for_shadow_predictions"
            ),
        },
        "implementation": {
            "base_commit": _recorded_base_commit(root),
            "source_bindings": _source_bindings(root),
            "synthetic_executable_lock_sha256": seeded.executable_lock_hash,
        },
        "controls": controls,
        "counts": {
            "batches": batch_count,
            "records": len(fixture_records),
            "scored": first.scored_count,
            "excluded": first.excluded_count,
            "product_labels": sum(
                row["product_label"] is not None for row in fixture_records
            ),
            "model_outcomes": outcome_count,
            "shadow_outcomes": shadow_outcome_count,
        },
        "claims_boundary": (
            "A passing rehearsal proves only ledger mechanics. The failed Sprint "
            "10.5 readiness decision and blocked 10.6 lock still prohibit real "
            "shadow prediction creation and any backfill."
        ),
    }
    return fixture, report


def render_markdown(report: Mapping[str, Any]) -> str:
    controls = report["controls"]
    lines = [
        "# Model V2 Shadow Ledger Rehearsal v1",
        "",
        "`claims_eligible=false`",
        "",
        f"- Decision: `{report['decision']}`",
        "- Scope: synthetic fixture only",
        "- Real shadow authorized: `false`",
        "- Outcome access: `false`",
        "",
        "## Decision",
        "",
        (
            "The shadow ledger mechanics passed the synthetic rehearsal. This does "
            "not override the failed Sprint 10.5 coverage gates or authorize a real "
            "shadow batch."
        ),
        "",
        "## Controls",
        "",
        "| Control | Result |",
        "| --- | --- |",
    ]
    for name, control in sorted(controls.items()):
        lines.append(f"| `{name}` | `{'PASS' if control['passed'] else 'FAIL'}` |")
    lines.extend(
        [
            "",
            "## Fixture evidence",
            "",
            f"- Batch hash: `{controls['immutable_batch_sealed']['evidence']['batch_hash']}`",
            f"- Fixture SHA-256: `{report['fixture']['sha256']}`",
            f"- Stored rows: `{report['counts']['records']}`",
            f"- Product labels: `{report['counts']['product_labels']}`",
            f"- Model outcomes: `{report['counts']['model_outcomes']}`",
            f"- Shadow outcomes: `{report['counts']['shadow_outcomes']}`",
            "",
            "## Authorization boundary",
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
        raise argparse.ArgumentTypeError("generated-at must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("generated-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the synthetic-only Model V2 shadow ledger rehearsal."
    )
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--generated-at", type=_timestamp)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        fixture, report = run_rehearsal(
            repository_root=REPOSITORY_ROOT,
            generated_at=args.generated_at or datetime.now(timezone.utc),
        )
        fixture_path = REPOSITORY_ROOT / args.fixture
        report_path = REPOSITORY_ROOT / args.report
        markdown_path = REPOSITORY_ROOT / args.markdown
        fixture_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        markdown_path.parent.mkdir(parents=True, exist_ok=True)
        fixture_path.write_bytes(_json_bytes(fixture))
        report_path.write_bytes(_json_bytes(report))
        markdown_path.write_text(render_markdown(report), encoding="utf-8")
        print(
            f"shadow_rehearsal_decision={report['decision']} "
            f"batch_hash={fixture['batch']['batch_hash']} "
            f"real_shadow_authorized={str(report['real_shadow_authorized']).lower()}"
        )
        return 0 if report["decision"] == "PASS_SYNTHETIC_REHEARSAL_ONLY" else 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"shadow rehearsal failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
