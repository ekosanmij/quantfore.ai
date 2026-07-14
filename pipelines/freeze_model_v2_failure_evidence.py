"""Freeze and verify the failed Model V2 evidence chain at its closure commit."""

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


FROZEN_BASELINE_COMMIT = "e18303e686f1946f83a5451e868a12cd1aa45375"
DEFAULT_OUTPUT = Path("experiments/model-v2-failure-evidence-freeze-v1.json")
DESIGN_LOCK = Path("experiments/multifactor-v2-hypothesis-lock-v1.json")
READINESS_REPORT = Path(
    "reports/data-audits/model-v2-coverage-readiness-v1.json"
)
PRE_SHADOW_LOCK = Path("experiments/model-v2-pre-shadow-lock-v1.json")
REHEARSAL_REPORT = Path(
    "reports/reproducibility/model-v2-shadow-ledger-rehearsal-v1.json"
)
FIRST_BATCH_DECISION = Path(
    "reports/reproducibility/model-v2-first-shadow-batch-decision-v1.json"
)

FROZEN_EVIDENCE_PATHS = {
    "accounting_coverage_json": Path(
        "reports/data-audits/model-v2-accounting-coverage-v1.json"
    ),
    "accounting_coverage_markdown": Path(
        "reports/data-audits/model-v2-accounting-coverage-v1.md"
    ),
    "branch_feature_input_manifest": Path(
        "experiments/model-v2-branch-feature-inputs-v1.manifest.json"
    ),
    "branch_score_manifest": Path(
        "experiments/model-v2-branch-aware-scores-v1.manifest.json"
    ),
    "classification_ledger": Path(
        "experiments/model-v2-point-in-time-subtype-classification-v1.jsonl.gz"
    ),
    "design_contract": Path("experiments/multifactor-v2-hypothesis-contract.md"),
    "design_lock": DESIGN_LOCK,
    "first_shadow_decision_json": FIRST_BATCH_DECISION,
    "first_shadow_decision_markdown": Path(
        "reports/reproducibility/model-v2-first-shadow-batch-decision-v1.md"
    ),
    "implementation_contract": Path(
        "docs/research/model-v2-branch-aware-implementation-v1.md"
    ),
    "point_in_time_classification_contract": Path(
        "docs/research/point-in-time-subtype-classification-v1.md"
    ),
    "pre_shadow_lock": PRE_SHADOW_LOCK,
    "pre_shadow_readiness_markdown": Path(
        "reports/reproducibility/model-v2-pre-shadow-readiness-v1.md"
    ),
    "readiness_report": READINESS_REPORT,
    "shadow_ledger_contract": Path("docs/research/shadow-ledger-v1.md"),
    "shadow_rehearsal_fixture": Path(
        "experiments/model-v2-shadow-ledger-rehearsal-fixture-v1.json"
    ),
    "shadow_rehearsal_json": REHEARSAL_REPORT,
    "shadow_rehearsal_markdown": Path(
        "reports/reproducibility/model-v2-shadow-ledger-rehearsal-v1.md"
    ),
    "subtype_coverage_json": Path(
        "reports/data-audits/model-v2-subtype-classification-coverage-v1.json"
    ),
}


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _hash_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON document must contain an object: {path}")
    return value


def _git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, stderr=subprocess.PIPE
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Git verification failed: {' '.join(args)}") from exc


def _git_bytes(root: Path, revision: str, relative: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", "show", f"{revision}:{relative}"],
            cwd=root,
            stderr=subprocess.PIPE,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            f"frozen baseline does not contain required evidence: {relative}"
        ) from exc


def _assert_baseline_ancestor(root: Path, baseline: str) -> None:
    try:
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", baseline, "HEAD"],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("frozen V2 closure commit must remain an ancestor") from exc


def _evidence_bindings(root: Path, baseline: str) -> list[dict[str, str]]:
    _assert_baseline_ancestor(root, baseline)
    bindings = []
    for name, relative in sorted(FROZEN_EVIDENCE_PATHS.items()):
        current = (root / relative).read_bytes()
        baseline_bytes = _git_bytes(root, baseline, relative.as_posix())
        if current != baseline_bytes:
            raise ValueError(
                f"frozen Model V2 evidence differs from {baseline}: {relative}"
            )
        bindings.append(
            {
                "name": name,
                "path": relative.as_posix(),
                "sha256": _sha256_bytes(current),
            }
        )
    return bindings


def _locked_design_contract(design: Mapping[str, Any]) -> dict[str, Any]:
    required = (
        "model",
        "universe",
        "evaluation_protocol",
        "portfolio_protocol",
        "engineering_gates",
        "promotion_gates",
        "allowed_change_categories",
        "prohibited_changes",
    )
    missing = [field for field in required if field not in design]
    if missing:
        raise ValueError("design lock is missing fields: " + ", ".join(missing))
    return {field: design[field] for field in required}


