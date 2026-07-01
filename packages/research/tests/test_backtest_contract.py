from dataclasses import FrozenInstanceError
from datetime import date, timedelta

import pytest

from quantfore_research.backtest import (
    BACKTEST_CONTRACT,
    SYNTHETIC_SECURITY_TICKERS,
)


PREDICTION_DATE = date(2025, 1, 31)


def session_dates(count: int, *, end: date = PREDICTION_DATE) -> list[date]:
    return [end - timedelta(days=index) for index in reversed(range(count))]


def test_sprint_5_backtest_contract_has_the_approved_configuration():
    contract = BACKTEST_CONTRACT

    assert SYNTHETIC_SECURITY_TICKERS == tuple(
        f"QF{index:02d}" for index in range(1, 21)
    )
    assert contract.ranked_universe == SYNTHETIC_SECURITY_TICKERS
    assert contract.price_panel_universe == (*SYNTHETIC_SECURITY_TICKERS, "SPY")
    assert contract.benchmark == "SPY"
    assert contract.frequency == "monthly"
    assert contract.rebalance_session == "final_available_session_of_month"
    assert contract.minimum_history_sessions == 253
    assert contract.evaluation_sessions == 127
    assert contract.horizon == "126d"
    assert contract.model_version == "baseline_v0.1"
    assert contract.minimum_test_periods == 12
    assert contract.deterministic is True


def test_benchmark_is_in_price_panel_but_excluded_from_ranked_universe():
    assert "SPY" in BACKTEST_CONTRACT.price_panel_universe
    assert "SPY" not in BACKTEST_CONTRACT.ranked_universe
    assert len(BACKTEST_CONTRACT.ranked_universe) == 20
    assert len(BACKTEST_CONTRACT.price_panel_universe) == 21


def test_contract_is_immutable_and_has_a_deterministic_fingerprint():
    with pytest.raises(FrozenInstanceError):
        BACKTEST_CONTRACT.horizon = "21d"

    assert BACKTEST_CONTRACT.sha256() == (
        "0399a067104b228dc3542513be000d358670d5c2b618c18c7bc6f2f14700f03e"
    )


def test_feature_boundary_accepts_exactly_253_sessions_through_prediction_date():
    dates = session_dates(253)

    assert BACKTEST_CONTRACT.validate_feature_dates(
        reversed(dates), prediction_date=PREDICTION_DATE
    ) == tuple(dates)


def test_feature_boundary_rejects_insufficient_or_future_prices():
    with pytest.raises(ValueError, match="requires at least 253.*found 252"):
        BACKTEST_CONTRACT.validate_feature_dates(
            session_dates(252), prediction_date=PREDICTION_DATE
        )

    with pytest.raises(ValueError, match="on or before"):
        BACKTEST_CONTRACT.validate_feature_dates(
            [*session_dates(253), PREDICTION_DATE + timedelta(days=1)],
            prediction_date=PREDICTION_DATE,
        )


def test_outcome_boundary_accepts_exactly_127_post_prediction_sessions():
    dates = [PREDICTION_DATE + timedelta(days=index) for index in range(1, 128)]

    assert BACKTEST_CONTRACT.validate_outcome_dates(
        reversed(dates), prediction_date=PREDICTION_DATE
    ) == tuple(dates)


def test_outcome_boundary_rejects_insufficient_or_nonfuture_prices():
    dates = [PREDICTION_DATE + timedelta(days=index) for index in range(1, 127)]
    with pytest.raises(ValueError, match="requires at least 127.*found 126"):
        BACKTEST_CONTRACT.validate_outcome_dates(
            dates, prediction_date=PREDICTION_DATE
        )

    with pytest.raises(ValueError, match="dated after"):
        BACKTEST_CONTRACT.validate_outcome_dates(
            [PREDICTION_DATE, *dates], prediction_date=PREDICTION_DATE
        )
