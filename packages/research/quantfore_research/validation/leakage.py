"""Point-in-time leakage guards for historical feature construction."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Iterable, Mapping, NamedTuple, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantfore_research.features.baseline import calculate_baseline_price_features
from quantfore_research.models import (
    DelistingEvent,
    Feature,
    Price,
    Security,
    SourceSnapshot,
    TickerAlias,
    UniverseDefinition,
    UniverseMembership,
)


@dataclass(frozen=True)
class LeakageViolation:
    code: str
    message: str
    record_id: Optional[str] = None
    security_id: Optional[str] = None


class PointInTimeLeakageError(ValueError):
    """One or more candidate inputs were unavailable at prediction time."""

    def __init__(self, violations: Iterable[LeakageViolation]) -> None:
        self.violations = tuple(violations)
        super().__init__(
            "; ".join(
                f"{violation.code}: {violation.message}"
                for violation in self.violations
            )
        )


@dataclass(frozen=True)
class PointInTimeInputEvidence:
    input_type: str
    record_id: str
    security_id: str
    model_available_at: datetime
    membership_effective_from: Optional[date] = None
    membership_effective_to: Optional[date] = None
    price_date: Optional[date] = None
    source_snapshot_id: Optional[str] = None


@dataclass(frozen=True)
class PointInTimeSecurityContext:
    universe_id: str
    security: Security
    membership: UniverseMembership
    ticker_alias: TickerAlias
    prediction_timestamp: datetime
    evidence: tuple[PointInTimeInputEvidence, ...]


class PriceHistoryRow(NamedTuple):
    """The price columns the point-in-time feature path actually consumes."""

    price_id: str
    security_id: str
    date: date
    adj_close: Decimal
    source_snapshot_id: str


@dataclass(frozen=True)
class PointInTimeFeatureInputs:
    context: PointInTimeSecurityContext
    source_snapshot: SourceSnapshot
    prices: tuple[PriceHistoryRow, ...]
    evidence: tuple[PointInTimeInputEvidence, ...]


@dataclass(frozen=True)
class PointInTimeFeatureResult:
    inputs: PointInTimeFeatureInputs
    values: Mapping[str, Decimal]


def _prediction_timestamp(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("prediction_timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _stored_timestamp(value: datetime) -> datetime:
    """Normalize database timestamps; SQLite returns UTC columns as naive."""

    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def prediction_timestamp_for_date(value: date) -> datetime:
    """Return the contract's date-level availability boundary in UTC."""

    return datetime.combine(value, time.max, tzinfo=timezone.utc)


def price_model_available_at(price_date: date) -> datetime:
    """Date-level market availability used by the daily Sprint 7 contract."""

    return datetime.combine(price_date, time.min, tzinfo=timezone.utc)


def validate_point_in_time_evidence(
    evidence: Sequence[PointInTimeInputEvidence],
    *,
    prediction_timestamp: datetime,
) -> None:
    """Enforce all four Sprint 7.5 availability inequalities."""

    timestamp = _prediction_timestamp(prediction_timestamp)
    prediction_date = timestamp.date()
    violations: list[LeakageViolation] = []
    for item in evidence:
        available_at = _stored_timestamp(item.model_available_at)
        if available_at > timestamp:
            violations.append(
                LeakageViolation(
                    "INPUT_AVAILABLE_IN_FUTURE",
                    f"{item.input_type} became available at {available_at.isoformat()} "
                    f"after prediction {timestamp.isoformat()}",
                    item.record_id,
                    item.security_id,
                )
            )
        if (
            item.membership_effective_from is not None
            and item.membership_effective_from > prediction_date
        ):
            violations.append(
                LeakageViolation(
                    "MEMBERSHIP_STARTS_IN_FUTURE",
                    "membership_effective_from is after prediction_date",
                    item.record_id,
                    item.security_id,
                )
            )
        if (
            item.membership_effective_to is not None
            and item.membership_effective_to < prediction_date
        ):
            violations.append(
                LeakageViolation(
                    "MEMBERSHIP_ENDED_BEFORE_PREDICTION",
                    "membership_effective_to is before prediction_date",
                    item.record_id,
                    item.security_id,
                )
            )
        if item.price_date is not None and item.price_date > prediction_date:
            violations.append(
                LeakageViolation(
                    "PRICE_FROM_FUTURE",
                    "price_date is after prediction_date",
                    item.record_id,
                    item.security_id,
                )
            )
    if violations:
        raise PointInTimeLeakageError(violations)


