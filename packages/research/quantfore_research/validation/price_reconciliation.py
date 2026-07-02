"""Independent price-source reconciliation for the real-data trial."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Mapping, Optional, Sequence
from urllib.parse import urlparse

from quantfore_research.validation.price_quality import (
    PriceObservation,
    exchange_sessions,
)


INDEPENDENT_FIELDS = (
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_open",
    "adj_high",
    "adj_low",
    "adj_close",
    "adj_volume",
    "source",
    "source_url",
    "retrieved_at",
    "license_tag",
)


@dataclass(frozen=True)
class SampleCase:
    ticker: str
    anchor_date: date
    event_type: str
    selection_reason: str


SAMPLE_CASES = (
    SampleCase(
        ticker="AAPL",
        anchor_date=date(2020, 8, 31),
        event_type="split",
        selection_reason="4-for-1 stock split effective date",
    ),
    SampleCase(
        ticker="NVDA",
        anchor_date=date(2024, 6, 10),
        event_type="split",
        selection_reason="10-for-1 stock split effective date",
    ),
    SampleCase(
        ticker="META",
        anchor_date=date(2022, 2, 3),
        event_type="volatile_period",
        selection_reason="large post-earnings price move",
    ),
    SampleCase(
        ticker="XOM",
        anchor_date=date(2020, 3, 16),
        event_type="volatile_period",
        selection_reason="COVID-19 and oil-market volatility",
    ),
    SampleCase(
        ticker="JPM",
        anchor_date=date(2023, 3, 13),
        event_type="volatile_period",
        selection_reason="US regional-bank stress period",
    ),
)


@dataclass(frozen=True)
class SamplePoint:
    ticker: str
    date: date
    anchor_date: date
    event_type: str
    selection_reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "ticker": self.ticker,
            "date": self.date.isoformat(),
            "anchor_date": self.anchor_date.isoformat(),
            "event_type": self.event_type,
            "selection_reason": self.selection_reason,
        }


@dataclass(frozen=True)
class ReconciliationPrice:
    ticker: str
    date: date
    open: Optional[Decimal]
    high: Optional[Decimal]
    low: Optional[Decimal]
    close: Optional[Decimal]
    volume: Optional[Decimal]
    adj_open: Optional[Decimal]
    adj_high: Optional[Decimal]
    adj_low: Optional[Decimal]
    adj_close: Decimal
    adj_volume: Optional[Decimal]
    source: str
    source_url: str
    retrieved_at: datetime
    license_tag: str
    source_snapshot_id: Optional[str] = None


@dataclass(frozen=True)
class ReconciliationConfig:
    raw_price_tolerance_bps: Decimal = Decimal("10")
    adjusted_price_tolerance_bps: Decimal = Decimal("25")
    volume_tolerance_percent: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        for name in (
            "raw_price_tolerance_bps",
            "adjusted_price_tolerance_bps",
            "volume_tolerance_percent",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class ComparisonRow:
    ticker: str
    date: date
    status: str
    raw_price_differences_bps: Mapping[str, Optional[Decimal]]
    adjusted_price_differences_bps: Mapping[str, Optional[Decimal]]
    volume_difference_percent: Optional[Decimal]
    adjusted_volume_difference_percent: Optional[Decimal]
    adjustment_factor_difference_bps: Optional[Decimal]
    notes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        def decimal_map(
            values: Mapping[str, Optional[Decimal]],
        ) -> dict[str, Optional[str]]:
            return {
                key: str(value) if value is not None else None
                for key, value in values.items()
            }

        return {
            "ticker": self.ticker,
            "date": self.date.isoformat(),
            "status": self.status,
            "raw_price_differences_bps": decimal_map(
                self.raw_price_differences_bps
            ),
            "adjusted_price_differences_bps": decimal_map(
                self.adjusted_price_differences_bps
            ),
            "volume_difference_percent": (
                str(self.volume_difference_percent)
                if self.volume_difference_percent is not None
                else None
            ),
            "adjusted_volume_difference_percent": (
                str(self.adjusted_volume_difference_percent)
                if self.adjusted_volume_difference_percent is not None
                else None
            ),
            "adjustment_factor_difference_bps": (
                str(self.adjustment_factor_difference_bps)
                if self.adjustment_factor_difference_bps is not None
                else None
            ),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SecurityReconciliation:
    ticker: str
    requested_dates: int
    primary_rows_received: int
    independent_rows_received: int
    rows_accepted: int
    coverage_percentage: float
    missing_primary_dates: tuple[date, ...]
    missing_independent_dates: tuple[date, ...]
    missing_session_count: Optional[int]
    failed_comparisons: int
    review_comparisons: int
    status: str

    def to_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "requested_dates": self.requested_dates,
            "primary_rows_received": self.primary_rows_received,
            "independent_rows_received": self.independent_rows_received,
            "rows_accepted": self.rows_accepted,
            "coverage_percentage": self.coverage_percentage,
            "missing_primary_dates": [
                value.isoformat() for value in self.missing_primary_dates
            ],
            "missing_independent_dates": [
                value.isoformat() for value in self.missing_independent_dates
            ],
            "missing_session_count": self.missing_session_count,
            "failed_comparisons": self.failed_comparisons,
            "review_comparisons": self.review_comparisons,
            "status": self.status,
        }


@dataclass(frozen=True)
class ReconciliationResult:
    decision: str
    sample: tuple[SamplePoint, ...]
    securities: tuple[SecurityReconciliation, ...]
    comparisons: tuple[ComparisonRow, ...]
    failed_securities: tuple[str, ...]
    adjustment_difference_count: int
    blocking_reasons: tuple[str, ...]
    manual_review_notes: tuple[str, ...]

    @property
    def rows_received(self) -> dict[str, int]:
        return {
            "primary": sum(item.primary_rows_received for item in self.securities),
            "independent": sum(
                item.independent_rows_received for item in self.securities
            ),
        }

    @property
    def rows_accepted(self) -> int:
        return sum(item.rows_accepted for item in self.securities)

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision,
            "rows_received": self.rows_received,
            "rows_accepted": self.rows_accepted,
            "sample_size": len(self.sample),
            "sample": [point.to_dict() for point in self.sample],
            "securities": [item.to_dict() for item in self.securities],
            "comparisons": [item.to_dict() for item in self.comparisons],
            "failed_securities": list(self.failed_securities),
            "adjustment_difference_count": self.adjustment_difference_count,
            "blocking_reasons": list(self.blocking_reasons),
            "manual_review_notes": list(self.manual_review_notes),
        }


def deterministic_sample(
    *,
    cases: Sequence[SampleCase] = SAMPLE_CASES,
    dates_per_security: int = 20,
) -> tuple[SamplePoint, ...]:
    """Select fixed exchange sessions around each documented event anchor."""

    if dates_per_security < 1:
        raise ValueError("dates_per_security must be positive")
    points = []
    for case in cases:
        sessions = exchange_sessions(
            case.anchor_date - timedelta(days=45),
            case.anchor_date + timedelta(days=45),
        )
        try:
            anchor_index = sessions.index(case.anchor_date)
        except ValueError as exc:
            raise ValueError(
                f"sample anchor is not an XNYS session: {case.anchor_date}"
            ) from exc
        before = (dates_per_security - 1) // 2
        start = anchor_index - before
        end = start + dates_per_security
        if start < 0 or end > len(sessions):
            raise ValueError(f"not enough sessions around {case.anchor_date}")
        points.extend(
            SamplePoint(
                ticker=case.ticker,
                date=day,
                anchor_date=case.anchor_date,
                event_type=case.event_type,
                selection_reason=case.selection_reason,
            )
            for day in sessions[start:end]
        )
    return tuple(points)


def _required(row: Mapping[str, str], field: str, row_number: int) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise ValueError(f"independent row {row_number}: {field} is required")
    return value


def _decimal(
    row: Mapping[str, str],
    field: str,
    row_number: int,
    *,
    required: bool,
) -> Optional[Decimal]:
    value = (row.get(field) or "").strip()
    if not value:
        if required:
            raise ValueError(f"independent row {row_number}: {field} is required")
        return None
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(
            f"independent row {row_number}: {field} must be numeric"
        ) from exc


def _required_decimal(
    row: Mapping[str, str], field: str, row_number: int
) -> Decimal:
    value = _decimal(row, field, row_number, required=True)
    assert value is not None
    return value


def _retrieved_at(value: str, row_number: int) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"independent row {row_number}: retrieved_at must be ISO-8601"
        ) from exc
    if parsed.tzinfo is None:
        raise ValueError(
            f"independent row {row_number}: retrieved_at must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def parse_independent_csv(payload: bytes) -> tuple[ReconciliationPrice, ...]:
    """Parse a licensed independent export without repairing any values."""

    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("independent export must be UTF-8 CSV") from exc
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != INDEPENDENT_FIELDS:
        raise ValueError(
            "independent export fields must exactly match: "
            + ",".join(INDEPENDENT_FIELDS)
        )
    prices = []
    seen = set()
    for row_number, row in enumerate(reader, start=2):
        ticker = _required(row, "ticker", row_number).upper()
        try:
            price_date = date.fromisoformat(_required(row, "date", row_number))
        except ValueError as exc:
            raise ValueError(
                f"independent row {row_number}: date must be ISO-8601"
            ) from exc
        key = (ticker, price_date)
        if key in seen:
            raise ValueError(
                f"independent export contains duplicate {ticker} {price_date}"
            )
        seen.add(key)
        prices.append(
            ReconciliationPrice(
                ticker=ticker,
                date=price_date,
                open=_decimal(row, "open", row_number, required=False),
                high=_decimal(row, "high", row_number, required=False),
                low=_decimal(row, "low", row_number, required=False),
                close=_decimal(row, "close", row_number, required=False),
                volume=_decimal(row, "volume", row_number, required=False),
                adj_open=_decimal(row, "adj_open", row_number, required=False),
                adj_high=_decimal(row, "adj_high", row_number, required=False),
                adj_low=_decimal(row, "adj_low", row_number, required=False),
                adj_close=_required_decimal(row, "adj_close", row_number),
                adj_volume=_decimal(
                    row, "adj_volume", row_number, required=False
                ),
                source=_required(row, "source", row_number),
                source_url=_required(row, "source_url", row_number),
                retrieved_at=_retrieved_at(
                    _required(row, "retrieved_at", row_number), row_number
                ),
                license_tag=_required(row, "license_tag", row_number),
            )
        )
    if not prices:
        raise ValueError("independent export must contain at least one row")
    sources = {price.source.casefold() for price in prices}
    if len(sources) != 1:
        raise ValueError("independent export must contain exactly one source")
    if any("tiingo" in source for source in sources):
        raise ValueError("independent source must not be Tiingo")
    if any(
        urlparse(price.source_url).scheme not in {"http", "https"}
        for price in prices
    ):
        raise ValueError("independent source_url must use HTTP or HTTPS")
    return tuple(prices)


def primary_prices(
    observations: Sequence[PriceObservation],
) -> tuple[ReconciliationPrice, ...]:
    """Select the latest primary row per date while preserving source identity."""

    selected: dict[tuple[str, date], PriceObservation] = {}
    for row in observations:
        key = (row.ticker, row.date)
        prior = selected.get(key)
        retrieved_at = row.retrieved_at
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
        if prior is not None:
            prior_retrieved = prior.retrieved_at
            if prior_retrieved.tzinfo is None:
                prior_retrieved = prior_retrieved.replace(tzinfo=timezone.utc)
            if (retrieved_at, row.source_snapshot_id) <= (
                prior_retrieved,
                prior.source_snapshot_id,
            ):
                continue
        selected[key] = row

    prices = []
    for row in selected.values():
        required = (row.open, row.high, row.low, row.close, row.adj_close)
        if any(value is None for value in required) or row.volume is None:
            continue
        prices.append(
            ReconciliationPrice(
                ticker=row.ticker,
                date=row.date,
                open=row.open,  # type: ignore[arg-type]
                high=row.high,  # type: ignore[arg-type]
                low=row.low,  # type: ignore[arg-type]
                close=row.close,  # type: ignore[arg-type]
                volume=Decimal(row.volume),
                adj_open=row.adj_open,
                adj_high=row.adj_high,
                adj_low=row.adj_low,
                adj_close=row.adj_close,  # type: ignore[arg-type]
                adj_volume=row.adj_volume,
                source="Tiingo",
                source_url="source_snapshot:" + row.source_snapshot_id,
                retrieved_at=row.retrieved_at,
                license_tag="tiingo_internal_research_trial_v0",
                source_snapshot_id=row.source_snapshot_id,
            )
        )
    return tuple(prices)


def _difference_bps(primary: Decimal, independent: Decimal) -> Optional[Decimal]:
    if independent == 0:
        return None
    return (abs(primary - independent) / abs(independent)) * Decimal("10000")


def _difference_percent(primary: Decimal, independent: Decimal) -> Optional[Decimal]:
    if independent == 0:
        return Decimal("0") if primary == 0 else None
    return (abs(primary - independent) / abs(independent)) * Decimal("100")


def compare_price_row(
    primary: ReconciliationPrice,
    independent: ReconciliationPrice,
    *,
    config: ReconciliationConfig,
) -> ComparisonRow:
    if (primary.ticker, primary.date) != (independent.ticker, independent.date):
        raise ValueError("comparison rows must have the same ticker and date")
    raw_differences = {}
    for field in ("open", "high", "low", "close"):
        primary_value = getattr(primary, field)
        independent_value = getattr(independent, field)
        raw_differences[field] = (
            _difference_bps(primary_value, independent_value)
            if primary_value is not None and independent_value is not None
            else None
        )
    adjusted_differences = {}
    for field in ("adj_open", "adj_high", "adj_low", "adj_close"):
        primary_value = getattr(primary, field)
        independent_value = getattr(independent, field)
        adjusted_differences[field] = (
            _difference_bps(primary_value, independent_value)
            if primary_value is not None and independent_value is not None
            else None
        )
    volume_difference = (
        _difference_percent(primary.volume, independent.volume)
        if primary.volume is not None and independent.volume is not None
        else None
    )
    adjusted_volume_difference = (
        _difference_percent(primary.adj_volume, independent.adj_volume)
        if primary.adj_volume is not None and independent.adj_volume is not None
        else None
    )
    primary_factor = primary.adj_close / primary.close if primary.close else None
    independent_factor = (
        independent.adj_close / independent.close if independent.close else None
    )
    factor_difference = (
        _difference_bps(primary_factor, independent_factor)
        if primary_factor is not None and independent_factor is not None
        else None
    )

    notes = []
    raw_failure = False
    raw_unavailable = False
    for field, difference in raw_differences.items():
        if difference is None:
            raw_unavailable = True
            notes.append(f"raw {field} is unavailable from one source")
        elif difference > config.raw_price_tolerance_bps:
            raw_failure = True
            notes.append(f"raw {field} exceeds tolerance")
    review = raw_unavailable
    for field, difference in adjusted_differences.items():
        if (
            difference is None
            or difference > config.adjusted_price_tolerance_bps
        ):
            review = True
            notes.append(
                f"adjusted {field} exceeds tolerance or is not comparable"
            )
    if (
        volume_difference is None
        or volume_difference > config.volume_tolerance_percent
    ):
        review = True
        notes.append("raw volume exceeds tolerance or is not comparable")
    if (
        adjusted_volume_difference is None
        or adjusted_volume_difference > config.volume_tolerance_percent
    ):
        review = True
        notes.append("adjusted volume exceeds tolerance or is not comparable")
    if (
        factor_difference is None
        or factor_difference > config.adjusted_price_tolerance_bps
    ):
        review = True
        notes.append("adjustment factor exceeds tolerance or is not comparable")
    status = "fail" if raw_failure else "review" if review else "pass"
    return ComparisonRow(
        ticker=primary.ticker,
        date=primary.date,
        status=status,
        raw_price_differences_bps=raw_differences,
        adjusted_price_differences_bps=adjusted_differences,
        volume_difference_percent=volume_difference,
        adjusted_volume_difference_percent=adjusted_volume_difference,
        adjustment_factor_difference_bps=factor_difference,
        notes=tuple(notes),
    )


def reconcile_sample(
    *,
    sample: Sequence[SamplePoint],
    primary: Sequence[ReconciliationPrice],
    independent: Sequence[ReconciliationPrice],
    missing_session_counts: Optional[Mapping[str, int]],
    price_quality_status: Optional[str],
    config: ReconciliationConfig = ReconciliationConfig(),
    prerequisite_blockers: Sequence[str] = (),
    manual_review_notes: Sequence[str] = (),
) -> ReconciliationResult:
    """Compare a fixed sample without modifying either source's values."""

    primary_map = {(row.ticker, row.date): row for row in primary}
    independent_map = {(row.ticker, row.date): row for row in independent}
    sample_by_ticker: dict[str, list[SamplePoint]] = {}
    for point in sample:
        sample_by_ticker.setdefault(point.ticker, []).append(point)

    comparisons = []
    securities = []
    blockers = list(prerequisite_blockers)
    for ticker, points in sample_by_ticker.items():
        dates = tuple(point.date for point in points)
        primary_dates = tuple(
            day for day in dates if (ticker, day) in primary_map
        )
        independent_dates = tuple(
            day for day in dates if (ticker, day) in independent_map
        )
        missing_primary = tuple(day for day in dates if day not in primary_dates)
        missing_independent = tuple(
            day for day in dates if day not in independent_dates
        )
        ticker_comparisons = []
        for day in dates:
            primary_row = primary_map.get((ticker, day))
            independent_row = independent_map.get((ticker, day))
            if primary_row is None or independent_row is None:
                continue
            comparison = compare_price_row(
                primary_row, independent_row, config=config
            )
            comparisons.append(comparison)
            ticker_comparisons.append(comparison)
        failed = sum(item.status == "fail" for item in ticker_comparisons)
        review = sum(item.status == "review" for item in ticker_comparisons)
        if missing_primary or missing_independent or failed:
            status = "fail"
        elif review:
            status = "conditional_pass"
        else:
            status = "pass"
        accepted = len(ticker_comparisons)
        securities.append(
            SecurityReconciliation(
                ticker=ticker,
                requested_dates=len(dates),
                primary_rows_received=len(primary_dates),
                independent_rows_received=len(independent_dates),
                rows_accepted=accepted,
                coverage_percentage=round(100.0 * accepted / len(dates), 4),
                missing_primary_dates=missing_primary,
                missing_independent_dates=missing_independent,
                missing_session_count=(
                    missing_session_counts.get(ticker)
                    if missing_session_counts is not None
                    else None
                ),
                failed_comparisons=failed,
                review_comparisons=review,
                status=status,
            )
        )

    if price_quality_status is None:
        blockers.append("WP6.3 price-quality audit is missing")
    elif price_quality_status == "fail":
        blockers.append("WP6.3 price-quality audit failed")
    failed_securities = tuple(
        item.ticker for item in securities if item.status == "fail"
    )
    if failed_securities:
        blockers.append("one or more sampled securities failed reconciliation")
    if blockers:
        decision = "fail"
    elif price_quality_status == "review" or any(
        item.status == "conditional_pass" for item in securities
    ):
        decision = "conditional_pass"
    else:
        decision = "pass"
    adjustment_difference_count = sum(
        any(
            note.startswith("adjusted ")
            or note.startswith("adjustment factor")
            for note in item.notes
        )
        for item in comparisons
    )
    notes = list(manual_review_notes)
    notes.append(
        "Vendor values were compared as received; no price or adjustment was repaired."
    )
    return ReconciliationResult(
        decision=decision,
        sample=tuple(sample),
        securities=tuple(securities),
        comparisons=tuple(comparisons),
        failed_securities=failed_securities,
        adjustment_difference_count=adjustment_difference_count,
        blocking_reasons=tuple(dict.fromkeys(blockers)),
        manual_review_notes=tuple(notes),
    )
