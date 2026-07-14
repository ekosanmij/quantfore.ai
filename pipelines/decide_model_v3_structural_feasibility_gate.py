"""Close the Model V3 pre-rebuild structural feasibility gate."""

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
    import audit_model_v3_expanded_universe_feasibility as feasibility_v3
    import create_model_v3_data_coverage_remediation_plan as remediation_v3
    import create_model_v3_expanded_universe_design_lock as design_v3
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines import audit_model_v3_expanded_universe_feasibility as feasibility_v3  # type: ignore
    from pipelines import create_model_v3_data_coverage_remediation_plan as remediation_v3  # type: ignore
    from pipelines import create_model_v3_expanded_universe_design_lock as design_v3  # type: ignore
    from pipelines._common import REPOSITORY_ROOT  # type: ignore


DEFAULT_JSON_OUTPUT = Path(
    "reports/reproducibility/model-v3-structural-feasibility-gate-decision-v1.json"
)
DEFAULT_MARKDOWN_OUTPUT = Path(
    "reports/reproducibility/model-v3-structural-feasibility-gate-decision-v1.md"
)


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


def build_decision(
    *, repository_root: Path, decided_at: datetime
) -> dict[str, Any]:
    if decided_at.tzinfo is None or decided_at.utcoffset() is None:
        raise ValueError("decided_at must include a timezone")
    root = repository_root.resolve()
    design = design_v3.verify_design_lock(repository_root=root)
    audit = feasibility_v3.verify_audit(repository_root=root)
    remediation = remediation_v3.verify_plan(repository_root=root)

    audit_passed = (
        audit["decision"] == "PASS_STRUCTURALLY_FEASIBLE"
        and all(result.get("passed") is True for result in audit["criteria"].values())
    )
    if audit_passed:
        decision = "GO_STRUCTURAL_FEASIBILITY_PASSED"
        status = "CLOSED_GO_TO_SEPARATE_DATA_AUTHORIZATION"
        reason = "ALL_STRUCTURAL_FEASIBILITY_GATES_PASSED"
    elif audit["status"] == "BLOCKED_MISSING_EXPANDED_UNIVERSE_INPUT":
        decision = "NO_GO_MISSING_EXPANDED_UNIVERSE_EVIDENCE"
        status = "CLOSED_NO_GO_BEFORE_REBUILD"
        reason = "QUALIFYING_POINT_IN_TIME_UNIVERSE_LEDGER_NOT_PRESENT"
    else:
        decision = "NO_GO_STRUCTURAL_FEASIBILITY_FAILED"
        status = "CLOSED_NO_GO_BEFORE_REBUILD"
        reason = audit["status"]

    criteria = {
        gate_id: {
            "metric": result["metric"],
            "operator": result["operator"],
            "threshold": result["threshold"],
            "observed": result.get("observed"),
            "passed": result["passed"],
            "status": result.get("status"),
        }
        for gate_id, result in audit["criteria"].items()
    }
    return {
        "schema_version": "model-v3-structural-feasibility-gate-decision-v1",
        "decided_at": decided_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "decision": decision,
        "status": status,
        "reason": reason,
        "claims_eligible": False,
        "outcomes_accessed": False,
        "bindings": {
            "design_lock": _binding(root, design_v3.DEFAULT_OUTPUT),
            "feasibility_contract": design["feasibility_contract"],
            "feasibility_audit_json": _binding(
                root, feasibility_v3.DEFAULT_JSON_OUTPUT
            ),
            "feasibility_audit_markdown": _binding(
                root, feasibility_v3.DEFAULT_MARKDOWN_OUTPUT
            ),
            "data_remediation_plan": _binding(
                root, remediation_v3.DEFAULT_OUTPUT
            ),
        },
        "structural_rule": {
            "minimum_eligible_names_per_active_branch": 20,
            "minimum_active_branch_coverage": 0.80,
            "minimum_expected_names_formula": "ceil(20 / 0.80)",
            "minimum_expected_names_per_populated_branch": 25,
            "populated_branches_may_be_deactivated": False,
            "denominator_may_shrink_for_missing_data": False,
        },
        "audit_result": {
            "decision": audit["decision"],
            "status": audit["status"],
            "failed_criteria": audit["failed_criteria"],
            "expected_months": audit["reconciliation"]["expected_months"],
            "evaluated_months": audit["reconciliation"]["evaluated_months"],
            "candidate_input": audit["candidate_input"],
            "criteria": criteria,
        },
        "authorization": {
            "structural_evidence_source_selection_allowed": not audit_passed,
            "structural_evidence_acquisition_requires_separate_authorization": True,
            "accounting_or_price_acquisition_authorized": audit_passed,
            "feature_or_score_rebuild_authorized": False,
            "executable_lock_authorized": False,
            "shadow_prediction_authorized": False,
            "outcome_evaluation_authorized": False,
            "july_2026_backfill_allowed": False,
        },
        "next_required_action": {
            "id": "W0_EXPANDED_UNIVERSE_DENOMINATOR",
            "action": (
                "select and separately authorize a source for historical full-exchange "
                "listing, identity, domicile, security type, primary exchange, delisting, "
                "branch, and sector evidence; then build two identical ledgers and rerun "
                "the same audit"
            ),
            "required_artifact": (
                "data/raw/model-v3/us-listed-common-equity-pit-v1/manifest.json"
            ),
            "required_decision_to_continue": "PASS_STRUCTURALLY_FEASIBLE",
            "threshold_changes_allowed": False,
            "return_or_outcome_access_allowed": False,
        },
        "remediation_state": {
            "status": remediation["status"],
            "all_downstream_work_packages_blocked": all(
                row["status"] == "BLOCKED_BY_W0"
                for row in remediation["ordered_work_packages"][1:]
            ),
        },
    }


