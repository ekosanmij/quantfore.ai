"""Create and verify the non-executable Model V3 expanded-universe design lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import REPOSITORY_ROOT
    import freeze_model_v2_failure_evidence as freeze_v2
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines import freeze_model_v2_failure_evidence as freeze_v2  # type: ignore
    from pipelines._common import REPOSITORY_ROOT  # type: ignore


DEFAULT_OUTPUT = Path(
    "experiments/multifactor-v3-expanded-universe-design-lock-v1.json"
)
CONTRACT_PATH = Path(
    "experiments/multifactor-v3-expanded-universe-hypothesis-contract.md"
)
FEASIBILITY_CONTRACT_PATH = Path(
    "docs/research/model-v3-expanded-universe-feasibility-v1.md"
)
PARENT_FREEZE_PATH = Path(
    "experiments/model-v2-failure-evidence-freeze-v1.json"
)
V2_SCORE_MANIFEST_PATH = Path(
    "experiments/model-v2-branch-aware-scores-v1.manifest.json"
)

MINIMUM_ELIGIBLE_NAMES = 20
MINIMUM_BRANCH_COVERAGE = 0.80
MINIMUM_EXPECTED_NAMES = math.ceil(
    MINIMUM_ELIGIBLE_NAMES / MINIMUM_BRANCH_COVERAGE
)
BRANCHES = [
    "INDUSTRIAL_GENERAL",
    "BANK",
    "INSURER_P_AND_C",
    "INSURER_LIFE_HEALTH",
    "BROKER_DEALER",
    "ASSET_MANAGER",
    "EQUITY_REIT",
    "MORTGAGE_REIT",
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


def _validate_v2_inheritance(
    *, freeze: Mapping[str, Any], score_manifest: Mapping[str, Any]
) -> None:
    if freeze.get("status") != "FROZEN_FAILED_NOT_SHADOW_READY":
        raise ValueError("Model V3 requires the frozen failed Model V2 predecessor")
    model = freeze["locked_design_contract"]["payload"]["model"]
    if score_manifest.get("model_version") != model.get("version"):
        raise ValueError("V2 score manifest does not match the frozen model version")
    if score_manifest.get("minimum_branch_cross_section") != MINIMUM_ELIGIBLE_NAMES:
        raise ValueError("V2 minimum branch cross-section changed")
    if score_manifest.get("all_five_families_required") is not True:
        raise ValueError("V2 all-five-family rule changed")
    if score_manifest.get("family_weight_renormalization") is not False:
        raise ValueError("V2 family renormalization rule changed")
    if score_manifest.get("cross_branch_fallback") is not False:
        raise ValueError("V2 cross-branch fallback rule changed")
    expected_weights = {name: "0.20" for name in model["family_weights"]}
    if score_manifest.get("family_weights") != expected_weights:
        raise ValueError("V2 score manifest family weights changed")
    branch_hash = score_manifest.get("branch_schema_sha256")
    if not isinstance(branch_hash, str) or len(branch_hash) != 64:
        raise ValueError("V2 score manifest lacks a valid branch schema binding")


def _structural_gates() -> dict[str, Any]:
    return {
        "F0": {
            "metric": "outcome_return_rank_ic_spread_or_portfolio_access_count",
            "operator": "equals",
            "threshold": 0,
        },
        "F1": {
            "metric": "expected_names_per_structurally_active_branch_every_month",
            "operator": "gte",
            "threshold": MINIMUM_EXPECTED_NAMES,
        },
        "F2": {
            "metric": "floor_expected_names_times_minimum_branch_coverage",
            "operator": "gte",
            "threshold": MINIMUM_ELIGIBLE_NAMES,
        },
        "F3": {
            "metric": "represented_structurally_active_branches_every_month",
            "operator": "gte",
            "threshold": 5,
        },
        "F4": {
            "metric": "represented_gics_sectors_every_month",
            "operator": "gte",
            "threshold": 5,
        },
        "F5": {
            "metric": "expected_security_months_with_exactly_one_structural_disposition_fraction",
            "operator": "equals",
            "threshold": 1.0,
        },
        "F6": {
            "metric": "known_point_in_time_branch_or_subtype_fraction_every_month",
            "operator": "gte",
            "threshold": 0.98,
        },
        "F7": {
            "metric": "two_clean_structural_rebuilds_match_exactly",
            "operator": "equals",
            "threshold": True,
        },
    }


def build_design_lock(
    *, repository_root: Path, locked_at: datetime
) -> dict[str, Any]:
    if locked_at.tzinfo is None or locked_at.utcoffset() is None:
        raise ValueError("locked_at must include a timezone")
    root = repository_root.resolve()
    freeze = freeze_v2.verify_failure_freeze(repository_root=root)
    score_manifest = _load_json(root / V2_SCORE_MANIFEST_PATH)
    _validate_v2_inheritance(freeze=freeze, score_manifest=score_manifest)

    frozen_design = freeze["locked_design_contract"]["payload"]
    frozen_model = frozen_design["model"]
    return {
        "schema_version": "multifactor-v3-expanded-universe-design-lock-v1",
        "locked_at": locked_at.astimezone(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        "status": "DESIGN_LOCK_PRE_FEASIBILITY",
        "decision": "PROCEED_TO_OUTCOME_BLIND_UNIVERSE_FEASIBILITY_ONLY",
        "claims_eligible": False,
        "executable_for_data_acquisition": False,
        "executable_for_score_rebuild": False,
        "executable_for_shadow_predictions": False,
        "executable_for_outcome_evaluation": False,
        "contract": _binding(root, CONTRACT_PATH),
        "feasibility_contract": _binding(root, FEASIBILITY_CONTRACT_PATH),
        "parent_failure_freeze": {
            **_binding(root, PARENT_FREEZE_PATH),
            "schema_version": freeze["schema_version"],
            "status": freeze["status"],
            "frozen_baseline_commit": freeze["frozen_baseline_commit"],
        },
        "model": {
            "version": "multifactor-v3-expanded-universe-branch-aware-equal-weight-v1",
            "change_from_v2": "point_in_time_universe_expansion_only",
            "branches": BRANCHES,
            "factor_families": frozen_model["factor_families"],
            "family_weights": frozen_model["family_weights"],
            "all_five_families_required": True,
            "missing_family_weight_renormalization": False,
            "minimum_component_coverage": frozen_model[
                "minimum_component_coverage"
            ],
            "minimum_required_components_per_family": frozen_model[
                "minimum_required_components_per_family"
            ],
            "minimum_branch_cross_section": MINIMUM_ELIGIBLE_NAMES,
            "cross_branch_fallback": False,
            "formula_inheritance": {
                **_binding(root, V2_SCORE_MANIFEST_PATH),
                "model_version": score_manifest["model_version"],
                "formula_version": score_manifest["formula_version"],
                "feature_version": score_manifest["feature_version"],
                "normalization_version": score_manifest["normalization_version"],
                "branch_schema_sha256": score_manifest["branch_schema_sha256"],
                "outcome_driven_formula_changes_allowed": False,
            },
        },
        "universe": {
            "universe_id": "us-listed-common-equity-pit-v1",
            "status": "SPECIFIED_NOT_ACQUIRED_OR_AUDITED",
            "membership": "historical_monthly_point_in_time",
            "benchmark": "SPY",
            "benchmark_excluded_from_rankings": True,
            "included_population": [
                "us_domiciled_operating_company_common_stock",
                "us_domiciled_equity_reit_common_stock",
                "us_domiciled_mortgage_reit_common_stock",
            ],
            "primary_listing": "active_us_national_securities_exchange",
            "excluded_instrument_types": [
                "etf_or_other_fund",
                "preferred_stock",
                "debt",
                "warrant",
                "right",
                "unit",
                "depositary_receipt",
                "otc_only_security",
                "non_operating_shell",
                "blank_check_company",
            ],
            "point_in_time_membership_required": True,
            "survivorship_free_required": True,
            "delisted_history_preserved": True,
            "availability_timestamp_required": True,
            "revision_policy": "append_only",
            "structural_denominator_fields": [
                "membership",
                "identity",
                "security_type",
                "domicile",
                "primary_listing",
                "branch_classification",
            ],
            "prohibited_denominator_filters": [
                "price_availability",
                "filing_availability",
                "feature_completeness",
                "score_eligibility",
                "liquidity",
                "market_cap",
                "survival",
                "returns_or_outcomes",
            ],
        },
        "structural_feasibility": {
            "status": "NOT_RUN",
            "window": {
                "start": "2017-01-01",
                "end": "2025-06-30",
                "frequency": "monthly_information_boundary",
                "expected_months": 102,
                "claims_eligible": False,
            },
            "branch_activation_rule": (
                "active_when_at_least_one_expected_member_is_assigned; "
                "small_or_data_incomplete_branches_may_not_be_deactivated"
            ),
            "minimum_eligible_names": MINIMUM_ELIGIBLE_NAMES,
            "minimum_branch_score_coverage": MINIMUM_BRANCH_COVERAGE,
            "minimum_expected_names_formula": "ceil(20 / 0.80)",
            "minimum_expected_names_per_active_branch": MINIMUM_EXPECTED_NAMES,
            "gates": _structural_gates(),
            "pass_decision": "PASS_STRUCTURALLY_FEASIBLE",
            "pass_authorizes": "separately_locked_data_readiness_and_acquisition_only",
            "pass_does_not_authorize": [
                "score_rebuild",
                "outcome_access",
                "shadow_prediction",
                "performance_claims",
            ],
        },
        "inherited_engineering_gates": frozen_design["engineering_gates"],
        "inherited_promotion_gates": frozen_design["promotion_gates"],
        "evaluation_protocol": {
            **frozen_design["evaluation_protocol"],
            "forward_shadow_window": {
                "status": "PENDING_POST_FEASIBILITY_EXECUTABLE_LOCK",
                "dates": [],
                "scheduled_monthly_cohorts": 24,
                "must_be_prospective_after_executable_lock_commit": True,
                "july_2026_backfill_allowed": False,
            },
        },
        "portfolio_protocol": frozen_design["portfolio_protocol"],
        "prohibited_changes": [
            "mutation_or_relabelling_of_frozen_model_v2_evidence",
            "july_2026_shadow_backfill",
            "outcome_access_before_new_prospective_shadow_evaluation",
            "availability_or_outcome_driven_structural_denominator_reduction",
            "small_branch_deactivation",
            "minimum_expected_branch_size_below_25",
            "minimum_eligible_branch_size_below_20",
            "minimum_branch_score_coverage_below_0_80",
            "minimum_overall_score_coverage_below_0_90",
            "cross_branch_normalization_or_industrial_fallback",
            "family_weight_renormalization",
            "shadow_schedule_selection_before_all_pre_shadow_gates_pass",
        ],
        "activation_requirements": {
            "structural_feasibility_must_pass_first": True,
            "separate_data_readiness_lock_required": True,
            "separate_executable_lock_required": True,
            "all_inherited_engineering_gates_must_pass": True,
            "two_identical_full_rebuilds_required": True,
            "zero_outcome_and_fallback_access_required": True,
            "future_prediction_schedule_must_be_locked_prospectively": True,
        },
    }


def verify_design_lock(
    *, repository_root: Path, lock_path: Path = DEFAULT_OUTPUT
) -> dict[str, Any]:
    root = repository_root.resolve()
    path = lock_path if lock_path.is_absolute() else root / lock_path
    lock = _load_json(path)
    if lock.get("schema_version") != (
        "multifactor-v3-expanded-universe-design-lock-v1"
    ):
        raise ValueError("unexpected Model V3 design lock schema")
    if lock.get("status") != "DESIGN_LOCK_PRE_FEASIBILITY":
        raise ValueError("Model V3 design lock status changed")
    for field in (
        "claims_eligible",
        "executable_for_data_acquisition",
        "executable_for_score_rebuild",
        "executable_for_shadow_predictions",
        "executable_for_outcome_evaluation",
    ):
        if lock.get(field) is not False:
            raise ValueError(f"Model V3 design lock must retain {field}=false")
    locked_at_value = lock.get("locked_at")
    if not isinstance(locked_at_value, str):
        raise ValueError("Model V3 design lock lacks a timestamp")
    try:
        locked_at = datetime.fromisoformat(locked_at_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Model V3 design lock timestamp is invalid") from exc
    expected = build_design_lock(repository_root=root, locked_at=locked_at)
    if dict(lock) != expected:
        raise ValueError("Model V3 expanded-universe design lock no longer reproduces")
    return dict(lock)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("locked-at must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("locked-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create or verify the non-executable Model V3 design lock."
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--locked-at", type=_timestamp)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        output = args.output if args.output.is_absolute() else REPOSITORY_ROOT / args.output
        if args.verify:
            lock = verify_design_lock(
                repository_root=REPOSITORY_ROOT, lock_path=args.output
            )
            print(
                "model_v3_expanded_universe_design_lock=PASS "
                f"status={lock['status']}"
            )
            return 0
        if output.exists():
            raise RuntimeError(f"refusing to overwrite existing lock: {args.output}")
        lock = build_design_lock(
            repository_root=REPOSITORY_ROOT,
            locked_at=args.locked_at or datetime.now(timezone.utc),
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(_json_bytes(lock))
        print(
            "model_v3_expanded_universe_design_lock=CREATED "
            f"status={lock['status']} output={args.output}"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Model V3 design lock failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
