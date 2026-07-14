"""Create or verify the fail-closed Model V2 pre-shadow lock.

The Sprint 10.5 readiness report is authoritative. This command will only create
the blocked, non-executable lock while that report fails; it never upgrades a
failed readiness decision into shadow authorization.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import REPOSITORY_ROOT
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import REPOSITORY_ROOT  # type: ignore

from quantfore_research.shadow.ledger import LOCKED_SHADOW_DATES, SHADOW_HORIZONS


DEFAULT_OUTPUT = Path("experiments/model-v2-pre-shadow-lock-v1.json")
DESIGN_LOCK = Path("experiments/multifactor-v2-hypothesis-lock-v1.json")
READINESS_REPORT = Path(
    "reports/data-audits/model-v2-coverage-readiness-v1.json"
)
INPUT_MANIFEST = Path("experiments/model-v2-branch-feature-inputs-v1.manifest.json")
SCORE_MANIFEST = Path("experiments/model-v2-branch-aware-scores-v1.manifest.json")

SOURCE_MANIFEST_PATHS = {
    "accounting_bundle_manifest": Path(
        "data/raw/free-point-in-time/sec-fundamentals-bundle-v2/manifest.json"
    ),
    "prepared_input_manifest": INPUT_MANIFEST,
    "score_manifest": SCORE_MANIFEST,
    "raw_data_storage_inventory": Path(
        "reports/data-audits/raw-data-storage-inventory-v1.json"
    ),
    "raw_data_hash_manifest": Path(
        "reports/data-audits/raw-data-sha256-v1.jsonl.gz"
    ),
    "subtype_coverage_report": Path(
        "reports/data-audits/model-v2-subtype-classification-coverage-v1.json"
    ),
}

IMPLEMENTATION_SOURCE_PATHS = (
    Path("packages/research/quantfore_research/classification/point_in_time_subtypes.py"),
    Path("packages/research/quantfore_research/features/model_v2.py"),
    Path("packages/research/quantfore_research/features/model_v2_inputs.py"),
    Path("packages/research/quantfore_research/scoring/model_v2.py"),
    Path("packages/research/quantfore_research/validation/accounting_coverage.py"),
    Path("packages/research/quantfore_research/validation/model_v2_coverage.py"),
    Path("pipelines/build_point_in_time_subtype_ledger.py"),
    Path("pipelines/build_model_v2_accounting_bundle.py"),
    Path("pipelines/build_model_v2_score_inputs.py"),
    Path("pipelines/build_model_v2_scores.py"),
    Path("pipelines/audit_model_v2_coverage_readiness.py"),
    Path("pipelines/create_model_v2_pre_shadow_lock.py"),
)

EVALUATION_SOURCE_PATHS = (
    Path("packages/research/quantfore_research/evaluation/outcomes.py"),
    Path("packages/research/quantfore_research/evaluation/ledger.py"),
    Path("packages/research/quantfore_research/shadow/ledger.py"),
    Path("pipelines/evaluate_predictions.py"),
    Path("pipelines/create_shadow_predictions.py"),
)

SHADOW_SOURCE_PATHS = (
    Path("docs/research/shadow-ledger-v1.md"),
    Path("packages/research/quantfore_research/shadow/ledger.py"),
    Path("pipelines/create_shadow_predictions.py"),
)

REPORT_ARTIFACT_PATHS = {
    "subtype_classification_coverage": Path(
        "reports/data-audits/model-v2-subtype-classification-coverage-v1.json"
    ),
    "accounting_coverage": Path(
        "reports/data-audits/model-v2-accounting-coverage-v1.json"
    ),
    "coverage_readiness": READINESS_REPORT,
    "pre_shadow_readiness": Path(
        "reports/reproducibility/model-v2-pre-shadow-readiness-v1.md"
    ),
}

REPORT_SCHEMAS = {
    "model_v2_subtype_classification_coverage_v1": {
        "artifact_path": str(REPORT_ARTIFACT_PATHS["subtype_classification_coverage"]),
        "required_top_level_fields": [
            "schema_version",
            "claims_eligible",
            "classification_version",
            "decision",
            "ledger",
            "metrics",
            "outcome_blinding",
            "pass_criteria",
            "source_registries",
            "warehouse",
        ],
    },
    "model_v2_accounting_coverage_v1": {
        "artifact_path": str(REPORT_ARTIFACT_PATHS["accounting_coverage"]),
        "required_top_level_fields": [
            "bundle",
            "claims_eligible",
            "component_comparison",
            "coverage",
            "decision",
            "material_improvement",
            "outcome_blinding",
            "schema_version",
            "source_bindings",
        ],
    },
    "model_v2_coverage_readiness_v1": {
        "artifact_path": str(READINESS_REPORT),
        "required_top_level_fields": [
            "claims_eligible",
            "controls",
            "coverage",
            "criteria",
            "decision",
            "reconciliation",
            "reproducibility",
        ],
    },
    "model_v2_score_manifest_v1": {
        "artifact_path": str(SCORE_MANIFEST),
        "required_top_level_fields": [
            "branch_schema",
            "branch_schema_sha256",
            "claims_eligible",
            "counts",
            "family_weights",
            "formula_version",
            "model_version",
            "normalization_version",
            "output",
        ],
    },
    "model_v2_pre_shadow_lock_v1": {
        "artifact_path": str(DEFAULT_OUTPUT),
        "required_top_level_fields": [
            "lock_version",
            "status",
            "activation_decision",
            "implementation",
            "prediction_schedule",
            "readiness",
            "shadow_ledger",
        ],
    },
}

SHADOW_LEDGER_RULES = {
    "batch_identity": "one immutable batch per model version and prediction date",
    "cohort_reconciliation": "every expected nonbenchmark member is SCORED or EXCLUDED",
    "exclusion_policy": "excluded rows carry stable reason codes and no prediction",
    "append_only": "sealed batches records and outcome links cannot be updated or deleted",
    "rerun_policy": "identical reruns are no-ops; changed reruns are conflicts",
    "timestamp_policy": "all inputs must be available no later than prediction_timestamp",
    "outcome_policy": "outcome links remain absent until the exact horizon is mature",
    "product_label_policy": "product_label is always null and status is WITHHELD_RESEARCH_ONLY",
    "blinding_policy": "aggregate forward results remain blinded through primary maturity",
    "failure_policy": "a missed or failed scheduled batch is never backfilled",
}


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


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
        raise ValueError(f"required lock input is missing: {relative}")
    return {"path": relative.as_posix(), "sha256": _sha256_file(absolute)}


def _bindings(
    root: Path, paths: Mapping[str, Path]
) -> dict[str, dict[str, str]]:
    return {name: _binding(root, path) for name, path in sorted(paths.items())}


def _source_bindings(root: Path, paths: Sequence[Path]) -> dict[str, dict[str, str]]:
    return {path.as_posix(): _binding(root, path) for path in sorted(paths)}


def _is_full_git_commit(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 40:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _failed_criteria(readiness: Mapping[str, Any]) -> list[str]:
    criteria = readiness.get("criteria")
    if not isinstance(criteria, Mapping):
        raise ValueError("readiness report is missing criteria")
    failed = sorted(
        str(name)
        for name, result in criteria.items()
        if isinstance(result, Mapping) and result.get("passed") is not True
    )
    if readiness.get("decision") != "FAIL_NOT_READY_FOR_EXECUTABLE_LOCK":
        raise ValueError(
            "blocked lock creation requires FAIL_NOT_READY_FOR_EXECUTABLE_LOCK"
        )
    if not failed:
        raise ValueError("failed readiness decision must name at least one failed criterion")
    return failed


def _validate_report_schemas(root: Path) -> None:
    for name, schema in REPORT_SCHEMAS.items():
        path = root / str(schema["artifact_path"])
        if path == root / DEFAULT_OUTPUT:
            continue
        document = _load_json(path)
        missing = sorted(set(schema["required_top_level_fields"]) - set(document))
        if missing:
            raise ValueError(f"{name} is missing required fields: {', '.join(missing)}")


def _assemble_lock(
    *,
    implementation_commit: str,
    locked_at: datetime,
    design_lock: Mapping[str, Any],
    design_lock_binding: Mapping[str, str],
    readiness: Mapping[str, Any],
    readiness_binding: Mapping[str, str],
    score_manifest: Mapping[str, Any],
    formula_ledger: Mapping[str, Any],
    classification_ledger: Mapping[str, str],
    source_manifests: Mapping[str, Mapping[str, str]],
    implementation_sources: Mapping[str, Mapping[str, str]],
    evaluation_sources: Mapping[str, Mapping[str, str]],
    report_artifacts: Mapping[str, Mapping[str, str]],
    shadow_sources: Mapping[str, Mapping[str, str]],
    reproducible_local_ledgers: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    if not _is_full_git_commit(implementation_commit):
        raise ValueError("implementation commit must be a full 40-character Git SHA")
    if locked_at.tzinfo is None or locked_at.utcoffset() is None:
        raise ValueError("locked_at must include a timezone")

    failed = _failed_criteria(readiness)
    reconciliation = readiness.get("reconciliation")
    coverage = readiness.get("coverage")
    criteria = readiness.get("criteria")
    if not isinstance(reconciliation, Mapping) or not isinstance(coverage, Mapping):
        raise ValueError("readiness report is missing reconciliation or coverage")
    assert isinstance(criteria, Mapping)

    formula_hash = formula_ledger.get("sha256")
    classification_hash = classification_ledger.get("sha256")
    if not _is_sha256(formula_hash) or not _is_sha256(classification_hash):
        raise ValueError("formula and classification ledgers require SHA-256 bindings")

    report_schema_hash = _hash_json(REPORT_SCHEMAS)
    source_manifest_hash = _hash_json(source_manifests)
    evaluation_code_hash = _hash_json(evaluation_sources)
    schedule = list(LOCKED_SHADOW_DATES)
    costs = design_lock.get("portfolio_protocol")
    universe = design_lock.get("universe")
    if not isinstance(costs, Mapping) or not isinstance(universe, Mapping):
        raise ValueError("design lock is missing universe or portfolio cost protocol")

    model = {
        "version": score_manifest.get("model_version"),
        "feature_version": score_manifest.get("feature_version"),
        "formula_version": score_manifest.get("formula_version"),
        "classification_version": design_lock.get("model", {}).get(
            "classification_version"
        ),
        "normalization_version": score_manifest.get("normalization_version"),
        "required_horizons": list(SHADOW_HORIZONS),
        "family_weights": {
            family: 0.2 for family in ("value", "quality", "growth", "momentum", "risk")
        },
        "required_family_count": 5,
        "minimum_component_coverage": 0.8,
        "minimum_required_components_per_family": 0.6,
        "minimum_branch_cross_section": score_manifest.get(
            "minimum_branch_cross_section"
        ),
        "cross_branch_fallback": False,
        "family_weight_renormalization": False,
    }

    lock = {
        "lock_version": "model-v2-pre-shadow-lock-v1",
        "locked_at": locked_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "status": "BLOCKED_COVERAGE_GATES_FAILED",
        "activation_decision": "DO_NOT_START_SHADOW",
        "claims_eligible": False,
        "executable_for_shadow_predictions": False,
        "executable_for_outcome_evaluation": False,
        "shadow_start_authorized": False,
        "design_lock": dict(design_lock_binding),
        "design_lock_sha256": design_lock_binding["sha256"],
        "model": model,
        "universe": dict(universe),
        "prediction_schedule": {
            "dates": schedule,
            "sha256": _hash_json(schedule),
            "scheduled_cohorts": len(schedule),
            "activation_state": "BOUND_BUT_NOT_AUTHORIZED",
        },
        "costs": {
            "protocol": dict(costs),
            "sha256": _hash_json(costs),
            "activation_state": "BOUND_BUT_NOT_AUTHORIZED",
        },
        "formula_ledger": dict(formula_ledger),
        "classification_ledger": dict(classification_ledger),
        "source_manifests": dict(source_manifests),
        "reproducible_local_ledgers": dict(reproducible_local_ledgers),
        "report_schemas": {
            "schemas": REPORT_SCHEMAS,
            "sha256": report_schema_hash,
        },
        "report_artifacts": dict(report_artifacts),
        "shadow_ledger": {
            "version": "shadow-ledger-v1",
            "rules": SHADOW_LEDGER_RULES,
            "rules_sha256": _hash_json(SHADOW_LEDGER_RULES),
            "source_bindings": dict(shadow_sources),
        },
        "readiness": {
            "decision": readiness["decision"],
            "report": dict(readiness_binding),
            "failed_criteria": failed,
            "reconciliation": {
                "expected_stock_months": reconciliation.get("expected_stock_months"),
                "scored_stock_months": reconciliation.get("scored_stock_months"),
                "excluded_stock_months": reconciliation.get("excluded_stock_months"),
                "final_disposition_fraction": reconciliation.get(
                    "final_disposition_fraction"
                ),
            },
            "coverage": {
                "aggregate_final_score_coverage": coverage.get(
                    "aggregate_final_score_coverage"
                ),
                "minimum_monthly_score_coverage": criteria[
                    "final_score_coverage_every_month"
                ].get("minimum_observed"),
                "minimum_monthly_known_branch_fraction": criteria[
                    "known_branch_or_subtype_every_month"
                ].get("minimum_observed"),
                "minimum_represented_active_branches": criteria[
                    "represented_active_branches_every_month"
                ].get("minimum_observed"),
                "minimum_represented_sectors": criteria[
                    "represented_sectors_every_month"
                ].get("minimum_observed"),
            },
        },
        "implementation": {
            "code_commit": implementation_commit,
            "formula_ledger_sha256": formula_hash,
            "classification_ledger_sha256": classification_hash,
            "source_manifest_sha256": source_manifest_hash,
            "evaluation_code_sha256": evaluation_code_hash,
            "report_schema_sha256": report_schema_hash,
            "shadow_ledger_rules_sha256": _hash_json(SHADOW_LEDGER_RULES),
            "cost_protocol_sha256": _hash_json(costs),
            "portfolio_notional_usd": None,
            "source_files": dict(implementation_sources),
            "evaluation_files": dict(evaluation_sources),
        },
        "blocked_reasons": [
            "READINESS_DECISION_FAIL_NOT_READY_FOR_EXECUTABLE_LOCK",
            *[f"FAILED_CRITERION:{name}" for name in failed],
            "PORTFOLIO_NOTIONAL_NOT_LOCKED",
        ],
        "claims_boundary": (
            "This artifact freezes the failed implementation evidence and prevents "
            "shadow execution. It is not an executable lock, performance claim, "
            "recommendation, or authorization to backfill a prediction."
        ),
    }
    return lock


def build_pre_shadow_lock(
    *, repository_root: Path, implementation_commit: str, locked_at: datetime
) -> dict[str, Any]:
    root = repository_root.resolve()
    _validate_report_schemas(root)
    design_lock = _load_json(root / DESIGN_LOCK)
    readiness = _load_json(root / READINESS_REPORT)
    input_manifest = _load_json(root / INPUT_MANIFEST)
    score_manifest = _load_json(root / SCORE_MANIFEST)

    branch_schema = score_manifest.get("branch_schema")
    branch_schema_sha256 = score_manifest.get("branch_schema_sha256")
    if not isinstance(branch_schema, Mapping):
        raise ValueError("score manifest is missing branch_schema")
    if _hash_json(branch_schema) != branch_schema_sha256:
        raise ValueError("score manifest branch schema hash does not reproduce")

    classification = input_manifest.get("inputs", {}).get("classification_ledger")
    if not isinstance(classification, Mapping):
        raise ValueError("prepared-input manifest is missing classification binding")
    classification_path = Path(str(classification.get("path", "")))
    classification_binding = _binding(root, classification_path)
    if classification_binding["sha256"] != classification.get("sha256"):
        raise ValueError("classification ledger differs from prepared-input manifest")

    local_ledgers = {
        "prepared_inputs": {
            "relative_path": input_manifest["output"]["path"],
            "sha256": input_manifest["output"]["sha256"],
            "rows": input_manifest["output"]["rows"],
            "storage": "LOCAL_REPRODUCIBLE_NOT_IN_GIT",
        },
        "scores": {
            "relative_path": score_manifest["output"]["path"],
            "sha256": score_manifest["output"]["sha256"],
            "rows": score_manifest["counts"]["rows"],
            "storage": "LOCAL_REPRODUCIBLE_NOT_IN_GIT",
        },
    }
    for ledger in local_ledgers.values():
        local_path = root / str(ledger["relative_path"])
        if not local_path.is_file() or _sha256_file(local_path) != ledger["sha256"]:
            raise ValueError(f"local reproducible ledger hash mismatch: {local_path}")

    formula_ledger = {
        "version": score_manifest.get("formula_version"),
        "branch_schema": branch_schema,
        "sha256": branch_schema_sha256,
    }
    return _assemble_lock(
        implementation_commit=implementation_commit,
        locked_at=locked_at,
        design_lock=design_lock,
        design_lock_binding=_binding(root, DESIGN_LOCK),
        readiness=readiness,
        readiness_binding=_binding(root, READINESS_REPORT),
        score_manifest=score_manifest,
        formula_ledger=formula_ledger,
        classification_ledger=classification_binding,
        source_manifests=_bindings(root, SOURCE_MANIFEST_PATHS),
        implementation_sources=_source_bindings(root, IMPLEMENTATION_SOURCE_PATHS),
        evaluation_sources=_source_bindings(root, EVALUATION_SOURCE_PATHS),
        report_artifacts=_bindings(root, REPORT_ARTIFACT_PATHS),
        shadow_sources=_source_bindings(root, SHADOW_SOURCE_PATHS),
        reproducible_local_ledgers=local_ledgers,
    )


def _git_output(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, text=True, stderr=subprocess.PIPE
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(f"Git verification failed: {' '.join(args)}") from exc


def clean_implementation_commit(repository_root: Path) -> str:
    root = repository_root.resolve()
    status = _git_output(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise RuntimeError("implementation must be fully committed before lock creation")
    commit = _git_output(root, "rev-parse", "HEAD")
    if not _is_full_git_commit(commit):
        raise RuntimeError("HEAD did not resolve to a full Git commit")
    return commit


def _iter_file_bindings(value: Any) -> Iterator[Mapping[str, str]]:
    if isinstance(value, Mapping):
        if set(("path", "sha256")).issubset(value):
            yield value  # type: ignore[misc]
        for item in value.values():
            yield from _iter_file_bindings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_file_bindings(item)


def verify_committed_lock(*, repository_root: Path, lock_path: Path) -> str:
    root = repository_root.resolve()
    absolute_lock = (root / lock_path).resolve() if not lock_path.is_absolute() else lock_path
    try:
        relative = absolute_lock.relative_to(root).as_posix()
    except ValueError as exc:
        raise RuntimeError("pre-shadow lock must be inside the repository") from exc

    lock = _load_json(absolute_lock)
    implementation = lock.get("implementation")
    if not isinstance(implementation, Mapping):
        raise RuntimeError("lock is missing implementation bindings")
    implementation_commit = implementation.get("code_commit")
    if not _is_full_git_commit(implementation_commit):
        raise RuntimeError("lock implementation commit is not a full Git SHA")
    if lock.get("status") != "BLOCKED_COVERAGE_GATES_FAILED":
        raise RuntimeError("failed readiness evidence must remain fail-closed")
    for field in (
        "claims_eligible",
        "executable_for_shadow_predictions",
        "executable_for_outcome_evaluation",
        "shadow_start_authorized",
    ):
        if lock.get(field) is not False:
            raise RuntimeError(f"blocked lock must retain {field}=false")

    head = clean_implementation_commit(root)
    parent = _git_output(root, "rev-parse", "HEAD^")
    if parent != implementation_commit:
        raise RuntimeError("lock commit must directly follow the implementation commit")
    changed_paths = _git_output(
        root, "diff", "--name-only", f"{implementation_commit}..{head}"
    ).splitlines()
    if changed_paths != [relative]:
        raise RuntimeError("the post-implementation commit must change only the lock file")
    try:
        committed_bytes = subprocess.check_output(
            ["git", "show", f"HEAD:{relative}"], cwd=root, stderr=subprocess.PIPE
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("lock file is not committed at HEAD") from exc
    if committed_bytes != absolute_lock.read_bytes():
        raise RuntimeError("working lock bytes differ from the committed lock")

    for binding in _iter_file_bindings(lock):
        bound_path = root / binding["path"]
        if not bound_path.is_file() or _sha256_file(bound_path) != binding["sha256"]:
            raise RuntimeError(f"bound artifact changed or is missing: {binding['path']}")

    schedule = lock.get("prediction_schedule")
    if not isinstance(schedule, Mapping) or schedule.get("sha256") != _hash_json(
        schedule.get("dates")
    ):
        raise RuntimeError("prediction schedule hash does not reproduce")
    if implementation.get("source_manifest_sha256") != _hash_json(
        lock.get("source_manifests")
    ):
        raise RuntimeError("source manifest aggregate hash does not reproduce")
    if implementation.get("report_schema_sha256") != _hash_json(
        lock.get("report_schemas", {}).get("schemas")
    ):
        raise RuntimeError("report schema aggregate hash does not reproduce")
    if implementation.get("evaluation_code_sha256") != _hash_json(
        implementation.get("evaluation_files")
    ):
        raise RuntimeError("evaluation source aggregate hash does not reproduce")
    return head


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("locked-at must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("locked-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or verify the fail-closed Model V2 pre-shadow lock."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--locked-at", type=_timestamp)
    parser.add_argument("--verify-committed-lock", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.verify_committed_lock:
            commit = verify_committed_lock(
                repository_root=REPOSITORY_ROOT, lock_path=args.output
            )
            print(f"pre_shadow_lock_verification=PASS lock_commit={commit}")
            return 0

        output = REPOSITORY_ROOT / args.output
        if output.exists():
            raise RuntimeError(f"refusing to overwrite existing lock: {args.output}")
        implementation_commit = clean_implementation_commit(REPOSITORY_ROOT)
        locked_at = args.locked_at or datetime.now(timezone.utc)
        lock = build_pre_shadow_lock(
            repository_root=REPOSITORY_ROOT,
            implementation_commit=implementation_commit,
            locked_at=locked_at,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(
            "pre_shadow_lock_status=BLOCKED_COVERAGE_GATES_FAILED "
            f"implementation_commit={implementation_commit} output={args.output}"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"pre-shadow lock failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