def render_markdown(decision: Mapping[str, Any]) -> str:
    audit = decision["audit_result"]
    authorization = decision["authorization"]
    lines = [
        "# Model V3 Structural Feasibility Gate Decision",
        "",
        f"- Decision: `{decision['decision']}`",
        f"- Status: `{decision['status']}`",
        f"- Reason: `{decision['reason']}`",
        "- Claims eligible: `false`",
        "- Outcomes accessed: `false`",
        "- Feature or score rebuild authorized: "
        f"`{str(authorization['feature_or_score_rebuild_authorized']).lower()}`",
        "- Shadow prediction authorized: `false`",
        "",
        "## Locked structural rule",
        "",
        "The model requires at least 20 eligible names at 80% minimum branch "
        "coverage. Therefore every populated branch must contain at least "
        "`ceil(20 / 0.80) = 25` expected names before an expensive rebuild begins.",
        "Populated branches cannot be deactivated and missing data cannot shrink the "
        "denominator.",
        "",
        "## Audit result",
        "",
        f"- Audit decision: `{audit['decision']}`",
        f"- Evaluated months: `{audit['evaluated_months']} / {audit['expected_months']}`",
        f"- Failed or unevaluable gates: `{', '.join(audit['failed_criteria'])}`",
        "",
        "| Gate | Result | Observed | Threshold |",
        "| --- | --- | ---: | ---: |",
    ]
    for gate_id in sorted(audit["criteria"]):
        gate = audit["criteria"][gate_id]
        observed = gate["observed"] if gate["observed"] is not None else "not evaluable"
        lines.append(
            f"| {gate_id} | {'PASS' if gate['passed'] else 'FAIL'} | "
            f"{observed} | {gate['threshold']} |"
        )
    lines.extend(
        [
            "",
            "## Decision",
            "",
            "No accounting, price, feature, score, executable-lock, or shadow rebuild "
            "may proceed. The next action is W0: select and separately authorize a "
            "historical expanded-universe evidence source, build two identical "
            "point-in-time ledgers, and rerun this unchanged audit.",
            "",
            "No threshold change, denominator shrinkage, outcome access, or July 2026 "
            "backfill is allowed.",
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
        "model-v3-structural-feasibility-gate-decision-v1"
    ):
        raise ValueError("unexpected Model V3 feasibility decision schema")
    if decision.get("claims_eligible") is not False:
        raise ValueError("feasibility decision cannot be claims eligible")
    decided_at_value = decision.get("decided_at")
    if not isinstance(decided_at_value, str):
        raise ValueError("feasibility decision lacks its timestamp")
    decided_at = datetime.fromisoformat(decided_at_value.replace("Z", "+00:00"))
    expected = build_decision(repository_root=root, decided_at=decided_at)
    if dict(decision) != expected:
        raise ValueError("Model V3 feasibility gate decision no longer reproduces")
    if md_path.read_text(encoding="utf-8") != render_markdown(expected):
        raise ValueError("Model V3 feasibility gate markdown no longer reproduces")
    return dict(decision)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("decided-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Close or verify the Model V3 structural feasibility gate."
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
                "model_v3_structural_feasibility_gate=VERIFIED "
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
            raise RuntimeError("refusing to overwrite existing feasibility decision")
        decision = build_decision(
            repository_root=REPOSITORY_ROOT,
            decided_at=args.decided_at or datetime.now(timezone.utc),
        )
        json_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_bytes(_json_bytes(decision))
        markdown_output.write_text(render_markdown(decision), encoding="utf-8")
        print(
            "model_v3_structural_feasibility_gate=CLOSED "
            f"decision={decision['decision']}"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Model V3 feasibility gate failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
