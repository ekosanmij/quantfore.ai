from datetime import date
from decimal import Decimal

import pytest

from quantfore_research.validation.factor_diagnostics import FamilyEvaluationRow
from quantfore_research.validation.investability import (
    build_portfolio_periods,
    concentration_summary,
    cost_sensitivity,
    summarize_liquidity_rows,
)


def _row(
    *,
    month: int,
    index: int,
    score: int,
    excess: str,
    sector: str = "Financials",
) -> FamilyEvaluationRow:
    excess_return = Decimal(excess)
    return FamilyEvaluationRow(
        security_id=f"security-{index}",
        ticker=f"T{index}",
        prediction_date=date(2020, month, 28),
        sector=sector,
        final_score=Decimal(score),
        family_z={},
        excess_return=excess_return,
        realised_return=Decimal("0.03") + excess_return,
        benchmark_return=Decimal("0.03"),
        max_drawdown=Decimal("-0.10"),
    )


def test_portfolio_periods_populate_top_and_bottom_only_with_five_names():
    rows = tuple(
        _row(month=1, index=index, score=index, excess=f"0.0{index}")
        for index in range(5)
    ) + tuple(
        _row(month=2, index=index, score=4 - index, excess=f"0.0{index}")
        for index in range(5)
    )

    periods = build_portfolio_periods(rows)

    assert len(periods) == 2
    assert [row.ticker for row in periods[0].selected] == ["T4"]
    assert [row.ticker for row in periods[0].bottom] == ["T0"]
    assert [row.ticker for row in periods[1].selected] == ["T0"]
    assert [row.ticker for row in periods[1].bottom] == ["T4"]
    assert periods[0].turnover == Decimal("1")
    assert periods[1].turnover == Decimal("1")


def test_small_cohort_has_no_bottom_bucket_and_one_top_holding():
    periods = build_portfolio_periods(
        tuple(
            _row(month=1, index=index, score=index, excess="0.01")
            for index in range(4)
        )
    )

    assert len(periods[0].selected) == 1
    assert periods[0].selected[0].ticker == "T3"
    assert periods[0].bottom == ()
    assert periods[0].bottom_excess_return is None


def test_cost_sensitivity_uses_turnover_and_does_not_change_benchmark():
    periods = build_portfolio_periods(
        tuple(
            _row(month=1, index=index, score=index, excess="0.02")
            for index in range(4)
        )
    )

    result = cost_sensitivity(periods, costs_bps=(10, 25, 50))

    assert result["10_bps"]["mean_net_excess_return"] == pytest.approx(0.019)
    assert result["25_bps"]["mean_net_excess_return"] == pytest.approx(0.0175)
    assert result["50_bps"]["mean_net_excess_return"] == pytest.approx(0.015)
    assert result["25_bps"]["mean_cost_drag"] == pytest.approx(0.0025)
    assert result["25_bps"]["net_benchmark_hit_rate"] == 1.0


def test_single_holding_periods_are_fully_concentrated():
    periods = build_portfolio_periods(
        tuple(
            _row(month=month, index=index, score=index, excess="0.01")
            for month in (1, 2)
            for index in range(4)
        )
    )

    result = concentration_summary(periods)

    assert result["single_name"]["mean_holdings_per_period"] == 1.0
    assert result["single_name"]["mean_hhi"] == 1.0
    assert result["single_name"]["maximum_period_name_weight"] == 1.0
    assert result["sector"]["mean_hhi"] == 1.0
    assert result["sector"]["maximum_period_sector_weight"] == 1.0


def test_liquidity_summary_keeps_coverage_separate_from_threshold_passes():
    rows = [
        {
            "prediction_date": "2020-01-31",
            "security_id": "security-1",
            "ticker": "A",
            "valid_lookback_sessions": 20,
            "median_daily_dollar_volume_20d": 30_000_000,
        },
        {
            "prediction_date": "2020-02-28",
            "security_id": "security-2",
            "ticker": "B",
            "valid_lookback_sessions": 19,
            "median_daily_dollar_volume_20d": 8_000_000,
        },
    ]

    result = summarize_liquidity_rows(
        rows, thresholds_usd=(5_000_000, 10_000_000, 25_000_000)
    )

    assert result["coverage"] == 0.5
    assert result["minimum_median_daily_dollar_volume_20d"] == 8_000_000
    checks = {row["threshold_usd"]: row for row in result["threshold_checks"]}
    assert checks[5_000_000]["pass_rate"] == 1.0
    assert checks[10_000_000]["pass_rate"] == 0.5
    assert checks[25_000_000]["pass_rate"] == 0.5
    assert result["market_impact_or_capacity_established"] is False


def test_duplicate_date_security_rows_are_rejected():
    row = _row(month=1, index=0, score=1, excess="0.01")
    with pytest.raises(ValueError, match="duplicate"):
        build_portfolio_periods((row, row))
