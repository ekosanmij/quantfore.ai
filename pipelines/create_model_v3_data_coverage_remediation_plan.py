"""Create and verify the blocked Model V3 data-coverage remediation plan."""

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
    import create_model_v3_expanded_universe_design_lock as design_v3
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines import audit_model_v3_expanded_universe_feasibility as feasibility_v3  # type: ignore
    from pipelines import create_model_v3_expanded_universe_design_lock as design_v3  # type: ignore
    from pipelines._common import REPOSITORY_ROOT  # type: ignore


DEFAULT_OUTPUT = Path(
    "experiments/model-v3-data-coverage-remediation-plan-v1.json"
)
CONTRACT_PATH = Path(
    "docs/research/model-v3-data-coverage-remediation-v1.md"
)
V2_ACCOUNTING_COVERAGE = Path(
    "reports/data-audits/model-v2-accounting-coverage-v1.json"
)
V2_READINESS = Path("reports/data-audits/model-v2-coverage-readiness-v1.json")
V2_FUNDAMENTAL_MANIFEST = Path(
    "data/raw/free-point-in-time/sec-fundamentals-bundle-v2/manifest.json"
)
PRICE_BATCHES = [
    Path("data/raw/free-point-in-time/tiingo-prices-v1/batch-001/batch-registry.json"),
    Path("data/raw/free-point-in-time/tiingo-prices-v1/batch-002/batch-registry.json"),
]


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


def _component_baseline(coverage: Mapping[str, Any]) -> list[dict[str, Any]]:
    components = coverage["coverage"]["model_v2_bundle_v2"]["components"]
    return [
        {
            "component": row["component"],
            "family": row["family"],
            "ready_rate": row["ready_rate"],
            "source_missing": row["reason_counts"].get("SOURCE_MISSING", 0),
            "stale_filing": row["reason_counts"].get("STALE_FILING", 0),
        }
        for row in sorted(components, key=lambda value: value["ready_rate"])
    ]


def build_plan(
    *, repository_root: Path, generated_at: datetime
) -> dict[str, Any]:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    root = repository_root.resolve()
    design_lock = design_v3.verify_design_lock(repository_root=root)
    feasibility = feasibility_v3.verify_audit(repository_root=root)
    accounting = _load_json(root / V2_ACCOUNTING_COVERAGE)
    readiness = _load_json(root / V2_READINESS)
    fundamentals = _load_json(root / V2_FUNDAMENTAL_MANIFEST)
    price_batches = [_load_json(root / path) for path in PRICE_BATCHES]

    structurally_feasible = feasibility["decision"] == "PASS_STRUCTURALLY_FEASIBLE"
    family_baseline = accounting["coverage"]["model_v2_bundle_v2"][
        "all_accounting_components_ready_by_family"
    ]
    normalization = fundamentals["accounting_history_contract"][
        "normalization_counts"
    ]
    score_reconciliation = readiness["reconciliation"]
    status = (
        "READY_FOR_SEPARATE_DATA_ACQUISITION_AUTHORIZATION"
        if structurally_feasible
        else "BLOCKED_STRUCTURAL_FEASIBILITY_INPUT_MISSING"
    )
    common_status = "PENDING_SEPARATE_AUTHORIZATION" if structurally_feasible else "BLOCKED_BY_W0"
    return {
        "schema_version": "model-v3-data-coverage-remediation-plan-v1",
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "status": status,
        "claims_eligible": False,
        "executable_for_data_acquisition": False,
        "executable_for_score_rebuild": False,
        "outcomes_accessed": False,
        "bindings": {
            "contract": _binding(root, CONTRACT_PATH),
            "design_lock": _binding(root, design_v3.DEFAULT_OUTPUT),
            "feasibility_audit": _binding(root, feasibility_v3.DEFAULT_JSON_OUTPUT),
            "v2_accounting_coverage": _binding(root, V2_ACCOUNTING_COVERAGE),
            "v2_readiness": _binding(root, V2_READINESS),
            "v2_fundamental_manifest": _binding(root, V2_FUNDAMENTAL_MANIFEST),
            "v2_price_batches": [
                _binding(root, path) for path in PRICE_BATCHES
            ],
        },
        "blocking_gate": {
            "work_package": "W0_EXPANDED_UNIVERSE_DENOMINATOR",
            "required_decision": "PASS_STRUCTURALLY_FEASIBLE",
            "observed_decision": feasibility["decision"],
            "passed": structurally_feasible,
            "accounting_or_price_acquisition_before_pass_allowed": False,
        },
        "v2_prioritization_baseline": {
            "expected_security_months": score_reconciliation[
                "expected_stock_months"
            ],
            "scored_security_months": score_reconciliation[
                "scored_stock_months"
            ],
            "excluded_security_months": score_reconciliation[
                "excluded_stock_months"
            ],
            "aggregate_final_score_coverage": readiness["coverage"][
                "aggregate_final_score_coverage"
            ],
            "complete_family_readiness": {
                family: value["rate"] for family, value in family_baseline.items()
            },
            "component_readiness": _component_baseline(accounting),
            "sec_normalization_gaps": {
                key: normalization[key]
                for key in (
                    "missing_filing_evidence",
                    "missing_company_source",
                    "denominator_security_without_v2_identity",
                    "discrete_quarter_unresolved",
                    "instant_quarter_unresolved",
                    "orphan_amendment_identity",
                    "unsupported_unit",
                )
            },
            "specialist_concept_fact_counts": {
                key.removeprefix("concept_fact_count::"): value
                for key, value in normalization.items()
                if key
                in {
                    "concept_fact_count::credit_loss_provision",
                    "concept_fact_count::customer_deposits",
                    "concept_fact_count::loans_and_leases_net",
                    "concept_fact_count::net_interest_income",
                    "concept_fact_count::net_investment_income",
                    "concept_fact_count::policyholder_benefits_claims_net",
                    "concept_fact_count::premiums_earned_net",
                    "concept_fact_count::real_estate_investment_property_net",
                    "concept_fact_count::investment_real_estate_sale_gain_loss",
                }
            },
            "existing_price_request_start": min(
                batch["start_date"] for batch in price_batches
            ),
            "baseline_scope_warning": (
                "V2 S&P 500 evidence prioritizes work but cannot prove Model V3 "
                "expanded-universe coverage"
            ),
        },
        "ordered_work_packages": [
            {
                "id": "W0",
                "name": "expanded_universe_denominator",
                "status": "BLOCKING_INPUT_MISSING" if not structurally_feasible else "PASSED",
                "acceptance": "PASS_STRUCTURALLY_FEASIBLE",
            },
            {
                "id": "W1",
                "name": "identity_and_classification_completion",
                "status": common_status,
                "acceptance": {
                    "final_disposition_fraction": 1.0,
                    "minimum_known_branch_fraction_every_month": 0.98,
                },
            },
            {
                "id": "W2",
                "name": "price_and_corporate_action_history",
                "status": common_status,
                "acceptance": {
                    "minimum_pre_boundary_sessions": 252,
                    "expected_member_denominator_reduction_allowed": False,
                    "delisted_history_preserved": True,
                },
            },
            {
                "id": "W3",
                "name": "filing_and_sec_concept_evidence",
                "status": common_status,
                "acceptance": {
                    "point_in_time_accession_lineage_required": True,
                    "outcome_driven_tag_selection_allowed": False,
                    "all_missing_evidence_has_stable_reason": True,
                },
            },
            {
                "id": "W4",
                "name": "industrial_quality_repair",
                "status": common_status,
                "priority_components": [
                    "gross_profitability",
                    "roic",
                    "fcf_conversion",
                ],
                "priority_family": "quality",
            },
            {
                "id": "W5",
                "name": "specialist_branch_repair",
                "status": common_status,
                "required_concepts": [
                    "loans_and_leases_net",
                    "customer_deposits",
                    "credit_loss_provision",
                    "net_interest_income",
                    "premiums_earned_net",
                    "policyholder_benefits_claims_net",
                    "net_investment_income",
                    "real_estate_investment_property_net",
                    "investment_real_estate_sale_gain_loss",
                    "cash_from_operations",
                    "capital_expenditure",
                    "diluted_shares",
                ],
            },
            {
                "id": "W6",
                "name": "readiness_and_reproducibility",
                "status": common_status,
                "acceptance": {
                    "minimum_overall_score_coverage_every_month": 0.90,
                    "minimum_active_branch_score_coverage_every_month": 0.80,
                    "minimum_eligible_names_per_active_branch_every_month": 20,
                    "minimum_represented_branches_every_month": 5,
                    "minimum_represented_sectors_every_month": 5,
                    "identical_clean_rebuilds": 2,
                    "fallback_or_outcome_access_count": 0,
                },
            },
        ],
        "stop_conditions": [
            "structural_feasibility_not_passed",
            "historical_membership_or_identity_not_proven",
            "populated_branch_below_25_expected_names",
            "rebuild_mismatch",
            "outcome_or_post_boundary_access",
            "availability_driven_denominator_reduction",
        ],
        "authorization_boundary": {
            "next_authorization_after_w0": "versioned_data_acquisition_authorization",
            "score_rebuild_requires_separate_readiness_authorization": True,
            "shadow_schedule_must_be_prospective": True,
            "july_2026_backfill_allowed": False,
        },
    }


