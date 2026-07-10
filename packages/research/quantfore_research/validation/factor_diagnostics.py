"""Deterministic Sprint 9 factor-family diagnostics for the Sprint 8 baseline."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from quantfore_research.backtest.baseline import summarize_backtest
from quantfore_research.evaluation.comparative import (
    ComparativeObservation,
    analyze_dataset,
)
from quantfore_research.evaluation.outcomes import calculate_forward_outcome
from quantfore_research.scoring.multifactor import FAMILY_WEIGHTS


FAMILIES = tuple(FAMILY_WEIGHTS)
FUNDAMENTAL_FAMILIES = ("value", "quality", "growth")
PRICE_RISK_FAMILIES = ("momentum", "risk")
PRIMARY_HORIZON = "126d"


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _date(value: Any) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def component_reason(
    *,
    directed_value: Any,
    missing_reason: Optional[str],
    applicability_status: str,
) -> str:
    """Return the stable diagnostic state for one normalized component."""

    if directed_value is not None:
        return "VALID"
    return str(missing_reason or applicability_status)


def summarize_component_aggregates(
    rows: Sequence[Mapping[str, Any]],
    *,
    expected_security_months: int,
) -> list[dict[str, Any]]:
    """Convert grouped component/reason counts into one row per component."""

    if expected_security_months <= 0:
        raise ValueError("expected security-month count must be positive")
    grouped: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in rows:
        family = str(row["family"])
        feature_name = str(row["feature_name"])
        if family not in FAMILIES:
            raise ValueError(f"unknown factor family: {family}")
        count = int(row["count"])
        if count < 0:
            raise ValueError("component reason count cannot be negative")
        grouped[(family, feature_name)][str(row["reason"])] += count

    output = []
    for (family, feature_name), reasons in sorted(grouped.items()):
        total = sum(reasons.values())
        if total != expected_security_months:
            raise ValueError(
                f"component {feature_name} has {total} rows; "
                f"expected {expected_security_months}"
            )
        valid = reasons.get("VALID", 0)
        not_applicable = reasons.get("NOT_APPLICABLE", 0)
        source_missing = reasons.get("SOURCE_MISSING", 0)
        applicable = total - not_applicable
        target_missing = not_applicable + source_missing
        dominant_reason, dominant_count = max(
            sorted(reasons.items()), key=lambda item: item[1]
        )
        output.append(
            {
                "family": family,
                "feature_name": feature_name,
                "security_months": total,
                "valid": valid,
                "valid_rate": valid / total,
                "valid_rate_among_applicable": (
                    valid / applicable if applicable else None
                ),
                "reason_counts": dict(sorted(reasons.items())),
                "dominant_state": dominant_reason,
                "dominant_state_rate": dominant_count / total,
                "not_applicable_or_source_missing": target_missing,
                "not_applicable_or_source_missing_rate": target_missing / total,
                "mostly_not_applicable_or_source_missing": (
                    target_missing / total > 0.5
                ),
            }
        )
    return output


def summarize_families(
    *,
    components: Sequence[Mapping[str, Any]],
    availability_counts: Mapping[str, int],
    security_months: int,
) -> list[dict[str, Any]]:
    """Aggregate component diagnostics and score-time family availability."""

    if security_months <= 0:
        raise ValueError("security-month count must be positive")
    by_family: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in components:
        by_family[str(row["family"])].append(row)
    output = []
    for family in FAMILIES:
        family_components = by_family.get(family, [])
        component_rows = sum(int(row["security_months"]) for row in family_components)
        valid_rows = sum(int(row["valid"]) for row in family_components)
        available = int(availability_counts.get(family, 0))
        if available < 0 or available > security_months:
            raise ValueError(f"invalid availability count for {family}")
        output.append(
            {
                "family": family,
                "feature_count": len(family_components),
                "component_rows": component_rows,
                "valid_component_rows": valid_rows,
                "valid_component_rate": (
                    valid_rows / component_rows if component_rows else None
                ),
                "family_available_security_months": available,
                "family_availability_rate": available / security_months,
                "component_reason_counts": dict(
                    sorted(
                        sum(
                            (
                                Counter(row["reason_counts"])
                                for row in family_components
                            ),
                            Counter(),
                        ).items()
                    )
                ),
            }
        )
    return output


@dataclass(frozen=True)
class FamilyEvaluationRow:
    """One evaluated score row with its reconstructed price outcome."""

    security_id: str
    ticker: str
    prediction_date: date
    sector: str
    final_score: Decimal
    family_z: Mapping[str, Optional[Decimal]]
    excess_return: Decimal
    realised_return: Decimal
    benchmark_return: Decimal
    max_drawdown: Decimal


def _signal_observations(
    rows: Sequence[FamilyEvaluationRow],
    score: Callable[[FamilyEvaluationRow], Optional[Decimal]],
) -> tuple[ComparativeObservation, ...]:
    output = []
    for row in rows:
        value = score(row)
        if value is None:
            continue
        output.append(
            ComparativeObservation(
                security_id=row.security_id,
                ticker=row.ticker,
                prediction_date=row.prediction_date,
                sector=row.sector,
                score=value,
                action_label="RANKED",
                excess_return=row.excess_return,
                realised_return=row.realised_return,
                benchmark_return=row.benchmark_return,
                max_drawdown=row.max_drawdown,
            )
        )
    return tuple(output)


def evaluate_signal(
    rows: Sequence[FamilyEvaluationRow],
    *,
    score: Callable[[FamilyEvaluationRow], Optional[Decimal]],
) -> dict[str, Any]:
    """Evaluate one family or grouped score on the exact eligible-row ledger."""

    observations = _signal_observations(rows, score)
    if not observations:
        return {
            "evaluable": False,
            "reason": "NO_AVAILABLE_SCORES_IN_EVALUATED_COHORT",
            "observations": 0,
            "prediction_months": 0,
            "calculable_rank_ic_months": 0,
            "mean_rank_ic": None,
            "median_rank_ic": None,
            "positive_rank_ic_month_rate": None,
            "top_bucket_gross_excess_return": None,
            "top_bucket_net_excess_return_25_bps": None,
            "top_minus_bottom_spread": None,
            "non_overlapping_rank_ic_periods": 0,
            "non_overlapping_rank_ic_t_statistic": None,
        }
    analysis = analyze_dataset(observations)
    summary = summarize_backtest(row.baseline_observation() for row in observations)
    rank_ics = [row.rank_ic for row in summary.periods if row.rank_ic is not None]
    return {
        "evaluable": True,
        "reason": None,
        "observations": len(observations),
        "unique_securities": len({row.security_id for row in observations}),
        "prediction_months": len({row.prediction_date for row in observations}),
        "calculable_rank_ic_months": len(rank_ics),
        "mean_rank_ic": analysis["mean_rank_ic"],
        "median_rank_ic": analysis["median_rank_ic"],
        "positive_rank_ic_month_rate": (
            sum(value > 0 for value in rank_ics) / len(rank_ics)
            if rank_ics
            else None
        ),
        "top_bucket_gross_excess_return": analysis["quintile_returns"]["5"],
        "top_bucket_net_excess_return_25_bps": analysis["transaction_costs"]
        ["25_bps"]["average_net_excess_return"],
        "top_minus_bottom_spread": analysis["top_minus_bottom_spread"],
        "non_overlapping_rank_ic_periods": analysis[
            "non_overlapping_rank_ic_periods"
        ],
        "non_overlapping_rank_ic_t_statistic": analysis[
            "non_overlapping_rank_ic_t_statistic"
        ],
    }


def summarize_ablations(
    comparison: Mapping[str, Any],
    *,
    evaluated_family_counts: Mapping[str, int],
) -> list[dict[str, Any]]:
    """Extract frozen no-retuning ablations and their full-model IC deltas."""

    root = comparison.get("comparison", comparison)
    full = root["models"]["sprint8_multifactor"]
    full_ic = full["mean_rank_ic"]
    if not isinstance(full_ic, (int, float)):
        raise ValueError("full-model mean Rank IC is unavailable")
    ablations = root["family_ablations"]
    output = []
    for family in FAMILIES:
        row = ablations[f"without_{family}"]
        design = row["design"]
        evaluation = row["evaluation"]
        if design.get("retuned") is not False:
            raise ValueError("Sprint 8 ablations must not be retuned")
        ablated_ic = evaluation["mean_rank_ic"]
        if not isinstance(ablated_ic, (int, float)):
            raise ValueError(f"ablation without {family} has no mean Rank IC")
        loss = float(full_ic) - float(ablated_ic)
        output.append(
            {
                "family": family,
                "family_present_in_evaluated_rows": int(
                    evaluated_family_counts.get(family, 0)
                ),
                "eligible_observations": int(
                    design["eligible_observations"]
                ),
                "excluded_observations": int(
                    design["excluded_observations"]
                ),
                "retuned": False,
                "ablated_mean_rank_ic": float(ablated_ic),
                "full_mean_rank_ic": float(full_ic),
                "rank_ic_loss_when_removed": loss,
                "top_bucket_net_excess_return_25_bps": evaluation[
                    "transaction_costs"
                ]["25_bps"]["average_net_excess_return"],
                "non_overlapping_rank_ic_periods": evaluation[
                    "non_overlapping_rank_ic_periods"
                ],
                "non_overlapping_rank_ic_t_statistic": evaluation[
                    "non_overlapping_rank_ic_t_statistic"
                ],
                "interpretation": (
                    "NO_EFFECT_FAMILY_ABSENT"
                    if int(evaluated_family_counts.get(family, 0)) == 0
                    and abs(loss) < 1e-12
                    else "NARROW_POSITIVE_MARGINAL_CONTRIBUTION"
                    if loss > 0
                    else "NARROW_NEGATIVE_MARGINAL_CONTRIBUTION"
                    if loss < 0
                    else "NO_MEASURED_EFFECT"
                ),
            }
        )
    return output


def build_dominance_assessment(
    *,
    standalone: Mapping[str, Mapping[str, Any]],
    ablations: Sequence[Mapping[str, Any]],
    contribution_attribution: Sequence[Mapping[str, Any]],
    grouped: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """Compare three notions of dominance without treating them as significance."""

    evaluable = {
        family: row
        for family, row in standalone.items()
        if row.get("evaluable") and row.get("mean_rank_ic") is not None
    }
    strongest_standalone = max(
        evaluable,
        key=lambda family: (float(evaluable[family]["mean_rank_ic"]), family),
        default=None,
    )
    strongest_marginal = max(
        ablations,
        key=lambda row: (float(row["rank_ic_loss_when_removed"]), row["family"]),
    )["family"]
    strongest_absolute = max(
        contribution_attribution,
        key=lambda row: (float(row["absolute_contribution_share"]), row["family"]),
    )["family"]
    leaders = (strongest_standalone, strongest_marginal, strongest_absolute)
    fundamental_ic = grouped["fundamentals_value_quality_growth"]["mean_rank_ic"]
    price_risk_ic = grouped["price_risk_momentum_risk"]["mean_rank_ic"]
    return {
        "strongest_standalone_rank_ic_family": strongest_standalone,
        "largest_rank_ic_loss_when_removed_family": strongest_marginal,
        "largest_absolute_score_contribution_family": strongest_absolute,
        "consistent_single_family_dominance": len(set(leaders)) == 1,
        "fundamentals_group_mean_rank_ic": fundamental_ic,
        "price_risk_group_mean_rank_ic": price_risk_ic,
        "price_risk_group_rank_ic_exceeds_fundamentals": (
            price_risk_ic is not None
            and fundamental_ic is not None
            and float(price_risk_ic) > float(fundamental_ic)
        ),
        "decision": "NO_ROBUST_SINGLE_FAMILY_DOMINANCE_ESTABLISHED",
        "reason": (
            "The standalone, ablation, and absolute-contribution diagnostics "
            "select different leaders, and all performance evidence is confined "
            "to nine calculable tiny cross-sections."
        ),
    }


def _load_grouped_component_rows(
    session: Session,
    *,
    eligible_only: bool,
) -> list[dict[str, Any]]:
    eligibility = "WHERE ms.eligible = 1" if eligible_only else ""
    join = (
        "JOIN multifactor_scores ms "
        "ON ms.normalization_run_id = nf.normalization_run_id "
        "AND ms.security_id = nf.security_id"
    )
    rows = session.execute(
        text(
            f"""
            SELECT nf.family, nf.feature_name,
                   CASE
                     WHEN nf.directed_value IS NOT NULL THEN 'VALID'
                     ELSE COALESCE(nf.missing_reason, nf.applicability_status)
                   END AS reason,
                   COUNT(*) AS count
            FROM normalized_features nf
            {join}
            {eligibility}
            GROUP BY nf.family, nf.feature_name, reason
            ORDER BY nf.family, nf.feature_name, reason
            """
        )
    ).mappings()
    return [dict(row) for row in rows]


def _load_scores(session: Session) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT ms.security_id, s.ticker, ms.asof_date, ms.eligible,
                   ms.final_score, ms.family_z_json, ms.family_available_json,
                   ms.renormalized_weights_json
            FROM multifactor_scores ms
            JOIN securities s ON s.security_id = ms.security_id
            ORDER BY ms.asof_date, ms.security_id
            """
        )
    ).mappings()
    output = []
    for row in rows:
        value = dict(row)
        value["asof_date"] = _date(value["asof_date"])
        value["eligible"] = bool(value["eligible"])
        value["family_z_json"] = _json(value["family_z_json"])
        value["family_available_json"] = _json(value["family_available_json"])
        value["renormalized_weights_json"] = _json(
            value["renormalized_weights_json"]
        )
        output.append(value)
    return output


