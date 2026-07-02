"""Comparative Sprint 6 versus point-in-time baseline diagnostics."""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from quantfore_research.backtest.baseline import (
    BacktestObservation,
    rank_cross_section,
    summarize_backtest,
)


@dataclass(frozen=True)
class ComparativeObservation:
    security_id: str
    ticker: str
    prediction_date: date
    sector: str
    score: Decimal
    action_label: str
    excess_return: Optional[Decimal]
    realised_return: Optional[Decimal]
    benchmark_return: Optional[Decimal]
    max_drawdown: Optional[Decimal]
    delisted_outcome: bool = False

    def baseline_observation(self) -> BacktestObservation:
        return BacktestObservation(
            ticker=self.ticker,
            prediction_date=self.prediction_date,
            score=self.score,
            action_label=self.action_label,
            excess_return=self.excess_return,
        )


@dataclass(frozen=True)
class UniverseCohort:
    prediction_date: date
    tickers: tuple[str, ...]


def _mean(values: Iterable[Decimal]) -> Optional[float]:
    rows = [float(value) for value in values]
    return statistics.fmean(rows) if rows else None


def _median(values: Iterable[Decimal]) -> Optional[float]:
    rows = [float(value) for value in values]
    return statistics.median(rows) if rows else None


def _summary_dict(observations: Sequence[ComparativeObservation]) -> dict[str, Any]:
    summary = summarize_backtest(
        row.baseline_observation() for row in observations
    )
    return {
        "eligible_observations": summary.eligible_observations,
        "evaluated_observations": summary.evaluated_observations,
        "coverage": summary.coverage,
        "mean_rank_ic": summary.mean_rank_ic,
        "median_rank_ic": summary.median_rank_ic,
        "non_overlapping_rank_ic_t_statistic": summary.rank_ic_t_statistic,
        "non_overlapping_rank_ic_periods": summary.rank_ic_t_statistic_periods,
        "non_overlap_stride_months": summary.rank_ic_non_overlap_stride,
        "quintile_returns": {
            str(key): value
            for key, value in summary.average_excess_return_by_quintile.items()
        },
        "quintile_observation_counts": {
            str(key): value
            for key, value in summary.observation_count_by_quintile.items()
        },
        "top_minus_bottom_spread": summary.top_minus_bottom_spread,
        "quintile_returns_monotonic": summary.monotonic,
    }


def _stability_group(
    observations: Sequence[ComparativeObservation],
) -> dict[str, Any]:
    summary = _summary_dict(observations)
    evaluated = [row for row in observations if row.excess_return is not None]
    return {
        "observations": len(observations),
        "evaluated": len(evaluated),
        "mean_excess_return": _mean(
            row.excess_return for row in evaluated if row.excess_return is not None
        ),
        "mean_rank_ic": summary["mean_rank_ic"],
        "median_rank_ic": summary["median_rank_ic"],
        "top_minus_bottom_spread": summary["top_minus_bottom_spread"],
    }


def _top_quintile_by_date(
    observations: Sequence[ComparativeObservation],
) -> list[tuple[date, tuple[ComparativeObservation, ...]]]:
    by_date: dict[date, list[ComparativeObservation]] = defaultdict(list)
    for row in observations:
        by_date[row.prediction_date].append(row)
    periods = []
    for prediction_date in sorted(by_date):
        source_rows = by_date[prediction_date]
        by_ticker = {row.ticker: row for row in source_rows}
        ranked = rank_cross_section(row.baseline_observation() for row in source_rows)
        top = tuple(
            by_ticker[row.observation.ticker]
            for row in ranked
            if row.quintile == 5
        )
        periods.append((prediction_date, top))
    return periods


def _turnover_and_costs(
    observations: Sequence[ComparativeObservation],
) -> tuple[dict[str, Any], dict[str, Any]]:
    periods = _top_quintile_by_date(observations)
    prior: set[str] = set()
    turnover_rows = []
    gross_returns: dict[date, Decimal] = {}
    for prediction_date, top in periods:
        current = {row.security_id for row in top}
        if not prior:
            turnover = Decimal("1") if current else Decimal("0")
        else:
            denominator = max(len(prior), len(current))
            turnover = (
                Decimal("1")
                - Decimal(len(prior & current)) / Decimal(denominator)
                if denominator
                else Decimal("0")
            )
        evaluated = [row.excess_return for row in top if row.excess_return is not None]
        if evaluated:
            gross_returns[prediction_date] = sum(evaluated, Decimal("0")) / Decimal(
                len(evaluated)
            )
        turnover_rows.append(
            {
                "prediction_date": prediction_date.isoformat(),
                "holdings": sorted(current),
                "turnover": float(turnover),
            }
        )
        prior = current
    turnover_values = [Decimal(str(row["turnover"])) for row in turnover_rows]
    turnover = {
        "method": "one_minus_overlap_divided_by_max_portfolio_size",
        "mean": _mean(turnover_values),
        "median": _median(turnover_values),
        "periods": turnover_rows,
    }
    costs = {}
    for bps in (10, 25, 50):
        net = []
        for row in turnover_rows:
            prediction_date = date.fromisoformat(row["prediction_date"])
            gross = gross_returns.get(prediction_date)
            if gross is None:
                continue
            cost = Decimal(bps) / Decimal("10000") * Decimal(str(row["turnover"]))
            net.append(gross - cost)
        costs[f"{bps}_bps"] = {
            "method": "gross_top_quintile_excess_minus_turnover_times_cost",
            "evaluated_periods": len(net),
            "average_net_excess_return": _mean(net),
            "benchmark_hit_rate": (
                sum(value > 0 for value in net) / len(net) if net else None
            ),
        }
    return turnover, costs