def _membership_evidence(row: UniverseMembership) -> PointInTimeInputEvidence:
    return PointInTimeInputEvidence(
        input_type="universe_membership",
        record_id=row.membership_id,
        security_id=row.security_id,
        model_available_at=_stored_timestamp(row.announced_at),
        membership_effective_from=row.effective_from,
        membership_effective_to=row.effective_to,
        source_snapshot_id=row.source_snapshot_id,
    )


def _alias_evidence(row: TickerAlias) -> PointInTimeInputEvidence:
    return PointInTimeInputEvidence(
        input_type="ticker_alias",
        record_id=row.ticker_alias_id,
        security_id=row.security_id,
        model_available_at=_stored_timestamp(row.announced_at),
        source_snapshot_id=row.source_snapshot_id,
    )


def _price_evidence(
    row: Price,
    *,
    model_available_at: Optional[datetime] = None,
) -> PointInTimeInputEvidence:
    return PointInTimeInputEvidence(
        input_type="daily_price",
        record_id=row.price_id,
        security_id=row.security_id,
        model_available_at=model_available_at or price_model_available_at(row.date),
        price_date=row.date,
        source_snapshot_id=row.source_snapshot_id,
    )


def validate_candidate_price_inputs(
    prices: Sequence[Price],
    *,
    prediction_timestamp: datetime,
    model_available_at: Optional[Mapping[str, datetime]] = None,
) -> tuple[PointInTimeInputEvidence, ...]:
    """Reject a candidate price collection containing any unavailable row."""

    evidence = tuple(
        _price_evidence(
            row,
            model_available_at=(model_available_at or {}).get(row.price_id),
        )
        for row in prices
    )
    validate_point_in_time_evidence(
        evidence, prediction_timestamp=prediction_timestamp
    )
    return evidence


def validate_stored_feature_inputs(
    features: Sequence[Feature],
    *,
    prediction_timestamp: datetime,
) -> None:
    """Prevent score construction from consuming future calculated features."""

    timestamp = _prediction_timestamp(prediction_timestamp)
    violations: list[LeakageViolation] = []
    for feature in features:
        available_at = _stored_timestamp(feature.available_at)
        if available_at > timestamp:
            violations.append(
                LeakageViolation(
                    "FEATURE_AVAILABLE_IN_FUTURE",
                    f"feature became available at {available_at.isoformat()} after "
                    f"prediction {timestamp.isoformat()}",
                    feature.feature_id,
                    feature.security_id,
                )
            )
        if feature.asof_date > timestamp.date():
            violations.append(
                LeakageViolation(
                    "FEATURE_ASOF_IN_FUTURE",
                    "feature asof_date is after prediction_date",
                    feature.feature_id,
                    feature.security_id,
                )
            )
        lineage = feature.inputs_json or {}
        for item in lineage.get("inputs", []):
            if not isinstance(item, Mapping):
                violations.append(
                    LeakageViolation(
                        "FEATURE_INPUT_LINEAGE_INVALID",
                        "feature input lineage must contain objects",
                        feature.feature_id,
                        feature.security_id,
                    )
                )
                continue
            raw_available_at = item.get("model_available_at")
            try:
                input_available_at = datetime.fromisoformat(
                    str(raw_available_at).replace("Z", "+00:00")
                )
            except ValueError:
                violations.append(
                    LeakageViolation(
                        "FEATURE_INPUT_LINEAGE_INVALID",
                        "feature input model_available_at must be ISO 8601",
                        str(item.get("record_id") or feature.feature_id),
                        feature.security_id,
                    )
                )
                continue
            if input_available_at.tzinfo is None:
                violations.append(
                    LeakageViolation(
                        "FEATURE_INPUT_LINEAGE_INVALID",
                        "feature input model_available_at must include a timezone",
                        str(item.get("record_id") or feature.feature_id),
                        feature.security_id,
                    )
                )
                continue
            if input_available_at.astimezone(timezone.utc) > timestamp:
                violations.append(
                    LeakageViolation(
                        "FEATURE_INPUT_AVAILABLE_IN_FUTURE",
                        "stored feature input became available after prediction",
                        str(item.get("record_id") or feature.feature_id),
                        feature.security_id,
                    )
                )
    if violations:
        raise PointInTimeLeakageError(violations)


