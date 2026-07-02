"""Frozen multi-horizon evaluation design for the Sprint 8 baseline."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from quantfore_research.backtest.baseline import (
    BacktestObservation,
    analyze_period,
    calculate_rank_ic_t_statistic,
    rank_cross_section,
)
from quantfore_research.evaluation.comparative import (
    ComparativeObservation,
    analyze_dataset,
)


HORIZON_MONTHS = {"21d": 1, "63d": 3, "126d": 6, "252d": 12}
TRANSACTION_COSTS_BPS = (10, 25, 50)


@dataclass(frozen=True)
class MultiFactorEvaluationObservation:
    security_id: str
    ticker: str
    prediction_date: date
    sector: str
    score: Optional[Decimal]
    family_scores: Mapping[str, Optional[Decimal]]
    component_coverage: Decimal
    missing_reasons: tuple[str, ...]
    horizon: str
    excess_return: Optional[Decimal]
    realised_return: Optional[Decimal]
    benchmark_return: Optional[Decimal]
    max_drawdown: Optional[Decimal]
    delisted_outcome: bool = False


def _non_overlapping_rank_ic(
    rows: Sequence[MultiFactorEvaluationObservation], horizon: str
) -> dict[str, Any]:
    by_date: dict[date, list[BacktestObservation]] = defaultdict(list)
    for row in rows:
        if row.score is None:
            continue
        by_date[row.prediction_date].append(
            BacktestObservation(
                ticker=row.ticker,
                prediction_date=row.prediction_date,
                score=row.score,
                action_label="multifactor_rank",
                excess_return=row.excess_return,
            )
        )
    periods = [analyze_period(by_date[value]) for value in sorted(by_date)]
    stride = HORIZON_MONTHS[horizon]
    independent = [
        period.rank_ic
        for index, period in enumerate(periods)
        if index % stride == 0 and period.rank_ic is not None
    ]
    return {
        "stride_months": stride,
        "periods": len(independent),
        "t_statistic": (
            calculate_rank_ic_t_statistic(independent)
            if len(independent) >= 5
            else None
        ),
        "method": "monthly Rank IC sampled at the outcome-horizon stride",
    }


def _turnover_costs(
    rows: Sequence[MultiFactorEvaluationObservation],
) -> dict[str, Any]:
    by_date: dict[date, list[MultiFactorEvaluationObservation]] = defaultdict(list)
    for row in rows:
        if row.score is not None:
            by_date[row.prediction_date].append(row)
    prior_top: set[str] = set()
    prior_bottom: set[str] = set()
    periods = []
    for prediction_date in sorted(by_date):
        source = by_date[prediction_date]
        by_ticker = {row.ticker.upper(): row for row in source}
        ranked = rank_cross_section(
            BacktestObservation(
                ticker=row.ticker,
                prediction_date=row.prediction_date,
                score=row.score,
                action_label="multifactor_rank",
                excess_return=row.excess_return,
            )
            for row in source
        )
        top = {
            by_ticker[row.observation.ticker].security_id
            for row in ranked
            if row.quintile == 5
        }
        bottom = {
            by_ticker[row.observation.ticker].security_id
            for row in ranked
            if row.quintile == 1
        }

        def turnover(current: set[str], prior: set[str]) -> Decimal:
            if not prior:
                return Decimal("1") if current else Decimal("0")
            denominator = max(len(current), len(prior))
            return (
                Decimal("1") - Decimal(len(current & prior)) / Decimal(denominator)
                if denominator
                else Decimal("0")
            )

        top_turnover = turnover(top, prior_top)
        bottom_turnover = turnover(bottom, prior_bottom)
        top_returns = [
            row.excess_return for row in source
            if row.security_id in top and row.excess_return is not None
        ]
        bottom_returns = [
            row.excess_return for row in source
            if row.security_id in bottom and row.excess_return is not None
        ]
        gross_top = (
            sum(top_returns, Decimal("0")) / Decimal(len(top_returns))
            if top_returns else None
        )
        gross_spread = (
            gross_top - sum(bottom_returns, Decimal("0")) / Decimal(len(bottom_returns))
            if gross_top is not None and bottom_returns else None
        )
        periods.append(
            {
                "prediction_date": prediction_date.isoformat(),
                "top_turnover": top_turnover,
                "bottom_turnover": bottom_turnover,
                "gross_top_excess": gross_top,
                "gross_top_minus_bottom": gross_spread,
            }
        )
        prior_top, prior_bottom = top, bottom
    costs = {}
    for bps in TRANSACTION_COSTS_BPS:
        one_way = Decimal(bps) / Decimal("10000")
        net_top = []
        net_spreads = []
        for row in periods:
            if row["gross_top_excess"] is not None:
                net_top.append(
                    row["gross_top_excess"]
                    - Decimal("2") * one_way * row["top_turnover"]
                )
            if row["gross_top_minus_bottom"] is not None:
                net_spreads.append(
                    row["gross_top_minus_bottom"]
                    - Decimal("2") * one_way
                    * (row["top_turnover"] + row["bottom_turnover"])
                )
        costs[f"{bps}_bps"] = {
            "one_way_bps": bps,
            "mean_net_top_excess": (
                float(sum(net_top, Decimal("0")) / Decimal(len(net_top)))
                if net_top else None
            ),
            "mean_net_top_minus_bottom": (
                float(sum(net_spreads, Decimal("0")) / Decimal(len(net_spreads)))
                if net_spreads else None
            ),
            "positive_net_spread_period_percentage": (
                sum(value > 0 for value in net_spreads) / len(net_spreads)
                if net_spreads else None
            ),
        }
    return {
        "method": "one-way entry and exit costs applied to top and bottom turnover",
        "periods": [
            {key: (float(value) if isinstance(value, Decimal) else value) for key, value in row.items()}
            for row in periods
        ],
        "cost_sensitivity": costs,
    }


def _pearson(left: Sequence[Decimal], right: Sequence[Decimal]) -> Optional[float]:
    if len(left) < 2 or len(left) != len(right):
        return None
    left_mean = sum(left, Decimal("0")) / Decimal(len(left))
    right_mean = sum(right, Decimal("0")) / Decimal(len(right))
    numerator = sum(
        ((x - left_mean) * (y - right_mean) for x, y in zip(left, right)),
        Decimal("0"),
    )
    left_sum = sum(((x - left_mean) ** 2 for x in left), Decimal("0"))
    right_sum = sum(((y - right_mean) ** 2 for y in right), Decimal("0"))
    denominator = (left_sum * right_sum).sqrt()
    return float(numerator / denominator) if denominator else None


def _family_correlations(
    rows: Sequence[MultiFactorEvaluationObservation],
) -> dict[str, dict[str, Optional[float]]]:
    unique = {}
    for row in rows:
        unique[(row.security_id, row.prediction_date)] = row
    families = sorted(
        {family for row in unique.values() for family in row.family_scores}
    )
    result = {}
    for left in families:
        result[left] = {}
        for right in families:
            pairs = [
                (row.family_scores.get(left), row.family_scores.get(right))
                for row in unique.values()
            ]
            valid = [(x, y) for x, y in pairs if x is not None and y is not None]
            result[left][right] = _pearson(
                [x for x, _ in valid], [y for _, y in valid]
            )
    return result


def evaluate_multifactor_baseline(
    observations: Sequence[MultiFactorEvaluationObservation],
) -> dict[str, Any]:
    """Evaluate all frozen Sprint 8.6 diagnostics without changing the model."""

    if not observations:
        raise ValueError("multi-factor evaluation requires observations")
    unknown = sorted({row.horizon for row in observations} - set(HORIZON_MONTHS))
    if unknown:
        raise ValueError(f"unsupported evaluation horizons: {unknown!r}")
    by_horizon: dict[str, list[MultiFactorEvaluationObservation]] = defaultdict(list)
    for row in observations:
        by_horizon[row.horizon].append(row)
    horizons = {}
    for horizon in HORIZON_MONTHS:
        rows = by_horizon.get(horizon, [])
        if not rows:
            horizons[horizon] = None
            continue
        comparable = [
            ComparativeObservation(
                security_id=row.security_id,
                ticker=row.ticker,
                prediction_date=row.prediction_date,
                sector=row.sector,
                score=row.score if row.score is not None else Decimal("0"),
                action_label="multifactor_rank",
                excess_return=row.excess_return if row.score is not None else None,
                realised_return=row.realised_return,
                benchmark_return=row.benchmark_return,
                max_drawdown=row.max_drawdown,
                delisted_outcome=row.delisted_outcome,
            )
            for row in rows
            if row.score is not None
        ]
        analysis = analyze_dataset(comparable) if comparable else {}
        analysis["non_overlapping_rank_ic"] = _non_overlapping_rank_ic(rows, horizon)
        analysis["turnover_and_one_way_costs"] = _turnover_costs(rows)
        horizons[horizon] = analysis
    reason_counts = Counter(
        reason for row in observations for reason in row.missing_reasons
    )
    unique_scores = {
        (row.security_id, row.prediction_date): row for row in observations
    }
    return {
        "evaluation_version": "pit-multifactor-evaluation-v1",
        "primary_horizon": "126d",
        "horizons": horizons,
        "family_score_correlations": _family_correlations(observations),
        "missingness_and_coverage_bias": {
            "score_rows": len(unique_scores),
            "eligible_score_rows": sum(row.score is not None for row in unique_scores.values()),
            "mean_component_coverage": statistics.fmean(
                float(row.component_coverage) for row in unique_scores.values()
            ),
            "missing_reason_counts": dict(sorted(reason_counts.items())),
            "evaluated_by_coverage_band": {
                "at_least_90_percent": sum(
                    row.component_coverage >= Decimal("0.90") and row.excess_return is not None
                    for row in observations
                ),
                "70_to_90_percent": sum(
                    Decimal("0.70") <= row.component_coverage < Decimal("0.90")
                    and row.excess_return is not None
                    for row in observations
                ),
            },
        },
    }