def _drawdown_and_downside(
    observations: Sequence[ComparativeObservation],
) -> dict[str, Any]:
    evaluated = [
        row
        for row in observations
        if row.max_drawdown is not None
        and row.realised_return is not None
        and row.benchmark_return is not None
    ]
    top_periods = _top_quintile_by_date(observations)
    top_rows = [row for _, rows in top_periods for row in rows]
    top_drawdowns = [
        row.max_drawdown for row in top_rows if row.max_drawdown is not None
    ]
    down_security_returns = []
    down_benchmark_returns = []
    for _, rows in top_periods:
        period_rows = [
            row
            for row in rows
            if row.realised_return is not None and row.benchmark_return is not None
        ]
        if not period_rows:
            continue
        benchmark_return = sum(
            (row.benchmark_return for row in period_rows if row.benchmark_return is not None),
            Decimal("0"),
        ) / Decimal(len(period_rows))
        if benchmark_return < 0:
            security_return = sum(
                (row.realised_return for row in period_rows if row.realised_return is not None),
                Decimal("0"),
            ) / Decimal(len(period_rows))
            down_security_returns.append(security_return)
            down_benchmark_returns.append(benchmark_return)
    mean_security = _mean(down_security_returns)
    mean_benchmark = _mean(down_benchmark_returns)
    downside_capture = (
        (mean_security / mean_benchmark) * 100
        if mean_security is not None and mean_benchmark not in (None, 0.0)
        else None
    )
    all_drawdowns = [row.max_drawdown for row in evaluated if row.max_drawdown is not None]
    return {
        "all_observations": {
            "mean_max_drawdown": _mean(all_drawdowns),
            "median_max_drawdown": _median(all_drawdowns),
            "worst_max_drawdown": (
                float(min(all_drawdowns)) if all_drawdowns else None
            ),
        },
        "top_quintile": {
            "mean_max_drawdown": _mean(top_drawdowns),
            "median_max_drawdown": _median(top_drawdowns),
            "worst_max_drawdown": (
                float(min(top_drawdowns)) if top_drawdowns else None
            ),
            "down_market_periods": len(down_benchmark_returns),
            "mean_security_return_in_down_markets": mean_security,
            "mean_benchmark_return_in_down_markets": mean_benchmark,
            "downside_capture_percentage": downside_capture,
        },
    }


def _delisted_contribution(
    observations: Sequence[ComparativeObservation],
) -> dict[str, Any]:
    evaluated = [row for row in observations if row.excess_return is not None]
    delisted = [row for row in evaluated if row.delisted_outcome]
    surviving = [row for row in evaluated if not row.delisted_outcome]
    overall_mean = _mean(
        row.excess_return for row in evaluated if row.excess_return is not None
    )
    surviving_mean = _mean(
        row.excess_return for row in surviving if row.excess_return is not None
    )
    return {
        "observation_count": len(delisted),
        "security_count": len({row.security_id for row in delisted}),
        "mean_excess_return": _mean(
            row.excess_return for row in delisted if row.excess_return is not None
        ),
        "sum_excess_return": (
            float(
                sum(
                    (row.excess_return for row in delisted if row.excess_return is not None),
                    Decimal("0"),
                )
            )
            if delisted
            else 0.0
        ),
        "contribution_to_all_observation_mean": (
            float(
                sum(
                    (row.excess_return for row in delisted if row.excess_return is not None),
                    Decimal("0"),
                )
                / Decimal(len(evaluated))
            )
            if evaluated
            else None
        ),
        "overall_mean_minus_excluding_delisted_mean": (
            overall_mean - surviving_mean
            if overall_mean is not None and surviving_mean is not None
            else None
        ),
    }


