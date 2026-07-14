"""Authorize or block the first outcome-blind Model V3 rebuild."""

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
    import create_model_v3_data_coverage_remediation_plan as remediation_v3
    import create_model_v3_expanded_universe_design_lock as design_v3
    import decide_model_v3_structural_feasibility_gate as structural_gate_v3
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines import create_model_v3_data_coverage_remediation_plan as remediation_v3  # type: ignore
    from pipelines import create_model_v3_expanded_universe_design_lock as design_v3  # type: ignore
    from pipelines import decide_model_v3_structural_feasibility_gate as structural_gate_v3  # type: ignore
    from pipelines._common import REPOSITORY_ROOT  # type: ignore


DEFAULT_JSON_OUTPUT = Path(
    "reports/reproducibility/model-v3-outcome-blind-rebuild-decision-v1.json"
)
DEFAULT_MARKDOWN_OUTPUT = Path(
    "reports/reproducibility/model-v3-outcome-blind-rebuild-decision-v1.md"
)
CONTRACT_PATH = Path("docs/research/model-v3-outcome-blind-rebuild-v1.md")

REQUIRED_INPUTS = {
    "expanded_universe_manifest": Path(
        "data/raw/model-v3/us-listed-common-equity-pit-v1/manifest.json"
    ),
    "data_acquisition_authorization": Path(
        "experiments/model-v3-data-acquisition-authorization-v1.json"
    ),
    "identity_and_classification_manifest": Path(
        "experiments/model-v3-point-in-time-classification-v1.manifest.json"
    ),
    "price_and_corporate_action_manifest": Path(
        "data/raw/model-v3/prices-and-corporate-actions-v1/manifest.json"
    ),
    "accounting_manifest": Path(
        "data/raw/model-v3/sec-fundamentals-v1/manifest.json"
    ),
    "data_readiness_authorization": Path(
        "experiments/model-v3-data-readiness-authorization-v1.json"
    ),
}

CANONICAL_REBUILD_OUTPUTS = {
    "classification_ledger": Path(
        "experiments/model-v3-point-in-time-classification-v1.jsonl.gz"
    ),
    "feature_input_ledger": Path(
        "experiments/model-v3-branch-feature-inputs-v1.jsonl.gz"
    ),
    "feature_input_manifest": Path(
        "experiments/model-v3-branch-feature-inputs-v1.manifest.json"
    ),
    "score_ledger": Path("experiments/model-v3-branch-aware-scores-v1.jsonl.gz"),
    "score_manifest": Path(
        "experiments/model-v3-branch-aware-scores-v1.manifest.json"
    ),
    "coverage_report": Path(
        "reports/data-audits/model-v3-coverage-readiness-v1.json"
    ),
}


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


def _input_inventory(root: Path) -> dict[str, dict[str, Any]]:
    inventory = {}
    for name, path in REQUIRED_INPUTS.items():
        absolute = root / path
        inventory[name] = {
            "path": path.as_posix(),
            "exists": absolute.is_file(),
            "sha256": _sha256_bytes(absolute.read_bytes()) if absolute.is_file() else None,
        }
    return inventory


def _output_inventory(root: Path) -> dict[str, dict[str, Any]]:
    return {
        name: {"path": path.as_posix(), "exists": (root / path).exists()}
        for name, path in CANONICAL_REBUILD_OUTPUTS.items()
    }


def _locked_acceptance_gates() -> dict[str, Any]:
    return {
        "R1": {
            "metric": "minimum_overall_score_coverage_every_month",
            "operator": "gte",
            "threshold": 0.90,
        },
        "R2": {
            "metric": "minimum_active_branch_score_coverage_every_month",
            "operator": "gte",
            "threshold": 0.80,
        },
        "R3": {
            "metric": "minimum_eligible_names_per_active_branch_every_month",
            "operator": "gte",
            "threshold": 20,
        },
        "R4": {
            "conditions": [
                {
                    "metric": "minimum_represented_active_branches_every_month",
                    "operator": "gte",
                    "threshold": 5,
                },
                {
                    "metric": "minimum_represented_gics_sectors_every_month",
                    "operator": "gte",
                    "threshold": 5,
                },
            ]
        },
        "R5": {
            "metric": "minimum_known_point_in_time_branch_fraction_every_month",
            "operator": "gte",
            "threshold": 0.98,
        },
        "R6": {
            "metric": "expected_security_months_with_stable_final_disposition_fraction",
            "operator": "equals",
            "threshold": 1.0,
        },
        "R7": {
            "metric": "clean_rebuilds_matching_all_locked_artifacts",
            "operator": "equals",
            "threshold": 2,
        },
        "R8": {
            "metric": "cross_branch_fallback_count",
            "operator": "equals",
            "threshold": 0,
        },
        "R9": {
            "metric": "return_outcome_or_post_boundary_access_count",
            "operator": "equals",
            "threshold": 0,
        },
    }


