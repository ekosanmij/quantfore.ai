"""Deterministic Sprint 8 audit for point-in-time company fundamentals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.models import (
    Fundamental,
    Security,
    SecurityIdentifier,
    SourceSnapshot,
)


HARD = "hard"
REVIEW = "review"
STANDARD_SECTORS = frozenset(
    {
        "Communication Services",
        "Consumer Discretionary",
        "Consumer Staples",
        "Energy",
        "Financials",
        "Health Care",
        "Industrials",
        "Information Technology",
        "Materials",
        "Real Estate",
        "Utilities",
    }
)


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
    if isinstance(value, Mapping):
        return {str(key): _iso(item) for key, item in sorted(value.items())}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_iso(item) for item in value]
    return value


@dataclass(frozen=True)
class FundamentalAuditFinding:
    severity: str
    code: str
    message: str
    security_id: Optional[str] = None
    fundamental_ids: tuple[str, ...] = ()
    context: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "security_id": self.security_id,
            "fundamental_ids": list(self.fundamental_ids),
            "context": _iso(dict(self.context or {})),
        }


@dataclass(frozen=True)
class SecReconciliationSample:
    vendor_fundamental_id: str
    security_id: str
    sector: str
    fiscal_period_end: date
    standardized_concept: str
    sec_value: Decimal
    sec_unit: str
    sec_filing_accession: str
    sec_source_snapshot_id: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SecReconciliationSample":
        try:
            period_end = date.fromisoformat(str(value["fiscal_period_end"]))
            sec_value = Decimal(str(value["sec_value"]))
        except (KeyError, ValueError, InvalidOperation) as exc:
            raise ValueError("invalid SEC reconciliation sample") from exc
        required = {
            name: str(value.get(name) or "").strip()
            for name in (
                "vendor_fundamental_id",
                "security_id",
                "sector",
                "standardized_concept",
                "sec_unit",
                "sec_filing_accession",
                "sec_source_snapshot_id",
            )
        }
        missing = [name for name, item in required.items() if not item]
        if missing or not sec_value.is_finite():
            raise ValueError(
                "invalid SEC reconciliation sample fields: " + ", ".join(missing)
            )
        return cls(
            fiscal_period_end=period_end,
            sec_value=sec_value,
            **required,
        )


@dataclass(frozen=True)
class PointInTimeFundamentalAudit:
    fact_count: int
    security_count: int
    source_snapshot_ids: tuple[str, ...]
    fact_hash: str
    availability_revision_hash: str
    findings: tuple[FundamentalAuditFinding, ...]
    reconciliation_sample_count: int
    reconciliation_issuer_period_count: int
    reconciliation_sectors: tuple[str, ...]
    reconciliation_gate_enforced: bool
    evidence_mode: str

    @property
    def hard_failure_count(self) -> int:
        return sum(row.severity == HARD for row in self.findings)

    @property
    def review_finding_count(self) -> int:
        return sum(row.severity == REVIEW for row in self.findings)

    @property
    def status(self) -> str:
        if self.hard_failure_count:
            return "fail"
        if self.review_finding_count:
            return "review"
        return "pass"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "hard_failure_count": self.hard_failure_count,
            "review_finding_count": self.review_finding_count,
            "counts": {
                "facts": self.fact_count,
                "securities": self.security_count,
                "source_snapshots": len(self.source_snapshot_ids),
            },
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "fact_hash": self.fact_hash,
            "availability_revision_hash": self.availability_revision_hash,
            "reconciliation": {
                "evidence_mode": self.evidence_mode,
                "gate_enforced": self.reconciliation_gate_enforced,
                "sample_count": self.reconciliation_sample_count,
                "issuer_period_count": self.reconciliation_issuer_period_count,
                "sectors": list(self.reconciliation_sectors),
                "minimum_issuer_periods": 30,
                "required_sectors": sorted(STANDARD_SECTORS),
            },
            "findings": [row.to_dict() for row in self.findings],
        }


def _hash_rows(rows: Iterable[Mapping[str, Any]]) -> str:
    payload = json.dumps(
        [_iso(row) for row in rows],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _fact_identity(row: Fundamental) -> tuple[object, ...]:
    return (
        row.security_id,
        row.fiscal_period_end,
        row.period_type,
        row.concept,
        row.unit,
    )


def _latest_standardized(
    facts: Sequence[Fundamental],
) -> dict[tuple[str, date, str, str], Fundamental]:
    result: dict[tuple[str, date, str, str], Fundamental] = {}
    for row in facts:
        key = (
            row.security_id,
            row.fiscal_period_end,
            row.period_type,
            row.standardized_concept,
        )
        prior = result.get(key)
        if prior is None or (
            _utc(row.model_available_at), row.revision_version, row.fundamental_id
        ) > (
            _utc(prior.model_available_at),
            prior.revision_version,
            prior.fundamental_id,
        ):
            result[key] = row
    return result


def _overlaps(left: SecurityIdentifier, right: SecurityIdentifier) -> bool:
    return (right.valid_to is None or left.valid_from <= right.valid_to) and (
        left.valid_to is None or right.valid_from <= left.valid_to
    )


def derive_sec_reconciliation_samples(
    session: Session,
    *,
    vendor_source_snapshot_ids: Optional[Sequence[str]] = None,
) -> tuple[SecReconciliationSample, ...]:
    """Deterministically pair primary facts with registered SEC facts.

    One concept is selected per issuer-period. Selection first covers sectors
    alphabetically, then fills the remaining issuer-periods in stable order.
    The audit gate, not this function, decides whether 30/11 coverage exists.
    """

    snapshots = {
        row.snapshot_id: row
        for row in session.scalars(select(SourceSnapshot)).all()
    }
    query = select(Fundamental.__table__)
    all_facts = list(session.execute(query).all())
    requested = set(vendor_source_snapshot_ids or ())
    vendor_facts = [
        row
        for row in all_facts
        if "SEC" not in snapshots[row.source_snapshot_id].vendor.upper()
        and (not requested or row.source_snapshot_id in requested)
    ]
    sec_facts = [
        row
        for row in all_facts
        if "SEC" in snapshots[row.source_snapshot_id].vendor.upper()
    ]

    def latest_by_key(rows: Sequence[Fundamental]) -> dict[tuple[object, ...], Fundamental]:
        result: dict[tuple[object, ...], Fundamental] = {}
        for row in rows:
            key = (
                row.security_id,
                row.fiscal_period_end,
                row.period_type,
                row.standardized_concept,
            )
            prior = result.get(key)
            if prior is None or (
                _utc(row.model_available_at), row.revision_version, row.fundamental_id
            ) > (
                _utc(prior.model_available_at),
                prior.revision_version,
                prior.fundamental_id,
            ):
                result[key] = row
        return result

    vendor_latest = latest_by_key(vendor_facts)
    sec_latest = latest_by_key(sec_facts)
    security_ids = {row.security_id for row in vendor_latest.values()}
    securities = {
        row.security_id: row
        for row in session.scalars(
            select(Security).where(Security.security_id.in_(security_ids))
        ).all()
    } if security_ids else {}
    candidates: list[SecReconciliationSample] = []
    for key in sorted(vendor_latest, key=lambda item: tuple(str(value) for value in item)):
        vendor = vendor_latest[key]
        sec = sec_latest.get(key)
        security = securities.get(vendor.security_id)
        if sec is None or security is None or not security.sector:
            continue
        candidates.append(
            SecReconciliationSample(
                vendor_fundamental_id=vendor.fundamental_id,
                security_id=vendor.security_id,
                sector=security.sector,
                fiscal_period_end=vendor.fiscal_period_end,
                standardized_concept=vendor.standardized_concept,
                sec_value=sec.value,
                sec_unit=sec.unit,
                sec_filing_accession=sec.filing_accession,
                sec_source_snapshot_id=sec.source_snapshot_id,
            )
        )

    selected: list[SecReconciliationSample] = []
    selected_periods: set[tuple[str, date]] = set()
    for sector in sorted(STANDARD_SECTORS):
        candidate = next(
            (
                row
                for row in candidates
                if row.sector == sector
                and (row.security_id, row.fiscal_period_end) not in selected_periods
            ),
            None,
        )
        if candidate is not None:
            selected.append(candidate)
            selected_periods.add((candidate.security_id, candidate.fiscal_period_end))
    for candidate in candidates:
        key = (candidate.security_id, candidate.fiscal_period_end)
        if key in selected_periods:
            continue
        selected.append(candidate)
        selected_periods.add(key)
    return tuple(selected)


def _reconciliation_findings(
    session: Session,
    facts_by_id: Mapping[str, Fundamental],
    samples: Sequence[SecReconciliationSample],
    *,
    enforce_gate: bool,
    relative_tolerance: Decimal,
) -> tuple[list[FundamentalAuditFinding], int, tuple[str, ...]]:
    findings: list[FundamentalAuditFinding] = []
    issuer_periods = {
        (sample.security_id, sample.fiscal_period_end) for sample in samples
    }
    sectors = tuple(sorted({sample.sector for sample in samples}))
    if enforce_gate and len(issuer_periods) < 30:
        findings.append(
            FundamentalAuditFinding(
                HARD,
                "SEC_SAMPLE_TOO_SMALL",
                "SEC reconciliation requires at least 30 issuer-period samples",
                context={"actual": len(issuer_periods), "required": 30},
            )
        )
    missing_sectors = sorted(STANDARD_SECTORS - set(sectors))
    if enforce_gate and missing_sectors:
        findings.append(
            FundamentalAuditFinding(
                HARD,
                "SEC_SECTOR_COVERAGE_INCOMPLETE",
                "SEC reconciliation does not cover all 11 sectors",
                context={"missing_sectors": missing_sectors},
            )
        )

    snapshot_ids = {sample.sec_source_snapshot_id for sample in samples}
    snapshots = {
        row.snapshot_id: row
        for row in session.scalars(
            select(SourceSnapshot).where(SourceSnapshot.snapshot_id.in_(snapshot_ids))
        ).all()
    } if snapshot_ids else {}
    sec_facts = list(
        session.scalars(
            select(Fundamental).where(
                Fundamental.source_snapshot_id.in_(snapshot_ids)
            )
        ).all()
    ) if snapshot_ids else []
    for sample in samples:
        fact = facts_by_id.get(sample.vendor_fundamental_id)
        if fact is None:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "SEC_VENDOR_FACT_MISSING",
                    "reconciliation sample references an unknown vendor fact",
                    security_id=sample.security_id,
                    fundamental_ids=(sample.vendor_fundamental_id,),
                )
            )
            continue
        snapshot = snapshots.get(sample.sec_source_snapshot_id)
        if snapshot is None or "SEC" not in snapshot.vendor.upper():
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "SEC_SOURCE_EVIDENCE_MISSING",
                    "sample lacks a registered SEC source snapshot",
                    security_id=sample.security_id,
                    fundamental_ids=(fact.fundamental_id,),
                    context={"sec_source_snapshot_id": sample.sec_source_snapshot_id},
                )
            )
            continue
        matching_sec_facts = [
            row
            for row in sec_facts
            if row.source_snapshot_id == sample.sec_source_snapshot_id
            and row.security_id == sample.security_id
            and row.fiscal_period_end == sample.fiscal_period_end
            and row.standardized_concept == sample.standardized_concept
            and row.filing_accession == sample.sec_filing_accession
            and row.unit == sample.sec_unit
            and row.value == sample.sec_value
        ]
        if not matching_sec_facts:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "SEC_SOURCE_EVIDENCE_MISMATCH",
                    "sample value cannot be reconstructed from its SEC snapshot",
                    security_id=sample.security_id,
                    fundamental_ids=(fact.fundamental_id,),
                    context={
                        "sec_filing_accession": sample.sec_filing_accession,
                        "sec_source_snapshot_id": sample.sec_source_snapshot_id,
                    },
                )
            )
            continue
        metadata_mismatches = []
        if fact.security_id != sample.security_id:
            metadata_mismatches.append("security_id")
        if fact.fiscal_period_end != sample.fiscal_period_end:
            metadata_mismatches.append("fiscal_period_end")
        if fact.standardized_concept != sample.standardized_concept:
            metadata_mismatches.append("standardized_concept")
        if metadata_mismatches:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "SEC_SAMPLE_IDENTITY_MISMATCH",
                    "SEC sample does not identify the referenced vendor fact",
                    security_id=sample.security_id,
                    fundamental_ids=(fact.fundamental_id,),
                    context={"mismatches": metadata_mismatches},
                )
            )
            continue
        if fact.unit != sample.sec_unit:
            findings.append(
                FundamentalAuditFinding(
                    REVIEW,
                    "SEC_UNIT_DIFFERENCE",
                    "vendor and SEC values use different units",
                    security_id=fact.security_id,
                    fundamental_ids=(fact.fundamental_id,),
                    context={"vendor_unit": fact.unit, "sec_unit": sample.sec_unit},
                )
            )
            continue
        denominator = max(abs(fact.value), abs(sample.sec_value), Decimal("1"))
        relative_difference = abs(fact.value - sample.sec_value) / denominator
        if relative_difference > relative_tolerance:
            findings.append(
                FundamentalAuditFinding(
                    REVIEW,
                    "SEC_VALUE_DIFFERENCE",
                    "vendor and SEC values differ beyond the frozen tolerance",
                    security_id=fact.security_id,
                    fundamental_ids=(fact.fundamental_id,),
                    context={
                        "vendor_value": fact.value,
                        "sec_value": sample.sec_value,
                        "relative_difference": relative_difference,
                        "tolerance": relative_tolerance,
                        "sec_filing_accession": sample.sec_filing_accession,
                        "sec_source_snapshot_id": sample.sec_source_snapshot_id,
                    },
                )
            )
    return findings, len(issuer_periods), sectors


def audit_point_in_time_fundamentals(
    session: Session,
    *,
    source_snapshot_ids: Optional[Sequence[str]] = None,
    prediction_timestamp: Optional[datetime] = None,
    candidate_fact_ids: Optional[Sequence[str]] = None,
    reconciliation_samples: Sequence[SecReconciliationSample] = (),
    enforce_reconciliation_gate: bool = True,
    require_sec_primary_evidence: bool = False,
    balance_sheet_tolerance: Decimal = Decimal("0.05"),
    cash_flow_tolerance: Decimal = Decimal("0.01"),
    reconciliation_tolerance: Decimal = Decimal("0.005"),
) -> PointInTimeFundamentalAudit:
    """Audit lineage, revisions, accounting plausibility and SEC samples."""

    # Core rows instead of ORM entities: the audit only reads mapped columns,
    # and both paths share the identical column type processors, so every
    # audited value (and therefore every hash) is unchanged while skipping
    # per-row ORM identity/instance overhead across ~765K facts.
    query = select(Fundamental.__table__).order_by(
        Fundamental.security_id,
        Fundamental.fiscal_period_end,
        Fundamental.concept,
        Fundamental.revision_version,
        Fundamental.fundamental_id,
    )
    if source_snapshot_ids is not None:
        query = query.where(Fundamental.source_snapshot_id.in_(source_snapshot_ids))
    facts = list(session.execute(query).all())
    findings: list[FundamentalAuditFinding] = []
    facts_by_id = {row.fundamental_id: row for row in facts}
    snapshots = {
        row.snapshot_id: row
        for row in session.scalars(
            select(SourceSnapshot).where(
                SourceSnapshot.snapshot_id.in_(
                    {row.source_snapshot_id for row in facts}
                )
            )
        ).all()
    } if facts else {}
    securities = {
        row.security_id: row
        for row in session.scalars(
            select(Security).where(
                Security.security_id.in_({row.security_id for row in facts})
            )
        ).all()
    } if facts else {}
    if not facts:
        findings.append(
            FundamentalAuditFinding(
                HARD,
                "NO_FUNDAMENTAL_FACTS",
                "no fundamental facts were selected for audit",
            )
        )

    duplicate_groups: dict[tuple[object, ...], list[Fundamental]] = {}
    series: dict[tuple[object, ...], list[Fundamental]] = {}
    units: dict[tuple[object, ...], set[str]] = {}
    for row in facts:
        duplicate_key = (
            *_fact_identity(row),
            row.revision_version,
            row.filing_accession,
            row.source_snapshot_id,
        )
        duplicate_groups.setdefault(duplicate_key, []).append(row)
        series.setdefault(_fact_identity(row), []).append(row)
        units.setdefault(
            (
                row.security_id,
                row.fiscal_period_end,
                row.period_type,
                row.standardized_concept,
            ),
            set(),
        ).add(row.unit)

        snapshot = snapshots.get(row.source_snapshot_id)
        if snapshot is None or snapshot.source_hash != row.source_hash:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "SOURCE_HASH_MISMATCH",
                    "fact source hash does not match a registered snapshot",
                    security_id=row.security_id,
                    fundamental_ids=(row.fundamental_id,),
                )
            )
        if require_sec_primary_evidence:
            if snapshot is None or "SEC EDGAR PRIMARY" not in snapshot.vendor.upper():
                findings.append(
                    FundamentalAuditFinding(
                        HARD,
                        "SEC_PRIMARY_SOURCE_MISSING",
                        "amended SEC-primary fact lacks its frozen SEC source snapshot",
                        security_id=row.security_id,
                        fundamental_ids=(row.fundamental_id,),
                    )
                )
            elif row.accepted_at is None:
                findings.append(
                    FundamentalAuditFinding(
                        HARD,
                        "SEC_ACCEPTANCE_TIMESTAMP_MISSING",
                        "amended SEC-primary fact lacks filing acceptance evidence",
                        security_id=row.security_id,
                        fundamental_ids=(row.fundamental_id,),
                    )
                )
            elif _utc(row.vendor_available_at) != _utc(row.accepted_at):
                findings.append(
                    FundamentalAuditFinding(
                        HARD,
                        "SEC_AVAILABILITY_BINDING_INVALID",
                        "SEC-primary availability must equal the filing acceptance time",
                        security_id=row.security_id,
                        fundamental_ids=(row.fundamental_id,),
                    )
                )
        if row.security_id not in securities:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "SECURITY_ID_MISSING",
                    "fact does not resolve to a permanent security",
                    security_id=row.security_id,
                    fundamental_ids=(row.fundamental_id,),
                )
            )
        if any(
            value is None
            for value in (
                row.filed_at,
                row.vendor_available_at,
                row.model_available_at,
            )
        ):
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "AVAILABILITY_TIMESTAMP_MISSING",
                    "filing and availability timestamps are required",
                    security_id=row.security_id,
                    fundamental_ids=(row.fundamental_id,),
                )
            )
        if row.accepted_at is None or (
            row.public_release_at is None and not require_sec_primary_evidence
        ):
            findings.append(
                FundamentalAuditFinding(
                    REVIEW,
                    "OPTIONAL_SOURCE_TIMESTAMP_MISSING",
                    "SEC acceptance or public-release evidence is unavailable",
                    security_id=row.security_id,
                    fundamental_ids=(row.fundamental_id,),
                    context={
                        "accepted_at_missing": row.accepted_at is None,
                        "public_release_at_missing": row.public_release_at is None,
                    },
                )
            )
        if row.model_available_at is not None and row.vendor_available_at is not None:
            known = [row.filed_at, row.vendor_available_at]
            known.extend(
                value
                for value in (row.accepted_at, row.public_release_at)
                if value is not None
            )
            if _utc(row.model_available_at) < max(_utc(value) for value in known):
                findings.append(
                    FundamentalAuditFinding(
                        HARD,
                        "AVAILABILITY_ORDER_INVALID",
                        "model availability precedes a source timestamp",
                        security_id=row.security_id,
                        fundamental_ids=(row.fundamental_id,),
                    )
                )
        if row.standardized_concept.startswith("unmapped:"):
            findings.append(
                FundamentalAuditFinding(
                    REVIEW,
                    "CONCEPT_UNMAPPED",
                    "vendor concept has no approved standardized mapping",
                    security_id=row.security_id,
                    fundamental_ids=(row.fundamental_id,),
                    context={"concept": row.concept},
                )
            )
        calendar_year_difference = abs(row.fiscal_year - row.fiscal_period_end.year)
        if calendar_year_difference > 1:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "FISCAL_PERIOD_MAPPING_INVALID",
                    "fiscal year is inconsistent with fiscal period end",
                    security_id=row.security_id,
                    fundamental_ids=(row.fundamental_id,),
                )
            )
        if row.period_type == "QUARTERLY" and row.fiscal_quarter not in {1, 2, 3, 4}:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "FISCAL_QUARTER_INVALID",
                    "quarterly fact lacks a valid fiscal quarter",
                    security_id=row.security_id,
                    fundamental_ids=(row.fundamental_id,),
                )
            )
        if row.form_type.upper().endswith("/A") and row.revision_version <= 1:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "AMENDMENT_NOT_VERSIONED",
                    "amended filing must create a later revision",
                    security_id=row.security_id,
                    fundamental_ids=(row.fundamental_id,),
                )
            )

    for rows in duplicate_groups.values():
        if len(rows) > 1:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "DUPLICATE_ISSUER_PERIOD_CONCEPT",
                    "duplicate fact identity, revision, accession, and source",
                    security_id=rows[0].security_id,
                    fundamental_ids=tuple(row.fundamental_id for row in rows),
                )
            )
    for key, values in units.items():
        if len(values) > 1:
            findings.append(
                FundamentalAuditFinding(
                    REVIEW,
                    "UNIT_CONFLICT",
                    "one standardized concept has multiple units",
                    security_id=str(key[0]),
                    context={"identity": key, "units": sorted(values)},
                )
            )

    for identity, rows in series.items():
        by_version: dict[int, list[Fundamental]] = {}
        for row in rows:
            by_version.setdefault(row.revision_version, []).append(row)
        versions = sorted(by_version)
        if versions != list(range(1, max(versions) + 1)):
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "REVISION_SEQUENCE_GAP",
                    "revision versions must be contiguous from one",
                    security_id=rows[0].security_id,
                    fundamental_ids=tuple(row.fundamental_id for row in rows),
                    context={"versions": versions},
                )
            )
        representatives = []
        for version in versions:
            version_rows = by_version[version]
            signatures = {
                (
                    row.value,
                    row.filing_accession,
                    _utc(row.model_available_at),
                )
                for row in version_rows
            }
            if len(signatures) > 1:
                findings.append(
                    FundamentalAuditFinding(
                        HARD,
                        "REVISION_VERSION_CONFLICT",
                        "one revision version has conflicting values or availability",
                        security_id=rows[0].security_id,
                        fundamental_ids=tuple(
                            row.fundamental_id for row in version_rows
                        ),
                    )
                )
            representatives.append(
                min(version_rows, key=lambda row: row.fundamental_id)
            )
        availability = [_utc(row.model_available_at) for row in representatives]
        if availability != sorted(availability):
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "RESTATEMENT_ORDER_INVALID",
                    "later revision was available before an earlier revision",
                    security_id=rows[0].security_id,
                    fundamental_ids=tuple(row.fundamental_id for row in rows),
                )
            )

    relevant_security_ids = {row.security_id for row in facts}
    identifiers = list(
        session.scalars(
            select(SecurityIdentifier).where(
                SecurityIdentifier.security_id.in_(relevant_security_ids)
            )
        ).all()
    ) if relevant_security_ids else []
    identifiers_by_security: dict[str, list[SecurityIdentifier]] = {}
    identifiers_by_value: dict[tuple[str, str], list[SecurityIdentifier]] = {}
    for row in identifiers:
        identifiers_by_security.setdefault(row.security_id, []).append(row)
        identifiers_by_value.setdefault(
            (row.identifier_type.upper(), row.identifier_value.upper()), []
        ).append(row)
    for security_id in relevant_security_ids:
        if not any(
            row.is_permanent for row in identifiers_by_security.get(security_id, [])
        ):
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "PERMANENT_IDENTIFIER_MISSING",
                    "fundamental security has no permanent vendor identifier",
                    security_id=security_id,
                )
            )
    for key, rows in identifiers_by_value.items():
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                if left.security_id != right.security_id and _overlaps(left, right):
                    findings.append(
                        FundamentalAuditFinding(
                            HARD,
                            "PERMANENT_IDENTIFIER_AMBIGUOUS",
                            "identifier maps to multiple securities for overlapping dates",
                            context={
                                "identifier_type": key[0],
                                "identifier_value": key[1],
                                "security_ids": sorted(
                                    {left.security_id, right.security_id}
                                ),
                            },
                        )
                    )

    if prediction_timestamp is not None:
        timestamp = _utc(prediction_timestamp)
        selected_ids = set(candidate_fact_ids or ())
        missing_candidate_ids = sorted(selected_ids - set(facts_by_id))
        for fundamental_id in missing_candidate_ids:
            findings.append(
                FundamentalAuditFinding(
                    HARD,
                    "CANDIDATE_FACT_MISSING",
                    "prediction references a fact outside the audited dataset",
                    fundamental_ids=(fundamental_id,),
                )
            )
        candidates = (
            [facts_by_id[item] for item in selected_ids if item in facts_by_id]
            if candidate_fact_ids is not None
            else facts
        )
        severity = HARD if candidate_fact_ids is not None else REVIEW
        code = (
            "VALUE_USED_BEFORE_AVAILABLE"
            if candidate_fact_ids is not None
            else "VALUE_NOT_AVAILABLE_AT_PREDICTION"
        )
        for row in candidates:
            if _utc(row.model_available_at) > timestamp:
                findings.append(
                    FundamentalAuditFinding(
                        severity,
                        code,
                        "fundamental became model-available after prediction",
                        security_id=row.security_id,
                        fundamental_ids=(row.fundamental_id,),
                        context={
                            "prediction_timestamp": timestamp,
                            "model_available_at": row.model_available_at,
                        },
                    )
                )

    latest = _latest_standardized(facts)
    accounting_groups: dict[tuple[str, date, str], dict[str, Fundamental]] = {}
    for (security_id, period_end, period_type, concept), row in latest.items():
        accounting_groups.setdefault(
            (security_id, period_end, period_type), {}
        )[concept] = row
    for (security_id, period_end, period_type), values in accounting_groups.items():
        assets = values.get("total_assets")
        liabilities = values.get("total_liabilities")
        equity = values.get("shareholders_equity")
        if assets is not None and assets.value == 0:
            findings.append(
                FundamentalAuditFinding(
                    REVIEW,
                    "INVALID_DENOMINATOR",
                    "total assets is zero",
                    security_id=security_id,
                    fundamental_ids=(assets.fundamental_id,),
                )
            )
        if assets is not None and liabilities is not None and equity is not None:
            denominator = max(abs(assets.value), Decimal("1"))
            difference = abs(assets.value - liabilities.value - equity.value)
            if difference / denominator > balance_sheet_tolerance:
                findings.append(
                    FundamentalAuditFinding(
                        REVIEW,
                        "BALANCE_SHEET_EQUATION_IMPLAUSIBLE",
                        "assets do not plausibly equal liabilities plus equity",
                        security_id=security_id,
                        fundamental_ids=(
                            assets.fundamental_id,
                            liabilities.fundamental_id,
                            equity.fundamental_id,
                        ),
                        context={
                            "period_end": period_end,
                            "period_type": period_type,
                            "relative_difference": difference / denominator,
                        },
                    )
                )
        cfo = values.get("cash_from_operations")
        capex = values.get("capital_expenditure")
        fcf = values.get("free_cash_flow")
        if cfo is not None and capex is not None and fcf is not None:
            expected = cfo.value - capex.value
            denominator = max(abs(expected), abs(fcf.value), Decimal("1"))
            if abs(expected - fcf.value) / denominator > cash_flow_tolerance:
                findings.append(
                    FundamentalAuditFinding(
                        REVIEW,
                        "CASH_FLOW_RECONCILIATION_IMPLAUSIBLE",
                        "free cash flow does not reconcile to CFO less capex",
                        security_id=security_id,
                        fundamental_ids=(
                            cfo.fundamental_id,
                            capex.fundamental_id,
                            fcf.fundamental_id,
                        ),
                    )
                )
        revenue = values.get("revenue")
        income = values.get("net_income_common")
        debt = values.get("total_debt")
        if revenue is not None and revenue.value == 0:
            findings.append(
                FundamentalAuditFinding(
                    REVIEW,
                    "INVALID_DENOMINATOR",
                    "revenue is zero for a ratio-bearing period",
                    security_id=security_id,
                    fundamental_ids=(revenue.fundamental_id,),
                )
            )
        if revenue is not None and income is not None and revenue.value != 0:
            if abs(income.value / revenue.value) > Decimal("5"):
                findings.append(
                    FundamentalAuditFinding(
                        REVIEW,
                        "EXTREME_RATIO",
                        "absolute net margin exceeds 500%",
                        security_id=security_id,
                        fundamental_ids=(income.fundamental_id, revenue.fundamental_id),
                    )
                )
        if assets is not None and debt is not None and assets.value != 0:
            if abs(debt.value / assets.value) > Decimal("10"):
                findings.append(
                    FundamentalAuditFinding(
                        REVIEW,
                        "EXTREME_RATIO",
                        "absolute debt-to-assets exceeds 10x",
                        security_id=security_id,
                        fundamental_ids=(debt.fundamental_id, assets.fundamental_id),
                    )
                )

    if require_sec_primary_evidence and enforce_reconciliation_gate:
        raise ValueError(
            "SEC-primary source-integrity mode cannot use vendor reconciliation gate"
        )
    reconciliation_findings, issuer_period_count, sectors = _reconciliation_findings(
        session,
        facts_by_id,
        reconciliation_samples,
        enforce_gate=enforce_reconciliation_gate,
        relative_tolerance=reconciliation_tolerance,
    )
    findings.extend(reconciliation_findings)

    fact_rows = [
        {
            "fundamental_id": row.fundamental_id,
            "security_id": row.security_id,
            "fiscal_period_end": row.fiscal_period_end,
            "fiscal_year": row.fiscal_year,
            "fiscal_quarter": row.fiscal_quarter,
            "period_type": row.period_type,
            "form_type": row.form_type,
            "filing_accession": row.filing_accession,
            "concept": row.concept,
            "standardized_concept": row.standardized_concept,
            "value": row.value,
            "unit": row.unit,
            "source_snapshot_id": row.source_snapshot_id,
            "source_hash": row.source_hash,
        }
        for row in facts
    ]
    availability_rows = [
        {
            "fundamental_id": row.fundamental_id,
            "filed_at": row.filed_at,
            "accepted_at": row.accepted_at,
            "public_release_at": row.public_release_at,
            "vendor_available_at": row.vendor_available_at,
            "model_available_at": row.model_available_at,
            "revision_version": row.revision_version,
        }
        for row in facts
    ]
    return PointInTimeFundamentalAudit(
        fact_count=len(facts),
        security_count=len({row.security_id for row in facts}),
        source_snapshot_ids=tuple(sorted({row.source_snapshot_id for row in facts})),
        fact_hash=_hash_rows(fact_rows),
        availability_revision_hash=_hash_rows(availability_rows),
        findings=tuple(
            sorted(
                findings,
                key=lambda row: (
                    row.severity,
                    row.code,
                    row.security_id or "",
                    row.fundamental_ids,
                ),
            )
        ),
        reconciliation_sample_count=len(reconciliation_samples),
        reconciliation_issuer_period_count=issuer_period_count,
        reconciliation_sectors=sectors,
        reconciliation_gate_enforced=enforce_reconciliation_gate,
        evidence_mode=(
            "sec_primary_source_integrity"
            if require_sec_primary_evidence
            else "vendor_sec_reconciliation"
        ),
    )
