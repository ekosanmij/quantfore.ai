from datetime import date
from decimal import Decimal

from quantfore_research.backtest.baseline import BacktestObservation
from quantfore_research.validation.plausibility import (
    FeatureReviewValue,
    analyze_plausibility,
)


def test_plausibility_review_flags_non_monotonic_unstable_sample():
    tickers = ("AAPL", "MSFT", "XOM", "JPM", "NEE")
    sectors = {
        "AAPL": "Technology",
        "MSFT": "Technology",
        "XOM": "Energy",
        "JPM": "Financials",
        "NEE": "Utilities",
    }
    observations = []
    features = []
    for month in range(1, 7):
        day = date(2024, month, 28)
        for index, ticker in enumerate(tickers):
            observations.append(
                BacktestObservation(
                    ticker=ticker,
                    prediction_date=day,
                    score=Decimal(20 + index * 15),
                    action_label="neutral",
                    excess_return=Decimal("0.05") if index in {0, 4} else Decimal("-0.01"),
                )
            )
            for name, value in (
                ("momentum_6_1", "0.10"),
                ("momentum_12_1", "0.20"),
                ("return_21d", "0.01"),
                ("volatility_126d", "0.02"),
            ):
                features.append(
                    FeatureReviewValue(
                        ticker=ticker,
                        asof_date=day,
                        feature_name=name,
                        value=Decimal(value),
                    )
                )

    review = analyze_plausibility(
        observations=observations,
        features=features,
        sectors=sectors,
        price_review_tickers=["AAPL"],
        mega_cap_proxy=["AAPL", "MSFT"],
    )

    assert review["decision"] == "requires_revision_before_model_claims"
    assert review["claims_eligible"] is False
    assert review["feature_diagnostics"]["missing_values"] == 0
    assert review["feature_diagnostics"]["received_values"] == 120
    assert review["price_review_exclusion"]["tickers"] == ["AAPL"]
    assert review["concern_flags"]["non_monotonic"] is True