def analyze_dataset(
    observations: Sequence[ComparativeObservation],
) -> dict[str, Any]:
    """Calculate every required Sprint 7.7 diagnostic for one dataset."""

    ordered = tuple(
        sorted(
            observations,
            key=lambda row: (row.prediction_date, row.ticker, row.security_id),
        )
    )
    if not ordered:
        raise ValueError("comparative analysis requires observations")
    summary = _summary_dict(ordered)
    by_year: dict[int, list[ComparativeObservation]] = defaultdict(list)
    by_sector: dict[str, list[ComparativeObservation]] = defaultdict(list)
    for row in ordered:
        by_year[row.prediction_date.year].append(row)
        by_sector[row.sector or "Unknown"].append(row)
    turnover, costs = _turnover_and_costs(ordered)
    return {
        **summary,
        "year_stability": {
            str(year): _stability_group(rows)
            for year, rows in sorted(by_year.items())
        },
        "sector_stability": {
            sector: _stability_group(rows)
            for sector, rows in sorted(by_sector.items())
        },
        "turnover": turnover,
        "transaction_costs": costs,
        "drawdown_and_downside_capture": _drawdown_and_downside(ordered),
        "delisted_security_contribution": _delisted_contribution(ordered),
    }


def compare_universes(
    *,
    static_tickers: Sequence[str],
    pit_cohorts: Sequence[UniverseCohort],
) -> dict[str, Any]:
    static = set(static_tickers)
    periods = []
    for cohort in sorted(pit_cohorts, key=lambda row: row.prediction_date):
        pit = set(cohort.tickers)
        union = static | pit
        periods.append(
            {
                "prediction_date": cohort.prediction_date.isoformat(),
                "static_count": len(static),
                "pit_count": len(pit),
                "intersection_count": len(static & pit),
                "static_only": sorted(static - pit),
                "pit_only": sorted(pit - static),
                "symmetric_difference_count": len(static ^ pit),
                "jaccard_similarity": len(static & pit) / len(union) if union else 1.0,
            }
        )
    return {
        "static_universe_size": len(static),
        "pit_period_count": len(periods),
        "mean_symmetric_difference_count": (
            statistics.fmean(row["symmetric_difference_count"] for row in periods)
            if periods
            else None
        ),
        "mean_jaccard_similarity": (
            statistics.fmean(row["jaccard_similarity"] for row in periods)
            if periods
            else None
        ),
        "periods": periods,
    }


def _numeric_delta(left: Any, right: Any) -> Optional[float]:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(right) - float(left)
    return None


def build_comparative_evidence(
    *,
    static_observations: Sequence[ComparativeObservation],
    pit_observations: Sequence[ComparativeObservation],
    static_tickers: Sequence[str],
    pit_cohorts: Sequence[UniverseCohort],
    static_lineage: Mapping[str, Any],
    pit_lineage: Mapping[str, Any],
) -> dict[str, Any]:
    static_dates = {row.prediction_date for row in static_observations}
    pit_dates = {row.prediction_date for row in pit_observations}
    shared_dates = static_dates & pit_dates
    if not shared_dates:
        raise ValueError("static and point-in-time observations have no shared dates")
    aligned_static = tuple(
        row for row in static_observations if row.prediction_date in shared_dates
    )
    aligned_pit = tuple(
        row for row in pit_observations if row.prediction_date in shared_dates
    )
    aligned_cohorts = tuple(
        row for row in pit_cohorts if row.prediction_date in shared_dates
    )
    missing_cohort_dates = shared_dates - {
        row.prediction_date for row in aligned_cohorts
    }
    if missing_cohort_dates:
        raise ValueError(
            "point-in-time universe cohorts are missing shared prediction dates: "
            + ", ".join(day.isoformat() for day in sorted(missing_cohort_dates))
        )
    static = analyze_dataset(aligned_static)
    pit = analyze_dataset(aligned_pit)
    comparison_fields = (
        "coverage",
        "mean_rank_ic",
        "median_rank_ic",
        "non_overlapping_rank_ic_t_statistic",
        "top_minus_bottom_spread",
    )
    return {
        "schema_version": "sprint6_vs_pit_comparison_v1",
        "claims_eligible": False,
        "comparison_complete": True,
        "comparison_window": {
            "method": "shared_prediction_dates_only",
            "start": min(shared_dates).isoformat(),
            "end": max(shared_dates).isoformat(),
            "shared_period_count": len(shared_dates),
            "static_only_dates_excluded": [
                day.isoformat() for day in sorted(static_dates - shared_dates)
            ],
            "point_in_time_only_dates_excluded": [
                day.isoformat() for day in sorted(pit_dates - shared_dates)
            ],
        },
        "static": static,
        "point_in_time": pit,
        "headline_deltas_pit_minus_static": {
            field: _numeric_delta(static[field], pit[field])
            for field in comparison_fields
        },
        "static_vs_pit_universe_difference": compare_universes(
            static_tickers=static_tickers,
            pit_cohorts=aligned_cohorts,
        ),
        "lineage": {
            "static": dict(static_lineage),
            "point_in_time": dict(pit_lineage),
        },
        "interpretation": (
            "The baseline is allowed to fail. This report measures the effect of "
            "the point-in-time dataset without changing the model."
        ),
    }
