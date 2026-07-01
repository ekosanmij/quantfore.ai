import csv
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from quantfore_research.evaluation import PricePoint, calculate_forward_outcome


SAMPLE_PATH = (
    Path(__file__).parents[3]
    / "data"
    / "sample"
    / "msft_spy_outcome_prices.csv"
)
PREDICTION_DATE = date(2025, 12, 26)
SYNTHETIC_WARNING = (
    "SYNTHETIC DATA ONLY - ALL PRICES AND VOLUMES ARE FICTIONAL"
)


def test_outcome_sample_has_aligned_history_and_predictable_outcome():
    with SAMPLE_PATH.open(newline="", encoding="utf-8") as sample_file:
        rows = list(csv.DictReader(sample_file))

    assert len(rows) == 762
    assert {row["ticker"] for row in rows} == {"MSFT", "SPY"}
    assert {row["synthetic_warning"] for row in rows} == {SYNTHETIC_WARNING}

    marked_rows = [
        row for row in rows if row["prediction_date_marker"] == "TEST_PREDICTION_DATE"
    ]
    assert len(marked_rows) == 2
    assert {row["ticker"] for row in marked_rows} == {"MSFT", "SPY"}
    assert {date.fromisoformat(row["date"]) for row in marked_rows} == {
        PREDICTION_DATE
    }

    prices: dict[str, list[PricePoint]] = defaultdict(list)
    for row in rows:
        prices[row["ticker"]].append(
            PricePoint(
                date=date.fromisoformat(row["date"]),
                adj_close=Decimal(row["adj_close"]),
            )
        )

    msft_dates = [point.date for point in prices["MSFT"]]
    spy_dates = [point.date for point in prices["SPY"]]
    assert msft_dates == spy_dates
    assert len([value for value in msft_dates if value < PREDICTION_DATE]) == 253
    assert PREDICTION_DATE in msft_dates
    assert len([value for value in msft_dates if value > PREDICTION_DATE]) == 127
    assert PREDICTION_DATE != msft_dates[-1]

    outcome = calculate_forward_outcome(
        prices["MSFT"],
        prices["SPY"],
        prediction_date=PREDICTION_DATE,
        horizon="126d",
    )

    assert outcome.entry_date == date(2025, 12, 29)
    assert outcome.exit_date == date(2026, 6, 23)
    assert outcome.security_entry_price == Decimal("100.000000")
    assert outcome.security_exit_price == Decimal("112.000000")
    assert outcome.benchmark_entry_price == Decimal("200.000000")
    assert outcome.benchmark_exit_price == Decimal("214.000000")
    assert outcome.realised_return == Decimal("0.12")
    assert outcome.benchmark_return == Decimal("0.07")
    assert outcome.excess_return == Decimal("0.05")
    assert outcome.max_drawdown == Decimal("-0.20")
