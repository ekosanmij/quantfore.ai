from datetime import date
from decimal import Decimal

import pytest

from quantfore_research.backtest import (
    BacktestObservation,
    analyze_period,
    calculate_coverage,
    calculate_hit_rate,
    calculate_rank_ic_t_statistic,
    calculate_top_quintile_cost_sensitivity,
    rank_cross_section,
    select_monthly_rebalance_dates,
    spearman_rank_correlation,
    summarize_backtest,
)


JANUARY_DATE = date(2024, 1, 31)
FEBRUARY_DATE = date(2024, 2, 29)


def observation(
    ticker: str,
    score: object,
    excess_return: object = None,
    *,
    prediction_date: date = JANUARY_DATE,
    label: str = "neutral",
) -> BacktestObservation:
    return BacktestObservation(
        ticker=ticker,
        prediction_date=prediction_date,
        score=Decimal(str(score)),
        action_label=label,
        excess_return=(
            Decimal(str(excess_return)) if excess_return is not None else None
        ),
    )


def test_monthly_rebalance_dates_are_final_available_sessions():
    available_dates = [
        date(2024, 1, 29),
        date(2024, 1, 31),
        date(2024, 1, 30),
        date(2024, 2, 27),
        date(2024, 2, 29),
        date(2024, 2, 29),
        date(2024, 3, 28),
    ]

    assert select_monthly_rebalance_dates(available_dates) == (
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 28),
    )
    assert select_monthly_rebalance_dates(
        available_dates,
        start_date=date(2024, 2, 1),
        end_date=date(2024, 2, 28),
    ) == ()


def test_cross_section_ranks_highest_score_first_and_assigns_quintiles():
    observations = [
        observation(f"QF{index:02d}", index)
        for index in reversed(range(1, 21))
    ]

    ranked = rank_cross_section(observations)

    assert [value.observation.ticker for value in ranked[:4]] == [
        "QF20",
        "QF19",
        "QF18",
        "QF17",
    ]
    assert [value.score_rank for value in ranked[:4]] == [1.0, 2.0, 3.0, 4.0]
    quintiles = {
        value.observation.ticker: value.quintile for value in ranked
    }
    assert {quintiles[f"QF{index:02d}"] for index in range(1, 5)} == {1}
    assert {quintiles[f"QF{index:02d}"] for index in range(17, 21)} == {5}


def test_tied_scores_share_average_rank_and_quintile_across_boundary():
    observations = [
        observation("QF01", 10),
        observation("QF02", 20),
        observation("QF03", 30),
        observation("QF04", 40),
        observation("QF05", 40),
        observation("QF06", 60),
        observation("QF07", 70),
        observation("QF08", 80),
        observation("QF09", 90),
        observation("QF10", 100),
    ]

    ranked = rank_cross_section(observations)
    by_ticker = {value.observation.ticker: value for value in ranked}

    assert by_ticker["QF04"].score_rank == 6.5
    assert by_ticker["QF05"].score_rank == 6.5
    assert by_ticker["QF04"].quintile == by_ticker["QF05"].quintile == 3


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ([1, 2, 3, 4], [10, 20, 30, 40], 1.0),
        ([1, 2, 3, 4], [40, 30, 20, 10], -1.0),
        ([1, 2, 2, 4], [1, 3, 2, 4], 0.9486832980505138),
    ],
)
def test_spearman_rank_correlation_known_examples(first, second, expected):
    assert spearman_rank_correlation(first, second) == pytest.approx(expected)


def test_spearman_returns_none_for_insufficient_or_constant_values():
    assert spearman_rank_correlation([1], [2]) is None
    assert spearman_rank_correlation([1, 1, 1], [1, 2, 3]) is None


def test_period_metrics_cover_ic_quintile_spread_hit_rate_and_monotonicity():
    observations = [
        observation("QF01", 10, -0.05, label="thesis_risk_review"),
        observation("QF02", 20, -0.02, label="watch_negative"),
        observation("QF03", 30, 0.00),
        observation("QF04", 40, 0.03, label="favourable_setup"),
        observation("QF05", 50, 0.08, label="watch_positive"),
    ]

    result = analyze_period(observations)

    assert result.eligible_observations == 5
    assert result.evaluated_observations == 5
    assert result.coverage == 1.0
    assert result.rank_ic == pytest.approx(1.0)
    assert result.average_excess_return_by_quintile == {
        1: -0.05,
        2: -0.02,
        3: 0.0,
        4: 0.03,
        5: 0.08,
    }
    assert result.top_minus_bottom_spread == pytest.approx(0.13)
    assert result.top_quintile_benchmark_hit_rate == 1.0
    assert result.monotonic is True
    assert result.score_distribution.minimum == 10.0
    assert result.score_distribution.maximum == 50.0
    assert result.score_distribution.median == 30.0
    assert result.label_distribution == {
        "favourable_setup": 1,
        "neutral": 1,
        "thesis_risk_review": 1,
        "watch_negative": 1,
        "watch_positive": 1,
    }


