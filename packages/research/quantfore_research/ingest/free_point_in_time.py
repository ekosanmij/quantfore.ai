"""Deterministic preflight for a free point-in-time equity data build.

The free build combines an open historical S&P 500 membership series with
Tiingo's public supported-ticker inventory.  It deliberately stops before
price ingestion when the requested history cannot be resolved without ticker
ambiguity or when the configured API symbol allowance is too small.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Mapping, Optional, Sequence

from quantfore_research.validation.price_quality import exchange_sessions


MEMBERSHIP_FIELDS = ("date", "tickers")
TIINGO_LISTING_FIELDS = (
    "ticker",
    "exchange",
    "assetType",
    "priceCurrency",
    "startDate",
    "endDate",
)
SUPPORTED_ASSET_TYPES = frozenset({"Stock", "ETF"})


class FreePointInTimeSourceError(ValueError):
    """A free source cannot be interpreted without ambiguity."""


@dataclass(frozen=True)
class MembershipSnapshot:
    effective_date: date
    tickers: frozenset[str]


@dataclass(frozen=True)
class MembershipEpisode:
    ticker: str
    effective_from: date
    effective_to: date

    @property
    def episode_id(self) -> str:
        return f"open-sp500:{self.ticker}:{self.effective_from.isoformat()}"


@dataclass(frozen=True)
class TiingoListing:
    ticker: str
    exchange: str
    asset_type: str
    currency: str
    start_date: date
    end_date: date


@dataclass(frozen=True)
class EpisodeCoverage:
    episode: MembershipEpisode
    tiingo_ticker: str
    status: str
    matching_listings: tuple[TiingoListing, ...]


def _strict_csv_rows(
    body: bytes, fields: Sequence[str], label: str
) -> list[dict[str, str]]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise FreePointInTimeSourceError(f"{label} is not valid UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if tuple(reader.fieldnames or ()) != tuple(fields):
        raise FreePointInTimeSourceError(
            f"{label} fields must exactly match: {','.join(fields)}"
        )
    rows = list(reader)
    if not rows:
        raise FreePointInTimeSourceError(f"{label} is empty")
    return rows


def _iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise FreePointInTimeSourceError(f"{label} must be an ISO date") from exc


def normalize_membership_ticker(value: str) -> str:
    """Normalize the two common S&P share-class spellings.

    The open histories use both ``BRK.B`` and ``BRK-B``.  A final one-letter
    class suffix is canonicalized to dot notation; ordinary hyphenated symbols
    are otherwise preserved.
    """

    ticker = value.strip().upper()
    match = re.fullmatch(r"([A-Z0-9]+)-([A-Z])", ticker)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return ticker


def tiingo_ticker(value: str) -> str:
    """Translate S&P share-class notation to Tiingo notation."""

    return normalize_membership_ticker(value).replace(".", "-")


def parse_membership_history(
    body: bytes,
    *,
    label: str,
    allow_same_date_revisions: bool = False,
) -> tuple[MembershipSnapshot, ...]:
    rows = _strict_csv_rows(body, MEMBERSHIP_FIELDS, label)
    snapshots: list[MembershipSnapshot] = []
    prior_date: Optional[date] = None
    for row_number, row in enumerate(rows, start=2):
        effective_date = _iso_date(row["date"], f"{label} row {row_number} date")
        values = [
            normalize_membership_ticker(value)
            for value in row["tickers"].split(",")
            if value.strip()
        ]
        tickers = frozenset(values)
        if not tickers:
            raise FreePointInTimeSourceError(
                f"{label} row {row_number} has no tickers"
            )
        if len(tickers) != len(values):
            raise FreePointInTimeSourceError(
                f"{label} row {row_number} has duplicate tickers"
            )
        if prior_date is not None and effective_date < prior_date:
            raise FreePointInTimeSourceError(
                f"{label} dates must be increasing"
            )
        if prior_date == effective_date:
            if tickers != snapshots[-1].tickers:
                if not allow_same_date_revisions:
                    raise FreePointInTimeSourceError(
                        f"{label} has conflicting snapshots on "
                        f"{effective_date.isoformat()}"
                    )
                snapshots[-1] = MembershipSnapshot(effective_date, tickers)
            continue
        snapshots.append(MembershipSnapshot(effective_date, tickers))
        prior_date = effective_date
    return tuple(snapshots)


def membership_on(
    snapshots: Sequence[MembershipSnapshot], as_of_date: date
) -> frozenset[str]:
    candidates = [row for row in snapshots if row.effective_date <= as_of_date]
    if not candidates:
        raise FreePointInTimeSourceError(
            f"membership history does not cover {as_of_date.isoformat()}"
        )
    return candidates[-1].tickers


def derive_membership_episodes(
    snapshots: Sequence[MembershipSnapshot],
    *,
    window_start: date,
    window_end: date,
) -> tuple[MembershipEpisode, ...]:
    """Convert dated full snapshots into non-overlapping ticker episodes."""

    if window_start > window_end:
        raise ValueError("window_start must not be after window_end")
    initial = membership_on(snapshots, window_start)
    changes = [
        row for row in snapshots if window_start < row.effective_date <= window_end
    ]
    states = [MembershipSnapshot(window_start, initial), *changes]
    deduplicated: list[MembershipSnapshot] = []
    for row in states:
        if deduplicated and row.tickers == deduplicated[-1].tickers:
            continue
        deduplicated.append(row)

    active = {ticker: window_start for ticker in initial}
    episodes: list[MembershipEpisode] = []
    prior_members = initial
    for row in deduplicated[1:]:
        for ticker in sorted(prior_members - row.tickers):
            episodes.append(
                MembershipEpisode(
                    ticker=ticker,
                    effective_from=active.pop(ticker),
                    effective_to=row.effective_date - timedelta(days=1),
                )
            )
        for ticker in sorted(row.tickers - prior_members):
            active[ticker] = row.effective_date
        prior_members = row.tickers
    episodes.extend(
        MembershipEpisode(ticker, effective_from, window_end)
        for ticker, effective_from in active.items()
    )
    return tuple(
        sorted(
            episodes,
            key=lambda row: (row.ticker, row.effective_from, row.effective_to),
        )
    )


def parse_tiingo_supported_tickers(body: bytes) -> tuple[TiingoListing, ...]:
    rows = _strict_csv_rows(body, TIINGO_LISTING_FIELDS, "Tiingo ticker inventory")
    listings: list[TiingoListing] = []
    for row_number, row in enumerate(rows, start=2):
        if (
            row["assetType"] not in SUPPORTED_ASSET_TYPES
            or row["priceCurrency"].upper() != "USD"
            or not row["startDate"].strip()
            or not row["endDate"].strip()
        ):
            continue
        start_date = _iso_date(
            row["startDate"], f"Tiingo ticker inventory row {row_number} startDate"
        )
        end_date = _iso_date(
            row["endDate"], f"Tiingo ticker inventory row {row_number} endDate"
        )
        if start_date > end_date:
            raise FreePointInTimeSourceError(
                f"Tiingo ticker inventory row {row_number} has an invalid range"
            )
        listings.append(
            TiingoListing(
                ticker=row["ticker"].strip().upper(),
                exchange=row["exchange"].strip(),
                asset_type=row["assetType"],
                currency=row["priceCurrency"].upper(),
                start_date=start_date,
                end_date=end_date,
            )
        )
    if not listings:
        raise FreePointInTimeSourceError("Tiingo ticker inventory has no usable rows")
    return tuple(listings)


def classify_episode_coverage(
    episodes: Iterable[MembershipEpisode],
    listings: Iterable[TiingoListing],
) -> tuple[EpisodeCoverage, ...]:
    by_ticker: dict[str, list[TiingoListing]] = {}
    for listing in listings:
        by_ticker.setdefault(listing.ticker, []).append(listing)

    results: list[EpisodeCoverage] = []
    for episode in episodes:
        translated = tiingo_ticker(episode.ticker)
        candidates = by_ticker.get(translated, [])
        overlapping = tuple(
            row
            for row in candidates
            if row.start_date <= episode.effective_to
            and row.end_date >= episode.effective_from
        )
        full = tuple(
            row
            for row in overlapping
            if row.start_date <= episode.effective_from
            and row.end_date >= episode.effective_to
        )
        if len(full) == 1:
            status = "full"
            matches = full
        elif len(full) > 1:
            status = "ambiguous"
            matches = full
        elif overlapping:
            status = "partial"
            matches = overlapping
        elif candidates:
            status = "recycled_or_nonoverlapping"
            matches = tuple(candidates)
        else:
            status = "missing"
            matches = ()
        results.append(EpisodeCoverage(episode, translated, status, matches))
    return tuple(results)


def monthly_membership_counts(
    snapshots: Sequence[MembershipSnapshot],
    *,
    window_start: date,
    window_end: date,
    calendar_name: str = "XNYS",
) -> dict[str, int]:
    sessions = exchange_sessions(window_start, window_end, calendar_name=calendar_name)
    if not sessions:
        raise FreePointInTimeSourceError("universe window has no exchange sessions")
    last_sessions: dict[str, date] = {}
    for session_date in sessions:
        last_sessions[session_date.strftime("%Y-%m")] = session_date
    return {
        month: len(membership_on(snapshots, session_date))
        for month, session_date in sorted(last_sessions.items())
    }


def reconcile_samples(
    primary: Sequence[MembershipSnapshot],
    secondary: Sequence[MembershipSnapshot],
    sample_dates: Iterable[date],
) -> tuple[Mapping[str, object], ...]:
    results = []
    for sample_date in sample_dates:
        left = membership_on(primary, sample_date)
        right = membership_on(secondary, sample_date)
        results.append(
            {
                "as_of_date": sample_date.isoformat(),
                "primary_count": len(left),
                "secondary_count": len(right),
                "matching_count": len(left & right),
                "primary_only": sorted(left - right),
                "secondary_only": sorted(right - left),
                "exact_match": left == right,
            }
        )
    return tuple(results)
