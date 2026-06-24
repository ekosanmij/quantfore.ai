"""Baseline price feature calculations for Sprint 2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, getcontext
from typing import Iterable, Mapping


getcontext().prec = 28


FEATURE_VERSION = "v0.1"
FEATURE_NAMES = (
    "momentum_6_1",
    "momentum_12_1",
    "return_21d",
    "volatility_126d",
)


class NotEnoughPriceHistory(ValueError):
    """Raised when the baseline feature set cannot be calculated."""


@dataclass(frozen=True)
class PricePoint:
    date: date
    adj_close: Decimal


def _to_decimal(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _price_date(value) -> date:
    if isinstance(value, Mapping):
        return value["date"]
    return value.date


def _price_adj_close(value) -> Decimal:
    if isinstance(value, Mapping):
        return _to_decimal(value["adj_close"])
    return _to_decimal(value.adj_close)


def normalize_price_points(price_points: Iterable[object]) -> list[PricePoint]:
    points = [
        PricePoint(date=_price_date(point), adj_close=_price_adj_close(point))
        for point in price_points
    ]
    return sorted(points, key=lambda point: point.date)


def _simple_return(end_price: Decimal, start_price: Decimal) -> Decimal:
    if start_price == 0:
        raise ValueError("cannot calculate return from a zero adjusted close")
    return (end_price / start_price) - Decimal("1")


def _sample_standard_deviation(values: list[Decimal]) -> Decimal:
    if len(values) < 2:
        raise NotEnoughPriceHistory("volatility_126d requires at least two returns")
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
    return variance.sqrt()


def calculate_baseline_price_features(
    price_points: Iterable[object],
    *,
    asof_date: date,
) -> dict[str, Decimal]:
    """Calculate baseline adjusted-close price features.

    `t` is the latest price observation on or before `asof_date`.

    - momentum_6_1 = adj_close[t-21] / adj_close[t-126] - 1
    - momentum_12_1 = adj_close[t-21] / adj_close[t-252] - 1
    - return_21d = adj_close[t] / adj_close[t-21] - 1
    - volatility_126d = sample standard deviation of the latest 126 daily returns
    """

    points = [
        point
        for point in normalize_price_points(price_points)
        if point.date <= asof_date
    ]

    minimum_observations = 253
    if len(points) < minimum_observations:
        raise NotEnoughPriceHistory(
            "baseline price features require at least "
            f"{minimum_observations} adjusted-close observations on or before {asof_date}"
        )

    t = points[-1]
    t_minus_21 = points[-22]
    t_minus_126 = points[-127]
    t_minus_252 = points[-253]
    recent_returns = [
        _simple_return(points[index].adj_close, points[index - 1].adj_close)
        for index in range(len(points) - 126, len(points))
    ]

    return {
        "momentum_6_1": _simple_return(t_minus_21.adj_close, t_minus_126.adj_close),
        "momentum_12_1": _simple_return(t_minus_21.adj_close, t_minus_252.adj_close),
        "return_21d": _simple_return(t.adj_close, t_minus_21.adj_close),
        "volatility_126d": _sample_standard_deviation(recent_returns),
    }
