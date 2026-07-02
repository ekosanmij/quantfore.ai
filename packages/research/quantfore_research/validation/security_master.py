"""Hard validation gates for the point-in-time security master."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.models import (
    CorporateAction,
    DelistingEvent,
    Security,
    SecurityIdentifier,
    SourceSnapshot,
    TickerAlias,
    UniverseDefinition,
    UniverseMembership,
)


@dataclass(frozen=True)
class SecurityMasterValidationSummary:
    """Counts produced after all security-master hard gates pass."""

    security_count: int
    identifier_count: int
    ticker_alias_count: int
    membership_count: int
    universe_count: int
    corporate_action_count: int
    delisting_event_count: int


class SecurityMasterValidationError(ValueError):
    """One or more security-master invariants were violated."""

    def __init__(self, findings: Iterable[str]) -> None:
        self.findings = tuple(findings)
        super().__init__("; ".join(self.findings))


def _overlaps(
    left_from: date,
    left_to: Optional[date],
    right_from: date,
    right_to: Optional[date],
) -> bool:
    return (right_to is None or left_from <= right_to) and (
        left_to is None or right_from <= left_to
    )


def _normalise_key(value: str) -> str:
    return value.strip().upper()


def _is_sha256(value: str) -> bool:
    return re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_security_master(session: Session) -> SecurityMasterValidationSummary:
    """Validate resolution, lineage, membership periods, and dated identifiers.

    Call this inside the ingestion transaction after loading normalized records.
    It flushes pending rows so foreign-key failures are also hard failures. Date
    intervals are inclusive; adjacent periods therefore must begin the day after
    the preceding period ends.
    """

    session.flush()
    securities = session.scalars(select(Security)).all()
    identifiers = session.scalars(select(SecurityIdentifier)).all()
    aliases = session.scalars(select(TickerAlias)).all()
    memberships = session.scalars(select(UniverseMembership)).all()
    universes = session.scalars(select(UniverseDefinition)).all()
    corporate_actions = session.scalars(select(CorporateAction)).all()
    delisting_events = session.scalars(select(DelistingEvent)).all()
    snapshots = {
        snapshot.snapshot_id: snapshot
        for snapshot in session.scalars(select(SourceSnapshot)).all()
    }
    security_ids = {security.security_id for security in securities}
    findings: list[str] = []

    for membership in memberships:
        if membership.security_id not in security_ids:
            findings.append(
                "membership "
                f"{membership.membership_id} does not resolve to one security"
            )

    lineage_rows = [
        *identifiers,
        *aliases,
        *universes,
        *memberships,
        *corporate_actions,
        *delisting_events,
    ]
    for row in lineage_rows:
        snapshot = snapshots.get(row.source_snapshot_id)
        primary_key = next(iter(row.__mapper__.primary_key)).key
        row_label = f"{row.__tablename__}:{getattr(row, primary_key)}"
        if snapshot is None:
            findings.append(f"{row_label} references a missing source snapshot")
        elif row.source_hash != snapshot.source_hash:
            findings.append(f"{row_label} source_hash does not match its snapshot")
        elif not _is_sha256(row.source_hash):
            findings.append(f"{row_label} source_hash is not a lowercase SHA-256")

    memberships_by_key: dict[tuple[str, str], list[UniverseMembership]] = {}
    for membership in memberships:
        memberships_by_key.setdefault(
            (membership.universe_id, membership.security_id), []
        ).append(membership)
    for key, rows in memberships_by_key.items():
        rows.sort(key=lambda row: (row.effective_from, row.membership_id))
        for position, left in enumerate(rows):
            for right in rows[position + 1 :]:
                if _overlaps(
                    left.effective_from,
                    left.effective_to,
                    right.effective_from,
                    right.effective_to,
                ):
                    findings.append(
                        "overlapping universe memberships for "
                        f"universe={key[0]} security={key[1]}: "
                        f"{left.membership_id} and {right.membership_id}"
                    )

    aliases_by_ticker: dict[str, list[TickerAlias]] = {}
    for alias in aliases:
        aliases_by_ticker.setdefault(_normalise_key(alias.ticker), []).append(alias)
    for ticker, rows in aliases_by_ticker.items():
        for position, left in enumerate(rows):
            for right in rows[position + 1 :]:
                if left.security_id != right.security_id and _overlaps(
                    left.effective_from,
                    left.effective_to,
                    right.effective_from,
                    right.effective_to,
                ):
                    findings.append(
                        f"ambiguous ticker mapping for {ticker}: "
                        f"security={left.security_id} and security={right.security_id}"
                    )

    identifiers_by_key: dict[tuple[str, str], list[SecurityIdentifier]] = {}
    for identifier in identifiers:
        key = (
            _normalise_key(identifier.identifier_type),
            _normalise_key(identifier.identifier_value),
        )
        identifiers_by_key.setdefault(key, []).append(identifier)
    for key, rows in identifiers_by_key.items():
        for position, left in enumerate(rows):
            for right in rows[position + 1 :]:
                if left.security_id != right.security_id and _overlaps(
                    left.valid_from,
                    left.valid_to,
                    right.valid_from,
                    right.valid_to,
                ):
                    findings.append(
                        "ambiguous security identifier mapping for "
                        f"{key[0]}={key[1]}: security={left.security_id} "
                        f"and security={right.security_id}"
                    )

    for universe in universes:
        if not universe.benchmark_excluded_from_rankings:
            findings.append(
                f"universe {universe.universe_id} permits benchmark ranking"
            )

    for action in corporate_actions:
        if (action.ratio_from is None) != (action.ratio_to is None):
            findings.append(
                f"corporate action {action.corporate_action_id} has an incomplete ratio"
            )

    for event in delisting_events:
        if event.delisting_return is not None and event.return_available_at is None:
            findings.append(
                f"delisting event {event.delisting_event_id} has a return without "
                "return_available_at"
            )

    if findings:
        raise SecurityMasterValidationError(findings)

    return SecurityMasterValidationSummary(
        security_count=len(securities),
        identifier_count=len(identifiers),
        ticker_alias_count=len(aliases),
        membership_count=len(memberships),
        universe_count=len(universes),
        corporate_action_count=len(corporate_actions),
        delisting_event_count=len(delisting_events),
    )
