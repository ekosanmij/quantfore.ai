"""Fixed contract for the Sprint 5 synthetic baseline backtest.

This module is the single source of truth for the backtest inputs and temporal
boundaries.  Later backtest work packages should consume ``BACKTEST_CONTRACT``
instead of repeating these values in pipelines or reports.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable

from quantfore_research.evaluation import parse_horizon
from quantfore_research.scoring import BASELINE_MODEL_VERSION


SYNTHETIC_SECURITY_TICKERS = tuple(f"QF{index:02d}" for index in range(1, 21))


@dataclass(frozen=True)
class BacktestContract:
    """Reproducible configuration and data-boundary rules for a backtest."""

    securities: tuple[str, ...]
    benchmark: str
    frequency: str
    rebalance_session: str
    minimum_history_sessions: int
    evaluation_sessions: int
    horizon: str
    model_version: str
    minimum_test_periods: int
    deterministic: bool

    def __post_init__(self) -> None:
        if not self.securities:
            raise ValueError("backtest securities cannot be empty")
        if len(self.securities) != len(set(self.securities)):
            raise ValueError("backtest securities must be unique")
        if self.benchmark in self.securities:
            raise ValueError("benchmark must be excluded from ranked securities")
        if self.frequency != "monthly":
            raise ValueError("Sprint 5 backtest frequency must be monthly")
        if self.rebalance_session != "final_available_session_of_month":
            raise ValueError(
                "Sprint 5 must rebalance on the final available session of each month"
            )
        if self.minimum_history_sessions < 1:
            raise ValueError("minimum history sessions must be positive")
        if self.evaluation_sessions != parse_horizon(self.horizon) + 1:
            raise ValueError(
                "evaluation sessions must equal the horizon intervals plus one"
            )
        if self.minimum_test_periods < 1:
            raise ValueError("minimum test periods must be positive")
        if not self.deterministic:
            raise ValueError("Sprint 5 backtest results must be deterministic")

    @property
    def ranked_universe(self) -> tuple[str, ...]:
        """Securities scored and ranked cross-sectionally."""

        return self.securities

    @property
    def price_panel_universe(self) -> tuple[str, ...]:
        """All required price series, including the unranked benchmark."""

        return (*self.securities, self.benchmark)

    def to_dict(self) -> dict[str, object]:
        """Return a stable, JSON-compatible representation of the contract."""

        values = asdict(self)
        values["securities"] = list(self.securities)
        return values

    def canonical_json(self) -> str:
        """Serialize the contract deterministically for lineage and reporting."""

        return json.dumps(
            self.to_dict(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )

    def sha256(self) -> str:
        """Return a deterministic fingerprint of the complete contract."""

        return hashlib.sha256(self.canonical_json().encode("utf-8")).hexdigest()

    def validate_feature_dates(
        self,
        price_dates: Iterable[date],
        *,
        prediction_date: date,
    ) -> tuple[date, ...]:
        """Validate that a feature input has enough history and no future dates."""

        dates = _validated_session_dates(price_dates, series_name="feature")
        future_dates = [value for value in dates if value > prediction_date]
        if future_dates:
            raise ValueError(
                "feature prices must be dated on or before the prediction date"
            )
        if len(dates) < self.minimum_history_sessions:
            raise ValueError(
                "feature calculation requires at least "
                f"{self.minimum_history_sessions} sessions on or before "
                f"{prediction_date}; found {len(dates)}"
            )
        return dates

    def validate_outcome_dates(
        self,
        price_dates: Iterable[date],
        *,
        prediction_date: date,
    ) -> tuple[date, ...]:
        """Validate that an outcome input is wholly after the prediction date."""

        dates = _validated_session_dates(price_dates, series_name="outcome")
        invalid_dates = [value for value in dates if value <= prediction_date]
        if invalid_dates:
            raise ValueError("outcome prices must be dated after the prediction date")
        if len(dates) < self.evaluation_sessions:
            raise ValueError(
                f"{self.horizon} evaluation requires at least "
                f"{self.evaluation_sessions} sessions after {prediction_date}; "
                f"found {len(dates)}"
            )
        return dates


def _validated_session_dates(
    values: Iterable[date],
    *,
    series_name: str,
) -> tuple[date, ...]:
    try:
        dates = tuple(values)
    except TypeError as exc:
        raise ValueError(f"{series_name} price dates must be iterable") from exc
    if any(not isinstance(value, date) for value in dates):
        raise ValueError(f"{series_name} price dates must be date values")
    if len(dates) != len(set(dates)):
        raise ValueError(f"{series_name} price dates must be unique")
    return tuple(sorted(dates))


BACKTEST_CONTRACT = BacktestContract(
    securities=SYNTHETIC_SECURITY_TICKERS,
    benchmark="SPY",
    frequency="monthly",
    rebalance_session="final_available_session_of_month",
    minimum_history_sessions=253,
    evaluation_sessions=127,
    horizon="126d",
    model_version=BASELINE_MODEL_VERSION,
    minimum_test_periods=12,
    deterministic=True,
)