def build_decision(
    *, repository_root: Path, decided_at: datetime
) -> dict[str, Any]:
    if decided_at.tzinfo is None or decided_at.utcoffset() is None:
        raise ValueError("decided_at must include a timezone")
    root = repository_root.resolve()
    design = design_v3.verify_design_lock(repository_root=root)
    structural = structural_gate_v3.verify_decision(repository_root=root)
    remediation = remediation_v3.verify_plan(repository_root=root)
    inputs = _input_inventory(root)
    outputs = _output_inventory(root)

    structural_pass = structural["decision"] == "GO_STRUCTURAL_FEASIBILITY_PASSED"
    data_plan_ready = remediation["blocking_gate"]["passed"] is True
    all_inputs_exist = all(item["exists"] for item in inputs.values())
    design_rebuild_executable = design["executable_for_score_rebuild"] is True
    prerequisites = {
        "P1": {
            "metric": "structural_feasibility_gate_passed",
            "required": True,
            "observed": structural_pass,
            "passed": structural_pass,
        },
        "P2": {
            "metric": "data_remediation_w0_passed",
            "required": True,
            "observed": data_plan_ready,
            "passed": data_plan_ready,
        },
        "P3": {
            "metric": "all_required_hash_bound_input_manifests_exist",
            "required": True,
            "observed": all_inputs_exist,
            "passed": all_inputs_exist,
        },
        "P4": {
            "metric": "design_or_later_readiness_lock_executable_for_score_rebuild",
            "required": True,
            "observed": design_rebuild_executable,
            "passed": design_rebuild_executable,
        },
        "P5": {
            "metric": "return_outcome_or_post_boundary_access_count",
            "required": 0,
            "observed": 0,
            "passed": True,
        },
    }
    authorized = all(item["passed"] for item in prerequisites.values())
    if authorized:
        decision = "GO_OUTCOME_BLIND_REBUILD_AUTHORIZED"
        status = "READY_FOR_TWO_CLEAN_REBUILDS"
        reason = "ALL_REBUILD_PREREQUISITES_PASSED"
    else:
        decision = "NO_GO_REBUILD_PREREQUISITES_FAILED"
        status = "BLOCKED_BEFORE_REBUILD_START"
        reason = "STRUCTURAL_AND_DATA_PREREQUISITES_NOT_SATISFIED"

    return {
        "schema_version": "model-v3-outcome-blind-rebuild-decision-v1",
        "decided_at": decided_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "decision": decision,
        "status": status,
        "reason": reason,
        "claims_eligible": False,
        "outcomes_accessed": False,
        "rebuild_authorized": authorized,
        "rebuild_started": False,
        "canonical_outputs_created": any(item["exists"] for item in outputs.values()),
        "bindings": {
            "contract": _binding(root, CONTRACT_PATH),
            "design_lock": _binding(root, design_v3.DEFAULT_OUTPUT),
            "structural_feasibility_gate": _binding(
                root, structural_gate_v3.DEFAULT_JSON_OUTPUT
            ),
            "data_remediation_plan": _binding(root, remediation_v3.DEFAULT_OUTPUT),
        },
        "prerequisites": prerequisites,
        "required_inputs": inputs,
        "canonical_output_inventory": outputs,
        "rebuild_protocol": {
            "clean_rebuild_count": 2,
            "separate_clean_work_directories_required": True,
            "canonical_publication_before_hash_match_allowed": False,
            "expected_member_denominator_may_shrink": False,
            "populated_branch_deactivation_allowed": False,
            "cross_branch_normalization_or_fallback_allowed": False,
            "family_weight_renormalization_allowed": False,
            "outcome_or_return_columns_allowed": False,
            "stages": [
                "reconstruct_expected_security_months",
                "resolve_point_in_time_identity_branch_and_sector",
                "select_point_in_time_price_and_accounting_inputs",
                "calculate_inherited_branch_features_and_five_families",
                "normalize_only_within_branch",
                "write_one_stable_disposition_per_expected_security_month",
                "compare_two_clean_rebuilds_on_all_locked_artifacts",
                "run_outcome_blind_coverage_audit",
            ],
        },
        "locked_acceptance_gates": _locked_acceptance_gates(),
        "prohibited_access": [
            "returns",
            "outcomes",
            "benchmark_returns",
            "rank_ic",
            "spreads",
            "portfolio_results",
            "post_information_boundary_data",
        ],
        "authorization": {
            "accounting_or_price_rebuild_authorized": authorized,
            "feature_or_score_rebuild_authorized": authorized,
            "coverage_gate_evaluation_authorized": authorized,
            "outcome_evaluation_authorized": False,
            "executable_shadow_lock_authorized": False,
            "shadow_prediction_authorized": False,
            "july_2026_backfill_allowed": False,
        },
        "next_required_action": structural["next_required_action"],
    }