def _active_memberships(
    session: Session,
    *,
    universe_id: str,
    security_id: str,
    prediction_date: date,
) -> list[UniverseMembership]:
    return session.scalars(
        select(UniverseMembership)
        .where(UniverseMembership.universe_id == universe_id)
        .where(UniverseMembership.security_id == security_id)
        .where(UniverseMembership.effective_from <= prediction_date)
        .where(
            (UniverseMembership.effective_to.is_(None))
            | (UniverseMembership.effective_to >= prediction_date)
        )
        .order_by(UniverseMembership.announced_at, UniverseMembership.membership_id)
    ).all()


def _one_known_membership(
    rows: Sequence[UniverseMembership],
    *,
    security_id: str,
    prediction_timestamp: datetime,
) -> UniverseMembership:
    timestamp = _prediction_timestamp(prediction_timestamp)
    if not rows:
        raise PointInTimeLeakageError(
            [
                LeakageViolation(
                    "MEMBERSHIP_NOT_EFFECTIVE",
                    "security is not a universe member on prediction_date",
                    security_id=security_id,
                )
            ]
        )
    unavailable = [row for row in rows if _stored_timestamp(row.announced_at) > timestamp]
    if unavailable:
        raise PointInTimeLeakageError(
            [
                LeakageViolation(
                    "REVISED_MEMBERSHIP_UNAVAILABLE",
                    "an effective membership record was announced after prediction time",
                    row.membership_id,
                    security_id,
                )
                for row in unavailable
            ]
        )
    if len(rows) != 1:
        raise PointInTimeLeakageError(
            [
                LeakageViolation(
                    "AMBIGUOUS_MEMBERSHIP",
                    "multiple effective membership records exist at prediction time",
                    security_id=security_id,
                )
            ]
        )
    return rows[0]


def resolve_point_in_time_security(
    session: Session,
    *,
    universe_id: str,
    ticker: str,
    prediction_timestamp: datetime,
) -> PointInTimeSecurityContext:
    """Resolve a ticker using only the alias and membership known at that time."""

    timestamp = _prediction_timestamp(prediction_timestamp)
    prediction_date = timestamp.date()
    universe = session.get(UniverseDefinition, universe_id)
    if universe is None:
        raise ValueError(f"unknown universe: {universe_id}")
    if not universe.window_start <= prediction_date <= universe.window_end:
        raise PointInTimeLeakageError(
            [
                LeakageViolation(
                    "PREDICTION_OUTSIDE_UNIVERSE_WINDOW",
                    "prediction_date is outside the frozen universe window",
                )
            ]
        )

    normalized_ticker = ticker.strip().upper()
    ticker_rows = session.scalars(
        select(TickerAlias)
        .where(TickerAlias.ticker == normalized_ticker)
        .order_by(TickerAlias.effective_from, TickerAlias.ticker_alias_id)
    ).all()
    active_aliases = [
        row
        for row in ticker_rows
        if row.effective_from <= prediction_date
        and (row.effective_to is None or row.effective_to >= prediction_date)
    ]
    if not active_aliases:
        code = "TICKER_NOT_EFFECTIVE" if ticker_rows else "UNKNOWN_TICKER"
        raise PointInTimeLeakageError(
            [
                LeakageViolation(
                    code,
                    f"ticker {normalized_ticker} was not an effective alias at prediction time",
                )
            ]
        )

    contexts: list[PointInTimeSecurityContext] = []
    violations: list[LeakageViolation] = []
    for alias in active_aliases:
        if _stored_timestamp(alias.announced_at) > timestamp:
            violations.append(
                LeakageViolation(
                    "TICKER_UNAVAILABLE",
                    f"ticker {normalized_ticker} was announced after prediction time",
                    alias.ticker_alias_id,
                    alias.security_id,
                )
            )
            continue
        memberships = _active_memberships(
            session,
            universe_id=universe_id,
            security_id=alias.security_id,
            prediction_date=prediction_date,
        )
        try:
            membership = _one_known_membership(
                memberships,
                security_id=alias.security_id,
                prediction_timestamp=timestamp,
            )
        except PointInTimeLeakageError as exc:
            violations.extend(exc.violations)
            continue
        security = session.get(Security, alias.security_id)
        if security is None:
            violations.append(
                LeakageViolation(
                    "UNRESOLVED_SECURITY",
                    "ticker alias does not resolve to a permanent security",
                    alias.ticker_alias_id,
                    alias.security_id,
                )
            )
            continue
        evidence = (_membership_evidence(membership), _alias_evidence(alias))
        validate_point_in_time_evidence(evidence, prediction_timestamp=timestamp)
        contexts.append(
            PointInTimeSecurityContext(
                universe_id=universe_id,
                security=security,
                membership=membership,
                ticker_alias=alias,
                prediction_timestamp=timestamp,
                evidence=evidence,
            )
        )
    if violations and not contexts:
        raise PointInTimeLeakageError(violations)
    if len(contexts) != 1:
        raise PointInTimeLeakageError(
            [
                LeakageViolation(
                    "AMBIGUOUS_TICKER",
                    f"ticker {normalized_ticker} resolves to multiple securities",
                )
            ]
        )
    return contexts[0]