def _availability_counts(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        for family, available in row["family_available_json"].items():
            if available:
                counts[str(family)] += 1
    return {family: counts[family] for family in FAMILIES}


def _load_contribution_attribution(
    session: Session,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = session.execute(
        text(
            """
            SELECT nf.family, nf.feature_name,
                   COUNT(nf.contribution) AS valid_contributions,
                   AVG(nf.contribution) AS mean_contribution,
                   AVG(ABS(nf.contribution)) AS mean_absolute_contribution,
                   SUM(ABS(nf.contribution)) AS absolute_contribution
            FROM normalized_features nf
            JOIN multifactor_scores ms
              ON ms.normalization_run_id = nf.normalization_run_id
             AND ms.security_id = nf.security_id
            WHERE ms.eligible = 1
            GROUP BY nf.family, nf.feature_name
            ORDER BY nf.family, nf.feature_name
            """
        )
    ).mappings()
    components = [dict(row) for row in rows]
    total_absolute = sum(
        float(row["absolute_contribution"] or 0.0) for row in components
    )
    for row in components:
        absolute = float(row["absolute_contribution"] or 0.0)
        row["valid_contributions"] = int(row["valid_contributions"])
        row["mean_contribution"] = (
            float(row["mean_contribution"])
            if row["mean_contribution"] is not None
            else None
        )
        row["mean_absolute_contribution"] = (
            float(row["mean_absolute_contribution"])
            if row["mean_absolute_contribution"] is not None
            else None
        )
        row["absolute_contribution_share"] = (
            absolute / total_absolute if total_absolute else 0.0
        )
        row.pop("absolute_contribution")
    family_rows = []
    for family in FAMILIES:
        source = [row for row in components if row["family"] == family]
        family_rows.append(
            {
                "family": family,
                "valid_contributions": sum(
                    int(row["valid_contributions"]) for row in source
                ),
                "absolute_contribution_share": sum(
                    float(row["absolute_contribution_share"]) for row in source
                ),
            }
        )
    return family_rows, components


def _load_evaluation_rows(
    session: Session,
    *,
    scores: Sequence[Mapping[str, Any]],
    comparison: Mapping[str, Any],
    universe_id: str,
) -> tuple[list[FamilyEvaluationRow], dict[str, Any]]:
    eligible = [row for row in scores if row["eligible"]]
    root = comparison.get("comparison", comparison)
    attribution = {
        (str(row["security_id"]), _date(row["prediction_date"])): row
        for row in root["prediction_attribution"]
    }
    score_keys = {
        (str(row["security_id"]), row["asof_date"]) for row in eligible
    }
    if score_keys != set(attribution):
        raise ValueError(
            "eligible warehouse scores do not match comparison attribution rows"
        )
    benchmark = session.execute(
        text(
            """
            SELECT benchmark_security_id
            FROM universe_definitions
            WHERE universe_id = :universe_id
            """
        ),
        {"universe_id": universe_id},
    ).scalar_one()
    security_ids = sorted({str(row["security_id"]) for row in eligible} | {benchmark})
    statement = text(
        """
        SELECT security_id, date, adj_close
        FROM prices
        WHERE security_id IN :security_ids
          AND adj_close IS NOT NULL
          AND adj_close > 0
        ORDER BY security_id, date
        """
    ).bindparams(bindparam("security_ids", expanding=True))
    prices: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in session.execute(statement, {"security_ids": security_ids}).mappings():
        prices[str(row["security_id"])].append(
            {"date": _date(row["date"]), "adj_close": _decimal(row["adj_close"])}
        )
    output = []
    reconstructed_period_returns: dict[str, list[Decimal]] = defaultdict(list)
    outcome_hash_rows = []
    for row in eligible:
        security_id = str(row["security_id"])
        prediction_date = row["asof_date"]
        outcome = calculate_forward_outcome(
            prices[security_id],
            prices[str(benchmark)],
            prediction_date=prediction_date,
            horizon=PRIMARY_HORIZON,
        )
        source = attribution[(security_id, prediction_date)]
        family_z = {
            family: (
                None
                if row["family_z_json"].get(family) is None
                else _decimal(row["family_z_json"][family])
            )
            for family in FAMILIES
        }
        output.append(
            FamilyEvaluationRow(
                security_id=security_id,
                ticker=str(row["ticker"]),
                prediction_date=prediction_date,
                sector=str(source["sector"]),
                final_score=_decimal(row["final_score"]),
                family_z=family_z,
                excess_return=outcome.excess_return,
                realised_return=outcome.realised_return,
                benchmark_return=outcome.benchmark_return,
                max_drawdown=outcome.max_drawdown,
            )
        )
        reconstructed_period_returns[prediction_date.isoformat()].append(
            outcome.excess_return
        )
        outcome_hash_rows.append(
            {
                "security_id": security_id,
                "prediction_date": prediction_date.isoformat(),
                "entry_date": outcome.entry_date.isoformat(),
                "exit_date": outcome.exit_date.isoformat(),
                "realised_return": format(outcome.realised_return, "f"),
                "benchmark_return": format(outcome.benchmark_return, "f"),
                "excess_return": format(outcome.excess_return, "f"),
            }
        )

    published = {
        str(row["prediction_date"]): Decimal(str(row["equal_weight_excess_return"]))
        for row in root["models"]["equal_weight_benchmark"]["periods"]
    }
    reconstructed = {
        prediction_date: sum(values, Decimal("0")) / Decimal(len(values))
        for prediction_date, values in reconstructed_period_returns.items()
    }
    if set(published) != set(reconstructed):
        raise ValueError("reconstructed outcome months do not match comparison")
    differences = [
        abs(reconstructed[prediction_date] - published[prediction_date])
        for prediction_date in published
    ]
    maximum_difference = max(differences, default=Decimal("0"))
    tolerance = Decimal("0.0000001")
    if maximum_difference > tolerance:
        raise ValueError(
            "price-reconstructed outcomes do not reproduce the published "
            "comparison within tolerance"
        )
    return output, {
        "method": (
            "Recalculate the first 126 aligned trading-session intervals after "
            "each prediction from adjusted closes in the authoritative warehouse."
        ),
        "benchmark_security_id": str(benchmark),
        "observations": len(output),
        "outcome_ledger_sha256": _canonical_sha256(outcome_hash_rows),
        "published_periods": len(published),
        "maximum_absolute_published_period_return_difference": float(
            maximum_difference
        ),
        "reproduction_tolerance": float(tolerance),
        "published_period_returns_reproduced": True,
    }


def _weighted_available_score(
    row: FamilyEvaluationRow,
    families: Sequence[str],
) -> Optional[Decimal]:
    available = [family for family in families if row.family_z.get(family) is not None]
    if not available:
        return None
    denominator = sum((FAMILY_WEIGHTS[family] for family in available), Decimal("0"))
    return sum(
        (
            row.family_z[family] * FAMILY_WEIGHTS[family] / denominator
            for family in available
            if row.family_z[family] is not None
        ),
        Decimal("0"),
    )


def _family_assessments(
    *,
    family_missingness: Sequence[Mapping[str, Any]],
    eligible_family_missingness: Sequence[Mapping[str, Any]],
    standalone: Mapping[str, Mapping[str, Any]],
    ablations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    all_by_family = {row["family"]: row for row in family_missingness}
    eligible_by_family = {
        row["family"]: row for row in eligible_family_missingness
    }
    ablation_by_family = {row["family"]: row for row in ablations}
    output = []
    for family in FAMILIES:
        availability = float(all_by_family[family]["family_availability_rate"])
        evaluated_available = int(
            eligible_by_family[family]["family_available_security_months"]
        )
        performance = standalone[family]
        positive_narrow_behavior = (
            performance.get("mean_rank_ic") is not None
            and float(performance["mean_rank_ic"]) > 0
            and float(
                ablation_by_family[family]["rank_ic_loss_when_removed"]
            )
            > 0
        )
        if evaluated_available == 0:
            state = "BROKEN_OR_EFFECTIVELY_ABSENT"
        elif availability < 0.01:
            state = (
                "SEVERELY_SPARSE_WITH_NARROW_POSITIVE_BEHAVIOR"
                if positive_narrow_behavior
                else "SEVERELY_SPARSE_WITHOUT_POSITIVE_ABLATION_SUPPORT"
            )
        elif availability < 0.10:
            state = (
                "SPARSE_WITH_NARROW_POSITIVE_BEHAVIOR"
                if positive_narrow_behavior
                else "SPARSE_AND_NOT_SUPPORTED_BY_ABLATION"
            )
        else:
            state = (
                "BROADLY_AVAILABLE_WITH_NARROW_POSITIVE_BEHAVIOR"
                if positive_narrow_behavior
                else "BROADLY_AVAILABLE_WITHOUT_POSITIVE_ABLATION_SUPPORT"
            )
        output.append(
            {
                "family": family,
                "state": state,
                "universe_family_availability_rate": availability,
                "evaluated_rows_with_family": evaluated_available,
                "standalone_mean_rank_ic": performance["mean_rank_ic"],
                "rank_ic_loss_when_removed": ablation_by_family[family][
                    "rank_ic_loss_when_removed"
                ],
                "evidence_is_sufficient_to_call_useful": False,
            }
        )
    return output


def diagnose_sprint9_factor_families(
    session: Session,
    *,
    comparison: Mapping[str, Any],
    backtest: Mapping[str, Any],
    cohort_audit: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the complete Sprint 9.3 diagnostic from warehouse and reports."""

    scores = _load_scores(session)
    eligible_scores = [row for row in scores if row["eligible"]]
    if not scores or not eligible_scores:
        raise ValueError("factor diagnostic requires score and eligible-score rows")
    cohort_root = cohort_audit.get("audit", cohort_audit)
    if cohort_root["funnel_totals"]["universe_members"] != len(scores):
        raise ValueError("Sprint 9.2 stock-month count does not match warehouse")
    if cohort_root["funnel_totals"]["eligible_final_scores"] != len(eligible_scores):
        raise ValueError("Sprint 9.2 eligible-score count does not match warehouse")

    all_component_rows = summarize_component_aggregates(
        _load_grouped_component_rows(session, eligible_only=False),
        expected_security_months=len(scores),
    )
    eligible_component_rows = summarize_component_aggregates(
        _load_grouped_component_rows(session, eligible_only=True),
        expected_security_months=len(eligible_scores),
    )
    all_family_rows = summarize_families(
        components=all_component_rows,
        availability_counts=_availability_counts(scores),
        security_months=len(scores),
    )
    eligible_availability = _availability_counts(eligible_scores)
    eligible_family_rows = summarize_families(
        components=eligible_component_rows,
        availability_counts=eligible_availability,
        security_months=len(eligible_scores),
    )
    family_attribution, component_attribution = _load_contribution_attribution(
        session
    )
    evaluation_rows, outcome_reproduction = _load_evaluation_rows(
        session,
        scores=scores,
        comparison=comparison,
        universe_id=str(cohort_root["universe_id"]),
    )
    full_recomputed = evaluate_signal(
        evaluation_rows, score=lambda row: row.final_score
    )
    comparison_root = comparison.get("comparison", comparison)
    published_full = comparison_root["models"]["sprint8_multifactor"]
    if abs(
        float(full_recomputed["mean_rank_ic"])
        - float(published_full["mean_rank_ic"])
    ) > 1e-12:
        raise ValueError("recomputed full-model Rank IC does not match comparison")

    standalone = {
        family: evaluate_signal(
            evaluation_rows,
            score=lambda row, family=family: row.family_z.get(family),
        )
        for family in FAMILIES
    }
    grouped = {
        "fundamentals_value_quality_growth": evaluate_signal(
            evaluation_rows,
            score=lambda row: _weighted_available_score(
                row, FUNDAMENTAL_FAMILIES
            ),
        ),
        "price_risk_momentum_risk": evaluate_signal(
            evaluation_rows,
            score=lambda row: _weighted_available_score(row, PRICE_RISK_FAMILIES),
        ),
    }
    ablations = summarize_ablations(
        comparison,
        evaluated_family_counts=eligible_availability,
    )
    dominance = build_dominance_assessment(
        standalone=standalone,
        ablations=ablations,
        contribution_attribution=family_attribution,
        grouped=grouped,
    )
    family_assessments = _family_assessments(
        family_missingness=all_family_rows,
        eligible_family_missingness=eligible_family_rows,
        standalone=standalone,
        ablations=ablations,
    )
    mostly_evaluated = [
        row
        for row in eligible_component_rows
        if row["mostly_not_applicable_or_source_missing"]
    ]
    root_backtest = backtest.get("evaluation", backtest)
    if root_backtest["primary_horizon"] != PRIMARY_HORIZON:
        raise ValueError("Sprint 8 primary horizon is not 126d")
    unique_securities = len({row.security_id for row in evaluation_rows})
    sectors = sorted({row.sector for row in evaluation_rows})
    weight_patterns = Counter(
        json.dumps(
            row["renormalized_weights_json"],
            sort_keys=True,
            separators=(",", ":"),
        )
        for row in eligible_scores
    )
    score_hash_rows = [
        {
            "security_id": str(row["security_id"]),
            "asof_date": row["asof_date"].isoformat(),
            "eligible": bool(row["eligible"]),
            "family_z": row["family_z_json"],
            "family_available": row["family_available_json"],
            "weights": row["renormalized_weights_json"],
        }
        for row in scores
    ]
    return {
        "schema_version": "sprint9-factor-diagnostics-v1",
        "claims_eligible": False,
        "decision": "NOT_A_BROADLY_VALIDATED_FIVE_FAMILY_SIGNAL",
        "scope": {
            "security_months": len(scores),
            "prediction_months": len({row["asof_date"] for row in scores}),
            "eligible_evaluated_security_months": len(evaluation_rows),
            "eligible_evaluated_unique_securities": unique_securities,
            "eligible_evaluated_sectors": sectors,
            "calculable_rank_ic_months": full_recomputed[
                "calculable_rank_ic_months"
            ],
            "primary_horizon": PRIMARY_HORIZON,
        },
        "methodology": {
            "missingness_scope": (
                "All normalized component rows for all Sprint 8 security-months; "
                "VALID means directed_value is present."
            ),
            "family_availability_rule": (
                "A family is available when at least half of its applicable "
                "components are valid, matching the frozen scorer."
            ),
            "performance_scope": (
                "Standalone families and grouped signals use the exact 60 eligible "
                "rows and reconstructed 126-session outcomes."
            ),
            "ablation_scope": (
                "Published frozen family ablations remove one family, renormalize "
                "equal weights, retain at least three families, and do not retune."
            ),
            "mostly_threshold": (
                "More than 50% of component rows are NOT_APPLICABLE or SOURCE_MISSING."
            ),
        },
        "warehouse_lineage": {
            "score_rows": len(scores),
            "normalized_component_rows": sum(
                int(row["security_months"]) for row in all_component_rows
            ),
            "score_family_ledger_sha256": _canonical_sha256(score_hash_rows),
            "component_aggregate_sha256": _canonical_sha256(all_component_rows),
            "outcome_reproduction": outcome_reproduction,
        },
        "family_missingness_all_security_months": all_family_rows,
        "component_missingness_all_security_months": all_component_rows,
        "family_missingness_evaluated_rows": eligible_family_rows,
        "component_missingness_evaluated_rows": eligible_component_rows,
        "components_mostly_not_applicable_or_source_missing_in_evaluated_rows": [
            {
                "family": row["family"],
                "feature_name": row["feature_name"],
                "rate": row["not_applicable_or_source_missing_rate"],
                "reason_counts": row["reason_counts"],
            }
            for row in mostly_evaluated
        ],
        "evaluated_weight_patterns": [
            {"weights": json.loads(pattern), "security_months": count}
            for pattern, count in sorted(weight_patterns.items())
        ],
        "score_contribution_attribution": {
            "method": "share_of_sum_of_absolute_component_contributions",
            "by_family": family_attribution,
            "by_component": component_attribution,
        },
        "performance": {
            "published_full_model": {
                "observations": published_full["evaluated_observations"],
                "mean_rank_ic": published_full["mean_rank_ic"],
                "median_rank_ic": published_full["median_rank_ic"],
                "non_overlapping_rank_ic_periods": published_full[
                    "non_overlapping_rank_ic_periods"
                ],
                "non_overlapping_rank_ic_t_statistic": published_full[
                    "non_overlapping_rank_ic_t_statistic"
                ],
                "top_bucket_gross_excess_return": published_full[
                    "quintile_returns"
                ]["5"],
                "top_bucket_net_excess_return_25_bps": published_full[
                    "transaction_costs"
                ]["25_bps"]["average_net_excess_return"],
                "top_minus_bottom_spread": published_full[
                    "top_minus_bottom_spread"
                ],
            },
            "recomputed_full_model": full_recomputed,
            "standalone_families": standalone,
            "grouped_signals": grouped,
            "published_family_ablations": ablations,
            "sprint7_price_only_mean_rank_ic": comparison_root["models"]
            ["sprint7_price_only"]["mean_rank_ic"],
        },
        "family_assessments": family_assessments,
        "dominance_assessment": dominance,
        "practice_assessment": {
            "all_evaluated_rows_have_exactly_four_available_families": all(
                sum(bool(value) for value in row["family_available_json"].values())
                == 4
                for row in eligible_scores
            ),
            "quality_present_in_any_evaluated_score": (
                eligible_availability["quality"] > 0
            ),
            "five_family_model_in_practice": all(
                sum(bool(value) for value in row["family_available_json"].values())
                == len(FAMILIES)
                for row in eligible_scores
            ),
            "broad_multifactor_validation_established": False,
            "useful_family_established": False,
            "reason": (
                "Every evaluated score is a four-family value/growth/momentum/risk "
                "composite, quality is absent, and performance is measurable in only "
                "nine two-to-four-security cross-sections from five Financials-labelled "
                "names."
            ),
        },
    }