def _observed_failure(readiness: Mapping[str, Any]) -> dict[str, Any]:
    criteria = readiness.get("criteria")
    reconciliation = readiness.get("reconciliation")
    coverage = readiness.get("coverage")
    if not all(isinstance(value, Mapping) for value in (criteria, reconciliation, coverage)):
        raise ValueError("readiness report is missing failure evidence")
    assert isinstance(criteria, Mapping)
    assert isinstance(reconciliation, Mapping)
    assert isinstance(coverage, Mapping)
    failed = sorted(
        str(name)
        for name, result in criteria.items()
        if isinstance(result, Mapping) and result.get("passed") is not True
    )
    return {
        "decision": readiness.get("decision"),
        "failed_criteria": failed,
        "criteria": dict(criteria),
        "reconciliation": dict(reconciliation),
        "aggregate_final_score_coverage": coverage.get(
            "aggregate_final_score_coverage"
        ),
    }


def _validate_closure_states(
    *,
    design: Mapping[str, Any],
    readiness: Mapping[str, Any],
    pre_shadow_lock: Mapping[str, Any],
    rehearsal: Mapping[str, Any],
    first_batch: Mapping[str, Any],
) -> None:
    if design.get("status") != "DESIGN_LOCK_PRE_IMPLEMENTATION":
        raise ValueError("the original design lock status changed")
    if readiness.get("decision") != "FAIL_NOT_READY_FOR_EXECUTABLE_LOCK":
        raise ValueError("the frozen readiness failure decision changed")
    if pre_shadow_lock.get("status") != "BLOCKED_COVERAGE_GATES_FAILED":
        raise ValueError("the frozen pre-shadow lock is no longer blocked")
    if pre_shadow_lock.get("executable_for_shadow_predictions") is not False:
        raise ValueError("the frozen pre-shadow lock must remain non-executable")
    if rehearsal.get("decision") != "PASS_SYNTHETIC_REHEARSAL_ONLY":
        raise ValueError("the synthetic-only rehearsal decision changed")
    if rehearsal.get("real_shadow_authorized") is not False:
        raise ValueError("the synthetic rehearsal cannot authorize real shadow")
    if first_batch.get("decision") != "NO_GO_COVERAGE_GATES_FAILED":
        raise ValueError("the frozen first-batch no-go decision changed")
    if first_batch.get("real_shadow_batch_created") is not False:
        raise ValueError("the frozen evidence must contain no real shadow batch")


def build_failure_freeze(
    *, repository_root: Path, frozen_at: datetime
) -> dict[str, Any]:
    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise ValueError("frozen_at must include a timezone")
    root = repository_root.resolve()
    design = _load_json(root / DESIGN_LOCK)
    readiness = _load_json(root / READINESS_REPORT)
    pre_shadow_lock = _load_json(root / PRE_SHADOW_LOCK)
    rehearsal = _load_json(root / REHEARSAL_REPORT)
    first_batch = _load_json(root / FIRST_BATCH_DECISION)
    _validate_closure_states(
        design=design,
        readiness=readiness,
        pre_shadow_lock=pre_shadow_lock,
        rehearsal=rehearsal,
        first_batch=first_batch,
    )
    design_contract = _locked_design_contract(design)
    observed_failure = _observed_failure(readiness)
    return {
        "schema_version": "model-v2-failure-evidence-freeze-v1",
        "frozen_at": frozen_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "status": "FROZEN_FAILED_NOT_SHADOW_READY",
        "claims_eligible": False,
        "shadow_authorized": False,
        "frozen_baseline_commit": FROZEN_BASELINE_COMMIT,
        "evidence_bindings": _evidence_bindings(
            root, FROZEN_BASELINE_COMMIT
        ),
        "locked_design_contract": {
            "payload": design_contract,
            "sha256": _hash_json(design_contract),
        },
        "observed_failure": {
            "payload": observed_failure,
            "sha256": _hash_json(observed_failure),
        },
        "closure_chain": {
            "readiness_decision": readiness["decision"],
            "pre_shadow_lock_status": pre_shadow_lock["status"],
            "pre_shadow_lock_executable": pre_shadow_lock[
                "executable_for_shadow_predictions"
            ],
            "rehearsal_decision": rehearsal["decision"],
            "rehearsal_real_shadow_authorized": rehearsal[
                "real_shadow_authorized"
            ],
            "first_batch_decision": first_batch["decision"],
            "real_shadow_batch_created": first_batch[
                "real_shadow_batch_created"
            ],
        },
        "mutation_policy": {
            "frozen_paths_may_change": False,
            "thresholds_may_change_in_place": False,
            "locks_may_be_rewritten": False,
            "failed_decision_may_be_relabelled": False,
            "correction_policy": (
                "append a versioned amendment that binds the original artifact; "
                "never overwrite frozen evidence"
            ),
        },
        "next_version_policy": {
            "new_model_version_required": True,
            "new_design_lock_required": True,
            "new_universe_or_normalization_design_must_be_predeclared": True,
            "outcome_blind_feasibility_required_before_rebuild": True,
            "july_2026_backfill_allowed": False,
        },
        "claims_boundary": (
            "This freeze preserves a failed engineering result. It does not "
            "authorize shadow predictions, performance claims, threshold changes, "
            "or retroactive batch creation."
        ),
    }