def expected_point_in_time_cohort(
    session: Session,
    *,
    universe_id: str,
    prediction_timestamp: datetime,
) -> tuple[PointInTimeSecurityContext, ...]:
    """Reconstruct every eligible member, retaining later-delisted securities."""

    timestamp = _prediction_timestamp(prediction_timestamp)
    prediction_date = timestamp.date()
    universe = session.get(UniverseDefinition, universe_id)
    if universe is None:
        raise ValueError(f"unknown universe: {universe_id}")
    rows = session.scalars(
        select(UniverseMembership)
        .where(UniverseMembership.universe_id == universe_id)
        .where(UniverseMembership.effective_from <= prediction_date)
        .where(
            (UniverseMembership.effective_to.is_(None))
            | (UniverseMembership.effective_to >= prediction_date)
        )
        .order_by(UniverseMembership.security_id, UniverseMembership.membership_id)
    ).all()
    grouped: dict[str, list[UniverseMembership]] = {}
    for row in rows:
        grouped.setdefault(row.security_id, []).append(row)
    contexts: list[PointInTimeSecurityContext] = []
    violations: list[LeakageViolation] = []
    for security_id, memberships in grouped.items():
        try:
            membership = _one_known_membership(
                memberships,
                security_id=security_id,
                prediction_timestamp=timestamp,
            )
        except PointInTimeLeakageError as exc:
            violations.extend(exc.violations)
            continue
        security = session.get(Security, security_id)
        if security is None:
            violations.append(
                LeakageViolation(
                    "UNRESOLVED_SECURITY",
                    "membership does not resolve to a permanent security",
                    membership.membership_id,
                    security_id,
                )
            )
            continue
        if (
            (security.active_from is not None and security.active_from > prediction_date)
            or (security.active_to is not None and security.active_to < prediction_date)
        ):
            violations.append(
                LeakageViolation(
                    "MEMBER_OUTSIDE_LISTING_PERIOD",
                    "effective member is outside its listing period",
                    membership.membership_id,
                    security_id,
                )
            )
            continue
        prior_delisting = session.scalar(
            select(DelistingEvent)
            .where(DelistingEvent.security_id == security_id)
            .where(DelistingEvent.delisting_date < prediction_date)
            .limit(1)
        )
        if prior_delisting is not None:
            violations.append(
                LeakageViolation(
                    "MEMBER_ALREADY_DELISTED",
                    "membership remains effective after the security delisted",
                    prior_delisting.delisting_event_id,
                    security_id,
                )
            )
            continue
        aliases = session.scalars(
            select(TickerAlias)
            .where(TickerAlias.security_id == security_id)
            .where(TickerAlias.effective_from <= prediction_date)
            .where(
                (TickerAlias.effective_to.is_(None))
                | (TickerAlias.effective_to >= prediction_date)
            )
            .order_by(TickerAlias.ticker_alias_id)
        ).all()
        known_aliases = [
            alias
            for alias in aliases
            if _stored_timestamp(alias.announced_at) <= timestamp
        ]
        if len(known_aliases) != 1:
            violations.append(
                LeakageViolation(
                    "MISSING_OR_AMBIGUOUS_HISTORICAL_TICKER",
                    "member must have exactly one ticker alias known at prediction time",
                    membership.membership_id,
                    security_id,
                )
            )
            continue
        alias = known_aliases[0]
        evidence = (_membership_evidence(membership), _alias_evidence(alias))
        validate_point_in_time_evidence(evidence, prediction_timestamp=timestamp)
        contexts.append(
            PointInTimeSecurityContext(
                universe_id=universe_id,
                security=security,
                membership=membership,
                ticker_alias=alias,
                prediction_timestamp=timestamp,
                evidence=evidence,
            )
        )
    if violations:
        raise PointInTimeLeakageError(violations)
    return tuple(sorted(contexts, key=lambda item: item.security.security_id))


