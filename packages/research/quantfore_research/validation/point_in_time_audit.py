"""Deterministic audit of a point-in-time US equity research panel."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.models import (
    CorporateAction,
    DelistingEvent,
    Price,
    Security,
    SecurityIdentifier,
    SourceSnapshot,
    TickerAlias,
    UniverseDefinition,
    UniverseMembership,
)
from quantfore_research.validation.price_quality import exchange_sessions
from quantfore_research.validation.reproducibility import universe_membership_hash


HARD = "hard"
REVIEW = "review"
DEFAULT_MINIMUM_MONTHLY_MEMBERS = 450
DEFAULT_MAXIMUM_MONTHLY_MEMBERS = 550


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _iso(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list)):
        return [_iso(item) for item in value]
    return value


@dataclass(frozen=True)
class PointInTimeAuditFinding:
    severity: str
    code: str
    message: str
    security_id: Optional[str] = None
    ticker: Optional[str] = None
    dates: tuple[date, ...] = ()
    context: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "security_id": self.security_id,
            "ticker": self.ticker,
            "dates": _iso(self.dates),
            "context": _iso(dict(self.context or {})),
        }


@dataclass(frozen=True)
class HistoricalRemovalEvidence:
    membership_id: str
    security_id: str
    ticker: str
    effective_to: date
    announced_at: datetime
    membership_source_snapshot_id: str
    membership_source_hash: str
    permanent_identifiers: tuple[str, ...]
    last_member_price_date: Optional[date]
    first_post_removal_price_date: Optional[date]
    security_history_retained: bool

    def to_dict(self) -> dict[str, Any]:
        return _iso(self.__dict__)


@dataclass(frozen=True)
class DelistingEvidence:
    delisting_event_id: str
    security_id: str
    ticker: str
    delisting_date: date
    announced_at: datetime
    delisting_return: Optional[Decimal]
    return_available_at: Optional[datetime]
    reason: str
    source_snapshot_id: str
    source_hash: str
    permanent_identifiers: tuple[str, ...]
    final_price_date: Optional[date]
    membership_closed_by_delisting: bool
    security_history_retained: bool

    def to_dict(self) -> dict[str, Any]:
        return _iso(self.__dict__)


@dataclass(frozen=True)
class PointInTimeEquityPanelAudit:
    universe_id: str
    window_start: date
    window_end: date
    calendar: str
    benchmark_security_id: str
    security_count: int
    membership_count: int
    price_count: int
    corporate_action_count: int
    delisting_count: int
    source_snapshot_ids: tuple[str, ...]
    membership_count_by_month: Mapping[str, int]
    membership_content_hash: str
    snapshot_binding: Mapping[str, Any]
    findings: tuple[PointInTimeAuditFinding, ...]
    historical_removal: Optional[HistoricalRemovalEvidence]
    delisting: Optional[DelistingEvidence]

    @property
    def hard_failure_count(self) -> int:
        return sum(finding.severity == HARD for finding in self.findings)

    @property
    def review_finding_count(self) -> int:
        return sum(finding.severity == REVIEW for finding in self.findings)

    @property
    def status(self) -> str:
        if self.hard_failure_count:
            return "fail"
        if self.review_finding_count:
            return "review"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_id": self.universe_id,
            "window_start": self.window_start.isoformat(),
            "window_end": self.window_end.isoformat(),
            "calendar": self.calendar,
            "benchmark_security_id": self.benchmark_security_id,
            "status": self.status,
            "hard_failure_count": self.hard_failure_count,
            "review_finding_count": self.review_finding_count,
            "counts": {
                "securities": self.security_count,
                "memberships": self.membership_count,
                "prices": self.price_count,
                "corporate_actions": self.corporate_action_count,
                "delistings": self.delisting_count,
                "source_snapshots": len(self.source_snapshot_ids),
            },
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "membership_count_by_month": dict(self.membership_count_by_month),
            "dataset_binding": {
                "membership_content_hash": self.membership_content_hash,
                **_iso(dict(self.snapshot_binding)),
            },
            "findings": [finding.to_dict() for finding in self.findings],
            "historical_removal_evidence": (
                self.historical_removal.to_dict()
                if self.historical_removal is not None
                else None
            ),
            "delisting_evidence": (
                self.delisting.to_dict() if self.delisting is not None else None
            ),
        }


def _overlaps(
    left_from: date,
    left_to: Optional[date],
    right_from: date,
    right_to: Optional[date],
) -> bool:
    return (right_to is None or left_from <= right_to) and (
        left_to is None or right_from <= left_to
    )


def _contains(start: date, end: Optional[date], value: date) -> bool:
    return start <= value and (end is None or value <= end)


def _price_signature(price: Price) -> tuple[Any, ...]:
    return (
        price.open,
        price.high,
        price.low,
        price.close,
        price.adj_open,
        price.adj_high,
        price.adj_low,
        price.adj_close,
        price.volume,
        price.adj_volume,
    )


def _invalid_price(price: Price) -> bool:
    values = (
        price.open,
        price.high,
        price.low,
        price.close,
        price.adj_open,
        price.adj_high,
        price.adj_low,
        price.adj_close,
    )
    if any(value is not None and value <= 0 for value in values):
        return True
    if price.volume is not None and price.volume < 0:
        return True
    if price.adj_volume is not None and price.adj_volume < 0:
        return True
    for open_price, high, low, close in (
        (price.open, price.high, price.low, price.close),
        (price.adj_open, price.adj_high, price.adj_low, price.adj_close),
    ):
        if any(value is None for value in (open_price, high, low, close)):
            continue
        assert open_price is not None and high is not None
        assert low is not None and close is not None
        if not low <= min(open_price, close) <= max(open_price, close) <= high:
            return True
    return False


def _simple_return(current: Optional[Decimal], prior: Optional[Decimal]) -> Optional[Decimal]:
    if current is None or prior is None or current <= 0 or prior <= 0:
        return None
    return current / prior - Decimal("1")


def _permanent_identifier_labels(
    identifiers: Iterable[SecurityIdentifier], security_id: str
) -> tuple[str, ...]:
    return tuple(
        sorted(
            f"{row.identifier_type}={row.identifier_value}"
            for row in identifiers
            if row.security_id == security_id and row.is_permanent
        )
    )


def audit_point_in_time_equity_panel(
    session: Session,
    *,
    universe_id: str = "sp500-pit-v1",
    calendar: str = "XNYS",
    audit_as_of: Optional[datetime] = None,
    split_raw_return_threshold: Decimal = Decimal("0.35"),
    adjusted_discontinuity_threshold: Decimal = Decimal("0.10"),
    minimum_monthly_members: int = DEFAULT_MINIMUM_MONTHLY_MEMBERS,
    maximum_monthly_members: int = DEFAULT_MAXIMUM_MONTHLY_MEMBERS,
) -> PointInTimeEquityPanelAudit:
    """Run every Sprint 7.4 hard gate and review check."""

    if minimum_monthly_members <= 0 or maximum_monthly_members < minimum_monthly_members:
        raise ValueError("monthly membership plausibility range is invalid")
    as_of = _utc(audit_as_of or datetime.now(timezone.utc))
    universe = session.get(UniverseDefinition, universe_id)
    if universe is None:
        raise ValueError(f"universe definition does not exist: {universe_id}")
    window_sessions = exchange_sessions(
        universe.window_start, universe.window_end, calendar_name=calendar
    )
    session_positions = {day: index for index, day in enumerate(window_sessions)}

    memberships = session.scalars(
        select(UniverseMembership)
        .where(UniverseMembership.universe_id == universe_id)
        .order_by(
            UniverseMembership.security_id,
            UniverseMembership.effective_from,
            UniverseMembership.membership_id,
        )
    ).all()
    member_security_ids = {row.security_id for row in memberships}
    relevant_security_ids = member_security_ids | {universe.benchmark_security_id}
    securities = session.scalars(
        select(Security).where(Security.security_id.in_(relevant_security_ids))
    ).all()
    security_by_id = {row.security_id: row for row in securities}
    identifiers = session.scalars(
        select(SecurityIdentifier).where(
            SecurityIdentifier.security_id.in_(relevant_security_ids)
        )
    ).all()
    aliases = session.scalars(
        select(TickerAlias).where(TickerAlias.security_id.in_(relevant_security_ids))
    ).all()
    price_records = session.execute(
        select(Price, SourceSnapshot)
        .join(SourceSnapshot, SourceSnapshot.snapshot_id == Price.source_snapshot_id)
        .where(Price.security_id.in_(relevant_security_ids))
        .order_by(Price.security_id, Price.date, SourceSnapshot.retrieved_at)
    ).all()
    actions = session.scalars(
        select(CorporateAction).where(
            CorporateAction.security_id.in_(relevant_security_ids)
        )
    ).all()
    delistings = session.scalars(
        select(DelistingEvent).where(
            DelistingEvent.security_id.in_(relevant_security_ids)
        )
    ).all()
    snapshots = {
        row.snapshot_id: row for row in session.scalars(select(SourceSnapshot)).all()
    }
    findings: list[PointInTimeAuditFinding] = []

    def add(
        severity: str,
        code: str,
        message: str,
        *,
        security_id: Optional[str] = None,
        dates: Iterable[date] = (),
        context: Optional[Mapping[str, Any]] = None,
    ) -> None:
        security = security_by_id.get(security_id or "")
        all_dates = tuple(sorted(set(dates)))
        finding_context = dict(context or {})
        if len(all_dates) > 100:
            finding_context.update(
                {
                    "date_count": len(all_dates),
                    "dates_truncated": True,
                    "first_date": all_dates[0],
                    "last_date": all_dates[-1],
                }
            )
            all_dates = (*all_dates[:50], *all_dates[-50:])
        findings.append(
            PointInTimeAuditFinding(
                severity=severity,
                code=code,
                message=message,
                security_id=security_id,
                ticker=security.ticker if security is not None else None,
                dates=all_dates,
                context=finding_context,
            )
        )

    if not memberships:
        add(HARD, "missing_membership_periods", "universe contains no membership periods")
    missing_security_ids = sorted(relevant_security_ids - set(security_by_id))
    for security_id in missing_security_ids:
        add(
            HARD,
            "unresolved_membership_security",
            "membership or benchmark does not resolve to a security",
            security_id=security_id,
        )

    memberships_by_security: dict[str, list[UniverseMembership]] = {}
    for membership in memberships:
        memberships_by_security.setdefault(membership.security_id, []).append(membership)
        end = membership.effective_to or universe.window_end
        if end < universe.window_start or membership.effective_from > universe.window_end:
            add(
                HARD,
                "membership_outside_universe_window",
                "membership does not overlap the frozen universe window",
                security_id=membership.security_id,
                dates=(membership.effective_from, end),
            )
    for security_id, rows in memberships_by_security.items():
        for position, left in enumerate(rows):
            for right in rows[position + 1 :]:
                if _overlaps(
                    left.effective_from,
                    left.effective_to,
                    right.effective_from,
                    right.effective_to,
                ):
                    add(
                        HARD,
                        "overlapping_memberships",
                        "membership periods overlap for one universe/security",
                        security_id=security_id,
                        context={
                            "membership_ids": [left.membership_id, right.membership_id]
                        },
                    )
            if position + 1 < len(rows) and left.effective_to is not None:
                right = rows[position + 1]
                if (right.effective_from - left.effective_to).days > 1:
                    add(
                        REVIEW,
                        "membership_period_gap",
                        "security leaves and later re-enters the universe; verify the gap",
                        security_id=security_id,
                        dates=(left.effective_to, right.effective_from),
                    )

    covered_membership_sessions: set[date] = set()
    for membership in memberships:
        start = max(membership.effective_from, universe.window_start)
        end = min(membership.effective_to or universe.window_end, universe.window_end)
        if start <= end:
            covered_membership_sessions.update(
                day for day in window_sessions if start <= day <= end
            )
    universe_sessions = set(window_sessions)
    missing_membership_sessions = sorted(
        universe_sessions - covered_membership_sessions
    )
    if missing_membership_sessions:
        add(
            HARD,
            "missing_membership_periods",
            "no universe membership is effective on one or more exchange sessions",
            dates=missing_membership_sessions,
            context={"missing_session_count": len(missing_membership_sessions)},
        )

    month_last_sessions: dict[str, date] = {}
    for day in window_sessions:
        month_last_sessions[day.strftime("%Y-%m")] = day
    membership_count_by_month = {
        month: sum(
            _contains(row.effective_from, row.effective_to, day)
            for row in memberships
        )
        for month, day in sorted(month_last_sessions.items())
    }
    implausible_months = {
        month: count
        for month, count in membership_count_by_month.items()
        if count < minimum_monthly_members or count > maximum_monthly_members
    }
    if implausible_months:
        add(
            HARD,
            "implausible_monthly_membership_count",
            "monthly constituent counts fall outside the accepted universe range",
            context={
                "minimum": minimum_monthly_members,
                "maximum": maximum_monthly_members,
                "counts": implausible_months,
            },
        )

    contract = universe.audit_contract_json or {}
    required_contract_fields = {
        "expected_row_counts",
        "monthly_membership_counts",
        "independent_membership_samples",
        "role_snapshots",
        "expected_security_ids",
    }
    if not required_contract_fields <= set(contract):
        add(
            HARD,
            "missing_universe_audit_contract",
            "universe lacks vendor totals, monthly counts, or independent samples",
        )
    else:
        expected_monthly = contract["monthly_membership_counts"]
        if expected_monthly != membership_count_by_month:
            add(
                HARD,
                "vendor_monthly_membership_count_mismatch",
                "normalized monthly membership counts differ from the vendor manifest",
                context={
                    "expected": expected_monthly,
                    "actual": membership_count_by_month,
                },
            )
        expected_security_ids = set(contract["expected_security_ids"])
        if expected_security_ids != set(security_by_id):
            add(
                HARD,
                "vendor_security_total_mismatch",
                "normalized securities differ from the manifest security inventory",
                context={
                    "expected_count": len(expected_security_ids),
                    "actual_count": len(security_by_id),
                },
            )
        actual_role_counts = {
            "securities": len(expected_security_ids & set(security_by_id)),
            "memberships": len(memberships),
            "prices": len(price_records),
            "corporate_actions": len(actions),
            "delistings": len(delistings),
        }
        if contract["expected_row_counts"] != actual_role_counts:
            add(
                HARD,
                "vendor_row_count_mismatch",
                "database row totals differ from the pinned vendor manifest",
                context={
                    "expected": contract["expected_row_counts"],
                    "actual": actual_role_counts,
                },
            )
        for role, expected_snapshot in contract["role_snapshots"].items():
            if not isinstance(expected_snapshot, dict):
                add(
                    HARD,
                    "invalid_vendor_snapshot_contract",
                    f"vendor snapshot contract for {role} is malformed",
                )
                continue
            snapshot_id = expected_snapshot.get("snapshot_id")
            expected_hash = expected_snapshot.get("source_hash")
            snapshot = snapshots.get(snapshot_id)
            if snapshot is None or snapshot.source_hash != expected_hash:
                add(
                    HARD,
                    "vendor_snapshot_hash_mismatch",
                    f"database snapshot for {role} differs from the vendor manifest",
                    context={"snapshot_id": snapshot_id},
                )
        for sample in contract["independent_membership_samples"]:
            sample_date = date.fromisoformat(sample["as_of_date"])
            actual_ids = sorted(
                row.security_id
                for row in memberships
                if _contains(row.effective_from, row.effective_to, sample_date)
            )
            if actual_ids != sorted(sample["security_ids"]):
                add(
                    HARD,
                    "independent_membership_sample_mismatch",
                    "historical membership differs from an independently sourced sample",
                    dates=(sample_date,),
                    context={
                        "source_uri": sample["source_uri"],
                        "source_sha256": sample["source_sha256"],
                        "expected_count": len(sample["security_ids"]),
                        "actual_count": len(actual_ids),
                    },
                )
    identifiers_by_security: dict[str, list[SecurityIdentifier]] = {}
    for identifier in identifiers:
        identifiers_by_security.setdefault(identifier.security_id, []).append(identifier)
    aliases_by_security: dict[str, list[TickerAlias]] = {}
    for alias in aliases:
        aliases_by_security.setdefault(alias.security_id, []).append(alias)
    for security_id in member_security_ids:
        if not any(
            row.is_permanent for row in identifiers_by_security.get(security_id, [])
        ):
            add(
                HARD,
                "missing_permanent_identifier",
                "member security has no permanent non-ticker identifier",
                security_id=security_id,
            )
        if not aliases_by_security.get(security_id):
            add(
                HARD,
                "missing_ticker_alias",
                "member security has no dated ticker mapping",
                security_id=security_id,
            )

    identifiers_by_key: dict[tuple[str, str], list[SecurityIdentifier]] = {}
    for row in identifiers:
        key = (row.identifier_type.strip().upper(), row.identifier_value.strip().upper())
        identifiers_by_key.setdefault(key, []).append(row)
    for key, rows in identifiers_by_key.items():
        for position, left in enumerate(rows):
            for right in rows[position + 1 :]:
                if left.security_id != right.security_id and _overlaps(
                    left.valid_from, left.valid_to, right.valid_from, right.valid_to
                ):
                    add(
                        HARD,
                        "conflicting_identifier_mapping",
                        f"{key[0]}={key[1]} maps to multiple securities",
                        context={
                            "security_ids": sorted(
                                {left.security_id, right.security_id}
                            )
                        },
                    )

    aliases_by_key: dict[str, list[TickerAlias]] = {}
    for row in aliases:
        aliases_by_key.setdefault(row.ticker.strip().upper(), []).append(row)
    for ticker, rows in aliases_by_key.items():
        for position, left in enumerate(rows):
            for right in rows[position + 1 :]:
                if left.security_id != right.security_id and _overlaps(
                    left.effective_from,
                    left.effective_to,
                    right.effective_from,
                    right.effective_to,
                ):
                    add(
                        HARD,
                        "ambiguous_ticker_mapping",
                        f"ticker {ticker} maps to multiple securities at one time",
                        context={
                            "security_ids": sorted(
                                {left.security_id, right.security_id}
                            )
                        },
                    )

    for identifier in identifiers:
        if identifier.valid_from > universe.window_end or identifier.valid_from > as_of.date():
            add(
                HARD,
                "future_dated_identifier",
                "identifier validity begins after the cutoff or audit timestamp",
                security_id=identifier.security_id,
                dates=(identifier.valid_from,),
            )
    for alias in aliases:
        if alias.effective_from > universe.window_end or alias.effective_from > as_of.date():
            add(
                HARD,
                "future_dated_ticker_alias",
                "ticker alias begins after the cutoff or audit timestamp",
                security_id=alias.security_id,
                dates=(alias.effective_from,),
            )

    lineage_rows = [*identifiers, *aliases, universe, *memberships, *actions, *delistings]
    for row in lineage_rows:
        snapshot = snapshots.get(row.source_snapshot_id)
        label = row.__tablename__
        if snapshot is None:
            add(HARD, "missing_source_snapshot", f"{label} row lacks source snapshot")
        elif row.source_hash != snapshot.source_hash:
            add(HARD, "source_hash_mismatch", f"{label} row hash differs from snapshot")
        elif re.fullmatch(r"[0-9a-f]{64}", row.source_hash) is None:
            add(HARD, "invalid_source_hash", f"{label} row hash is not lowercase SHA-256")

    referenced_snapshot_ids = {
        universe.source_snapshot_id,
        *(row.source_snapshot_id for row in identifiers),
        *(row.source_snapshot_id for row in aliases),
        *(row.source_snapshot_id for row in memberships),
        *(row.source_snapshot_id for row in actions),
        *(row.source_snapshot_id for row in delistings),
        *(price.source_snapshot_id for price, _ in price_records),
    }
    for snapshot_id in sorted(referenced_snapshot_ids):
        snapshot = snapshots.get(snapshot_id)
        if snapshot is None:
            continue
        if _utc(snapshot.retrieved_at) > as_of:
            add(
                HARD,
                "future_dated_source_snapshot",
                "source retrieval timestamp is later than the audit timestamp",
                context={"source_snapshot_id": snapshot_id},
            )
        if re.fullmatch(r"[0-9a-f]{64}", snapshot.source_hash) is None:
            add(
                HARD,
                "invalid_snapshot_hash",
                "source snapshot hash is not lowercase SHA-256",
                context={"source_snapshot_id": snapshot_id},
            )

    prices_by_security_snapshot: dict[
        tuple[str, str], list[tuple[Price, SourceSnapshot]]
    ] = {}
    for price, snapshot in price_records:
        prices_by_security_snapshot.setdefault(
            (price.security_id, snapshot.snapshot_id), []
        ).append((price, snapshot))
    chosen_price_snapshot_ids: dict[str, str] = {}
    for security_id in sorted(relevant_security_ids):
        candidates = [
            (rows, snapshot_id)
            for (candidate_security_id, snapshot_id), rows in prices_by_security_snapshot.items()
            if candidate_security_id == security_id
            and any(row.adj_close is not None for row, _ in rows)
        ]
        if not candidates:
            add(
                HARD,
                "missing_audited_price_snapshot",
                "security has no adjusted-price snapshot that can be bound to a backtest",
                security_id=security_id,
            )
            continue
        _, selected_snapshot_id = max(
            candidates,
            key=lambda item: (
                sum(row.adj_close is not None for row, _ in item[0]),
                _utc(item[0][0][1].retrieved_at),
                item[1],
            ),
        )
        chosen_price_snapshot_ids[security_id] = selected_snapshot_id

    grouped_prices: dict[tuple[str, date], list[tuple[Price, SourceSnapshot]]] = {}
    for price, snapshot in price_records:
        grouped_prices.setdefault((price.security_id, price.date), []).append(
            (price, snapshot)
        )
    selected_prices: dict[str, dict[date, Price]] = {}
    source_snapshot_ids: set[str] = set()
    for (security_id, price_date), rows in grouped_prices.items():
        rows.sort(key=lambda item: (_utc(item[1].retrieved_at), item[1].snapshot_id))
        selected = next(
            (
                item
                for item in rows
                if item[1].snapshot_id == chosen_price_snapshot_ids.get(security_id)
            ),
            None,
        )
        if selected is not None:
            selected_prices.setdefault(security_id, {})[price_date] = selected[0]
        source_snapshot_ids.update(item[1].snapshot_id for item in rows)
        if len(rows) > 1:
            signatures = {_price_signature(item[0]) for item in rows}
            add(
                REVIEW,
                "duplicate_or_revised_price",
                "multiple source snapshots contain this security/date",
                security_id=security_id,
                dates=(price_date,),
                context={"conflicting_values": len(signatures) > 1},
            )
        for price, snapshot in rows:
            if price_date > universe.window_end or price_date > as_of.date():
                add(
                    HARD,
                    "future_dated_price",
                    "price is beyond the dataset cutoff or audit timestamp",
                    security_id=security_id,
                    dates=(price_date,),
                )
            if price_date > _utc(snapshot.retrieved_at).date():
                add(
                    HARD,
                    "price_beyond_retrieval",
                    "price date is later than its source retrieval date",
                    security_id=security_id,
                    dates=(price_date,),
                )
            security = security_by_id.get(security_id)
            if security is not None and (
                (security.active_from is not None and price_date < security.active_from)
                or (security.active_to is not None and price_date > security.active_to)
            ):
                add(
                    HARD,
                    "price_outside_listing_boundary",
                    "price falls outside the security listing/activity period",
                    security_id=security_id,
                    dates=(price_date,),
                )
            if _invalid_price(price):
                add(
                    HARD,
                    "impossible_ohlc_or_volume",
                    "price contains non-positive values, negative volume, or invalid OHLC",
                    security_id=security_id,
                    dates=(price_date,),
                )

    for security_id, rows in memberships_by_security.items():
        outside_membership = [
            price_date
            for price_date in selected_prices.get(security_id, {})
            if universe.window_start <= price_date <= universe.window_end
            and not any(
                _contains(row.effective_from, row.effective_to, price_date)
                for row in rows
            )
        ]
        if outside_membership:
            add(
                REVIEW,
                "prices_outside_membership",
                "prices outside membership are retained for lookbacks/outcomes; verify scope",
                security_id=security_id,
                dates=outside_membership,
            )

        expected: set[date] = set()
        security = security_by_id.get(security_id)
        for membership in rows:
            start = max(membership.effective_from, universe.window_start)
            end = min(membership.effective_to or universe.window_end, universe.window_end)
            if security is not None and security.active_from is not None:
                start = max(start, security.active_from)
            if security is not None and security.active_to is not None:
                end = min(end, security.active_to)
            if start <= end:
                expected.update(day for day in window_sessions if start <= day <= end)
        missing_sessions = sorted(expected - set(selected_prices.get(security_id, {})))
        if missing_sessions:
            add(
                REVIEW,
                "exchange_calendar_gaps",
                "member lacks prices on expected exchange sessions",
                security_id=security_id,
                dates=missing_sessions,
                context={"missing_session_count": len(missing_sessions)},
            )

    benchmark_prices = selected_prices.get(universe.benchmark_security_id, {})
    benchmark_expected = set(window_sessions)
    benchmark_gaps = sorted(benchmark_expected - set(benchmark_prices))
    if benchmark_gaps:
        add(
            REVIEW,
            "benchmark_exchange_calendar_gaps",
            "benchmark lacks prices on expected exchange sessions",
            security_id=universe.benchmark_security_id,
            dates=benchmark_gaps,
            context={"missing_session_count": len(benchmark_gaps)},
        )

    delistings_by_security: dict[str, list[DelistingEvent]] = {}
    for event in delistings:
        delistings_by_security.setdefault(event.security_id, []).append(event)
        if event.delisting_date > universe.window_end or event.delisting_date > as_of.date():
            add(
                HARD,
                "future_dated_delisting",
                "delisting is beyond the cutoff or audit timestamp",
                security_id=event.security_id,
                dates=(event.delisting_date,),
            )
        if event.delisting_return is None:
            add(
                REVIEW,
                "missing_delisting_return",
                "delisting return is unavailable and remains un-imputed",
                security_id=event.security_id,
                dates=(event.delisting_date,),
            )
        if event.delisting_return is not None and event.return_available_at is None:
            add(
                HARD,
                "delisting_return_without_availability",
                "delisting return lacks an availability timestamp",
                security_id=event.security_id,
                dates=(event.delisting_date,),
            )
        post_delisting = sorted(
            day
            for day in selected_prices.get(event.security_id, {})
            if day > event.delisting_date
        )
        if post_delisting:
            add(
                HARD,
                "unexpected_post_delisting_prices",
                "prices exist after the delisting date",
                security_id=event.security_id,
                dates=post_delisting,
            )
        for membership in memberships_by_security.get(event.security_id, []):
            if membership.effective_to is None or membership.effective_to > event.delisting_date:
                add(
                    HARD,
                    "membership_extends_after_delisting",
                    "universe membership remains active after delisting",
                    security_id=event.security_id,
                    dates=(event.delisting_date,),
                    context={"membership_id": membership.membership_id},
                )
    for security_id in member_security_ids:
        security = security_by_id.get(security_id)
        if (
            security is not None
            and security.active_to is not None
            and universe.window_start <= security.active_to <= universe.window_end
            and security.active_to <= as_of.date()
            and not delistings_by_security.get(security_id)
        ):
            add(
                HARD,
                "missing_delisting_event",
                "inactive member security has no delisting event",
                security_id=security_id,
                dates=(security.active_to,),
            )

    actions_by_security_date: dict[tuple[str, date], list[CorporateAction]] = {}
    for action in actions:
        actions_by_security_date.setdefault(
            (action.security_id, action.effective_date), []
        ).append(action)
        if action.effective_date > universe.window_end or action.effective_date > as_of.date():
            add(
                HARD,
                "future_dated_corporate_action",
                "corporate action is beyond the cutoff or audit timestamp",
                security_id=action.security_id,
                dates=(action.effective_date,),
            )
        if "dividend" in action.action_type.lower() and action.cash_amount is None:
            add(
                REVIEW,
                "dividend_missing_cash_amount",
                "dividend action has no cash amount",
                security_id=action.security_id,
                dates=(action.effective_date,),
            )
        if (
            "split" in action.action_type.lower()
            and (action.ratio_from is None or action.ratio_to is None)
        ):
            add(
                REVIEW,
                "split_missing_ratio",
                "split action has no complete ratio terms",
                security_id=action.security_id,
                dates=(action.effective_date,),
            )
        if (
            "split" in action.action_type.lower()
            or "dividend" in action.action_type.lower()
        ):
            price_dates = sorted(selected_prices.get(action.security_id, {}))
            prior_dates = [day for day in price_dates if day < action.effective_date]
            if action.effective_date not in price_dates or not prior_dates:
                add(
                    REVIEW,
                    "corporate_action_price_gap",
                    "split/dividend lacks prices on both sides of its effective date",
                    security_id=action.security_id,
                    dates=(action.effective_date,),
                    context={
                        "has_effective_date_price": action.effective_date in price_dates,
                        "has_prior_price": bool(prior_dates),
                    },
                )

    for security_id, prices_by_date in selected_prices.items():
        ordered_dates = sorted(prices_by_date)
        for prior_date, current_date in zip(ordered_dates, ordered_dates[1:]):
            prior_position = session_positions.get(prior_date)
            current_position = session_positions.get(current_date)
            if (
                prior_position is None
                or current_position is None
                or current_position != prior_position + 1
            ):
                continue
            prior = prices_by_date[prior_date]
            current = prices_by_date[current_date]
            raw_return = _simple_return(current.close, prior.close)
            adjusted_return = _simple_return(current.adj_close, prior.adj_close)
            current_actions = actions_by_security_date.get(
                (security_id, current_date), []
            )
            split_actions = [
                action
                for action in current_actions
                if "split" in action.action_type.lower()
            ]
            action_rows = [
                action
                for action in current_actions
                if "split" in action.action_type.lower()
                or "dividend" in action.action_type.lower()
            ]
            if (
                raw_return is not None
                and abs(raw_return) >= split_raw_return_threshold
                and (
                    adjusted_return is None
                    or abs(adjusted_return) <= adjusted_discontinuity_threshold
                )
                and not split_actions
            ):
                add(
                    REVIEW,
                    "unexplained_split_discontinuity",
                    "raw split-like discontinuity has no matching split action",
                    security_id=security_id,
                    dates=(prior_date, current_date),
                    context={
                        "raw_return": raw_return,
                        "adjusted_return": adjusted_return,
                    },
                )
            if (
                action_rows
                and adjusted_return is not None
                and abs(adjusted_return) > adjusted_discontinuity_threshold
            ):
                add(
                    REVIEW,
                    "adjusted_action_discontinuity",
                    "adjusted price is discontinuous across a split/dividend action",
                    security_id=security_id,
                    dates=(prior_date, current_date),
                    context={"adjusted_return": adjusted_return},
                )

    removal_candidates = sorted(
        (
            row
            for row in memberships
            if row.effective_to is not None and row.effective_to < universe.window_end
        ),
        key=lambda row: (row.effective_to, row.security_id, row.membership_id),
    )
    historical_removal: Optional[HistoricalRemovalEvidence] = None
    if not removal_candidates:
        add(
            HARD,
            "missing_historical_removal_evidence",
            "no historical universe removal is available for end-to-end evidence",
        )
    else:
        row = removal_candidates[0]
        security = security_by_id[row.security_id]
        price_dates = sorted(selected_prices.get(row.security_id, {}))
        before = [day for day in price_dates if day <= row.effective_to]
        after = [day for day in price_dates if day > row.effective_to]
        historical_removal = HistoricalRemovalEvidence(
            membership_id=row.membership_id,
            security_id=row.security_id,
            ticker=security.ticker,
            effective_to=row.effective_to,
            announced_at=row.announced_at,
            membership_source_snapshot_id=row.source_snapshot_id,
            membership_source_hash=row.source_hash,
            permanent_identifiers=_permanent_identifier_labels(
                identifiers, row.security_id
            ),
            last_member_price_date=before[-1] if before else None,
            first_post_removal_price_date=after[0] if after else None,
            security_history_retained=True,
        )

    delisting_candidates = sorted(
        (
            row
            for row in delistings
            if universe.window_start <= row.delisting_date <= universe.window_end
        ),
        key=lambda row: (row.delisting_date, row.security_id, row.delisting_event_id),
    )
    delisting_evidence: Optional[DelistingEvidence] = None
    if not delisting_candidates:
        add(
            HARD,
            "missing_delisting_evidence",
            "no delisted member is available for end-to-end evidence",
        )
    else:
        event = delisting_candidates[0]
        security = security_by_id[event.security_id]
        price_dates = sorted(
            day
            for day in selected_prices.get(event.security_id, {})
            if day <= event.delisting_date
        )
        membership_closed = any(
            row.effective_to is not None
            and row.effective_to <= event.delisting_date
            for row in memberships_by_security.get(event.security_id, [])
        )
        delisting_evidence = DelistingEvidence(
            delisting_event_id=event.delisting_event_id,
            security_id=event.security_id,
            ticker=security.ticker,
            delisting_date=event.delisting_date,
            announced_at=event.announced_at,
            delisting_return=event.delisting_return,
            return_available_at=event.return_available_at,
            reason=event.reason,
            source_snapshot_id=event.source_snapshot_id,
            source_hash=event.source_hash,
            permanent_identifiers=_permanent_identifier_labels(
                identifiers, event.security_id
            ),
            final_price_date=price_dates[-1] if price_dates else None,
            membership_closed_by_delisting=membership_closed,
            security_history_retained=True,
        )
        if not membership_closed:
            add(
                HARD,
                "delisting_without_closed_membership",
                "delisted evidence security has no membership closed by delisting",
                security_id=event.security_id,
                dates=(event.delisting_date,),
            )
        if not price_dates:
            add(
                HARD,
                "delisting_without_terminal_price_history",
                "delisted evidence security has no price on or before delisting",
                security_id=event.security_id,
                dates=(event.delisting_date,),
            )

    findings.sort(
        key=lambda row: (
            0 if row.severity == HARD else 1,
            row.code,
            row.ticker or "",
            row.dates,
        )
    )
    lineage_ids = {
        universe.source_snapshot_id,
        *(row.source_snapshot_id for row in memberships),
        *(row.source_snapshot_id for row in identifiers),
        *(row.source_snapshot_id for row in aliases),
        *(row.source_snapshot_id for row in actions),
        *(row.source_snapshot_id for row in delistings),
        *source_snapshot_ids,
    }
    return PointInTimeEquityPanelAudit(
        universe_id=universe_id,
        window_start=universe.window_start,
        window_end=universe.window_end,
        calendar=calendar,
        benchmark_security_id=universe.benchmark_security_id,
        security_count=len(member_security_ids),
        membership_count=len(memberships),
        price_count=len(price_records),
        corporate_action_count=len(actions),
        delisting_count=len(delistings),
        source_snapshot_ids=tuple(sorted(lineage_ids)),
        membership_count_by_month=membership_count_by_month,
        membership_content_hash=universe_membership_hash(
            session, universe_id=universe_id
        ),
        snapshot_binding={
            "universe_snapshot": {
                "snapshot_id": universe.source_snapshot_id,
                "source_hash": universe.source_hash,
            },
            "membership_snapshots": {
                snapshot_id: snapshots[snapshot_id].source_hash
                for snapshot_id in sorted(
                    {row.source_snapshot_id for row in memberships}
                )
                if snapshot_id in snapshots
            },
            "price_snapshots_by_security": {
                security_id: {
                    "snapshot_id": snapshot_id,
                    "source_hash": snapshots[snapshot_id].source_hash,
                }
                for security_id, snapshot_id in sorted(
                    chosen_price_snapshot_ids.items()
                )
                if snapshot_id in snapshots
            },
            "role_snapshots": contract.get("role_snapshots", {}),
        },
        findings=tuple(findings),
        historical_removal=historical_removal,
        delisting=delisting_evidence,
    )
