from datetime import date, timedelta
from decimal import Decimal

import pytest

from quantfore_research.features import (
    FEATURE_NAMES,
    NotEnoughPriceHistory,
    PricePoint,
    calculate_baseline_price_features,
)


ASOF_DATE = date(2026, 6, 24)


def make_price_points(count: int, *, start_price: Decimal = Decimal("100")):
    start_date = ASOF_DATE - timedelta(days=count - 1)
    return [
        PricePoint(
            date=start_date + timedelta(days=index),
            adj_close=start_price + Decimal(index),
        )
        for index in range(count)
    ]


def test_baseline_feature_calculator_returns_expected_feature_names():
    features = calculate_baseline_price_features(
        make_price_points(253),
        asof_date=ASOF_DATE,
    )

    assert set(features) == set(FEATURE_NAMES)


def test_momentum_6_1_calculation_uses_adjusted_close_lookbacks():
    points = make_price_points(253)
    features = calculate_baseline_price_features(points, asof_date=ASOF_DATE)

    expected = (points[-22].adj_close / points[-127].adj_close) - Decimal("1")

    assert features["momentum_6_1"] == expected


def test_momentum_12_1_calculation_uses_adjusted_close_lookbacks():
    points = make_price_points(253)
    features = calculate_baseline_price_features(points, asof_date=ASOF_DATE)

    expected = (points[-22].adj_close / points[-253].adj_close) - Decimal("1")

    assert features["momentum_12_1"] == expected


def test_baseline_feature_calculator_ignores_prices_after_asof_date():
    points = make_price_points(253)
    future_points = points + [
        PricePoint(date=ASOF_DATE + timedelta(days=1), adj_close=Decimal("9999")),
        PricePoint(date=ASOF_DATE + timedelta(days=2), adj_close=Decimal("12000")),
    ]

    without_future = calculate_baseline_price_features(points, asof_date=ASOF_DATE)
    with_future = calculate_baseline_price_features(future_points, asof_date=ASOF_DATE)

    assert with_future == without_future


def test_baseline_feature_calculator_refuses_insufficient_price_history():
    with pytest.raises(NotEnoughPriceHistory, match="253"):
        calculate_baseline_price_features(make_price_points(252), asof_date=ASOF_DATE)