def render_markdown(decision: Mapping[str, Any]) -> str:
    missing_inputs = [
        item["path"]
        for item in decision["required_inputs"].values()
        if not item["exists"]
    ]
    lines = [
        "# Model V3 Outcome-Blind Rebuild Decision",
        "",
        f"- Decision: `{decision['decision']}`",
        f"- Status: `{decision['status']}`",
        f"- Reason: `{decision['reason']}`",
        "- Claims eligible: `false`",
        "- Outcomes accessed: `false`",
        f"- Rebuild authorized: `{str(decision['rebuild_authorized']).lower()}`",
        f"- Rebuild started: `{str(decision['rebuild_started']).lower()}`",
        "- Shadow authorized: `false`",
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
    lines.extend(["", "## Missing required inputs", ""])
    lines.extend(f"- `{path}`" for path in missing_inputs)
    lines.extend(
        [
            "",
            "## Locked rebuild acceptance gates",
            "",
            "- At least 90% overall monthly score coverage.",
            "- At least 80% coverage in every active branch every month.",
            "- At least 20 eligible names in every active branch every month.",
            "- At least five represented branches and sectors every month.",
            "- At least 98% known point-in-time classification every month.",
            "- 100% final dispositions with stable reason codes.",
            "- Two identical clean rebuilds.",
            "- Zero fallback, return, outcome, or post-boundary access.",
            "",
            "## Decision",
            "",
            "The outcome-blind rebuild did not start and no canonical Model V3 score "
            "artifact was created. The only next action remains W0: establish the "
            "expanded point-in-time universe, pass structural feasibility, then obtain "
            "separate data and rebuild authorizations.",
            "",
            "No threshold change, denominator shrinkage, outcome access, shadow "
            "prediction, or July 2026 backfill is allowed.",
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
    if decision.get("schema_version") != (
        "model-v3-outcome-blind-rebuild-decision-v1"
    ):
        raise ValueError("unexpected Model V3 rebuild decision schema")
    if decision.get("claims_eligible") is not False:
        raise ValueError("rebuild decision cannot be claims eligible")
    if decision.get("outcomes_accessed") is not False:
        raise ValueError("rebuild decision cannot access outcomes")
    decided_at_value = decision.get("decided_at")
    if not isinstance(decided_at_value, str):
        raise ValueError("rebuild decision lacks its timestamp")
    decided_at = datetime.fromisoformat(decided_at_value.replace("Z", "+00:00"))
    expected = build_decision(repository_root=root, decided_at=decided_at)
    if dict(decision) != expected:
        raise ValueError("Model V3 outcome-blind rebuild decision no longer reproduces")
    if md_path.read_text(encoding="utf-8") != render_markdown(expected):
        raise ValueError("Model V3 outcome-blind rebuild markdown no longer reproduces")
    return dict(decision)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("decided-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Authorize or block the first outcome-blind Model V3 rebuild."
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
                "model_v3_outcome_blind_rebuild=VERIFIED "
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
            raise RuntimeError("refusing to overwrite existing rebuild decision")
        decision = build_decision(
            repository_root=REPOSITORY_ROOT,
            decided_at=args.decided_at or datetime.now(timezone.utc),
        )
        json_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_bytes(_json_bytes(decision))
        markdown_output.write_text(render_markdown(decision), encoding="utf-8")
        print(
            "model_v3_outcome_blind_rebuild=CLOSED "
            f"decision={decision['decision']}"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Model V3 outcome-blind rebuild decision failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