def verify_plan(
    *, repository_root: Path, plan_path: Path = DEFAULT_OUTPUT
) -> dict[str, Any]:
    root = repository_root.resolve()
    path = plan_path if plan_path.is_absolute() else root / plan_path
    plan = _load_json(path)
    if plan.get("schema_version") != "model-v3-data-coverage-remediation-plan-v1":
        raise ValueError("unexpected Model V3 data remediation plan schema")
    if plan.get("claims_eligible") is not False:
        raise ValueError("data remediation plan cannot be claims eligible")
    if plan.get("executable_for_data_acquisition") is not False:
        raise ValueError("plan cannot self-authorize data acquisition")
    if plan.get("executable_for_score_rebuild") is not False:
        raise ValueError("plan cannot self-authorize a score rebuild")
    generated_at_value = plan.get("generated_at")
    if not isinstance(generated_at_value, str):
        raise ValueError("data remediation plan lacks its timestamp")
    generated_at = datetime.fromisoformat(generated_at_value.replace("Z", "+00:00"))
    expected = build_plan(repository_root=root, generated_at=generated_at)
    if dict(plan) != expected:
        raise ValueError("Model V3 data remediation plan no longer reproduces")
    return dict(plan)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("generated-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or verify the Model V3 data remediation plan."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--generated-at", type=_timestamp)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.verify:
            plan = verify_plan(
                repository_root=REPOSITORY_ROOT, plan_path=args.output
            )
            print(f"model_v3_data_remediation_plan=PASS status={plan['status']}")
            return 0
        output = args.output if args.output.is_absolute() else REPOSITORY_ROOT / args.output
        if output.exists():
            raise RuntimeError(f"refusing to overwrite existing plan: {args.output}")
        plan = build_plan(
            repository_root=REPOSITORY_ROOT,
            generated_at=args.generated_at or datetime.now(timezone.utc),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_json_bytes(plan))
        print(f"model_v3_data_remediation_plan=CREATED status={plan['status']}")
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Model V3 data remediation plan failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
