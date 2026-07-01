import csv
import hashlib
import statistics
import subprocess
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path

from quantfore_research.backtest import BACKTEST_CONTRACT


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "generate_synthetic_backtest_data.py"
DATASET_PATH = REPO_ROOT / "data" / "sample" / "synthetic_backtest_prices.csv"
EXPECTED_WARNING = (
    "SYNTHETIC ENGINEERING DATA ONLY - ALL PRICES AND VOLUMES ARE FICTIONAL "
    "- NOT VALIDATION EVIDENCE"
)


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as csv_file:
        return list(csv.DictReader(csv_file))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_checked_in_synthetic_panel_matches_the_backtest_contract():
    rows = read_rows(DATASET_PATH)
    rows_by_ticker: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        rows_by_ticker[row["ticker"]].append(row)

    assert len(rows) == 21_000
    assert tuple(rows_by_ticker) == BACKTEST_CONTRACT.price_panel_universe
    assert set(rows_by_ticker) == set(BACKTEST_CONTRACT.price_panel_universe)
    assert {len(ticker_rows) for ticker_rows in rows_by_ticker.values()} == {1_000}

    expected_dates = [row["date"] for row in rows_by_ticker["SPY"]]
    assert len(set(expected_dates)) == 1_000
    assert all(
        [row["date"] for row in ticker_rows] == expected_dates
        for ticker_rows in rows_by_ticker.values()
    )
    assert all(date.fromisoformat(value).weekday() < 5 for value in expected_dates)
    assert {row["synthetic_warning"] for row in rows} == {EXPECTED_WARNING}


def test_synthetic_panel_has_at_least_12_valid_monthly_prediction_dates():
    rows = read_rows(DATASET_PATH)
    spy_dates = [
        date.fromisoformat(row["date"])
        for row in rows
        if row["ticker"] == BACKTEST_CONTRACT.benchmark
    ]
    month_end_indexes: dict[tuple[int, int], int] = {}
    for index, value in enumerate(spy_dates):
        month_end_indexes[(value.year, value.month)] = index

    valid_prediction_dates = [
        spy_dates[index]
        for index in month_end_indexes.values()
        if index + 1 >= BACKTEST_CONTRACT.minimum_history_sessions
        and len(spy_dates) - index - 1 >= BACKTEST_CONTRACT.evaluation_sessions
    ]

    assert len(valid_prediction_dates) >= BACKTEST_CONTRACT.minimum_test_periods
    assert len(valid_prediction_dates) == 29


def test_synthetic_prices_are_valid_and_have_diverse_patterns():
    rows = read_rows(DATASET_PATH)
    closes: dict[str, list[float]] = defaultdict(list)
    patterns: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        open_price = Decimal(row["open"])
        high = Decimal(row["high"])
        low = Decimal(row["low"])
        close = Decimal(row["close"])
        assert low > 0
        assert low <= min(open_price, close) <= max(open_price, close) <= high
        assert close == Decimal(row["adj_close"])
        assert int(row["volume"]) > 0
        closes[row["ticker"]].append(float(close))
        patterns[row["ticker"]].add(row["synthetic_pattern"])

    security_returns = {}
    security_volatility = {}
    security_drawdowns = {}
    for ticker in BACKTEST_CONTRACT.ranked_universe:
        prices = closes[ticker]
        daily_returns = [
            (prices[index] / prices[index - 1]) - 1.0
            for index in range(1, len(prices))
        ]
        running_peak = prices[0]
        drawdowns = []
        for price in prices:
            running_peak = max(running_peak, price)
            drawdowns.append((price / running_peak) - 1.0)
        security_returns[ticker] = (prices[-1] / prices[0]) - 1.0
        security_volatility[ticker] = statistics.stdev(daily_returns)
        security_drawdowns[ticker] = min(drawdowns)

    assert len({next(iter(patterns[ticker])) for ticker in patterns}) == 5
    assert len({round(value, 2) for value in security_returns.values()}) >= 12
    assert max(security_returns.values()) - min(security_returns.values()) > 0.50
    assert max(security_volatility.values()) > min(security_volatility.values()) * 2
    assert min(security_drawdowns.values()) < -0.25
    assert max(security_drawdowns.values()) - min(security_drawdowns.values()) > 0.15


def test_generator_is_repeatable_and_matches_the_checked_in_dataset(tmp_path):
    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    hashes = []
    for output_path in (first_path, second_path):
        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "--output", str(output_path)],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert "seed=20250302" in result.stdout
        assert "SYNTHETIC ENGINEERING DATA - NOT VALIDATION EVIDENCE" in result.stdout
        hashes.append(sha256(output_path))

    assert hashes[0] == hashes[1] == sha256(DATASET_PATH)
