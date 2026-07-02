"""Strict vendor-neutral adapter for point-in-time fundamental bundles.

Retrieval is deliberately outside this module. A vendor export and manifest
are frozen first; this adapter verifies the exact bytes and maps vendor field
names into the Sprint 8 canonical contract without network access.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


BUNDLE_SCHEMA_VERSION = "point-in-time-fundamentals-bundle-v1"
CANONICAL_FIELDS = (
    "vendor_id",
    "fiscal_period_end",
    "fiscal_year",
    "fiscal_quarter",
    "period_type",
    "form_type",
    "filing_accession",
    "filed_at",
    "accepted_at",
    "public_release_at",
    "vendor_available_at",
    "model_available_at",
    "revision_version",
    "concept",
    "value",
    "unit",
)
REQUIRED_CANONICAL_FIELDS = frozenset(CANONICAL_FIELDS) - {
    "fiscal_quarter",
    "accepted_at",
    "public_release_at",
}
PERIOD_TYPES = frozenset({"ANNUAL", "QUARTERLY", "TTM"})
FUNDAMENTAL_ID_NAMESPACE = uuid.UUID("f89e68a4-c21d-58fc-a031-a5ee3bfe8dbc")


class PointInTimeFundamentalIngestionError(ValueError):
    """A fundamental bundle cannot be accepted without ambiguity or loss."""


@dataclass(frozen=True)
class FundamentalBundleSource:
    dataset: str
    source_uri: str
    retrieved_at: datetime
    path: Path
    body: bytes
    source_hash: str


@dataclass(frozen=True)
class CanonicalFundamental:
    vendor_id: str
    fiscal_period_end: date
    fiscal_year: int
    fiscal_quarter: Optional[int]
    period_type: str
    form_type: str
    filing_accession: str
    filed_at: datetime
    accepted_at: Optional[datetime]
    public_release_at: Optional[datetime]
    vendor_available_at: datetime
    model_available_at: datetime
    revision_version: int
    concept: str
    standardized_concept: str
    value: Decimal
    unit: str
    source_row_number: int

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.vendor_id,
            self.fiscal_period_end,
            self.period_type,
            self.concept,
            self.unit,
        )


@dataclass(frozen=True)
class NormalizedFundamentalBundle:
    vendor: str
    dataset: str
    license_tag: str
    license_evidence_uri: str
    vendor_identifier_type: str
    concept_map_version: str
    manifest_hash: str
    manifest_path: Path
    manifest_body: bytes
    source: FundamentalBundleSource
    facts: tuple[CanonicalFundamental, ...]


def deterministic_fundamental_id(
    vendor: str,
    source_hash: str,
    fact: CanonicalFundamental,
) -> str:
    key = "|".join(
        str(value)
        for value in (
            vendor,
            source_hash,
            fact.vendor_id,
            fact.fiscal_period_end,
            fact.period_type,
            fact.concept,
            fact.unit,
            fact.revision_version,
            fact.filing_accession,
        )
    )
    return str(uuid.uuid5(FUNDAMENTAL_ID_NAMESPACE, key))


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PointInTimeFundamentalIngestionError(
                f"duplicate JSON key {key!r}"
            )
        result[key] = value
    return result


def _json_bytes(body: bytes, label: str) -> Any:
    try:
        return json.loads(
            body.decode("utf-8"),
            parse_float=Decimal,
            parse_int=int,
            object_pairs_hook=_strict_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PointInTimeFundamentalIngestionError(
            f"{label} is not valid UTF-8 JSON"
        ) from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PointInTimeFundamentalIngestionError(f"{label} must be an object")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PointInTimeFundamentalIngestionError(f"{label} is required")
    return value.strip()


def _utc_timestamp(value: Any, label: str, *, optional: bool = False) -> Optional[datetime]:
    if value is None and optional:
        return None
    text = _required_text(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PointInTimeFundamentalIngestionError(
            f"{label} must be an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise PointInTimeFundamentalIngestionError(
            f"{label} must include a timezone"
        )
    return parsed.astimezone(timezone.utc)


def _date(value: Any, label: str) -> date:
    text = _required_text(value, label)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise PointInTimeFundamentalIngestionError(
            f"{label} must be an ISO date"
        ) from exc


def _integer(value: Any, label: str, *, optional: bool = False) -> Optional[int]:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise PointInTimeFundamentalIngestionError(f"{label} must be an integer")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PointInTimeFundamentalIngestionError(
            f"{label} must be an integer"
        ) from exc
    if parsed != parsed.to_integral_value():
        raise PointInTimeFundamentalIngestionError(f"{label} must be an integer")
    return int(parsed)


def _decimal(value: Any, label: str) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise PointInTimeFundamentalIngestionError(f"{label} must be numeric")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PointInTimeFundamentalIngestionError(
            f"{label} must be numeric"
        ) from exc
    if not parsed.is_finite():
        raise PointInTimeFundamentalIngestionError(f"{label} must be finite")
    fractional_digits = max(0, -parsed.as_tuple().exponent)
    integer_digits = max(1, parsed.adjusted() + 1)
    if fractional_digits > 6 or integer_digits > 18:
        raise PointInTimeFundamentalIngestionError(
            f"{label} exceeds NUMERIC(24,6) warehouse precision"
        )
    return parsed


def _mapped(row: Mapping[str, Any], field_map: Mapping[str, str], field: str) -> Any:
    vendor_field = field_map.get(field)
    if vendor_field is None:
        return None
    return row.get(vendor_field)


def _normalize_fact(
    row: Mapping[str, Any],
    *,
    row_number: int,
    field_map: Mapping[str, str],
    concept_map: Mapping[str, str],
) -> CanonicalFundamental:
    label = f"fundamentals row {row_number}"
    vendor_id = _required_text(_mapped(row, field_map, "vendor_id"), f"{label} vendor_id")
    fiscal_period_end = _date(
        _mapped(row, field_map, "fiscal_period_end"),
        f"{label} fiscal_period_end",
    )
    fiscal_year = _integer(
        _mapped(row, field_map, "fiscal_year"), f"{label} fiscal_year"
    )
    fiscal_quarter = _integer(
        _mapped(row, field_map, "fiscal_quarter"),
        f"{label} fiscal_quarter",
        optional=True,
    )
    period_type = _required_text(
        _mapped(row, field_map, "period_type"), f"{label} period_type"
    ).upper()
    if period_type not in PERIOD_TYPES:
        raise PointInTimeFundamentalIngestionError(
            f"{label} period_type must be ANNUAL, QUARTERLY, or TTM"
        )
    if period_type == "QUARTERLY" and fiscal_quarter not in {1, 2, 3, 4}:
        raise PointInTimeFundamentalIngestionError(
            f"{label} quarterly fact requires fiscal_quarter 1-4"
        )
    if period_type == "ANNUAL" and fiscal_quarter is not None:
        raise PointInTimeFundamentalIngestionError(
            f"{label} annual fact cannot have a fiscal_quarter"
        )

    filed_at = _utc_timestamp(
        _mapped(row, field_map, "filed_at"), f"{label} filed_at"
    )
    accepted_at = _utc_timestamp(
        _mapped(row, field_map, "accepted_at"),
        f"{label} accepted_at",
        optional=True,
    )
    public_release_at = _utc_timestamp(
        _mapped(row, field_map, "public_release_at"),
        f"{label} public_release_at",
        optional=True,
    )
    vendor_available_at = _utc_timestamp(
        _mapped(row, field_map, "vendor_available_at"),
        f"{label} vendor_available_at",
    )
    model_available_at = _utc_timestamp(
        _mapped(row, field_map, "model_available_at"),
        f"{label} model_available_at",
    )
    assert filed_at is not None
    assert vendor_available_at is not None
    assert model_available_at is not None
    known_times = [filed_at, vendor_available_at]
    known_times.extend(
        value for value in (accepted_at, public_release_at) if value is not None
    )
    if model_available_at < max(known_times):
        raise PointInTimeFundamentalIngestionError(
            f"{label} model_available_at precedes a source availability timestamp"
        )

    revision_version = _integer(
        _mapped(row, field_map, "revision_version"),
        f"{label} revision_version",
    )
    if revision_version is None or revision_version < 1:
        raise PointInTimeFundamentalIngestionError(
            f"{label} revision_version must be positive"
        )
    form_type = _required_text(
        _mapped(row, field_map, "form_type"), f"{label} form_type"
    )
    if form_type.upper().endswith("/A") and revision_version <= 1:
        raise PointInTimeFundamentalIngestionError(
            f"{label} amended filing must have revision_version greater than 1"
        )
    concept = _required_text(
        _mapped(row, field_map, "concept"), f"{label} concept"
    )
    standardized_concept = concept_map.get(concept, f"unmapped:{concept}")
    standardized_concept = _required_text(
        standardized_concept, f"{label} standardized_concept"
    )

    assert fiscal_year is not None
    return CanonicalFundamental(
        vendor_id=vendor_id,
        fiscal_period_end=fiscal_period_end,
        fiscal_year=fiscal_year,
        fiscal_quarter=fiscal_quarter,
        period_type=period_type,
        form_type=form_type,
        filing_accession=_required_text(
            _mapped(row, field_map, "filing_accession"),
            f"{label} filing_accession",
        ),
        filed_at=filed_at,
        accepted_at=accepted_at,
        public_release_at=public_release_at,
        vendor_available_at=vendor_available_at,
        model_available_at=model_available_at,
        revision_version=revision_version,
        concept=concept,
        standardized_concept=standardized_concept,
        value=_decimal(_mapped(row, field_map, "value"), f"{label} value"),
        unit=_required_text(_mapped(row, field_map, "unit"), f"{label} unit"),
        source_row_number=row_number,
    )


def _validate_revisions(facts: Sequence[CanonicalFundamental]) -> None:
    grouped: dict[tuple[object, ...], list[CanonicalFundamental]] = {}
    for fact in facts:
        grouped.setdefault(fact.identity, []).append(fact)

    for identity, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: row.revision_version)
        versions = [row.revision_version for row in ordered]
        if versions != list(range(1, len(ordered) + 1)):
            raise PointInTimeFundamentalIngestionError(
                f"fact {identity!r} revision versions must be contiguous from 1"
            )
        availability = [row.model_available_at for row in ordered]
        if availability != sorted(availability):
            raise PointInTimeFundamentalIngestionError(
                f"fact {identity!r} revision availability is out of order"
            )
        accessions = [row.filing_accession for row in ordered]
        if len(accessions) != len(set(accessions)):
            raise PointInTimeFundamentalIngestionError(
                f"fact {identity!r} repeats a filing accession"
            )


class PointInTimeFundamentalBundleAdapter:
    """Verify and normalize one immutable vendor bundle directory."""

    @classmethod
    def load(
        cls,
        bundle_dir: Path,
        *,
        expected_manifest_hash: str,
    ) -> NormalizedFundamentalBundle:
        manifest_path = bundle_dir / "manifest.json"
        try:
            manifest_body = manifest_path.read_bytes()
        except OSError as exc:
            raise PointInTimeFundamentalIngestionError(
                f"cannot read {manifest_path}"
            ) from exc
        manifest_hash = sha256(manifest_body).hexdigest()
        if manifest_hash != expected_manifest_hash.lower():
            raise PointInTimeFundamentalIngestionError(
                "manifest SHA-256 does not match --expected-manifest-hash"
            )
        manifest = _object(_json_bytes(manifest_body, "manifest"), "manifest")
        if manifest.get("schema_version") != BUNDLE_SCHEMA_VERSION:
            raise PointInTimeFundamentalIngestionError(
                f"manifest schema_version must be {BUNDLE_SCHEMA_VERSION}"
            )

        vendor = _required_text(manifest.get("vendor"), "manifest vendor")
        dataset = _required_text(manifest.get("dataset"), "manifest dataset")
        license_tag = _required_text(
            manifest.get("license_tag"), "manifest license_tag"
        )
        license_evidence_uri = _required_text(
            manifest.get("license_evidence_uri"),
            "manifest license_evidence_uri",
        )
        vendor_identifier_type = _required_text(
            manifest.get("vendor_identifier_type"),
            "manifest vendor_identifier_type",
        ).upper()
        concept_map_version = _required_text(
            manifest.get("concept_map_version"), "manifest concept_map_version"
        )

        field_map_raw = _object(manifest.get("field_map"), "manifest field_map")
        field_map = {
            str(key): _required_text(value, f"field_map {key}")
            for key, value in field_map_raw.items()
        }
        missing_map = sorted(REQUIRED_CANONICAL_FIELDS - set(field_map))
        if missing_map:
            raise PointInTimeFundamentalIngestionError(
                "manifest field_map is missing: " + ", ".join(missing_map)
            )
        unknown_map = sorted(set(field_map) - set(CANONICAL_FIELDS))
        if unknown_map:
            raise PointInTimeFundamentalIngestionError(
                "manifest field_map has unknown canonical fields: "
                + ", ".join(unknown_map)
            )

        concept_map_raw = _object(
            manifest.get("concept_map"), "manifest concept_map"
        )
        concept_map = {
            _required_text(key, "concept_map source concept"): _required_text(
                value, f"concept_map {key}"
            )
            for key, value in concept_map_raw.items()
        }

        file_meta = _object(
            manifest.get("fundamentals_file"), "manifest fundamentals_file"
        )
        relative_path = _required_text(
            file_meta.get("path"), "fundamentals_file path"
        )
        path = (bundle_dir / relative_path).resolve()
        try:
            path.relative_to(bundle_dir.resolve())
        except ValueError as exc:
            raise PointInTimeFundamentalIngestionError(
                "fundamentals_file path escapes bundle directory"
            ) from exc
        try:
            body = path.read_bytes()
        except OSError as exc:
            raise PointInTimeFundamentalIngestionError(f"cannot read {path}") from exc
        source_hash = sha256(body).hexdigest()
        expected_hash = _required_text(
            file_meta.get("sha256"), "fundamentals_file sha256"
        ).lower()
        if source_hash != expected_hash:
            raise PointInTimeFundamentalIngestionError(
                "fundamentals file SHA-256 does not match manifest"
            )
        retrieved_at = _utc_timestamp(
            file_meta.get("retrieved_at"), "fundamentals_file retrieved_at"
        )
        assert retrieved_at is not None
        source = FundamentalBundleSource(
            dataset=dataset,
            source_uri=_required_text(
                file_meta.get("source_uri"), "fundamentals_file source_uri"
            ),
            retrieved_at=retrieved_at,
            path=path,
            body=body,
            source_hash=source_hash,
        )

        raw_rows = _json_bytes(body, "fundamentals file")
        if not isinstance(raw_rows, list) or not raw_rows:
            raise PointInTimeFundamentalIngestionError(
                "fundamentals file must be a non-empty JSON array"
            )
        facts = tuple(
            _normalize_fact(
                _object(row, f"fundamentals row {index}"),
                row_number=index,
                field_map=field_map,
                concept_map=concept_map,
            )
            for index, row in enumerate(raw_rows, start=1)
        )
        _validate_revisions(facts)
        return NormalizedFundamentalBundle(
            vendor=vendor,
            dataset=dataset,
            license_tag=license_tag,
            license_evidence_uri=license_evidence_uri,
            vendor_identifier_type=vendor_identifier_type,
            concept_map_version=concept_map_version,
            manifest_hash=manifest_hash,
            manifest_path=manifest_path,
            manifest_body=manifest_body,
            source=source,
            facts=facts,
        )