def test_missing_outcomes_reduce_coverage_without_changing_score_assignments():
    complete = [
        observation("QF01", 10, -0.05),
        observation("QF02", 20, -0.02),
        observation("QF03", 30, 0.00),
        observation("QF04", 40, 0.03),
        observation("QF05", 50, 0.08),
    ]
    missing = [*complete[:2], observation("QF03", 30), *complete[3:]]

    complete_result = analyze_period(complete)
    missing_result = analyze_period(missing)

    assert missing_result.coverage == 0.8
    assert missing_result.evaluated_observations == 4
    assert [
        (value.observation.ticker, value.score_rank, value.quintile)
        for value in missing_result.ranked_observations
    ] == [
        (value.observation.ticker, value.score_rank, value.quintile)
        for value in complete_result.ranked_observations
    ]
    assert missing_result.average_excess_return_by_quintile[3] is None
    assert missing_result.monotonic is None


def test_outcomes_never_affect_cross_sectional_ranking():
    first = rank_cross_section(
        [
            observation("QF01", 10, 999),
            observation("QF02", 20, -999),
        ]
    )
    second = rank_cross_section(
        [
            observation("QF01", 10, -999),
            observation("QF02", 20, 999),
        ]
    )

    assert [
        (value.observation.ticker, value.score_rank, value.quintile)
        for value in first
    ] == [
        (value.observation.ticker, value.score_rank, value.quintile)
        for value in second
    ]


def test_summary_calculates_ic_statistics_coverage_and_distributions():
    january = [
        observation("QF01", 10, -0.02, label="watch_negative"),
        observation("QF02", 20, 0.01, label="neutral"),
        observation("QF03", 30, 0.04, label="watch_positive"),
    ]
    february = [
        observation("QF01", 10, 0.04, prediction_date=FEBRUARY_DATE, label="watch_negative"),
        observation("QF02", 20, None, prediction_date=FEBRUARY_DATE, label="neutral"),
        observation("QF03", 30, -0.02, prediction_date=FEBRUARY_DATE, label="watch_positive"),
    ]

    summary = summarize_backtest([*february, *reversed(january)])

    assert [period.prediction_date for period in summary.periods] == [
        JANUARY_DATE,
        FEBRUARY_DATE,
    ]
    assert summary.period_count == 2
    assert summary.eligible_observations == 6
    assert summary.evaluated_observations == 5
    assert summary.coverage == pytest.approx(5 / 6)
    assert summary.mean_rank_ic == 0.0
    assert summary.median_rank_ic == 0.0
    assert summary.rank_ic_t_statistic == 0.0
    assert summary.positive_rank_ic_period_percentage == 0.5
    assert summary.label_distribution == {
        "neutral": 2,
        "watch_negative": 2,
        "watch_positive": 2,
    }
    assert summary.score_distribution.count == 6
    assert summary.score_distribution.mean == 20.0


def test_coverage_hit_rate_and_ic_t_statistic_helpers():
    assert calculate_coverage(eligible=5, evaluated=4) == 0.8
    assert calculate_hit_rate([-0.01, 0, 0.02, 0.03]) == 0.5
    assert calculate_hit_rate([]) is None
    assert calculate_rank_ic_t_statistic([0.1, 0.2, 0.3]) == pytest.approx(
        3.464101615137754
    )


def test_top_quintile_cost_sensitivity_deducts_round_trip_cost_once():
    observations = [
        observation(f"QF{index:02d}", index, -0.01)
        for index in range(1, 9)
    ] + [
        observation("QF09", 9, 0.001),
        observation("QF10", 10, 0.004),
    ]

    sensitivity = calculate_top_quintile_cost_sensitivity(
        rank_cross_section(observations)
    )

    assert tuple(sensitivity) == (0, 10, 25)
    assert sensitivity[0].evaluated_observations == 2
    assert sensitivity[0].average_net_excess_return == pytest.approx(0.0025)
    assert sensitivity[0].benchmark_hit_rate == 1.0
    assert sensitivity[10].average_net_excess_return == pytest.approx(0.0015)
    assert sensitivity[10].benchmark_hit_rate == 0.5
    assert sensitivity[25].average_net_excess_return == pytest.approx(0.0)
    assert sensitivity[25].benchmark_hit_rate == 0.5


def test_summary_is_deterministic_for_shuffled_observation_input():
    observations = [
        observation("QF02", 20, 0.02),
        observation("QF01", 10, -0.01),
        observation("QF03", 30, 0.04),
    ]

    assert summarize_backtest(observations) == summarize_backtest(
        list(reversed(observations))
    )
