"""Pure adjusted-close outcome calculations.

The entry observation is the first trading session after the prediction date.
The exit observation is ``horizon`` trading intervals after entry, so an
evaluation needs ``horizon + 1`` aligned security and benchmark observations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping


SUPPORTED_HORIZONS = ("21d", "63d", "126d", "252d")


class NotEnoughFuturePrices(ValueError):
    """Raised when a prediction has not accumulated enough future prices."""


@dataclass(frozen=True)
class PricePoint:
    """An adjusted-close observation for one trading session."""

    date: date
    adj_close: Decimal


@dataclass(frozen=True)
class OutcomeResult:
    """Forward security outcome and its aligned benchmark comparison."""

    entry_date: date
    exit_date: date
    security_entry_price: Decimal
    security_exit_price: Decimal
    benchmark_entry_price: Decimal
    benchmark_exit_price: Decimal
    realised_return: Decimal
    benchmark_return: Decimal
    excess_return: Decimal
    max_drawdown: Decimal

    @property
    def security_return(self) -> Decimal:
        """Alias describing ``realised_return`` in formula terminology."""

        return self.realised_return


def parse_horizon(horizon: str) -> int:
    """Return the number of trading intervals in a supported horizon."""

    if not isinstance(horizon, str) or horizon not in SUPPORTED_HORIZONS:
        supported = ", ".join(SUPPORTED_HORIZONS)
        raise ValueError(f"unsupported horizon {horizon!r}; expected one of: {supported}")
    return int(horizon[:-1])


def _price_date(value: object, *, series_name: str) -> date:
    try:
        raw_date = value["date"] if isinstance(value, Mapping) else value.date
    except (AttributeError, KeyError) as exc:
        raise ValueError(f"{series_name} price point must include a date") from exc
    if not isinstance(raw_date, date):
        raise ValueError(f"{series_name} price point date must be a date")
    return raw_date


def _price_adj_close(value: object, *, series_name: str) -> Decimal:
    try:
        raw_price = value["adj_close"] if isinstance(value, Mapping) else value.adj_close
    except (AttributeError, KeyError) as exc:
        raise ValueError(
            f"{series_name} price point must include an adjusted close"
        ) from exc

    try:
        price = raw_price if isinstance(raw_price, Decimal) else Decimal(str(raw_price))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{series_name} adjusted close must be numeric") from exc
    if not price.is_finite() or price <= 0:
        raise ValueError(f"{series_name} adjusted close must be positive and finite")
    return price


def _normalize_price_points(
    price_points: Iterable[object],
    *,
    series_name: str,
) -> list[PricePoint]:
    try:
        # A PricePoint that already satisfies every conversion-time check is
        # reused as-is; reconstruction would produce an equal object, and any
        # point failing the checks takes the original path so the exact same
        # errors are raised.
        points = [
            point
            if (
                type(point) is PricePoint
                and isinstance(point.date, date)
                and isinstance(point.adj_close, Decimal)
                and point.adj_close.is_finite()
                and point.adj_close > 0
            )
            else PricePoint(
                date=_price_date(point, series_name=series_name),
                adj_close=_price_adj_close(point, series_name=series_name),
            )
            for point in price_points
        ]
    except TypeError as exc:
        raise ValueError(f"{series_name} prices must be iterable") from exc

    seen_dates: set[date] = set()
    for point in points:
        if point.date in seen_dates:
            raise ValueError(f"{series_name} prices contain duplicate date {point.date}")
        seen_dates.add(point.date)
    return sorted(points, key=lambda point: point.date)


def _simple_return(exit_price: Decimal, entry_price: Decimal) -> Decimal:
    return (exit_price / entry_price) - Decimal("1")


def calculate_max_drawdown(price_points: Iterable[object]) -> Decimal:
    """Calculate the worst peak-to-trough adjusted-close return.

    The result is zero for a path that never declines and otherwise negative.
    """

    prices = _normalize_price_points(price_points, series_name="security")
    if not prices:
        raise ValueError("cannot calculate maximum drawdown without security prices")

    peak = prices[0].adj_close
    maximum_drawdown = Decimal("0")
    for point in prices[1:]:
        peak = max(peak, point.adj_close)
        drawdown = _simple_return(point.adj_close, peak)
        maximum_drawdown = min(maximum_drawdown, drawdown)
    return maximum_drawdown


def calculate_forward_outcome(
    security_prices: Iterable[object],
    benchmark_prices: Iterable[object],
    *,
    prediction_date: date,
    horizon: str,
) -> OutcomeResult:
    """Calculate a forward outcome using aligned adjusted-close observations."""

    if not isinstance(prediction_date, date):
        raise ValueError("prediction_date must be a date")
    trading_intervals = parse_horizon(horizon)
    required_observations = trading_intervals + 1

    security = _normalize_price_points(security_prices, series_name="security")
    benchmark = _normalize_price_points(benchmark_prices, series_name="benchmark")
    future_security = [point for point in security if point.date > prediction_date]
    future_benchmark = [point for point in benchmark if point.date > prediction_date]

    if len(future_security) < required_observations:
        raise NotEnoughFuturePrices(
            f"{horizon} evaluation requires {required_observations} security "
            f"observations after {prediction_date}; found {len(future_security)}"
        )

    evaluation_security = future_security[:required_observations]
    evaluation_dates = [point.date for point in evaluation_security]
    benchmark_by_date = {point.date: point for point in future_benchmark}
    missing_dates = [value for value in evaluation_dates if value not in benchmark_by_date]
    if missing_dates:
        missing = ", ".join(str(value) for value in missing_dates)
        raise ValueError(f"benchmark prices missing evaluation dates: {missing}")

    if future_benchmark and future_benchmark[0].date != evaluation_dates[0]:
        raise ValueError(
            "security and benchmark prices have mismatched evaluation dates"
        )

    benchmark_dates_in_window = [
        point.date
        for point in future_benchmark
        if evaluation_dates[0] <= point.date <= evaluation_dates[-1]
    ]
    if benchmark_dates_in_window != evaluation_dates:
        raise ValueError(
            "security and benchmark prices have mismatched evaluation dates"
        )

    evaluation_benchmark = [benchmark_by_date[value] for value in evaluation_dates]
    security_entry = evaluation_security[0]
    security_exit = evaluation_security[-1]
    benchmark_entry = evaluation_benchmark[0]
    benchmark_exit = evaluation_benchmark[-1]
    realised_return = _simple_return(
        security_exit.adj_close,
        security_entry.adj_close,
    )
    benchmark_return = _simple_return(
        benchmark_exit.adj_close,
        benchmark_entry.adj_close,
    )

    return OutcomeResult(
        entry_date=security_entry.date,
        exit_date=security_exit.date,
        security_entry_price=security_entry.adj_close,
        security_exit_price=security_exit.adj_close,
        benchmark_entry_price=benchmark_entry.adj_close,
        benchmark_exit_price=benchmark_exit.adj_close,
        realised_return=realised_return,
        benchmark_return=benchmark_return,
        excess_return=realised_return - benchmark_return,
        max_drawdown=calculate_max_drawdown(evaluation_security),
    )
