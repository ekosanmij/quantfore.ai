"""Aligned Sprint 7/Sprint 8 comparison, ablations, and attribution."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from quantfore_research.evaluation.comparative import (
    ComparativeObservation,
    analyze_dataset,
)
from quantfore_research.scoring.multifactor import FAMILY_WEIGHTS


FAMILIES = tuple(FAMILY_WEIGHTS)


@dataclass(frozen=True)
class AttributionComponent:
    """One source-bound normalized component used in a final prediction."""

    name: str
    family: str
    contribution: Optional[Decimal]
    raw_value: Optional[Decimal]
    directed_value: Optional[Decimal]
    normalization_scope: str
    group_label: Optional[str]
    group_count: int
    group_mean: Optional[Decimal]
    group_std: Optional[Decimal]
    missing_reason: Optional[str] = None
    evidence_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class MultiModelObservation:
    """Scores, outcome, and evidence for one date/security intersection."""

    security_id: str
    ticker: str
    prediction_date: date
    sector: str
    price_score: Optional[Decimal]
    multifactor_score: Optional[Decimal]
    family_z: Mapping[str, Optional[Decimal]]
    family_scores: Mapping[str, Optional[Decimal]]
    missing_data_flags: Mapping[str, Any]
    components: tuple[AttributionComponent, ...]
    excess_return: Optional[Decimal]
    realised_return: Optional[Decimal]
    benchmark_return: Optional[Decimal]
    max_drawdown: Optional[Decimal]
    delisted_outcome: bool = False


def _decimal_text(value: Optional[Decimal]) -> Optional[str]:
    return None if value is None else format(value, "f")


def _numeric_delta(left: Any, right: Any) -> Optional[float]:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(right) - float(left)
    return None


def _as_comparative(
    row: MultiModelObservation,
    score: Decimal,
) -> ComparativeObservation:
    return ComparativeObservation(
        security_id=row.security_id,
        ticker=row.ticker,
        prediction_date=row.prediction_date,
        sector=row.sector,
        score=score,
        action_label="RANKED",
        excess_return=row.excess_return,
        realised_return=row.realised_return,
        benchmark_return=row.benchmark_return,
        max_drawdown=row.max_drawdown,
        delisted_outcome=row.delisted_outcome,
    )


def _equal_weight_summary(
    observations: Sequence[MultiModelObservation],
) -> dict[str, Any]:
    by_date: dict[date, list[Decimal]] = defaultdict(list)
    for row in observations:
        if row.excess_return is not None:
            by_date[row.prediction_date].append(row.excess_return)
    periods = [
        {
            "prediction_date": prediction_date.isoformat(),
            "security_count": len(values),
            "equal_weight_excess_return": float(
                sum(values, Decimal("0")) / Decimal(len(values))
            ),
        }
        for prediction_date, values in sorted(by_date.items())
        if values
    ]
    period_returns = [row["equal_weight_excess_return"] for row in periods]
    return {
        "method": "equal_weight_all_aligned_securities",
        "eligible_observations": len(observations),
        "evaluated_observations": sum(len(values) for values in by_date.values()),
        "coverage": (
            sum(len(values) for values in by_date.values()) / len(observations)
            if observations
            else None
        ),
        "evaluated_periods": len(periods),
        "mean_excess_return": (
            statistics.fmean(period_returns) if period_returns else None
        ),
        "median_excess_return": (
            statistics.median(period_returns) if period_returns else None
        ),
        "positive_period_rate": (
            sum(value > 0 for value in period_returns) / len(period_returns)
            if period_returns
            else None
        ),
        "rank_ic": None,
        "top_minus_bottom_spread": None,
        "periods": periods,
    }


def _average_tie_percentiles(
    values: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    if not values:
        return {}
    if len(values) == 1:
        return {next(iter(values)): Decimal("50")}
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    result: dict[str, Decimal] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (Decimal(index + 1) + Decimal(end)) / Decimal("2")
        percentile = Decimal("100") * (average_rank - 1) / Decimal(
            len(ordered) - 1
        )
        for position in range(index, end):
            result[ordered[position][0]] = percentile
        index = end
    return result


def _ablation(
    observations: Sequence[MultiModelObservation],
    omitted_family: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_date: dict[date, list[MultiModelObservation]] = defaultdict(list)
    for row in observations:
        by_date[row.prediction_date].append(row)
    scored: list[ComparativeObservation] = []
    excluded = 0
    for prediction_date in sorted(by_date):
        composites: dict[str, Decimal] = {}
        source: dict[str, MultiModelObservation] = {}
        for row in by_date[prediction_date]:
            available = [
                family
                for family in FAMILIES
                if family != omitted_family and row.family_z.get(family) is not None
            ]
            if len(available) < 3:
                excluded += 1
                continue
            denominator = sum(
                (FAMILY_WEIGHTS[family] for family in available), Decimal("0")
            )
            composite = sum(
                (
                    row.family_z[family]
                    * FAMILY_WEIGHTS[family]
                    / denominator
                    for family in available
                    if row.family_z[family] is not None
                ),
                Decimal("0"),
            )
            key = row.security_id
            composites[key] = composite
            source[key] = row
        percentiles = _average_tie_percentiles(composites)
        scored.extend(
            _as_comparative(source[security_id], score)
            for security_id, score in percentiles.items()
        )
    if not scored:
        raise ValueError(f"ablation without {omitted_family} has no eligible observations")
    return analyze_dataset(scored), {
        "omitted_family": omitted_family,
        "weight_policy": "renormalize_frozen_equal_weights_across_available_families",
        "minimum_remaining_families": 3,
        "eligible_observations": len(scored),
        "excluded_observations": excluded,
        "retuned": False,
    }


def _component_document(component: AttributionComponent) -> dict[str, Any]:
    return {
        "name": component.name,
        "family": component.family,
        "contribution": _decimal_text(component.contribution),
        "raw_value": _decimal_text(component.raw_value),
        "directed_value": _decimal_text(component.directed_value),
        "missing_reason": component.missing_reason,
        "normalization": {
            "scope": component.normalization_scope,
            "group_label": component.group_label,
            "group_count": component.group_count,
            "group_mean": _decimal_text(component.group_mean),
            "group_std": _decimal_text(component.group_std),
        },
        "source_evidence_refs": sorted(set(component.evidence_refs)),
    }


def _prediction_attribution(row: MultiModelObservation) -> dict[str, Any]:
    valid = [item for item in row.components if item.contribution is not None]
    positive = [item for item in valid if item.contribution > 0]
    negative = [item for item in valid if item.contribution < 0]
    strongest_positive = max(
        positive, key=lambda item: (item.contribution, item.name), default=None
    )
    strongest_negative = min(
        negative, key=lambda item: (item.contribution, item.name), default=None
    )
    evidence_refs = sorted(
        {reference for item in row.components for reference in item.evidence_refs}
    )
    return {
        "security_id": row.security_id,
        "ticker": row.ticker,
        "prediction_date": row.prediction_date.isoformat(),
        "sector": row.sector,
        "final_score": _decimal_text(row.multifactor_score),
        "family_scores": {
            family: _decimal_text(row.family_scores.get(family))
            for family in FAMILIES
        },
        "strongest_positive_component": (
            _component_document(strongest_positive) if strongest_positive else None
        ),
        "strongest_negative_component": (
            _component_document(strongest_negative) if strongest_negative else None
        ),
        "missing_data_flags": dict(row.missing_data_flags),
        "sector_normalization_context": [
            _component_document(item) for item in sorted(row.components, key=lambda x: x.name)
        ],
        "source_evidence_refs": evidence_refs,
    }


def build_multifactor_comparison(
    observations: Sequence[MultiModelObservation],
) -> dict[str, Any]:
    """Compare all three baselines on one exact date/security intersection."""

    if not observations:
        raise ValueError("multi-model comparison requires observations")
    ordered = tuple(
        sorted(observations, key=lambda row: (row.prediction_date, row.security_id))
    )
    keys = [(row.prediction_date, row.security_id) for row in ordered]
    if len(keys) != len(set(keys)):
        raise ValueError("comparison ledger contains duplicate date/security rows")
    aligned = tuple(
        row
        for row in ordered
        if row.price_score is not None and row.multifactor_score is not None
    )
    if not aligned:
        raise ValueError("price and multi-factor models have no aligned predictions")
    price = analyze_dataset(
        [_as_comparative(row, row.price_score) for row in aligned if row.price_score is not None]
    )
    multifactor = analyze_dataset(
        [
            _as_comparative(row, row.multifactor_score)
            for row in aligned
            if row.multifactor_score is not None
        ]
    )
    ablations = {}
    for family in FAMILIES:
        result, design = _ablation(aligned, family)
        ablations[f"without_{family}"] = {"design": design, "evaluation": result}
    fingerprint_rows = [
        {"prediction_date": row.prediction_date.isoformat(), "security_id": row.security_id}
        for row in aligned
    ]
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_rows, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    comparison_fields = (
        "coverage",
        "mean_rank_ic",
        "median_rank_ic",
        "non_overlapping_rank_ic_t_statistic",
        "top_minus_bottom_spread",
    )
    return {
        "schema_version": "price-vs-multifactor-v1",
        "claims_eligible": False,
        "comparison_complete": True,
        "alignment": {
            "method": "exact_prediction_date_and_security_intersection",
            "input_observations": len(ordered),
            "aligned_observations": len(aligned),
            "excluded_missing_price_score": sum(row.price_score is None for row in ordered),
            "excluded_missing_multifactor_score": sum(
                row.multifactor_score is None for row in ordered
            ),
            "prediction_dates": len({row.prediction_date for row in aligned}),
            "start": min(row.prediction_date for row in aligned).isoformat(),
            "end": max(row.prediction_date for row in aligned).isoformat(),
            "date_security_sha256": fingerprint,
        },
        "models": {
            "equal_weight_benchmark": _equal_weight_summary(aligned),
            "sprint7_price_only": price,
            "sprint8_multifactor": multifactor,
        },
        "headline_deltas_multifactor_minus_price": {
            field: _numeric_delta(price[field], multifactor[field])
            for field in comparison_fields
        },
        "family_ablations": ablations,
        "prediction_attribution": [_prediction_attribution(row) for row in aligned],
        "interpretation": (
            "All models use the exact same date/security rows. Family ablations "
            "remove one family, renormalize frozen equal weights, and never retune."
        ),
    }
