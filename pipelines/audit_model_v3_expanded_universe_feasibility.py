"""Audit Model V3 structural universe feasibility without prices or outcomes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import REPOSITORY_ROOT
    import create_model_v3_expanded_universe_design_lock as design_v3
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines import create_model_v3_expanded_universe_design_lock as design_v3  # type: ignore
    from pipelines._common import REPOSITORY_ROOT  # type: ignore


DEFAULT_CANDIDATE_MANIFEST = Path(
    "data/raw/model-v3/us-listed-common-equity-pit-v1/manifest.json"
)
DEFAULT_JSON_OUTPUT = Path(
    "reports/data-audits/model-v3-expanded-universe-feasibility-v1.json"
)
DEFAULT_MARKDOWN_OUTPUT = Path(
    "reports/data-audits/model-v3-expanded-universe-feasibility-v1.md"
)
V2_READINESS_REPORT = Path(
    "reports/data-audits/model-v2-coverage-readiness-v1.json"
)
CURRENT_BUNDLE_MANIFEST = Path(
    "data/raw/free-point-in-time/composite-equity-bundle-v1/manifest.json"
)
CURRENT_SEC_TICKERS = Path(
    "data/raw/free-point-in-time/sec/company_tickers.json"
)

REQUIRED_ROW_FIELDS = {
    "information_boundary",
    "security_id",
    "issuer_id",
    "historical_ticker",
    "domicile",
    "primary_exchange",
    "security_type",
    "membership_effective_from",
    "membership_effective_to",
    "identity_effective_from",
    "identity_effective_to",
    "source_available_at",
    "branch",
    "gics_sector",
    "source_snapshot_ids",
    "structural_disposition",
    "reason_code",
}
PROHIBITED_FIELD_TOKENS = {
    "price",
    "return",
    "outcome",
    "rank_ic",
    "spread",
    "portfolio",
    "filing_availability",
    "feature",
    "score",
    "liquidity",
    "market_cap",
    "survival",
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


def _expected_boundaries(root: Path) -> list[str]:
    readiness = _load_json(root / V2_READINESS_REPORT)
    monthly = readiness.get("coverage", {}).get("monthly", [])
    boundaries = [
        row.get("prediction_date")
        for row in monthly
        if isinstance(row, Mapping)
    ]
    if (
        len(boundaries) != 102
        or len(set(boundaries)) != 102
        or not all(isinstance(value, str) for value in boundaries)
    ):
        raise ValueError("frozen diagnostic boundaries must contain 102 unique dates")
    return list(boundaries)


def _local_inventory(root: Path) -> dict[str, Any]:
    bundle_path = root / CURRENT_BUNDLE_MANIFEST
    tickers_path = root / CURRENT_SEC_TICKERS
    bundle = _load_json(bundle_path) if bundle_path.is_file() else {}
    tickers = _load_json(tickers_path) if tickers_path.is_file() else {}
    counts = bundle.get("audit_contract", {}).get("expected_row_counts", {})
    return {
        "current_point_in_time_bundle": {
            **(_binding(root, CURRENT_BUNDLE_MANIFEST) if bundle_path.is_file() else {}),
            "security_count": counts.get("securities"),
            "membership_episode_count": counts.get("memberships"),
            "qualification": "S_AND_P_500_ONLY_NOT_MODEL_V3_UNIVERSE",
        },
        "current_sec_ticker_file": {
            **(_binding(root, CURRENT_SEC_TICKERS) if tickers_path.is_file() else {}),
            "record_count": len(tickers),
            "qualification": "CURRENT_ONLY_NOT_HISTORICAL_MEMBERSHIP_EVIDENCE",
        },
        "missing_qualifying_fields": [
            "historical_full_exchange_listing_census",
            "point_in_time_domicile",
            "point_in_time_security_type",
            "point_in_time_primary_exchange",
            "historical_delisting_episodes_for_full_universe",
            "point_in_time_branch_and_sector_for_full_universe",
            "two_independent_rebuild_ledgers",
        ],
    }


def _criteria_not_evaluable() -> dict[str, Any]:
    criteria = {}
    locked = design_v3._structural_gates()
    for gate_id, gate in locked.items():
        if gate_id == "F0":
            criteria[gate_id] = {
                **gate,
                "observed": 0,
                "passed": True,
                "status": "PASS_NO_PROHIBITED_INPUT_ACCESSED",
            }
        else:
            criteria[gate_id] = {
                **gate,
                "observed": None,
                "passed": False,
                "status": "NOT_EVALUABLE_INPUT_MISSING",
            }
    return criteria


def _missing_input_report(
    *, root: Path, design_lock: Mapping[str, Any], manifest_path: Path
) -> dict[str, Any]:
    criteria = _criteria_not_evaluable()
    return {
        "decision": "FAIL_LINEAGE_OR_REPRODUCIBILITY",
        "status": "BLOCKED_MISSING_EXPANDED_UNIVERSE_INPUT",
        "candidate_input": {
            "path": manifest_path.as_posix(),
            "exists": False,
            "qualification": "NO_QUALIFYING_MODEL_V3_MANIFEST",
        },
        "local_inventory": _local_inventory(root),
        "criteria": criteria,
        "failed_criteria": [
            gate_id for gate_id, result in criteria.items() if not result["passed"]
        ],
        "monthly": [],
        "reconciliation": {
            "expected_months": 102,
            "evaluated_months": 0,
            "expected_security_months": None,
            "duplicate_security_months": None,
            "structural_disposition_fraction": None,
        },
    }


def _read_ledger(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    body = path.read_bytes()
    raw = gzip.decompress(body) if path.suffix == ".gz" else body
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.decode("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"ledger row {line_number} must be an object")
        rows.append(value)
    return body, rows


def _resolve_ledger(
    manifest_path: Path, binding: Mapping[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    relative = binding.get("path")
    if not isinstance(relative, str) or not relative:
        raise ValueError("rebuild ledger binding lacks a path")
    path = Path(relative)
    if not path.is_absolute():
        path = manifest_path.parent / path
    body, rows = _read_ledger(path)
    sha256 = _sha256_bytes(body)
    if binding.get("sha256") != sha256:
        raise ValueError("rebuild ledger SHA-256 does not match")
    if binding.get("row_count") != len(rows):
        raise ValueError("rebuild ledger row count does not match")
    return {
        "path": relative,
        "sha256": sha256,
        "row_count": len(rows),
    }, rows


def _row_errors(
    rows: Iterable[Mapping[str, Any]], expected_boundaries: Sequence[str]
) -> tuple[list[str], int, int]:
    errors: list[str] = []
    keys: Counter[tuple[str, str]] = Counter()
    disposition_rows = 0
    expected_boundary_set = set(expected_boundaries)
    for index, row in enumerate(rows):
        missing = sorted(REQUIRED_ROW_FIELDS - set(row))
        if missing:
            errors.append(f"row_{index}_missing_fields:{','.join(missing)}")
        prohibited = sorted(
            key
            for key in row
            if any(token in key.lower() for token in PROHIBITED_FIELD_TOKENS)
        )
        if prohibited:
            errors.append(f"row_{index}_prohibited_fields:{','.join(prohibited)}")
        boundary = row.get("information_boundary")
        security_id = row.get("security_id")
        if boundary not in expected_boundary_set:
            errors.append(f"row_{index}_unexpected_boundary")
        if not isinstance(security_id, str) or not security_id:
            errors.append(f"row_{index}_invalid_security_id")
        elif isinstance(boundary, str):
            keys[(boundary, security_id)] += 1
        if row.get("structural_disposition") and row.get("reason_code"):
            disposition_rows += 1
    duplicate_count = sum(count - 1 for count in keys.values() if count > 1)
    if duplicate_count:
        errors.append(f"duplicate_security_months:{duplicate_count}")
    return errors, duplicate_count, disposition_rows


def _evaluated_report(
    *,
    root: Path,
    design_lock: Mapping[str, Any],
    manifest_path: Path,
    expected_boundaries: Sequence[str],
) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    errors: list[str] = []
    if manifest.get("schema_version") != (
        "model-v3-expanded-universe-membership-evidence-v1"
    ):
        errors.append("unexpected_manifest_schema")
    if manifest.get("universe_id") != "us-listed-common-equity-pit-v1":
        errors.append("unexpected_universe_id")
    if manifest.get("claims_eligible") is not False:
        errors.append("claims_eligible_must_be_false")
    outcomes_accessed = manifest.get("outcomes_accessed")
    forbidden_columns = manifest.get("prohibited_columns_read")
    if outcomes_accessed is not False:
        errors.append("outcomes_accessed_must_be_false")
    if forbidden_columns != []:
        errors.append("prohibited_columns_read_must_be_empty")
    if manifest.get("information_boundaries") != list(expected_boundaries):
        errors.append("information_boundaries_do_not_match_lock")

    rebuilds = manifest.get("rebuilds")
    ledger_bindings: list[dict[str, Any]] = []
    rebuild_rows: list[list[dict[str, Any]]] = []
    if not isinstance(rebuilds, list) or len(rebuilds) != 2:
        errors.append("exactly_two_rebuild_ledgers_required")
    else:
        for binding in rebuilds:
            if not isinstance(binding, Mapping):
                errors.append("invalid_rebuild_binding")
                continue
            try:
                resolved, rows = _resolve_ledger(manifest_path, binding)
            except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"invalid_rebuild_ledger:{exc}")
                continue
            ledger_bindings.append(resolved)
            rebuild_rows.append(rows)

    rows = rebuild_rows[0] if rebuild_rows else []
    row_errors, duplicate_count, disposition_rows = _row_errors(
        rows, expected_boundaries
    )
    errors.extend(row_errors)
    rebuilds_match = len(rebuild_rows) == 2 and rebuild_rows[0] == rebuild_rows[1]
    if not rebuilds_match:
        errors.append("clean_rebuild_ledgers_differ")

    monthly_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        boundary = row.get("information_boundary")
        if isinstance(boundary, str):
            monthly_rows[boundary].append(row)

    monthly: list[dict[str, Any]] = []
    f1_pass = True
    f2_pass = True
    f3_pass = True
    f4_pass = True
    f6_pass = True
    minimum_branch_count: Optional[int] = None
    minimum_theoretical_eligible: Optional[int] = None
    minimum_branch_count_month: Optional[str] = None
    minimum_known_branch_fraction: Optional[float] = None
    for boundary in expected_boundaries:
        cohort = monthly_rows.get(boundary, [])
        branch_counts = Counter(
            str(row.get("branch"))
            for row in cohort
            if row.get("branch") in design_v3.BRANCHES
        )
        active = sorted(branch_counts)
        branch_min = min(branch_counts.values()) if branch_counts else 0
        theoretical = (
            min(math.floor(count * design_v3.MINIMUM_BRANCH_COVERAGE) for count in branch_counts.values())
            if branch_counts
            else 0
        )
        sectors = sorted(
            {
                str(row.get("gics_sector"))
                for row in cohort
                if row.get("gics_sector") not in (None, "", "UNKNOWN")
            }
        )
        known_branch = sum(row.get("branch") in design_v3.BRANCHES for row in cohort)
        known_fraction = known_branch / len(cohort) if cohort else 0.0
        f1_pass &= bool(branch_counts) and branch_min >= 25
        f2_pass &= bool(branch_counts) and theoretical >= 20
        f3_pass &= len(active) >= 5
        f4_pass &= len(sectors) >= 5
        f6_pass &= known_fraction >= 0.98
        if minimum_branch_count is None or branch_min < minimum_branch_count:
            minimum_branch_count = branch_min
            minimum_branch_count_month = boundary
        if minimum_theoretical_eligible is None or theoretical < minimum_theoretical_eligible:
            minimum_theoretical_eligible = theoretical
        if minimum_known_branch_fraction is None or known_fraction < minimum_known_branch_fraction:
            minimum_known_branch_fraction = known_fraction
        monthly.append(
            {
                "information_boundary": boundary,
                "expected_members": len(cohort),
                "branch_counts": dict(sorted(branch_counts.items())),
                "minimum_active_branch_expected_names": branch_min,
                "minimum_theoretical_eligible_names_at_80pct": theoretical,
                "represented_active_branches": active,
                "represented_active_branch_count": len(active),
                "represented_gics_sectors": sectors,
                "represented_gics_sector_count": len(sectors),
                "known_branch_fraction": known_fraction,
            }
        )

    denominator = len(rows)
    disposition_fraction = disposition_rows / denominator if denominator else 0.0
    f0_pass = outcomes_accessed is False and forbidden_columns == [] and not any(
        "prohibited_fields" in error for error in errors
    )
    f5_pass = (
        denominator > 0
        and disposition_fraction == 1.0
        and duplicate_count == 0
        and not row_errors
        and len(monthly_rows) == len(expected_boundaries)
    )
    f7_pass = rebuilds_match and len(rebuild_rows) == 2
    locked = design_v3._structural_gates()
    criteria = {
        "F0": {**locked["F0"], "observed": 0 if f0_pass else 1, "passed": f0_pass},
        "F1": {
            **locked["F1"],
            "observed": minimum_branch_count,
            "observed_month": minimum_branch_count_month,
            "passed": f1_pass,
        },
        "F2": {
            **locked["F2"],
            "observed": minimum_theoretical_eligible,
            "passed": f2_pass,
        },
        "F3": {
            **locked["F3"],
            "observed": min(
                (row["represented_active_branch_count"] for row in monthly),
                default=0,
            ),
            "passed": f3_pass,
        },
        "F4": {
            **locked["F4"],
            "observed": min(
                (row["represented_gics_sector_count"] for row in monthly),
                default=0,
            ),
            "passed": f4_pass,
        },
        "F5": {
            **locked["F5"],
            "observed": disposition_fraction,
            "passed": f5_pass,
        },
        "F6": {
            **locked["F6"],
            "observed": minimum_known_branch_fraction,
            "passed": f6_pass,
        },
        "F7": {
            **locked["F7"],
            "observed": f7_pass,
            "passed": f7_pass,
        },
    }
    failed = [gate_id for gate_id, result in criteria.items() if not result["passed"]]
    lineage_failed = any(gate in failed for gate in ("F0", "F5", "F6", "F7")) or bool(errors)
    if lineage_failed:
        decision = "FAIL_LINEAGE_OR_REPRODUCIBILITY"
        status = "BLOCKED_LINEAGE_OR_REPRODUCIBILITY"
    elif failed:
        decision = "FAIL_UNIVERSE_STILL_TOO_SMALL"
        status = "BLOCKED_STRUCTURAL_BRANCH_FEASIBILITY"
    else:
        decision = "PASS_STRUCTURALLY_FEASIBLE"
        status = "STRUCTURAL_FEASIBILITY_PASSED"
    return {
        "decision": decision,
        "status": status,
        "candidate_input": {
            "path": manifest_path.as_posix(),
            "exists": True,
            "sha256": _sha256_bytes(manifest_path.read_bytes()),
            "qualification": "EVALUATED_AGAINST_MODEL_V3_CONTRACT",
            "rebuild_ledgers": ledger_bindings,
        },
        "local_inventory": _local_inventory(root),
        "criteria": criteria,
        "failed_criteria": failed,
        "monthly": monthly,
        "reconciliation": {
            "expected_months": len(expected_boundaries),
            "evaluated_months": len(monthly_rows),
            "expected_security_months": denominator,
            "duplicate_security_months": duplicate_count,
            "structural_disposition_fraction": disposition_fraction,
            "input_errors": sorted(set(errors)),
        },
    }


def build_audit(
    *,
    repository_root: Path,
    generated_at: datetime,
    candidate_manifest: Path = DEFAULT_CANDIDATE_MANIFEST,
    expected_boundaries: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    root = repository_root.resolve()
    design_lock = design_v3.verify_design_lock(repository_root=root)
    manifest_path = (
        candidate_manifest
        if candidate_manifest.is_absolute()
        else root / candidate_manifest
    )
    boundaries = list(expected_boundaries or _expected_boundaries(root))
    result = (
        _evaluated_report(
            root=root,
            design_lock=design_lock,
            manifest_path=manifest_path,
            expected_boundaries=boundaries,
        )
        if manifest_path.is_file()
        else _missing_input_report(
            root=root,
            design_lock=design_lock,
            manifest_path=candidate_manifest,
        )
    )
    result.update(
        {
            "schema_version": "model-v3-expanded-universe-feasibility-audit-v1",
            "generated_at": generated_at.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "claims_eligible": False,
            "outcomes_accessed": False,
            "design_lock": _binding(root, design_v3.DEFAULT_OUTPUT),
            "authorization": {
                "data_acquisition_authorized": result["decision"]
                == "PASS_STRUCTURALLY_FEASIBLE",
                "accounting_rebuild_authorized": False,
                "score_rebuild_authorized": False,
                "shadow_authorized": False,
                "performance_claims_authorized": False,
                "july_2026_backfill_allowed": False,
            },
            "required_next_evidence": [
                "historical_full_exchange_listing_census",
                "point_in_time_identity_domicile_type_and_exchange",
                "historical_delisting_preservation",
                "point_in_time_branch_and_sector_classification",
                "two_hash_bound_identical_rebuild_ledgers",
            ],
        }
    )
    return dict(sorted(result.items()))


def render_markdown(report: Mapping[str, Any]) -> str:
    inventory = report["local_inventory"]
    bundle = inventory["current_point_in_time_bundle"]
    tickers = inventory["current_sec_ticker_file"]
    criteria = report["criteria"]
    lines = [
        "# Model V3 Expanded-Universe Structural Feasibility Audit",
        "",
        f"- Decision: `{report['decision']}`",
        f"- Status: `{report['status']}`",
        "- Claims eligible: `false`",
        "- Outcomes accessed: `false`",
        "- Data acquisition authorized: "
        f"`{str(report['authorization']['data_acquisition_authorized']).lower()}`",
        "- Score rebuild authorized: `false`",
        "- Shadow authorized: `false`",
        "",
        "## Gate results",
        "",
        "| Gate | Result | Observed | Threshold |",
        "| --- | --- | ---: | ---: |",
    ]
    for gate_id in sorted(criteria):
        gate = criteria[gate_id]
        observed = gate.get("observed")
        threshold = gate.get("threshold")
        lines.append(
            f"| {gate_id} | {'PASS' if gate['passed'] else 'FAIL'} | "
            f"{observed if observed is not None else 'not evaluable'} | {threshold} |"
        )
    lines.extend(
        [
            "",
            "## Local evidence inventory",
            "",
            f"- Existing point-in-time bundle: {bundle.get('security_count')} securities "
            f"and {bundle.get('membership_episode_count')} membership episodes; it is "
            "S&P 500-only and does not qualify as the Model V3 universe.",
            f"- Existing SEC ticker file: {tickers.get('record_count')} current records; "
            "it has no historical monthly membership or delisting proof.",
            "- No qualifying expanded-universe manifest or two-rebuild structural ledger "
            "is present.",
            "",
            "## Decision",
            "",
        ]
    )
    if report["decision"] == "PASS_STRUCTURALLY_FEASIBLE":
        lines.append(
            "The structural universe is feasible. A separately locked data-readiness "
            "and acquisition phase may now be prepared; scoring remains unauthorized."
        )
    else:
        lines.append(
            "The data-repair phase is blocked. Acquire and reconcile the required "
            "historical listing, identity, security-type, domicile, exchange, delisting, "
            "branch, and sector evidence first. Do not acquire accounting or price data "
            "for the expanded universe and do not rebuild scores."
        )
    lines.extend(
        [
            "",
            "July 2026 remains non-backfillable.",
            "",
        ]
    )
    return "\n".join(lines)


def verify_audit(
    *,
    repository_root: Path,
    report_path: Path = DEFAULT_JSON_OUTPUT,
    markdown_path: Path = DEFAULT_MARKDOWN_OUTPUT,
) -> dict[str, Any]:
    root = repository_root.resolve()
    json_path = report_path if report_path.is_absolute() else root / report_path
    md_path = markdown_path if markdown_path.is_absolute() else root / markdown_path
    report = _load_json(json_path)
    generated_at_value = report.get("generated_at")
    if not isinstance(generated_at_value, str):
        raise ValueError("feasibility report lacks a timestamp")
    generated_at = datetime.fromisoformat(generated_at_value.replace("Z", "+00:00"))
    candidate_value = report.get("candidate_input", {}).get("path")
    if not isinstance(candidate_value, str):
        raise ValueError("feasibility report lacks its candidate path")
    expected = build_audit(
        repository_root=root,
        generated_at=generated_at,
        candidate_manifest=Path(candidate_value),
    )
    if dict(report) != expected:
        raise ValueError("Model V3 feasibility report no longer reproduces")
    if md_path.read_text(encoding="utf-8") != render_markdown(expected):
        raise ValueError("Model V3 feasibility markdown no longer reproduces")
    return dict(report)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("generated-at must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit Model V3 expanded-universe structural feasibility."
    )
    parser.add_argument(
        "--candidate-manifest", type=Path, default=DEFAULT_CANDIDATE_MANIFEST
    )
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument(
        "--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT
    )
    parser.add_argument("--generated-at", type=_timestamp)
    parser.add_argument("--verify", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.verify:
            report = verify_audit(
                repository_root=REPOSITORY_ROOT,
                report_path=args.json_output,
                markdown_path=args.markdown_output,
            )
            print(
                "model_v3_expanded_universe_feasibility=VERIFIED "
                f"decision={report['decision']}"
            )
            return 0
        report = build_audit(
            repository_root=REPOSITORY_ROOT,
            generated_at=args.generated_at or datetime.now(timezone.utc),
            candidate_manifest=args.candidate_manifest,
        )
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
        json_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_bytes(_json_bytes(report))
        markdown_output.write_text(render_markdown(report), encoding="utf-8")
        print(
            "model_v3_expanded_universe_feasibility=COMPLETE "
            f"decision={report['decision']}"
        )
        return 0 if report["decision"] == "PASS_STRUCTURALLY_FEASIBLE" else 2
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Model V3 structural feasibility audit failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
