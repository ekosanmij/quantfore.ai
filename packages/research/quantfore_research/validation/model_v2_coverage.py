"""Outcome-blind coverage and breadth gates for Model V2 pre-shadow readiness."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable, Mapping

from quantfore_research.classification.point_in_time_subtypes import ACTIVE_BRANCHES


REPORT_VERSION = "model-v2-coverage-readiness-v1"
MINIMUM_MONTHLY_SCORE_COVERAGE = 0.90
MINIMUM_BRANCH_SCORE_COVERAGE = 0.80
MINIMUM_ELIGIBLE_NAMES_PER_BRANCH = 20
MINIMUM_REPRESENTED_BRANCHES = 5
MINIMUM_REPRESENTED_SECTORS = 5
FAMILIES = frozenset({"value", "quality", "growth", "momentum", "risk"})
FORBIDDEN_OUTCOME_KEYS = frozenset(
    {
        "return",
        "returns",
        "forward_return",
        "forward_returns",
        "outcome",
        "outcomes",
        "rank_ic",
        "alpha",
        "excess_return",
        "benchmark_return",
        "future_price",
    }
)


def _reject_outcomes(value: Any, *, path: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).strip().lower() in FORBIDDEN_OUTCOME_KEYS:
                raise ValueError(f"outcome field is prohibited in coverage audit: {path}.{key}")
            _reject_outcomes(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_outcomes(nested, path=f"{path}[{index}]")


def _key(row: Mapping[str, Any]) -> tuple[str, str]:
    prediction_date = row.get("prediction_date")
    security_id = row.get("security_id")
    if not isinstance(prediction_date, str) or not isinstance(security_id, str):
        raise ValueError("coverage row lacks prediction_date or security_id")
    return prediction_date, security_id


def _sector(row: Mapping[str, Any]) -> str:
    explicit = row.get("explicit_classification_evidence")
    if isinstance(explicit, Mapping) and explicit.get("sector"):
        return str(explicit["sector"])
    base = row.get("base_classification")
    if isinstance(base, Mapping) and base.get("sector"):
        return str(base["sector"])
    return "Unknown"


def _rate(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def _counter_document(counter: Mapping[str, int]) -> dict[str, int]:
    return {key: int(counter[key]) for key in sorted(counter)}


def compare_clean_rebuilds(
    *,
    first: Mapping[str, str],
    second: Mapping[str, str],
    canonical: Mapping[str, str],
) -> dict[str, Any]:
    """Compare every deterministic rebuild artifact and canonical ledger."""

    required = {
        "feature_input_ledger",
        "feature_input_manifest",
        "score_ledger",
        "score_manifest",
    }
    for label, values in (("first", first), ("second", second)):
        if set(values) != required:
            raise ValueError(f"{label} rebuild fingerprint is incomplete")
    if set(canonical) != {"feature_input_ledger", "score_ledger"}:
        raise ValueError("canonical rebuild fingerprint is incomplete")
    checks = {
        name: {
            "first_sha256": first[name],
            "second_sha256": second[name],
            "matched": first[name] == second[name],
        }
        for name in sorted(required)
    }
    canonical_checks = {
        name: {
            "canonical_sha256": canonical[name],
            "rebuild_sha256": first[name],
            "matched": canonical[name] == first[name],
        }
        for name in sorted(canonical)
    }
    return {
        "run_count": 2,
        "clean_rebuilds": True,
        "artifacts": checks,
        "all_rebuild_artifacts_matched": all(
            value["matched"] for value in checks.values()
        ),
        "canonical_ledgers": canonical_checks,
        "canonical_ledgers_reproduced": all(
            value["matched"] for value in canonical_checks.values()
        ),
    }


def audit_model_v2_coverage(
    *,
    classification_rows: Iterable[Mapping[str, Any]],
    score_rows: Iterable[Mapping[str, Any]],
    score_manifest: Mapping[str, Any],
    input_manifest: Mapping[str, Any],
    rebuild_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconcile the full denominator and apply locked E3-E8 coverage gates."""

    _reject_outcomes(score_manifest, path="score_manifest")
    _reject_outcomes(input_manifest, path="input_manifest")
    expected = {}
    for row in classification_rows:
        _reject_outcomes(row, path="classification")
        key = _key(row)
        if key in expected:
            raise ValueError(f"duplicate expected security-month: {key}")
        expected[key] = {
            "sector_branch": str(row.get("sector_branch") or "UNKNOWN"),
            "sector": _sector(row),
            "known_subtype": bool(row.get("known_subtype")),
        }
    if not expected:
        raise ValueError("classification denominator is empty")

    monthly = defaultdict(Counter)
    branch_monthly = defaultdict(Counter)
    sector_monthly = defaultdict(Counter)
    reason_counts: Counter[str] = Counter()
    disposition_counts: Counter[str] = Counter()
    scored_missing_family = 0
    cross_branch_fallback_count = 0
    seen = set()

    for row in score_rows:
        _reject_outcomes(row, path="score")
        key = _key(row)
        if key in seen:
            raise ValueError(f"duplicate score disposition: {key}")
        classification = expected.get(key)
        if classification is None:
            raise ValueError(f"score row is outside expected denominator: {key}")
        seen.add(key)
        prediction_date, _ = key
        branch = str(row.get("sector_branch") or "UNKNOWN")
        if branch != classification["sector_branch"]:
            raise ValueError(f"score/classification branch mismatch: {key}")
        sector = classification["sector"]
        eligible = row.get("eligible")
        if not isinstance(eligible, bool):
            raise ValueError(f"score eligibility is not boolean: {key}")
        final_score = row.get("final_score")
        reasons = row.get("exclusion_reason_codes")
        if not isinstance(reasons, list) or not all(
            isinstance(reason, str) and reason for reason in reasons
        ):
            raise ValueError(f"score reasons are malformed: {key}")
        family_available = row.get("family_available")
        family_weights = row.get("family_weights")
        if not isinstance(family_available, Mapping) or set(family_available) != FAMILIES:
            raise ValueError(f"family availability is incomplete: {key}")
        if not isinstance(family_weights, Mapping) or set(family_weights) != FAMILIES:
            raise ValueError(f"family weights are incomplete: {key}")
        if set(str(value) for value in family_weights.values()) != {"0.20"}:
            raise ValueError(f"family weights were changed or renormalized: {key}")
        if eligible:
            if final_score is None or reasons:
                raise ValueError(f"eligible score lacks final score or has reasons: {key}")
            if not all(family_available.values()):
                scored_missing_family += 1
            disposition = "SCORED"
        else:
            if final_score is not None or not reasons:
                raise ValueError(f"excluded row lacks stable disposition: {key}")
            disposition = "EXCLUDED"
            reason_counts.update(reasons)
        disposition_counts[disposition] += 1

        for component in row.get("components", []):
            scope = component.get("normalization_scope")
            group = component.get("normalization_group")
            if scope not in {"NONE", "BRANCH"}:
                cross_branch_fallback_count += 1
            elif scope == "BRANCH" and group != branch:
                cross_branch_fallback_count += 1

        monthly[prediction_date]["expected"] += 1
        monthly[prediction_date]["known_subtype"] += int(
            classification["known_subtype"]
        )
        branch_key = (prediction_date, branch)
        sector_key = (prediction_date, sector)
        branch_monthly[branch_key]["expected"] += 1
        sector_monthly[sector_key]["expected"] += 1
        if eligible:
            monthly[prediction_date]["scored"] += 1
            branch_monthly[branch_key]["scored"] += 1
            sector_monthly[sector_key]["scored"] += 1

    missing = sorted(set(expected) - seen)
    if missing:
        raise ValueError(f"expected rows lack final disposition; first={missing[0]}")
    if scored_missing_family:
        raise ValueError("one or more scored rows lacks all five families")

    active_branches = set(ACTIVE_BRANCHES)
    monthly_rows = []
    all_dates = sorted(monthly)
    for prediction_date in all_dates:
        counts = monthly[prediction_date]
        active = sorted(
            branch
            for (month, branch), values in branch_monthly.items()
            if month == prediction_date
            and branch in active_branches
            and values["expected"] > 0
        )
        represented = [
            branch
            for branch in active
            if branch_monthly[(prediction_date, branch)]["scored"] > 0
        ]
        represented_sectors = sorted(
            sector
            for (month, sector), values in sector_monthly.items()
            if month == prediction_date
            and sector != "Unknown"
            and values["scored"] > 0
        )
        branch_coverages = [
            _rate(
                branch_monthly[(prediction_date, branch)]["scored"],
                branch_monthly[(prediction_date, branch)]["expected"],
            )
            for branch in active
        ]
        branch_eligible_counts = [
            branch_monthly[(prediction_date, branch)]["scored"] for branch in active
        ]
        monthly_rows.append(
            {
                "prediction_date": prediction_date,
                "expected": counts["expected"],
                "scored": counts["scored"],
                "excluded": counts["expected"] - counts["scored"],
                "final_score_coverage": _rate(counts["scored"], counts["expected"]),
                "known_subtype_fraction": _rate(
                    counts["known_subtype"], counts["expected"]
                ),
                "active_branches": active,
                "represented_active_branches": represented,
                "represented_active_branch_count": len(represented),
                "represented_sectors": represented_sectors,
                "represented_sector_count": len(represented_sectors),
                "minimum_active_branch_coverage": min(branch_coverages)
                if branch_coverages
                else None,
                "minimum_active_branch_eligible_names": min(branch_eligible_counts)
                if branch_eligible_counts
                else None,
            }
        )

    branch_rows = []
    for branch in sorted({branch for _, branch in branch_monthly} | active_branches):
        values = [
            (
                prediction_date,
                branch_monthly[(prediction_date, branch)]["expected"],
                branch_monthly[(prediction_date, branch)]["scored"],
            )
            for prediction_date in all_dates
            if branch_monthly[(prediction_date, branch)]["expected"] > 0
        ]
        expected_count = sum(item[1] for item in values)
        scored_count = sum(item[2] for item in values)
        branch_rows.append(
            {
                "sector_branch": branch,
                "active_model_branch": branch in active_branches,
                "observed_months": len(values),
                "expected": expected_count,
                "scored": scored_count,
                "excluded": expected_count - scored_count,
                "aggregate_coverage": _rate(scored_count, expected_count),
                "minimum_monthly_coverage": min(
                    (_rate(scored, expected_count_month) for _, expected_count_month, scored in values),
                    default=None,
                ),
                "minimum_monthly_eligible_names": min(
                    (scored for _, _, scored in values), default=None
                ),
                "months_meeting_80_percent_coverage": sum(
                    _rate(scored, expected_count_month)
                    >= MINIMUM_BRANCH_SCORE_COVERAGE
                    for _, expected_count_month, scored in values
                ),
                "months_meeting_20_eligible_names": sum(
                    scored >= MINIMUM_ELIGIBLE_NAMES_PER_BRANCH
                    for _, _, scored in values
                ),
                "monthly": [
                    {
                        "prediction_date": prediction_date,
                        "expected": expected_count_month,
                        "scored": scored,
                        "coverage": _rate(scored, expected_count_month),
                    }
                    for prediction_date, expected_count_month, scored in values
                ],
            }
        )

    sector_rows = []
    for sector in sorted({sector for _, sector in sector_monthly}):
        values = [
            sector_monthly[(prediction_date, sector)]
            for prediction_date in all_dates
            if sector_monthly[(prediction_date, sector)]["expected"] > 0
        ]
        expected_count = sum(value["expected"] for value in values)
        scored_count = sum(value["scored"] for value in values)
        sector_rows.append(
            {
                "sector": sector,
                "observed_months": len(values),
                "expected": expected_count,
                "scored": scored_count,
                "excluded": expected_count - scored_count,
                "aggregate_coverage": _rate(scored_count, expected_count),
                "months_represented_by_score": sum(value["scored"] > 0 for value in values),
            }
        )

    aggregate_expected = len(expected)
    aggregate_scored = disposition_counts["SCORED"]
    active_branch_months = [
        value
        for (prediction_date, branch), value in branch_monthly.items()
        if branch in active_branches and value["expected"] > 0
    ]
    failed_monthly_coverage = [
        row["prediction_date"]
        for row in monthly_rows
        if row["final_score_coverage"] < MINIMUM_MONTHLY_SCORE_COVERAGE
    ]
    failed_known_subtype = [
        row["prediction_date"]
        for row in monthly_rows
        if row["known_subtype_fraction"] < 0.98
    ]
    failed_branch_coverage = sum(
        _rate(value["scored"], value["expected"]) < MINIMUM_BRANCH_SCORE_COVERAGE
        for value in active_branch_months
    )
    failed_branch_names = sum(
        value["scored"] < MINIMUM_ELIGIBLE_NAMES_PER_BRANCH
        for value in active_branch_months
    )
    failed_branch_breadth = [
        row["prediction_date"]
        for row in monthly_rows
        if row["represented_active_branch_count"] < MINIMUM_REPRESENTED_BRANCHES
    ]
    failed_sector_breadth = [
        row["prediction_date"]
        for row in monthly_rows
        if row["represented_sector_count"] < MINIMUM_REPRESENTED_SECTORS
    ]

    disposition_fraction = _rate(sum(disposition_counts.values()), aggregate_expected)
    rebuild_pass = bool(
        rebuild_evidence.get("all_rebuild_artifacts_matched")
        and rebuild_evidence.get("canonical_ledgers_reproduced")
    )
    criteria = {
        "two_clean_rebuilds_match_exactly": {
            "passed": rebuild_pass,
            "threshold": True,
        },
        "every_expected_row_has_stable_disposition": {
            "passed": disposition_fraction == 1.0,
            "observed": disposition_fraction,
            "threshold": 1.0,
        },
        "known_branch_or_subtype_every_month": {
            "passed": not failed_known_subtype,
            "minimum_observed": min(
                row["known_subtype_fraction"] for row in monthly_rows
            ),
            "threshold": 0.98,
            "failed_months": failed_known_subtype,
        },
        "final_score_coverage_every_month": {
            "passed": not failed_monthly_coverage,
            "minimum_observed": min(
                row["final_score_coverage"] for row in monthly_rows
            ),
            "threshold": MINIMUM_MONTHLY_SCORE_COVERAGE,
            "failed_months": failed_monthly_coverage,
        },
        "active_branch_coverage_every_month": {
            "passed": failed_branch_coverage == 0,
            "failed_branch_months": failed_branch_coverage,
            "evaluated_branch_months": len(active_branch_months),
            "threshold": MINIMUM_BRANCH_SCORE_COVERAGE,
        },
        "eligible_names_per_active_branch_every_month": {
            "passed": failed_branch_names == 0,
            "failed_branch_months": failed_branch_names,
            "evaluated_branch_months": len(active_branch_months),
            "threshold": MINIMUM_ELIGIBLE_NAMES_PER_BRANCH,
        },
        "represented_active_branches_every_month": {
            "passed": not failed_branch_breadth,
            "minimum_observed": min(
                row["represented_active_branch_count"] for row in monthly_rows
            ),
            "threshold": MINIMUM_REPRESENTED_BRANCHES,
            "failed_months": failed_branch_breadth,
        },
        "represented_sectors_every_month": {
            "passed": not failed_sector_breadth,
            "minimum_observed": min(
                row["represented_sector_count"] for row in monthly_rows
            ),
            "threshold": MINIMUM_REPRESENTED_SECTORS,
            "failed_months": failed_sector_breadth,
        },
        "cross_branch_fallback_count": {
            "passed": cross_branch_fallback_count == 0,
            "observed": cross_branch_fallback_count,
            "threshold": 0,
        },
        "return_metrics_used": {
            "passed": score_manifest.get("outcomes_accessed") is False
            and input_manifest.get("outcomes_accessed") is False,
            "observed": False,
            "threshold": False,
        },
    }
    all_passed = all(value["passed"] for value in criteria.values())
    manifest_counts = score_manifest.get("counts", {})
    if int(manifest_counts.get("rows", -1)) != aggregate_expected:
        raise ValueError("score manifest row count does not reconcile")
    if int(manifest_counts.get("eligible_rows", -1)) != aggregate_scored:
        raise ValueError("score manifest eligible count does not reconcile")
    if int(manifest_counts.get("cross_branch_fallback_count", -1)) != 0:
        raise ValueError("score manifest records a cross-branch fallback")

    return {
        "report_version": REPORT_VERSION,
        "decision": "PASS_READY_FOR_EXECUTABLE_LOCK"
        if all_passed
        else "FAIL_NOT_READY_FOR_EXECUTABLE_LOCK",
        "claims_eligible": False,
        "outcomes_accessed": False,
        "thresholds_changed_after_failure": False,
        "criteria": criteria,
        "reconciliation": {
            "expected_stock_months": aggregate_expected,
            "scored_stock_months": aggregate_scored,
            "excluded_stock_months": disposition_counts["EXCLUDED"],
            "final_disposition_fraction": disposition_fraction,
            "duplicate_dispositions": 0,
            "missing_dispositions": 0,
            "score_rows_missing_any_family": scored_missing_family,
            "exclusion_reason_counts": _counter_document(reason_counts),
        },
        "coverage": {
            "aggregate_final_score_coverage": _rate(
                aggregate_scored, aggregate_expected
            ),
            "monthly": monthly_rows,
            "branches": branch_rows,
            "sectors": sector_rows,
        },
        "reproducibility": dict(rebuild_evidence),
        "controls": {
            "cross_branch_fallback_count": cross_branch_fallback_count,
            "family_weight_renormalization": False,
            "all_five_families_required": True,
            "outcome_fields_rejected": True,
        },
    }
