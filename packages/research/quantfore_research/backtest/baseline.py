"""Pure analytics for the Sprint 5 baseline signal backtest.

Ranking and quintile assignment depend only on prediction-time scores. Outcome
values are consulted only after those assignments have been frozen in the
returned ``RankedObservation`` objects.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Optional, Sequence


QUINTILES = (1, 2, 3, 4, 5)
ROUND_TRIP_COSTS_BPS = (0, 10, 25)


@dataclass(frozen=True)
class BacktestObservation:
    """One historical prediction and its optional matured excess return."""

    ticker: str
    prediction_date: date
    score: Decimal
    action_label: str
    excess_return: Optional[Decimal] = None

    def __post_init__(self) -> None:
        ticker = self.ticker.strip() if isinstance(self.ticker, str) else ""
        if not ticker:
            raise ValueError("observation ticker is required")
        if not isinstance(self.prediction_date, date):
            raise ValueError("observation prediction_date must be a date")
        label = self.action_label.strip() if isinstance(self.action_label, str) else ""
        if not label:
            raise ValueError("observation action_label is required")
        object.__setattr__(self, "ticker", ticker.upper())
        object.__setattr__(self, "action_label", label)
        object.__setattr__(self, "score", _finite_decimal(self.score, name="score"))
        if self.excess_return is not None:
            object.__setattr__(
                self,
                "excess_return",
                _finite_decimal(self.excess_return, name="excess_return"),
            )


@dataclass(frozen=True)
class RankedObservation:
    """Prediction-time rank and quintile attached to one observation."""

    observation: BacktestObservation
    score_rank: float
    quintile: int


@dataclass(frozen=True)
class ScoreDistribution:
    """Compact descriptive distribution for prediction scores."""

    count: int
    minimum: Optional[float]
    maximum: Optional[float]
    mean: Optional[float]
    median: Optional[float]


@dataclass(frozen=True)
class CostSensitivityResult:
    """Top-quintile diagnostic after one fixed round-trip cost deduction."""

    round_trip_cost_bps: int
    evaluated_observations: int
    average_net_excess_return: Optional[float]
    benchmark_hit_rate: Optional[float]


@dataclass(frozen=True)
class PeriodResult:
    """Cross-sectional analytics for one prediction date."""

    prediction_date: date
    ranked_observations: tuple[RankedObservation, ...]
    eligible_observations: int
    evaluated_observations: int
    coverage: float
    rank_ic: Optional[float]
    average_excess_return_by_quintile: Mapping[int, Optional[float]]
    observation_count_by_quintile: Mapping[int, int]
    top_minus_bottom_spread: Optional[float]
    top_quintile_benchmark_hit_rate: Optional[float]
    monotonic: Optional[bool]
    score_distribution: ScoreDistribution
    label_distribution: Mapping[str, int]


@dataclass(frozen=True)
class BacktestSummary:
    """Aggregate analytics across all supplied prediction dates."""

    periods: tuple[PeriodResult, ...]
    period_count: int
    eligible_observations: int
    evaluated_observations: int
    coverage: float
    mean_rank_ic: Optional[float]
    median_rank_ic: Optional[float]
    rank_ic_t_statistic: Optional[float]
    positive_rank_ic_period_percentage: Optional[float]
    average_excess_return_by_quintile: Mapping[int, Optional[float]]
    observation_count_by_quintile: Mapping[int, int]
    top_minus_bottom_spread: Optional[float]
    top_quintile_benchmark_hit_rate: Optional[float]
    top_quintile_cost_sensitivity: Mapping[int, CostSensitivityResult]
    monotonic: Optional[bool]
    score_distribution: ScoreDistribution
    label_distribution: Mapping[str, int]


def _finite_decimal(value: object, *, name: str) -> Decimal:
    try:
        decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not decimal_value.is_finite():
        raise ValueError(f"{name} must be finite")
    return decimal_value


def select_monthly_rebalance_dates(
    available_dates: Iterable[date],
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> tuple[date, ...]:
    """Select the final available session in every represented calendar month."""

    try:
        values = tuple(available_dates)
    except TypeError as exc:
        raise ValueError("available_dates must be iterable") from exc
    if any(not isinstance(value, date) for value in values):
        raise ValueError("available_dates must contain date values")
    if start_date is not None and not isinstance(start_date, date):
        raise ValueError("start_date must be a date")
    if end_date is not None and not isinstance(end_date, date):
        raise ValueError("end_date must be a date")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("start_date cannot be after end_date")

    month_ends: dict[tuple[int, int], date] = {}
    for value in sorted(set(values)):
        month_ends[(value.year, value.month)] = value
    return tuple(
        value
        for value in month_ends.values()
        if (start_date is None or value >= start_date)
        and (end_date is None or value <= end_date)
    )


def _average_ranks(values: Sequence[object], *, reverse: bool = False) -> list[float]:
    """Return one-based average ranks, assigning equal values the same midrank."""

    indexed_values = sorted(
        enumerate(values),
        key=lambda item: item[1],
        reverse=reverse,
    )
    ranks = [0.0] * len(values)
    position = 0
    while position < len(indexed_values):
        end = position + 1
        while end < len(indexed_values) and indexed_values[end][1] == indexed_values[position][1]:
            end += 1
        average_rank = ((position + 1) + end) / 2.0
        for group_position in range(position, end):
            original_index = indexed_values[group_position][0]
            ranks[original_index] = average_rank
        position = end
    return ranks


def rank_cross_section(
    observations: Iterable[BacktestObservation],
) -> tuple[RankedObservation, ...]:
    """Rank one period by score and assign tie-aware quintiles.

    Quintile 1 contains the lowest scores and quintile 5 the highest. Tied
    scores receive the same average rank and therefore the same quintile, even
    when the tie crosses an otherwise equal-sized bucket boundary.
    """

    values = tuple(observations)
    if not values:
        return ()
    prediction_dates = {value.prediction_date for value in values}
    if len(prediction_dates) != 1:
        raise ValueError("cross-sectional observations must share a prediction date")
    tickers = [value.ticker for value in values]
    if len(tickers) != len(set(tickers)):
        raise ValueError("cross-sectional observation tickers must be unique")

    scores = [value.score for value in values]
    descending_ranks = _average_ranks(scores, reverse=True)
    ascending_ranks = _average_ranks(scores)
    observation_count = len(values)
    ranked = [
        RankedObservation(
            observation=observation,
            score_rank=descending_ranks[index],
            quintile=min(
                5,
                max(1, math.ceil((ascending_ranks[index] * 5) / observation_count)),
            ),
        )
        for index, observation in enumerate(values)
    ]
    return tuple(
        sorted(
            ranked,
            key=lambda value: (
                -value.observation.score,
                value.observation.ticker,
            ),
        )
    )


def spearman_rank_correlation(
    first: Iterable[object],
    second: Iterable[object],
) -> Optional[float]:
    """Calculate tie-aware Spearman correlation without external dependencies."""

    first_values = tuple(_finite_decimal(value, name="first value") for value in first)
    second_values = tuple(
        _finite_decimal(value, name="second value") for value in second
    )
    if len(first_values) != len(second_values):
        raise ValueError("Spearman inputs must have the same length")
    if len(first_values) < 2:
        return None
    first_ranks = _average_ranks(first_values)
    second_ranks = _average_ranks(second_values)
    return _pearson_correlation(first_ranks, second_ranks)


def _pearson_correlation(first: Sequence[float], second: Sequence[float]) -> Optional[float]:
    first_mean = statistics.fmean(first)
    second_mean = statistics.fmean(second)
    first_deltas = [value - first_mean for value in first]
    second_deltas = [value - second_mean for value in second]
    denominator = math.sqrt(
        sum(value * value for value in first_deltas)
        * sum(value * value for value in second_deltas)
    )
    if denominator == 0:
        return None
    return sum(
        first_value * second_value
        for first_value, second_value in zip(first_deltas, second_deltas)
    ) / denominator


def calculate_rank_ic_t_statistic(rank_ics: Iterable[float]) -> Optional[float]:
    """Return the one-sample t-statistic for non-missing period Rank ICs."""

    values = [float(value) for value in rank_ics]
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Rank IC values must be finite")
    if len(values) < 2:
        return None
    standard_deviation = statistics.stdev(values)
    if standard_deviation == 0:
        return None
    return statistics.fmean(values) / (standard_deviation / math.sqrt(len(values)))


def calculate_coverage(*, eligible: int, evaluated: int) -> float:
    """Return evaluated observations divided by eligible observations."""

    if eligible < 0 or evaluated < 0:
        raise ValueError("coverage counts cannot be negative")
    if evaluated > eligible:
        raise ValueError("evaluated observations cannot exceed eligible observations")
    return evaluated / eligible if eligible else 0.0


def calculate_hit_rate(excess_returns: Iterable[object]) -> Optional[float]:
    """Return the share of supplied excess returns strictly above zero."""

    values = tuple(
        _finite_decimal(value, name="excess return") for value in excess_returns
    )
    if not values:
        return None
    return sum(value > 0 for value in values) / len(values)


def calculate_top_quintile_cost_sensitivity(
    ranked_observations: Iterable[RankedObservation],
    *,
    round_trip_costs_bps: Iterable[int] = ROUND_TRIP_COSTS_BPS,
) -> Mapping[int, CostSensitivityResult]:
    """Apply fixed round-trip costs to every evaluated top-quintile outcome.

    One basis point is ``0.0001`` in return units. This is intentionally a
    simple diagnostic and does not estimate holdings, turnover or market impact.
    """

    top_quintile_returns = [
        ranked.observation.excess_return
        for ranked in ranked_observations
        if ranked.quintile == 5 and ranked.observation.excess_return is not None
    ]
    results = {}
    for raw_cost in round_trip_costs_bps:
        if isinstance(raw_cost, bool) or not isinstance(raw_cost, int):
            raise ValueError("round-trip costs must be integer basis points")
        if raw_cost < 0:
            raise ValueError("round-trip costs cannot be negative")
        if raw_cost in results:
            raise ValueError("round-trip costs must be unique")
        cost_return = Decimal(raw_cost) / Decimal("10000")
        net_returns = [value - cost_return for value in top_quintile_returns]
        results[raw_cost] = CostSensitivityResult(
            round_trip_cost_bps=raw_cost,
            evaluated_observations=len(net_returns),
            average_net_excess_return=(
                statistics.fmean(float(value) for value in net_returns)
                if net_returns
                else None
            ),
            benchmark_hit_rate=calculate_hit_rate(net_returns),
        )
    return dict(sorted(results.items()))


def _score_distribution(observations: Sequence[BacktestObservation]) -> ScoreDistribution:
    if not observations:
        return ScoreDistribution(0, None, None, None, None)
    values = [float(value.score) for value in observations]
    return ScoreDistribution(
        count=len(values),
        minimum=min(values),
        maximum=max(values),
        mean=statistics.fmean(values),
        median=statistics.median(values),
    )


def _label_distribution(
    observations: Sequence[BacktestObservation],
) -> Mapping[str, int]:
    counts = Counter(value.action_label for value in observations)
    return dict(sorted(counts.items()))


def _quintile_analytics(
    ranked_observations: Sequence[RankedObservation],
) -> tuple[
    Mapping[int, Optional[float]],
    Mapping[int, int],
    Optional[float],
    Optional[float],
    Optional[bool],
]:
    returns_by_quintile: dict[int, list[Decimal]] = defaultdict(list)
    for ranked in ranked_observations:
        if ranked.observation.excess_return is not None:
            returns_by_quintile[ranked.quintile].append(
                ranked.observation.excess_return
            )
    average_returns = {
        quintile: (
            statistics.fmean(float(value) for value in returns_by_quintile[quintile])
            if returns_by_quintile[quintile]
            else None
        )
        for quintile in QUINTILES
    }
    counts = {
        quintile: len(returns_by_quintile[quintile])
        for quintile in QUINTILES
    }
    bottom_return = average_returns[1]
    top_return = average_returns[5]
    spread = (
        top_return - bottom_return
        if top_return is not None and bottom_return is not None
        else None
    )
    hit_rate = calculate_hit_rate(returns_by_quintile[5])
    ordered_returns = [average_returns[quintile] for quintile in QUINTILES]
    monotonic = (
        all(left <= right for left, right in zip(ordered_returns, ordered_returns[1:]))
        if all(value is not None for value in ordered_returns)
        else None
    )
    return average_returns, counts, spread, hit_rate, monotonic


def analyze_period(observations: Iterable[BacktestObservation]) -> PeriodResult:
    """Calculate one period after freezing its score ranks and quintiles."""

    values = tuple(observations)
    if not values:
        raise ValueError("a period requires at least one observation")
    ranked = rank_cross_section(values)
    evaluated = tuple(
        value for value in values if value.excess_return is not None
    )
    rank_ic = spearman_rank_correlation(
        (value.score for value in evaluated),
        (value.excess_return for value in evaluated),
    )
    quintile_returns, quintile_counts, spread, hit_rate, monotonic = (
        _quintile_analytics(ranked)
    )
    return PeriodResult(
        prediction_date=values[0].prediction_date,
        ranked_observations=ranked,
        eligible_observations=len(values),
        evaluated_observations=len(evaluated),
        coverage=calculate_coverage(eligible=len(values), evaluated=len(evaluated)),
        rank_ic=rank_ic,
        average_excess_return_by_quintile=quintile_returns,
        observation_count_by_quintile=quintile_counts,
        top_minus_bottom_spread=spread,
        top_quintile_benchmark_hit_rate=hit_rate,
        monotonic=monotonic,
        score_distribution=_score_distribution(values),
        label_distribution=_label_distribution(values),
    )


def summarize_backtest(
    observations: Iterable[BacktestObservation],
) -> BacktestSummary:
    """Calculate deterministic period and aggregate baseline backtest analytics."""

    values = tuple(observations)
    observations_by_date: dict[date, list[BacktestObservation]] = defaultdict(list)
    for observation in values:
        observations_by_date[observation.prediction_date].append(observation)
    periods = tuple(
        analyze_period(observations_by_date[prediction_date])
        for prediction_date in sorted(observations_by_date)
    )
    ranked_observations = tuple(
        ranked
        for period in periods
        for ranked in period.ranked_observations
    )
    evaluated_count = sum(
        observation.excess_return is not None for observation in values
    )
    rank_ics = [period.rank_ic for period in periods if period.rank_ic is not None]
    quintile_returns, quintile_counts, spread, hit_rate, monotonic = (
        _quintile_analytics(ranked_observations)
    )
    return BacktestSummary(
        periods=periods,
        period_count=len(periods),
        eligible_observations=len(values),
        evaluated_observations=evaluated_count,
        coverage=calculate_coverage(eligible=len(values), evaluated=evaluated_count),
        mean_rank_ic=statistics.fmean(rank_ics) if rank_ics else None,
        median_rank_ic=statistics.median(rank_ics) if rank_ics else None,
        rank_ic_t_statistic=calculate_rank_ic_t_statistic(rank_ics),
        positive_rank_ic_period_percentage=(
            sum(value > 0 for value in rank_ics) / len(rank_ics)
            if rank_ics
            else None
        ),
        average_excess_return_by_quintile=quintile_returns,
        observation_count_by_quintile=quintile_counts,
        top_minus_bottom_spread=spread,
        top_quintile_benchmark_hit_rate=hit_rate,
        top_quintile_cost_sensitivity=(
            calculate_top_quintile_cost_sensitivity(ranked_observations)
        ),
        monotonic=monotonic,
        score_distribution=_score_distribution(values),
        label_distribution=_label_distribution(values),
    )
