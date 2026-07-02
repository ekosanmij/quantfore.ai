"""Deterministic data-quality checks for daily US equity price panels."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Mapping, Optional, Sequence

import pandas_market_calendars


DEFAULT_CALENDAR = "XNYS"


@dataclass(frozen=True)
class PriceQualityConfig:
    minimum_history_sessions: int = 1250
    stale_run_sessions: int = 5
    extreme_return_threshold: Decimal = Decimal("0.30")
    split_raw_return_threshold: Decimal = Decimal("0.35")
    split_adjusted_return_tolerance: Decimal = Decimal("0.10")

    def __post_init__(self) -> None:
        if self.minimum_history_sessions < 1:
            raise ValueError("minimum_history_sessions must be positive")
        if self.stale_run_sessions < 2:
            raise ValueError("stale_run_sessions must be at least 2")
        for name in (
            "extreme_return_threshold",
            "split_raw_return_threshold",
            "split_adjusted_return_tolerance",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class PriceObservation:
    ticker: str
    date: date
    open: Optional[Decimal]
    high: Optional[Decimal]
    low: Optional[Decimal]
    close: Optional[Decimal]
    adj_close: Optional[Decimal]
    volume: Optional[int]
    source_snapshot_id: str
    retrieved_at: datetime
    adj_open: Optional[Decimal] = None
    adj_high: Optional[Decimal] = None
    adj_low: Optional[Decimal] = None
    adj_volume: Optional[Decimal] = None
    cik: Optional[str] = None


@dataclass(frozen=True)
class ReturnFinding:
    prior_date: date
    date: date
    adjusted_return: Optional[Decimal]
    raw_return: Optional[Decimal]

    def to_dict(self) -> dict[str, object]:
        return {
            "prior_date": self.prior_date.isoformat(),
            "date": self.date.isoformat(),
            "adjusted_return": (
                str(self.adjusted_return)
                if self.adjusted_return is not None
                else None
            ),
            "raw_return": str(self.raw_return) if self.raw_return is not None else None,
        }


@dataclass(frozen=True)
class StaleRun:
    start_date: date
    end_date: date
    sessions: int
    adjusted_close: Decimal

    def to_dict(self) -> dict[str, object]:
        return {
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "sessions": self.sessions,
            "adjusted_close": str(self.adjusted_close),
        }


def _iso_dates(values: Sequence[date]) -> list[str]:
    return [value.isoformat() for value in values]


@dataclass(frozen=True)
class SecurityPriceAudit:
    ticker: str
    expected_session_count: int
    observed_row_count: int
    unique_date_count: int
    coverage_percentage: float
    duplicate_dates: tuple[date, ...]
    missing_sessions: tuple[date, ...]
    off_calendar_dates: tuple[date, ...]
    non_positive_price_dates: tuple[date, ...]
    invalid_ohlc_dates: tuple[date, ...]
    missing_adjusted_close_dates: tuple[date, ...]
    negative_volume_dates: tuple[date, ...]
    stale_runs: tuple[StaleRun, ...]
    extreme_returns: tuple[ReturnFinding, ...]
    split_like_discontinuities: tuple[ReturnFinding, ...]
    insufficient_history: bool
    dates_beyond_retrieval: tuple[date, ...]
    missing_vs_benchmark: tuple[date, ...]
    extra_vs_benchmark: tuple[date, ...]
    unexpected_tickers: tuple[str, ...]
    unexpected_ciks: tuple[str, ...]
    source_snapshot_ids: tuple[str, ...]
    status: str

    @property
    def issue_counts(self) -> dict[str, int]:
        return {
            "duplicate_dates": len(self.duplicate_dates),
            "missing_expected_sessions": len(self.missing_sessions),
            "off_calendar_dates": len(self.off_calendar_dates),
            "non_positive_prices": len(self.non_positive_price_dates),
            "invalid_ohlc_relationships": len(self.invalid_ohlc_dates),
            "missing_adjusted_closes": len(self.missing_adjusted_close_dates),
            "negative_volumes": len(self.negative_volume_dates),
            "stale_runs": len(self.stale_runs),
            "extreme_daily_returns": len(self.extreme_returns),
            "split_like_discontinuities": len(self.split_like_discontinuities),
            "insufficient_history": int(self.insufficient_history),
            "dates_beyond_retrieval": len(self.dates_beyond_retrieval),
            "missing_vs_benchmark": len(self.missing_vs_benchmark),
            "extra_vs_benchmark": len(self.extra_vs_benchmark),
            "unexpected_tickers": len(self.unexpected_tickers),
            "unexpected_ciks": len(self.unexpected_ciks),
        }

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "status": self.status,
            "expected_session_count": self.expected_session_count,
            "observed_row_count": self.observed_row_count,
            "unique_date_count": self.unique_date_count,
            "coverage_percentage": self.coverage_percentage,
            "issue_counts": self.issue_counts,
            "duplicate_dates": _iso_dates(self.duplicate_dates),
            "missing_sessions": _iso_dates(self.missing_sessions),
            "off_calendar_dates": _iso_dates(self.off_calendar_dates),
            "non_positive_price_dates": _iso_dates(
                self.non_positive_price_dates
            ),
            "invalid_ohlc_dates": _iso_dates(self.invalid_ohlc_dates),
            "missing_adjusted_close_dates": _iso_dates(
                self.missing_adjusted_close_dates
            ),
            "negative_volume_dates": _iso_dates(self.negative_volume_dates),
            "stale_runs": [run.to_dict() for run in self.stale_runs],
            "extreme_returns": [item.to_dict() for item in self.extreme_returns],
            "split_like_discontinuities": [
                item.to_dict() for item in self.split_like_discontinuities
            ],
            "insufficient_history": self.insufficient_history,
            "dates_beyond_retrieval": _iso_dates(self.dates_beyond_retrieval),
            "missing_vs_benchmark": _iso_dates(self.missing_vs_benchmark),
            "extra_vs_benchmark": _iso_dates(self.extra_vs_benchmark),
            "unexpected_tickers": list(self.unexpected_tickers),
            "unexpected_ciks": list(self.unexpected_ciks),
            "source_snapshot_ids": list(self.source_snapshot_ids),
        }


@dataclass(frozen=True)
class PricePanelAudit:
    calendar: str
    start_date: date
    end_date: date
    benchmark: str
    securities: tuple[SecurityPriceAudit, ...]
    unexpected_panel_tickers: tuple[str, ...]
    status: str

    @property
    def audit_passed(self) -> bool:
        return self.status == "pass"

    def to_dict(self) -> dict[str, object]:
        return {
            "calendar": self.calendar,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "benchmark": self.benchmark,
            "status": self.status,
            "audit_passed": self.audit_passed,
            "unexpected_panel_tickers": list(self.unexpected_panel_tickers),
            "security_count": len(self.securities),
            "securities": [audit.to_dict() for audit in self.securities],
        }


def exchange_sessions(
    start_date: date,
    end_date: date,
    *,
    calendar_name: str = DEFAULT_CALENDAR,
) -> tuple[date, ...]:
    """Return actual exchange sessions for the inclusive date range."""

    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    try:
        provider_name = "NYSE" if calendar_name == "XNYS" else calendar_name
        calendar = pandas_market_calendars.get_calendar(provider_name)
    except (KeyError, ValueError, RuntimeError) as exc:
        raise ValueError(f"unknown exchange calendar: {calendar_name}") from exc
    sessions = calendar.valid_days(
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
    )
    return tuple(session.date() for session in sessions)


def _utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _latest_by_date(
    observations: Sequence[PriceObservation],
) -> tuple[dict[date, PriceObservation], tuple[date, ...]]:
    grouped: dict[date, list[PriceObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.date, []).append(observation)
    duplicate_dates = tuple(
        sorted(day for day, rows in grouped.items() if len(rows) > 1)
    )
    selected = {
        day: max(
            rows,
            key=lambda row: (
                _utc_datetime(row.retrieved_at),
                row.source_snapshot_id,
            ),
        )
        for day, rows in grouped.items()
    }
    return selected, duplicate_dates


def _has_non_positive_price(observation: PriceObservation) -> bool:
    values = (
        observation.open,
        observation.high,
        observation.low,
        observation.close,
        observation.adj_open,
        observation.adj_high,
        observation.adj_low,
        observation.adj_close,
    )
    return any(value is not None and value <= 0 for value in values)


def _has_invalid_ohlc(observation: PriceObservation) -> bool:
    for values in (
        (
            observation.open,
            observation.high,
            observation.low,
            observation.close,
        ),
        (
            observation.adj_open,
            observation.adj_high,
            observation.adj_low,
            observation.adj_close,
        ),
    ):
        if any(value is None for value in values):
            continue
        open_price, high, low, close = values
        assert open_price is not None
        assert high is not None
        assert low is not None
        assert close is not None
        if not (
            low <= min(open_price, close) <= max(open_price, close) <= high
        ):
            return True
    return False


def _simple_return(current: Optional[Decimal], prior: Optional[Decimal]) -> Optional[Decimal]:
    if current is None or prior is None or current <= 0 or prior <= 0:
        return None
    return (current / prior) - Decimal("1")


def _return_findings(
    selected: Mapping[date, PriceObservation],
    session_positions: Mapping[date, int],
    config: PriceQualityConfig,
) -> tuple[tuple[ReturnFinding, ...], tuple[ReturnFinding, ...]]:
    extremes = []
    splits = []
    ordered = sorted(selected.values(), key=lambda row: row.date)
    for prior, current in zip(ordered, ordered[1:]):
        prior_position = session_positions.get(prior.date)
        current_position = session_positions.get(current.date)
        if (
            prior_position is None
            or current_position is None
            or current_position != prior_position + 1
        ):
            continue
        adjusted_return = _simple_return(current.adj_close, prior.adj_close)
        raw_return = _simple_return(current.close, prior.close)
        finding = ReturnFinding(
            prior_date=prior.date,
            date=current.date,
            adjusted_return=adjusted_return,
            raw_return=raw_return,
        )
        if (
            adjusted_return is not None
            and abs(adjusted_return) >= config.extreme_return_threshold
        ):
            extremes.append(finding)
        if (
            raw_return is not None
            and abs(raw_return) >= config.split_raw_return_threshold
            and (
                adjusted_return is None
                or abs(adjusted_return)
                <= config.split_adjusted_return_tolerance
            )
        ):
            splits.append(finding)
    return tuple(extremes), tuple(splits)


def _stale_runs(
    selected: Mapping[date, PriceObservation],
    session_positions: Mapping[date, int],
    minimum_sessions: int,
) -> tuple[StaleRun, ...]:
    runs = []
    ordered = sorted(selected.values(), key=lambda row: row.date)
    current_run: list[PriceObservation] = []

    def finish_run() -> None:
        if len(current_run) < minimum_sessions:
            return
        value = current_run[-1].adj_close
        assert value is not None
        runs.append(
            StaleRun(
                start_date=current_run[0].date,
                end_date=current_run[-1].date,
                sessions=len(current_run),
                adjusted_close=value,
            )
        )

    for observation in ordered:
        if observation.adj_close is None:
            finish_run()
            current_run = []
            continue
        if not current_run:
            current_run = [observation]
            continue
        prior = current_run[-1]
        consecutive = (
            prior.date in session_positions
            and observation.date in session_positions
            and session_positions[observation.date]
            == session_positions[prior.date] + 1
        )
        if consecutive and observation.adj_close == prior.adj_close:
            current_run.append(observation)
        else:
            finish_run()
            current_run = [observation]
    finish_run()
    return tuple(runs)


def audit_price_series(
    observations: Sequence[PriceObservation],
    *,
    expected_ticker: str,
    expected_cik: Optional[str],
    expected_sessions: Sequence[date],
    benchmark_dates: Optional[Sequence[date]] = None,
    config: PriceQualityConfig = PriceQualityConfig(),
) -> SecurityPriceAudit:
    """Audit one security, selecting the latest row only after flagging duplicates."""

    sessions = tuple(sorted(set(expected_sessions)))
    if not sessions:
        raise ValueError("expected_sessions must not be empty")
    session_set = set(sessions)
    session_positions = {day: index for index, day in enumerate(sessions)}
    selected, duplicate_dates = _latest_by_date(observations)
    selected_dates = set(selected)
    observed_session_dates = selected_dates.intersection(session_set)

    missing_sessions = tuple(sorted(session_set - selected_dates))
    off_calendar_dates = tuple(sorted(selected_dates - session_set))
    non_positive = tuple(
        sorted(day for day, row in selected.items() if _has_non_positive_price(row))
    )
    invalid_ohlc = tuple(
        sorted(day for day, row in selected.items() if _has_invalid_ohlc(row))
    )
    missing_adj_close = tuple(
        sorted(day for day, row in selected.items() if row.adj_close is None)
    )
    negative_volume = tuple(
        sorted(
            day
            for day, row in selected.items()
            if (row.volume is not None and row.volume < 0)
            or (row.adj_volume is not None and row.adj_volume < 0)
        )
    )
    beyond_retrieval = tuple(
        sorted(
            {
                row.date
                for row in observations
                if row.date > _utc_datetime(row.retrieved_at).date()
            }
        )
    )
    unexpected_tickers = tuple(
        sorted({row.ticker for row in observations if row.ticker != expected_ticker})
    )
    unexpected_ciks = tuple(
        sorted(
            {
                row.cik or "<missing>"
                for row in observations
                if expected_cik is not None and row.cik != expected_cik
            }
        )
    )
    extremes, splits = _return_findings(selected, session_positions, config)
    stale = _stale_runs(selected, session_positions, config.stale_run_sessions)

    if benchmark_dates is None or expected_ticker == "SPY":
        missing_vs_benchmark: tuple[date, ...] = ()
        extra_vs_benchmark: tuple[date, ...] = ()
    else:
        benchmark_set = set(benchmark_dates).intersection(session_set)
        missing_vs_benchmark = tuple(
            sorted(benchmark_set - observed_session_dates)
        )
        extra_vs_benchmark = tuple(
            sorted(observed_session_dates - benchmark_set)
        )

    insufficient_history = (
        len(observed_session_dates) < config.minimum_history_sessions
    )
    coverage = round(100.0 * len(observed_session_dates) / len(sessions), 4)
    hard_failure = any(
        (
            duplicate_dates,
            missing_sessions,
            off_calendar_dates,
            non_positive,
            invalid_ohlc,
            missing_adj_close,
            negative_volume,
            beyond_retrieval,
            missing_vs_benchmark,
            extra_vs_benchmark,
            unexpected_tickers,
            unexpected_ciks,
        )
    ) or insufficient_history
    review_finding = bool(stale or extremes or splits)
    status = "fail" if hard_failure else "review" if review_finding else "pass"

    return SecurityPriceAudit(
        ticker=expected_ticker,
        expected_session_count=len(sessions),
        observed_row_count=len(observations),
        unique_date_count=len(selected),
        coverage_percentage=coverage,
        duplicate_dates=duplicate_dates,
        missing_sessions=missing_sessions,
        off_calendar_dates=off_calendar_dates,
        non_positive_price_dates=non_positive,
        invalid_ohlc_dates=invalid_ohlc,
        missing_adjusted_close_dates=missing_adj_close,
        negative_volume_dates=negative_volume,
        stale_runs=stale,
        extreme_returns=extremes,
        split_like_discontinuities=splits,
        insufficient_history=insufficient_history,
        dates_beyond_retrieval=beyond_retrieval,
        missing_vs_benchmark=missing_vs_benchmark,
        extra_vs_benchmark=extra_vs_benchmark,
        unexpected_tickers=unexpected_tickers,
        unexpected_ciks=unexpected_ciks,
        source_snapshot_ids=tuple(
            sorted({row.source_snapshot_id for row in observations})
        ),
        status=status,
    )


def audit_price_panel(
    panel: Mapping[str, Sequence[PriceObservation]],
    *,
    expected_tickers: Sequence[str],
    expected_ciks: Optional[Mapping[str, str]],
    start_date: date,
    end_date: date,
    benchmark: str = "SPY",
    calendar_name: str = DEFAULT_CALENDAR,
    config: PriceQualityConfig = PriceQualityConfig(),
) -> PricePanelAudit:
    """Audit a fixed panel against exchange sessions and benchmark alignment."""

    ordered_tickers = tuple(expected_tickers)
    if len(set(ordered_tickers)) != len(ordered_tickers):
        raise ValueError("expected_tickers contains duplicates")
    if benchmark not in ordered_tickers:
        raise ValueError(f"benchmark {benchmark} is not in expected_tickers")
    sessions = exchange_sessions(
        start_date, end_date, calendar_name=calendar_name
    )
    benchmark_observations = panel.get(benchmark, ())
    benchmark_selected, _ = _latest_by_date(benchmark_observations)
    benchmark_dates = tuple(benchmark_selected)
    cik_map = expected_ciks or {}
    audits = tuple(
        audit_price_series(
            panel.get(ticker, ()),
            expected_ticker=ticker,
            expected_cik=cik_map.get(ticker),
            expected_sessions=sessions,
            benchmark_dates=benchmark_dates,
            config=config,
        )
        for ticker in ordered_tickers
    )
    unexpected_panel_tickers = tuple(
        sorted(set(panel).difference(ordered_tickers))
    )
    if unexpected_panel_tickers or any(audit.status == "fail" for audit in audits):
        status = "fail"
    elif any(audit.status == "review" for audit in audits):
        status = "review"
    else:
        status = "pass"
    return PricePanelAudit(
        calendar=calendar_name,
        start_date=start_date,
        end_date=end_date,
        benchmark=benchmark,
        securities=audits,
        unexpected_panel_tickers=unexpected_panel_tickers,
        status=status,
    )