def validate_point_in_time_cohort(
    session: Session,
    *,
    universe_id: str,
    prediction_timestamp: datetime,
    candidate_security_ids: Sequence[str],
) -> tuple[PointInTimeSecurityContext, ...]:
    """Prove a candidate cohort contains every historical member exactly once."""

    expected = expected_point_in_time_cohort(
        session,
        universe_id=universe_id,
        prediction_timestamp=prediction_timestamp,
    )
    expected_ids = {context.security.security_id for context in expected}
    candidate_ids = set(candidate_security_ids)
    violations: list[LeakageViolation] = []
    if len(candidate_ids) != len(candidate_security_ids):
        violations.append(
            LeakageViolation(
                "DUPLICATE_COHORT_SECURITY",
                "candidate cohort contains duplicate security IDs",
            )
        )
    for security_id in sorted(expected_ids - candidate_ids):
        violations.append(
            LeakageViolation(
                "COHORT_MISSING_SECURITY",
                "historically eligible security is missing from the candidate cohort",
                security_id=security_id,
            )
        )
    for security_id in sorted(candidate_ids - expected_ids):
        violations.append(
            LeakageViolation(
                "COHORT_INELIGIBLE_SECURITY",
                "candidate cohort includes a security not historically eligible",
                security_id=security_id,
            )
        )
    if violations:
        raise PointInTimeLeakageError(violations)
    return expected


def _price_snapshot_candidates(
    session: Session, *, security_id: str
) -> tuple[tuple[SourceSnapshot, date], ...]:
    """All adjusted-price snapshots for one security, newest first.

    Each entry carries the snapshot's earliest adjusted-close date so callers
    can reproduce the per-prediction-date eligibility filter without repeating
    the join. Memoized per session: prices are immutable while features are
    being constructed and stored.
    """

    cache = session.info.setdefault("pit_price_snapshot_candidates", {})
    cached = cache.get(security_id)
    if cached is None:
        rows = session.execute(
            select(
                Price.source_snapshot_id,
                func.min(Price.date).label("first_date"),
            )
            .where(Price.security_id == security_id)
            .where(Price.adj_close.is_not(None))
            .group_by(Price.source_snapshot_id)
        ).all()
        snapshots = {
            snapshot.snapshot_id: snapshot
            for snapshot in session.scalars(
                select(SourceSnapshot).where(
                    SourceSnapshot.snapshot_id.in_(
                        [row.source_snapshot_id for row in rows]
                    )
                )
            )
        } if rows else {}
        cached = tuple(
            sorted(
                (
                    (snapshots[row.source_snapshot_id], row.first_date)
                    for row in rows
                ),
                key=lambda item: (
                    _stored_timestamp(item[0].retrieved_at),
                    item[0].snapshot_id,
                ),
                reverse=True,
            )
        )
        cache[security_id] = cached
    return cached