def verify_failure_freeze(
    *, repository_root: Path, manifest_path: Path = DEFAULT_OUTPUT
) -> dict[str, Any]:
    root = repository_root.resolve()
    path = manifest_path if manifest_path.is_absolute() else root / manifest_path
    manifest = _load_json(path)
    if manifest.get("schema_version") != "model-v2-failure-evidence-freeze-v1":
        raise ValueError("unexpected Model V2 failure freeze schema")
    if manifest.get("status") != "FROZEN_FAILED_NOT_SHADOW_READY":
        raise ValueError("Model V2 failure freeze status changed")
    if manifest.get("frozen_baseline_commit") != FROZEN_BASELINE_COMMIT:
        raise ValueError("Model V2 frozen baseline commit changed")
    if manifest.get("claims_eligible") is not False:
        raise ValueError("failed Model V2 evidence cannot become claims eligible")
    if manifest.get("shadow_authorized") is not False:
        raise ValueError("failed Model V2 evidence cannot authorize shadow")

    expected_bindings = _evidence_bindings(root, FROZEN_BASELINE_COMMIT)
    if manifest.get("evidence_bindings") != expected_bindings:
        raise ValueError("frozen evidence bindings no longer reproduce")

    design = _load_json(root / DESIGN_LOCK)
    readiness = _load_json(root / READINESS_REPORT)
    pre_shadow_lock = _load_json(root / PRE_SHADOW_LOCK)
    rehearsal = _load_json(root / REHEARSAL_REPORT)
    first_batch = _load_json(root / FIRST_BATCH_DECISION)
    _validate_closure_states(
        design=design,
        readiness=readiness,
        pre_shadow_lock=pre_shadow_lock,
        rehearsal=rehearsal,
        first_batch=first_batch,
    )
    design_contract = _locked_design_contract(design)
    observed_failure = _observed_failure(readiness)
    if manifest.get("locked_design_contract") != {
        "payload": design_contract,
        "sha256": _hash_json(design_contract),
    }:
        raise ValueError("locked Model V2 design thresholds changed")
    if manifest.get("observed_failure") != {
        "payload": observed_failure,
        "sha256": _hash_json(observed_failure),
    }:
        raise ValueError("observed Model V2 failure evidence changed")

    mutation = manifest.get("mutation_policy")
    if not isinstance(mutation, Mapping) or any(
        mutation.get(field) is not False
        for field in (
            "frozen_paths_may_change",
            "thresholds_may_change_in_place",
            "locks_may_be_rewritten",
            "failed_decision_may_be_relabelled",
        )
    ):
        raise ValueError("freeze mutation policy is not fail-closed")
    frozen_at_value = manifest.get("frozen_at")
    if not isinstance(frozen_at_value, str):
        raise ValueError("failure freeze must retain its timestamp")
    try:
        frozen_at = datetime.fromisoformat(
            frozen_at_value.replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("failure freeze timestamp is invalid") from exc
    expected_manifest = build_failure_freeze(
        repository_root=root, frozen_at=frozen_at
    )
    if dict(manifest) != expected_manifest:
        raise ValueError("failure freeze manifest no longer reproduces exactly")
    return dict(manifest)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("frozen-at must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("frozen-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze or verify the closed failed Model V2 evidence chain."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--frozen-at", type=_timestamp)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        output = REPOSITORY_ROOT / args.output
        if args.verify:
            manifest = verify_failure_freeze(
                repository_root=REPOSITORY_ROOT, manifest_path=args.output
            )
            print(
                f"model_v2_failure_freeze=PASS "
                f"baseline={manifest['frozen_baseline_commit']}"
            )
            return 0
        if output.exists():
            raise RuntimeError(f"refusing to overwrite existing freeze: {args.output}")
        manifest = build_failure_freeze(
            repository_root=REPOSITORY_ROOT,
            frozen_at=args.frozen_at or datetime.now(timezone.utc),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_json_bytes(manifest))
        print(
            "model_v2_failure_freeze=CREATED "
            f"baseline={manifest['frozen_baseline_commit']} output={args.output}"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Model V2 failure freeze failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
