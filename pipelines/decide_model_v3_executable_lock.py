"""Create a no-go or go decision for the Model V3 executable lock and schedule."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import REPOSITORY_ROOT
    import create_model_v3_expanded_universe_design_lock as design_v3
    import decide_model_v3_outcome_blind_rebuild as rebuild_v3
    import decide_model_v3_structural_feasibility_gate as structural_gate_v3
    import freeze_model_v2_failure_evidence as freeze_v2
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines import create_model_v3_expanded_universe_design_lock as design_v3  # type: ignore
    from pipelines import decide_model_v3_outcome_blind_rebuild as rebuild_v3  # type: ignore
    from pipelines import decide_model_v3_structural_feasibility_gate as structural_gate_v3  # type: ignore
    from pipelines import freeze_model_v2_failure_evidence as freeze_v2  # type: ignore
    from pipelines._common import REPOSITORY_ROOT  # type: ignore


DEFAULT_JSON_OUTPUT = Path(
    "reports/reproducibility/model-v3-executable-lock-decision-v1.json"
)
DEFAULT_MARKDOWN_OUTPUT = Path(
    "reports/reproducibility/model-v3-executable-lock-decision-v1.md"
)
CONTRACT_PATH = Path(
    "docs/research/model-v3-executable-lock-and-shadow-schedule-v1.md"
)
READINESS_REPORT = Path("reports/data-audits/model-v3-coverage-readiness-v1.json")
EXECUTABLE_LOCK = Path("experiments/model-v3-executable-lock-v1.json")
PREDICTION_SCHEDULE = Path("experiments/model-v3-prediction-schedule-v1.json")
FIRST_SHADOW_BATCH = Path("predictions/model-v3-first-shadow-batch-v1.jsonl.gz")


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON document must contain an object: {path}")
    return value


def _binding(root: Path, path: Path) -> dict[str, str]:
    body = (root / path).read_bytes()
    return {"path": path.as_posix(), "sha256": _sha256_bytes(body)}


def _coverage_prerequisites(readiness: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if readiness is None:
        return {
            "coverage_report_exists": False,
            "decision": None,
            "all_locked_gates_passed": False,
            "minimum_overall_monthly_coverage": None,
            "minimum_active_branch_monthly_coverage": None,
            "minimum_eligible_names_per_active_branch": None,
            "minimum_represented_branches": None,
            "minimum_represented_sectors": None,
            "clean_rebuilds_matching": None,
            "fallback_or_outcome_access_count": None,
        }
    criteria = readiness.get("criteria", {})
    all_passed = bool(criteria) and all(
        isinstance(result, Mapping) and result.get("passed") is True
        for result in criteria.values()
    )
    return {
        "coverage_report_exists": True,
        "decision": readiness.get("decision"),
        "all_locked_gates_passed": all_passed,
        "minimum_overall_monthly_coverage": readiness.get(
            "minimum_overall_monthly_coverage"
        ),
        "minimum_active_branch_monthly_coverage": readiness.get(
            "minimum_active_branch_monthly_coverage"
        ),
        "minimum_eligible_names_per_active_branch": readiness.get(
            "minimum_eligible_names_per_active_branch"
        ),
        "minimum_represented_branches": readiness.get(
            "minimum_represented_branches"
        ),
        "minimum_represented_sectors": readiness.get("minimum_represented_sectors"),
        "clean_rebuilds_matching": readiness.get("clean_rebuilds_matching"),
        "fallback_or_outcome_access_count": readiness.get(
            "fallback_or_outcome_access_count"
        ),
    }


def build_decision(
    *, repository_root: Path, decided_at: datetime
) -> dict[str, Any]:
    if decided_at.tzinfo is None or decided_at.utcoffset() is None:
        raise ValueError("decided_at must include a timezone")
    root = repository_root.resolve()
    v2_freeze = freeze_v2.verify_failure_freeze(repository_root=root)
    design = design_v3.verify_design_lock(repository_root=root)
    structural = structural_gate_v3.verify_decision(repository_root=root)
    rebuild = rebuild_v3.verify_decision(repository_root=root)
    readiness_path = root / READINESS_REPORT
    readiness = _load_json(readiness_path) if readiness_path.is_file() else None
    coverage = _coverage_prerequisites(readiness)

    prerequisites = {
        "L1": {
            "metric": "model_v2_failure_frozen",
            "required": True,
            "observed": v2_freeze["status"] == "FROZEN_FAILED_NOT_SHADOW_READY",
        },
        "L2": {
            "metric": "model_v3_structural_feasibility_passed",
            "required": True,
            "observed": structural["decision"]
            == "GO_STRUCTURAL_FEASIBILITY_PASSED",
        },
        "L3": {
            "metric": "outcome_blind_rebuild_completed_and_authorized",
            "required": True,
            "observed": (
                rebuild["decision"] == "GO_OUTCOME_BLIND_REBUILD_AUTHORIZED"
                and rebuild["rebuild_authorized"] is True
            ),
        },
        "L4": {
            "metric": "coverage_readiness_report_exists",
            "required": True,
            "observed": coverage["coverage_report_exists"],
        },
        "L5": {
            "metric": "all_locked_coverage_and_reproducibility_gates_passed",
            "required": True,
            "observed": coverage["all_locked_gates_passed"],
        },
        "L6": {
            "metric": "return_outcome_or_post_boundary_access_count",
            "required": 0,
            "observed": 0,
        },
        "L7": {
            "metric": "design_lock_self_authorizes_shadow",
            "required": False,
            "observed": design["executable_for_shadow_predictions"],
        },
    }
    for result in prerequisites.values():
        result["passed"] = result["observed"] == result["required"]
    prerequisites_passed = all(result["passed"] for result in prerequisites.values())

    required_lock_bindings = {
        "implementation_code_commit": None,
        "dependency_environment_sha256": None,
        "expanded_universe_manifest_sha256": None,
        "identity_ledger_sha256": None,
        "classification_ledger_sha256": None,
        "price_and_corporate_action_manifest_sha256": None,
        "accounting_manifest_sha256": None,
        "formula_and_branch_schema_sha256": design["model"]["formula_inheritance"][
            "branch_schema_sha256"
        ],
        "feature_and_eligibility_schema_sha256": None,
        "score_and_reason_code_schema_sha256": None,
        "two_rebuild_fingerprint_sha256": None,
        "coverage_readiness_report_sha256": (
            _sha256_bytes(readiness_path.read_bytes()) if readiness_path.is_file() else None
        ),
        "prediction_schedule_sha256": None,
        "portfolio_notional_usd": None,
        "cost_and_liquidity_protocol_sha256": None,
    }
    all_bindings_complete = all(
        value is not None for value in required_lock_bindings.values()
    )
    executable = prerequisites_passed and all_bindings_complete
    if executable:
        decision = "GO_CREATE_EXECUTABLE_LOCK_AND_PROSPECTIVE_SCHEDULE"
        status = "READY_FOR_NEW_EXECUTABLE_LOCK"
        reason = "ALL_PRE_SHADOW_GATES_AND_BINDINGS_PASSED"
    else:
        decision = "NO_GO_EXECUTABLE_LOCK_PREREQUISITES_FAILED"
        status = "BLOCKED_NO_EXECUTABLE_LOCK_OR_SHADOW_DATE"
        reason = "STRUCTURAL_DATA_REBUILD_AND_COVERAGE_GATES_NOT_PASSED"

    output_inventory = {
        "executable_lock": {
            "path": EXECUTABLE_LOCK.as_posix(),
            "exists": (root / EXECUTABLE_LOCK).exists(),
        },
        "prediction_schedule": {
            "path": PREDICTION_SCHEDULE.as_posix(),
            "exists": (root / PREDICTION_SCHEDULE).exists(),
        },
        "first_shadow_batch": {
            "path": FIRST_SHADOW_BATCH.as_posix(),
            "exists": (root / FIRST_SHADOW_BATCH).exists(),
        },
    }
    return {
        "schema_version": "model-v3-executable-lock-decision-v1",
        "decided_at": decided_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "decision": decision,
        "status": status,
        "reason": reason,
        "claims_eligible": False,
        "outcomes_accessed": False,
        "executable_lock_created": False,
        "shadow_date_selected": False,
        "prediction_schedule": [],
        "real_shadow_batch_created": False,
        "bindings": {
            "contract": _binding(root, CONTRACT_PATH),
            "model_v2_failure_freeze": _binding(root, freeze_v2.DEFAULT_OUTPUT),
            "model_v3_design_lock": _binding(root, design_v3.DEFAULT_OUTPUT),
            "structural_feasibility_gate": _binding(
                root, structural_gate_v3.DEFAULT_JSON_OUTPUT
            ),
            "outcome_blind_rebuild_decision": _binding(
                root, rebuild_v3.DEFAULT_JSON_OUTPUT
            ),
        },
        "prerequisites": prerequisites,
        "coverage_readiness": coverage,
        "required_executable_lock_bindings": required_lock_bindings,
        "all_required_bindings_complete": all_bindings_complete,
        "output_inventory": output_inventory,
        "prospective_schedule_rule": {
            "schedule_may_be_selected_now": False,
            "scheduled_monthly_cohorts_after_go": 24,
            "first_boundary_strictly_after_executable_lock_commit": True,
            "first_boundary_must_be_operationally_reachable": True,
            "prediction_artifact_must_precede_outcome_availability": True,
            "aggregate_results_blinded_until_primary_maturity": True,
            "july_2026_backfill_allowed": False,
        },
        "authorization": {
            "create_executable_lock_authorized": executable,
            "select_shadow_schedule_authorized": executable,
            "create_source_snapshots_for_shadow_authorized": False,
            "create_real_shadow_prediction_authorized": False,
            "evaluate_outcomes_authorized": False,
            "publish_performance_claims_authorized": False,
            "july_2026_backfill_allowed": False,
        },
        "next_required_action": structural["next_required_action"],
    }


def render_markdown(decision: Mapping[str, Any]) -> str:
    missing_bindings = [
        name
        for name, value in decision["required_executable_lock_bindings"].items()
        if value is None
    ]
    lines = [
        "# Model V3 Executable Lock Decision",
        "",
        f"- Decision: `{decision['decision']}`",
        f"- Status: `{decision['status']}`",
        f"- Reason: `{decision['reason']}`",
        "- Claims eligible: `false`",
        "- Outcomes accessed: `false`",
        "- Executable lock created: `false`",
        "- Shadow date selected: `false`",
        "- Real shadow batch created: `false`",
        "",
        "## Prerequisites",
        "",
        "| Gate | Requirement | Observed | Result |",
        "| --- | --- | --- | --- |",
    ]
    for gate_id in sorted(decision["prerequisites"]):
        gate = decision["prerequisites"][gate_id]
        lines.append(
            f"| {gate_id} | {gate['metric']} | {gate['observed']} | "
            f"{'PASS' if gate['passed'] else 'FAIL'} |"
        )
    lines.extend(["", "## Missing executable-lock bindings", ""])
    lines.extend(f"- `{name}`" for name in missing_bindings)
    lines.extend(
        [
            "",
            "## Prospective schedule boundary",
            "",
            "No shadow date is selected. After every prerequisite and binding passes, "
            "a separate immutable schedule may select 24 future monthly cohorts, with "
            "the first boundary strictly after the executable-lock commit and still "
            "operationally reachable before its source cutoff.",
            "",
            "## Decision",
            "",
            "No executable lock, prediction schedule, source snapshot, or real shadow "
            "batch was created. The only next action remains W0: establish the expanded "
            "point-in-time universe and pass the unchanged structural, data, rebuild, "
            "coverage, and reproducibility gates.",
            "",
            "July 2026 remains permanently non-backfillable.",
            "",
        ]
    )
    return "\n".join(lines)


def verify_decision(
    *,
    repository_root: Path,
    json_path: Path = DEFAULT_JSON_OUTPUT,
    markdown_path: Path = DEFAULT_MARKDOWN_OUTPUT,
) -> dict[str, Any]:
    root = repository_root.resolve()
    report_path = json_path if json_path.is_absolute() else root / json_path
    md_path = markdown_path if markdown_path.is_absolute() else root / markdown_path
    decision = _load_json(report_path)
    if decision.get("schema_version") != "model-v3-executable-lock-decision-v1":
        raise ValueError("unexpected Model V3 executable-lock decision schema")
    if decision.get("claims_eligible") is not False:
        raise ValueError("executable-lock decision cannot be claims eligible")
    if decision.get("outcomes_accessed") is not False:
        raise ValueError("executable-lock decision cannot access outcomes")
    decided_at_value = decision.get("decided_at")
    if not isinstance(decided_at_value, str):
        raise ValueError("executable-lock decision lacks its timestamp")
    decided_at = datetime.fromisoformat(decided_at_value.replace("Z", "+00:00"))
    expected = build_decision(repository_root=root, decided_at=decided_at)
    if dict(decision) != expected:
        raise ValueError("Model V3 executable-lock decision no longer reproduces")
    if md_path.read_text(encoding="utf-8") != render_markdown(expected):
        raise ValueError("Model V3 executable-lock markdown no longer reproduces")
    return dict(decision)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("decided-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decide Model V3 executable-lock and shadow-schedule readiness."
    )
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument(
        "--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT
    )
    parser.add_argument("--decided-at", type=_timestamp)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.verify:
            decision = verify_decision(
                repository_root=REPOSITORY_ROOT,
                json_path=args.json_output,
                markdown_path=args.markdown_output,
            )
            print(
                "model_v3_executable_lock=VERIFIED "
                f"decision={decision['decision']}"
            )
            return 0
        json_output = (
            args.json_output
            if args.json_output.is_absolute()
            else REPOSITORY_ROOT / args.json_output
        )
        markdown_output = (
            args.markdown_output
            if args.markdown_output.is_absolute()
            else REPOSITORY_ROOT / args.markdown_output
        )
        if json_output.exists() or markdown_output.exists():
            raise RuntimeError("refusing to overwrite existing executable-lock decision")
        decision = build_decision(
            repository_root=REPOSITORY_ROOT,
            decided_at=args.decided_at or datetime.now(timezone.utc),
        )
        json_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_bytes(_json_bytes(decision))
        markdown_output.write_text(render_markdown(decision), encoding="utf-8")
        print(
            "model_v3_executable_lock=CLOSED "
            f"decision={decision['decision']}"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Model V3 executable-lock decision failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