def _snapshot_price_history(
    session: Session, *, security_id: str, snapshot_id: str
) -> tuple[
    tuple[PriceHistoryRow, ...],
    tuple[date, ...],
    tuple[PointInTimeInputEvidence, ...],
]:
    """One immutable adjusted-close history, its date index, and its evidence.

    Every element is a pure function of the stored rows, so prefix slices of
    these parallel tuples reproduce exactly what per-prediction-date queries
    and per-row evidence construction previously returned.
    """

    cache = session.info.setdefault("pit_price_history_cache", {})
    key = (security_id, snapshot_id)
    cached = cache.get(key)
    if cached is None:
        rows = tuple(
            PriceHistoryRow(
                price_id=row.price_id,
                security_id=row.security_id,
                date=row.date,
                adj_close=row.adj_close,
                source_snapshot_id=row.source_snapshot_id,
            )
            for row in session.execute(
                select(
                    Price.price_id,
                    Price.security_id,
                    Price.date,
                    Price.adj_close,
                    Price.source_snapshot_id,
                )
                .where(Price.security_id == security_id)
                .where(Price.source_snapshot_id == snapshot_id)
                .where(Price.adj_close.is_not(None))
                .order_by(Price.date, Price.price_id)
            )
        )
        cached = (
            rows,
            tuple(row.date for row in rows),
            tuple(_price_evidence(row) for row in rows),
        )
        cache[key] = cached
    return cached


def load_point_in_time_feature_inputs(
    session: Session,
    *,
    context: PointInTimeSecurityContext,
    source_snapshot_id: Optional[str] = None,
) -> PointInTimeFeatureInputs:
    """Load one coherent historical price snapshot and prove every input."""

    prediction_date = context.prediction_timestamp.date()
    # Equivalent to the previous per-call query, which picked the newest
    # (retrieved_at, snapshot_id) snapshot owning at least one adjusted close
    # on or before prediction_date, then loaded that snapshot's rows up to
    # prediction_date ordered by (date, price_id).
    snapshot = None
    for candidate_snapshot, first_date in _price_snapshot_candidates(
        session, security_id=context.security.security_id
    ):
        if (
            source_snapshot_id is not None
            and candidate_snapshot.snapshot_id != source_snapshot_id
        ):
            continue
        if first_date <= prediction_date:
            snapshot = candidate_snapshot
            break
    if snapshot is None:
        raise PointInTimeLeakageError(
            [
                LeakageViolation(
                    "PRICE_HISTORY_UNAVAILABLE",
                    "no eligible adjusted price snapshot exists by prediction_date",
                    source_snapshot_id,
                    context.security.security_id,
                )
            ]
        )
    history, history_dates, history_evidence = _snapshot_price_history(
        session,
        security_id=context.security.security_id,
        snapshot_id=snapshot.snapshot_id,
    )
    eligible = bisect_right(history_dates, prediction_date)
    prices = history[:eligible]
    # Slicing the precomputed per-row evidence and validating it matches
    # validate_candidate_price_inputs, which built the same evidence rows
    # before running the identical availability gate.
    price_evidence = history_evidence[:eligible]
    validate_point_in_time_evidence(
        price_evidence, prediction_timestamp=context.prediction_timestamp
    )
    evidence = (*context.evidence, *price_evidence)
    validate_point_in_time_evidence(
        evidence, prediction_timestamp=context.prediction_timestamp
    )
    return PointInTimeFeatureInputs(
        context=context,
        source_snapshot=snapshot,
        prices=prices,
        evidence=tuple(evidence),
    )


def construct_point_in_time_baseline_features(
    session: Session,
    *,
    universe_id: str,
    ticker: str,
    prediction_timestamp: datetime,
    source_snapshot_id: Optional[str] = None,
) -> PointInTimeFeatureResult:
    """Resolve, load, validate, then calculate the unchanged baseline features."""

    context = resolve_point_in_time_security(
        session,
        universe_id=universe_id,
        ticker=ticker,
        prediction_timestamp=prediction_timestamp,
    )
    inputs = load_point_in_time_feature_inputs(
        session,
        context=context,
        source_snapshot_id=source_snapshot_id,
    )
    values = calculate_baseline_price_features(
        inputs.prices,
        asof_date=context.prediction_timestamp.date(),
    )
    return PointInTimeFeatureResult(inputs=inputs, values=values)
