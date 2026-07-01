from datetime import date, timedelta
from decimal import Decimal

import pytest

from quantfore_research.evaluation import (
    SUPPORTED_HORIZONS,
    NotEnoughFuturePrices,
    PricePoint,
    calculate_forward_outcome,
    calculate_max_drawdown,
    parse_horizon,
)


PREDICTION_DATE = date(2025, 1, 3)  # Friday


def weekday_dates(start: date, count: int) -> list[date]:
    dates: list[date] = []
    candidate = start
    while len(dates) < count:
        if candidate.weekday() < 5:
            dates.append(candidate)
        candidate += timedelta(days=1)
    return dates


def make_prices(
    count: int,
    *,
    start_price: Decimal = Decimal("100"),
    daily_change: Decimal = Decimal("1"),
) -> list[PricePoint]:
    return [
        PricePoint(
            date=price_date,
            adj_close=start_price + daily_change * Decimal(index),
        )
        for index, price_date in enumerate(
            weekday_dates(PREDICTION_DATE + timedelta(days=1), count)
        )
    ]


@pytest.mark.parametrize(
    ("horizon", "intervals"),
    [("21d", 21), ("63d", 63), ("126d", 126), ("252d", 252)],
)
def test_parse_horizon_accepts_only_supported_horizons(horizon, intervals):
    assert horizon in SUPPORTED_HORIZONS
    assert parse_horizon(horizon) == intervals


@pytest.mark.parametrize("horizon", ["", "20d", "126", "126D", 126, None])
def test_parse_horizon_rejects_unsupported_values(horizon):
    with pytest.raises(ValueError, match="unsupported horizon"):
        parse_horizon(horizon)


def test_outcome_sorts_prices_and_uses_first_session_after_prediction():
    security = make_prices(127)
    benchmark = make_prices(
        127,
        start_price=Decimal("200"),
        daily_change=Decimal("0.5"),
    )
    on_prediction_date = PricePoint(PREDICTION_DATE, Decimal("9999"))
    before_prediction = PricePoint(PREDICTION_DATE - timedelta(days=1), Decimal("1"))

    result = calculate_forward_outcome(
        reversed([before_prediction, on_prediction_date, *security]),
        reversed([before_prediction, on_prediction_date, *benchmark]),
        prediction_date=PREDICTION_DATE,
        horizon="126d",
    )

    assert result.entry_date == date(2025, 1, 6)
    assert result.exit_date == security[126].date
    assert result.security_entry_price == Decimal("100")
    assert result.security_exit_price == Decimal("226")
    assert result.benchmark_entry_price == Decimal("200")
    assert result.benchmark_exit_price == Decimal("263.0")


def test_outcome_uses_126_intervals_and_exact_decimal_return_formulas():
    security = make_prices(127)
    benchmark = make_prices(
        127,
        start_price=Decimal("200"),
        daily_change=Decimal("0.5"),
    )

    result = calculate_forward_outcome(
        security,
        benchmark,
        prediction_date=PREDICTION_DATE,
        horizon="126d",
    )

    expected_security = Decimal("226") / Decimal("100") - Decimal("1")
    expected_benchmark = Decimal("263") / Decimal("200") - Decimal("1")
    assert result.realised_return == expected_security
    assert result.security_return == expected_security
    assert result.benchmark_return == expected_benchmark
    assert result.excess_return == expected_security - expected_benchmark


def test_outcome_calculates_security_maximum_drawdown_over_evaluation_path():
    security = make_prices(127, daily_change=Decimal("0"))
    security[1] = PricePoint(security[1].date, Decimal("120"))
    security[2] = PricePoint(security[2].date, Decimal("90"))
    security[3] = PricePoint(security[3].date, Decimal("130"))
    security[4] = PricePoint(security[4].date, Decimal("104"))

    result = calculate_forward_outcome(
        security,
        make_prices(127, start_price=Decimal("200"), daily_change=Decimal("0")),
        prediction_date=PREDICTION_DATE,
        horizon="126d",
    )

    assert result.max_drawdown == Decimal("90") / Decimal("120") - Decimal("1")


def test_max_drawdown_is_zero_when_prices_never_decline():
    assert calculate_max_drawdown(make_prices(3)) == Decimal("0")


def test_max_drawdown_sorts_prices_chronologically():
    points = [
        PricePoint(date(2025, 1, 8), Decimal("75")),
        PricePoint(date(2025, 1, 6), Decimal("100")),
        PricePoint(date(2025, 1, 7), Decimal("120")),
    ]

    assert calculate_max_drawdown(points) == Decimal("75") / Decimal("120") - Decimal("1")


def test_outcome_refuses_insufficient_future_prices():
    with pytest.raises(NotEnoughFuturePrices, match="requires 127.*found 126"):
        calculate_forward_outcome(
            make_prices(126),
            make_prices(126, start_price=Decimal("200")),
            prediction_date=PREDICTION_DATE,
            horizon="126d",
        )


@pytest.mark.parametrize("series", ["security", "benchmark"])
@pytest.mark.parametrize("invalid_price", [Decimal("0"), Decimal("-1"), Decimal("NaN")])
def test_outcome_requires_positive_finite_adjusted_closes(series, invalid_price):
    security = make_prices(127)
    benchmark = make_prices(127, start_price=Decimal("200"))
    prices = security if series == "security" else benchmark
    prices[10] = PricePoint(prices[10].date, invalid_price)

    with pytest.raises(ValueError, match=f"{series} adjusted close must be positive and finite"):
        calculate_forward_outcome(
            security,
            benchmark,
            prediction_date=PREDICTION_DATE,
            horizon="126d",
        )


@pytest.mark.parametrize("series", ["security", "benchmark"])
def test_outcome_rejects_duplicate_dates(series):
    security = make_prices(127)
    benchmark = make_prices(127, start_price=Decimal("200"))
    prices = security if series == "security" else benchmark
    prices.append(PricePoint(prices[10].date, Decimal("500")))

    with pytest.raises(ValueError, match=f"{series} prices contain duplicate date"):
        calculate_forward_outcome(
            security,
            benchmark,
            prediction_date=PREDICTION_DATE,
            horizon="126d",
        )


def test_outcome_rejects_a_missing_benchmark_evaluation_date():
    security = make_prices(127)
    benchmark = make_prices(127, start_price=Decimal("200"))
    missing_date = benchmark[50].date
    del benchmark[50]

    with pytest.raises(ValueError, match=f"benchmark prices missing evaluation dates: {missing_date}"):
        calculate_forward_outcome(
            security,
            benchmark,
            prediction_date=PREDICTION_DATE,
            horizon="126d",
        )


def test_outcome_rejects_mismatched_trading_dates():
    security = make_prices(127)
    benchmark = make_prices(127, start_price=Decimal("200"))
    extra_date = PREDICTION_DATE + timedelta(days=1)  # weekend
    benchmark.insert(0, PricePoint(extra_date, Decimal("199")))

    with pytest.raises(ValueError, match="mismatched evaluation dates"):
        calculate_forward_outcome(
            security,
            benchmark,
            prediction_date=PREDICTION_DATE,
            horizon="126d",
        )


def test_max_drawdown_rejects_an_empty_price_path():
    with pytest.raises(ValueError, match="without security prices"):
        calculate_max_drawdown([])
