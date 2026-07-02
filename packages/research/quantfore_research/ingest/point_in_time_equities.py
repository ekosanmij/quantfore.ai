"""Strict adapter for immutable point-in-time US equity vendor bundles.

The adapter deliberately separates vendor retrieval from normalization. A
licensed Sharadar (or equivalent) export is first frozen as a bundle of JSON
files and a manifest. This module verifies every byte and maps it to canonical
records without making a network request or leaking credentials.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Optional


BUNDLE_SCHEMA_VERSION = "point-in-time-equity-bundle-v1"
REQUIRED_FILE_ROLES = (
    "securities",
    "memberships",
    "prices",
    "corporate_actions",
    "delistings",
)
QUANTFORE_ID_NAMESPACE = uuid.UUID("c694815c-7255-5ac0-a061-01c159c978e7")


class PointInTimeIngestionError(ValueError):
    """A vendor bundle cannot be accepted without ambiguity or data loss."""


@dataclass(frozen=True)
class BundleSource:
    role: str
    dataset: str
    source_uri: str
    retrieved_at: datetime
    path: Path
    body: bytes
    source_hash: str


@dataclass(frozen=True)
class CanonicalIdentifier:
    identifier_type: str
    identifier_value: str
    valid_from: date
    valid_to: Optional[date]
    is_permanent: bool


@dataclass(frozen=True)
class CanonicalTickerAlias:
    ticker: str
    exchange: Optional[str]
    effective_from: date
    effective_to: Optional[date]
    announced_at: datetime


@dataclass(frozen=True)
class CanonicalSecurity:
    vendor_id: str
    ticker: str
    name: str
    exchange: Optional[str]
    sector: Optional[str]
    industry: Optional[str]
    cik: Optional[str]
    active_from: Optional[date]
    active_to: Optional[date]
    identifiers: tuple[CanonicalIdentifier, ...]
    ticker_aliases: tuple[CanonicalTickerAlias, ...]


@dataclass(frozen=True)
class CanonicalMembership:
    vendor_id: str
    effective_from: date
    effective_to: Optional[date]
    announced_at: datetime


@dataclass(frozen=True)
class CanonicalPrice:
    vendor_id: str
    date: date
    open: Optional[Decimal]
    high: Optional[Decimal]
    low: Optional[Decimal]
    close: Optional[Decimal]
    adj_open: Optional[Decimal]
    adj_high: Optional[Decimal]
    adj_low: Optional[Decimal]
    adj_close: Optional[Decimal]
    volume: Optional[int]
    adj_volume: Optional[Decimal]


@dataclass(frozen=True)
class CanonicalCorporateAction:
    vendor_id: str
    action_type: str
    effective_date: date
    announced_at: datetime
    cash_amount: Optional[Decimal]
    currency: Optional[str]
    ratio_from: Optional[Decimal]
    ratio_to: Optional[Decimal]
    related_vendor_id: Optional[str]
    details: Mapping[str, Any]


@dataclass(frozen=True)
class CanonicalDelisting:
    vendor_id: str
    delisting_date: date
    announced_at: datetime
    delisting_return: Optional[Decimal]
    return_available_at: Optional[datetime]
    reason: str
    successor_vendor_id: Optional[str]


@dataclass(frozen=True)
class NormalizedPointInTimeBundle:
    vendor: str
    license_tag: str
    license_evidence_uri: str
    vendor_identifier_type: str
    universe_id: str
    universe_name: str
    universe_version: str
    universe_description: str
    window_start: date
    window_end: date
    benchmark_vendor_id: str
    audit_contract: Mapping[str, Any]
    manifest: BundleSource
    sources: Mapping[str, BundleSource]
    securities: tuple[CanonicalSecurity, ...]
    memberships: tuple[CanonicalMembership, ...]
    prices: tuple[CanonicalPrice, ...]
    corporate_actions: tuple[CanonicalCorporateAction, ...]
    delistings: tuple[CanonicalDelisting, ...]


def deterministic_id(kind: str, *parts: object) -> str:
    """Return a stable UUID for one normalized business key."""

    key = "|".join([kind, *(str(part) for part in parts)])
    return str(uuid.uuid5(QUANTFORE_ID_NAMESPACE, key))


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PointInTimeIngestionError(f"duplicate JSON key {key!r}")
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
        raise PointInTimeIngestionError(f"{label} is not valid UTF-8 JSON") from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PointInTimeIngestionError(f"{label} must be a JSON object")
    return value


def _rows(value: Any, label: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list):
        raise PointInTimeIngestionError(f"{label} must be a JSON array")
    return tuple(_object(row, f"{label} row {index}") for index, row in enumerate(value, 1))


def _required_text(row: Mapping[str, Any], field: str, label: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise PointInTimeIngestionError(f"{label}: {field} is required")
    return value.strip()


def _optional_text(row: Mapping[str, Any], field: str, label: str) -> Optional[str]:
    value = row.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PointInTimeIngestionError(f"{label}: {field} must be null or non-empty")
    return value.strip()


def _date_value(value: Any, field: str, label: str, *, optional: bool = False) -> Optional[date]:
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise PointInTimeIngestionError(f"{label}: {field} must be an ISO date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise PointInTimeIngestionError(f"{label}: {field} must be an ISO date") from exc


def _timestamp(value: Any, field: str, label: str, *, optional: bool = False) -> Optional[datetime]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise PointInTimeIngestionError(f"{label}: {field} must be a UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise PointInTimeIngestionError(f"{label}: {field} must be a UTC timestamp") from exc
    if parsed.tzinfo is None:
        raise PointInTimeIngestionError(f"{label}: {field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _decimal_value(value: Any, field: str, label: str) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, bool):
        raise PointInTimeIngestionError(f"{label}: {field} must be numeric or null")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PointInTimeIngestionError(
            f"{label}: {field} must be numeric or null"
        ) from exc


def _integer_value(value: Any, field: str, label: str) -> Optional[int]:
    parsed = _decimal_value(value, field, label)
    if parsed is None:
        return None
    if parsed != parsed.to_integral_value():
        raise PointInTimeIngestionError(f"{label}: {field} must be an integer or null")
    return int(parsed)


def _boolean(value: Any, field: str, label: str) -> bool:
    if not isinstance(value, bool):
        raise PointInTimeIngestionError(f"{label}: {field} must be boolean")
    return value


def _check_fields(
    row: Mapping[str, Any],
    *,
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    missing = sorted(required - set(row))
    unknown = sorted(set(row) - required - optional)
    if missing:
        raise PointInTimeIngestionError(f"{label}: missing fields {', '.join(missing)}")
    if unknown:
        raise PointInTimeIngestionError(f"{label}: unknown fields {', '.join(unknown)}")


def _assert_period(start: date, end: Optional[date], label: str) -> None:
    if end is not None and end < start:
        raise PointInTimeIngestionError(f"{label}: end date precedes start date")


def _safe_bundle_path(bundle_dir: Path, relative: str, label: str) -> Path:
    candidate = (bundle_dir / relative).resolve()
    root = bundle_dir.resolve()
    if candidate == root or root not in candidate.parents:
        raise PointInTimeIngestionError(f"{label}: path escapes bundle directory")
    if not candidate.is_file():
        raise PointInTimeIngestionError(f"{label}: file does not exist: {relative}")
    return candidate


def _month_keys(start: date, end: date) -> tuple[str, ...]:
    keys = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        keys.append(f"{year:04d}-{month:02d}")
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return tuple(keys)


class PointInTimeEquityBundleAdapter:
    """Load and strictly normalize one licensed, immutable vendor bundle."""

    def __init__(self, bundle_dir: Path, *, expected_manifest_hash: Optional[str] = None) -> None:
        self.bundle_dir = bundle_dir
        self.expected_manifest_hash = expected_manifest_hash

    def _load_manifest(self) -> tuple[dict[str, Any], BundleSource]:
        path = self.bundle_dir / "manifest.json"
        if not path.is_file():
            raise PointInTimeIngestionError("bundle manifest.json does not exist")
        body = path.read_bytes()
        source_hash = sha256(body).hexdigest()
        if self.expected_manifest_hash and source_hash != self.expected_manifest_hash:
            raise PointInTimeIngestionError(
                "manifest SHA-256 does not match --expected-manifest-hash"
            )
        document = _object(_json_bytes(body, "manifest"), "manifest")
        retrieved_at = _timestamp(document.get("created_at"), "created_at", "manifest")
        assert retrieved_at is not None
        source = BundleSource(
            role="manifest",
            dataset="point_in_time_equity_bundle_manifest",
            source_uri=path.resolve().as_uri(),
            retrieved_at=retrieved_at,
            path=path,
            body=body,
            source_hash=source_hash,
        )
        return document, source

    def load(self) -> NormalizedPointInTimeBundle:
        manifest, manifest_source = self._load_manifest()
        _check_fields(
            manifest,
            required={
                "schema_version",
                "created_at",
                "vendor",
                "license_tag",
                "license_rights_confirmed",
                "license_evidence_uri",
                "vendor_identifier_type",
                "universe",
                "files",
                "audit_contract",
            },
            optional=set(),
            label="manifest",
        )
        if manifest["schema_version"] != BUNDLE_SCHEMA_VERSION:
            raise PointInTimeIngestionError(
                f"unsupported bundle schema_version {manifest['schema_version']!r}"
            )
        if not _boolean(
            manifest["license_rights_confirmed"],
            "license_rights_confirmed",
            "manifest",
        ):
            raise PointInTimeIngestionError("licensing rights are not confirmed")
        vendor = _required_text(manifest, "vendor", "manifest")
        license_tag = _required_text(manifest, "license_tag", "manifest")
        evidence_uri = _required_text(manifest, "license_evidence_uri", "manifest")
        identifier_type = _required_text(
            manifest, "vendor_identifier_type", "manifest"
        ).upper()

        universe = _object(manifest["universe"], "manifest universe")
        _check_fields(
            universe,
            required={
                "universe_id",
                "name",
                "version",
                "description",
                "window_start",
                "window_end",
                "benchmark_vendor_id",
                "benchmark_excluded_from_rankings",
            },
            optional=set(),
            label="manifest universe",
        )
        if not _boolean(
            universe["benchmark_excluded_from_rankings"],
            "benchmark_excluded_from_rankings",
            "manifest universe",
        ):
            raise PointInTimeIngestionError("benchmark must be excluded from rankings")
        window_start = _date_value(
            universe["window_start"], "window_start", "manifest universe"
        )
        window_end = _date_value(
            universe["window_end"], "window_end", "manifest universe"
        )
        assert window_start is not None and window_end is not None
        _assert_period(window_start, window_end, "manifest universe")

        files = _object(manifest["files"], "manifest files")
        if set(files) != set(REQUIRED_FILE_ROLES):
            raise PointInTimeIngestionError(
                "manifest files must exactly contain: " + ", ".join(REQUIRED_FILE_ROLES)
            )
        sources: dict[str, BundleSource] = {}
        documents: dict[str, Any] = {}
        for role in REQUIRED_FILE_ROLES:
            spec = _object(files[role], f"manifest files.{role}")
            _check_fields(
                spec,
                required={"path", "dataset", "source_uri", "retrieved_at", "sha256"},
                optional=set(),
                label=f"manifest files.{role}",
            )
            relative_path = _required_text(spec, "path", f"manifest files.{role}")
            source_path = _safe_bundle_path(
                self.bundle_dir, relative_path, f"manifest files.{role}"
            )
            body = source_path.read_bytes()
            actual_hash = sha256(body).hexdigest()
            expected_hash = _required_text(spec, "sha256", f"manifest files.{role}")
            if not re.fullmatch(r"[0-9a-f]{64}", expected_hash):
                raise PointInTimeIngestionError(
                    f"manifest files.{role}: sha256 must be lowercase SHA-256"
                )
            if actual_hash != expected_hash:
                raise PointInTimeIngestionError(f"{role} SHA-256 does not match manifest")
            retrieved_at = _timestamp(
                spec["retrieved_at"], "retrieved_at", f"manifest files.{role}"
            )
            assert retrieved_at is not None
            sources[role] = BundleSource(
                role=role,
                dataset=_required_text(spec, "dataset", f"manifest files.{role}"),
                source_uri=_required_text(spec, "source_uri", f"manifest files.{role}"),
                retrieved_at=retrieved_at,
                path=source_path,
                body=body,
                source_hash=actual_hash,
            )
            documents[role] = _json_bytes(body, role)

        document_rows = {
            role: _rows(documents[role], role) for role in REQUIRED_FILE_ROLES
        }
        securities = self._parse_securities(
            document_rows["securities"], identifier_type
        )
        security_ids = {row.vendor_id for row in securities}
        benchmark_vendor_id = _required_text(
            universe, "benchmark_vendor_id", "manifest universe"
        )
        if benchmark_vendor_id not in security_ids:
            raise PointInTimeIngestionError("benchmark_vendor_id does not resolve to a security")
        memberships = self._parse_memberships(
            document_rows["memberships"], security_ids
        )
        prices = self._parse_prices(
            document_rows["prices"], security_ids, window_start, window_end
        )
        actions = self._parse_actions(
            document_rows["corporate_actions"], security_ids
        )
        delistings = self._parse_delistings(
            document_rows["delistings"], security_ids
        )
        audit_contract = self._parse_audit_contract(
            manifest["audit_contract"],
            row_counts={role: len(rows) for role, rows in document_rows.items()},
            security_ids=security_ids,
            window_start=window_start,
            window_end=window_end,
        )

        return NormalizedPointInTimeBundle(
            vendor=vendor,
            license_tag=license_tag,
            license_evidence_uri=evidence_uri,
            vendor_identifier_type=identifier_type,
            universe_id=_required_text(universe, "universe_id", "manifest universe"),
            universe_name=_required_text(universe, "name", "manifest universe"),
            universe_version=_required_text(universe, "version", "manifest universe"),
            universe_description=_required_text(
                universe, "description", "manifest universe"
            ),
            window_start=window_start,
            window_end=window_end,
            benchmark_vendor_id=benchmark_vendor_id,
            audit_contract=audit_contract,
            manifest=manifest_source,
            sources=sources,
            securities=securities,
            memberships=memberships,
            prices=prices,
            corporate_actions=actions,
            delistings=delistings,
        )

    def _parse_securities(
        self, rows: tuple[dict[str, Any], ...], vendor_identifier_type: str
    ) -> tuple[CanonicalSecurity, ...]:
        parsed: list[CanonicalSecurity] = []
        vendor_ids: set[str] = set()
        for index, row in enumerate(rows, 1):
            label = f"securities row {index}"
            _check_fields(
                row,
                required={"vendor_id", "ticker", "name", "identifiers", "ticker_aliases"},
                optional={"exchange", "sector", "industry", "cik", "active_from", "active_to"},
                label=label,
            )
            vendor_id = _required_text(row, "vendor_id", label)
            ticker = _required_text(row, "ticker", label).upper()
            if vendor_id in vendor_ids:
                raise PointInTimeIngestionError(f"duplicate security vendor_id {vendor_id}")
            vendor_ids.add(vendor_id)
            active_from = _date_value(
                row.get("active_from"), "active_from", label, optional=True
            )
            active_to = _date_value(
                row.get("active_to"), "active_to", label, optional=True
            )
            if active_from is not None:
                _assert_period(active_from, active_to, label)

            identifiers = [
                CanonicalIdentifier(
                    identifier_type=vendor_identifier_type,
                    identifier_value=vendor_id,
                    valid_from=active_from or date(1900, 1, 1),
                    valid_to=active_to,
                    is_permanent=True,
                )
            ]
            identifier_rows = _rows(row["identifiers"], f"{label} identifiers")
            for nested_index, item in enumerate(identifier_rows, 1):
                nested_label = f"{label} identifier {nested_index}"
                _check_fields(
                    item,
                    required={
                        "identifier_type",
                        "identifier_value",
                        "valid_from",
                        "valid_to",
                        "is_permanent",
                    },
                    optional=set(),
                    label=nested_label,
                )
                valid_from = _date_value(item["valid_from"], "valid_from", nested_label)
                valid_to = _date_value(item["valid_to"], "valid_to", nested_label, optional=True)
                assert valid_from is not None
                _assert_period(valid_from, valid_to, nested_label)
                identifiers.append(
                    CanonicalIdentifier(
                        identifier_type=_required_text(
                            item, "identifier_type", nested_label
                        ).upper(),
                        identifier_value=_required_text(
                            item, "identifier_value", nested_label
                        ),
                        valid_from=valid_from,
                        valid_to=valid_to,
                        is_permanent=_boolean(
                            item["is_permanent"], "is_permanent", nested_label
                        ),
                    )
                )
            identifier_keys = {
                (item.identifier_type.upper(), item.identifier_value.upper(), item.valid_from)
                for item in identifiers
            }
            if len(identifier_keys) != len(identifiers):
                raise PointInTimeIngestionError(f"{label}: duplicate identifier")

            aliases: list[CanonicalTickerAlias] = []
            alias_rows = _rows(row["ticker_aliases"], f"{label} ticker_aliases")
            for nested_index, item in enumerate(alias_rows, 1):
                nested_label = f"{label} ticker alias {nested_index}"
                _check_fields(
                    item,
                    required={"ticker", "effective_from", "effective_to", "announced_at"},
                    optional={"exchange"},
                    label=nested_label,
                )
                effective_from = _date_value(
                    item["effective_from"], "effective_from", nested_label
                )
                effective_to = _date_value(
                    item["effective_to"], "effective_to", nested_label, optional=True
                )
                announced_at = _timestamp(
                    item["announced_at"], "announced_at", nested_label
                )
                assert effective_from is not None and announced_at is not None
                _assert_period(effective_from, effective_to, nested_label)
                aliases.append(
                    CanonicalTickerAlias(
                        ticker=_required_text(item, "ticker", nested_label).upper(),
                        exchange=_optional_text(item, "exchange", nested_label),
                        effective_from=effective_from,
                        effective_to=effective_to,
                        announced_at=announced_at,
                    )
                )
            alias_keys = {(item.ticker, item.effective_from) for item in aliases}
            if len(alias_keys) != len(aliases):
                raise PointInTimeIngestionError(f"{label}: duplicate ticker alias")
            parsed.append(
                CanonicalSecurity(
                    vendor_id=vendor_id,
                    ticker=ticker,
                    name=_required_text(row, "name", label),
                    exchange=_optional_text(row, "exchange", label),
                    sector=_optional_text(row, "sector", label),
                    industry=_optional_text(row, "industry", label),
                    cik=_optional_text(row, "cik", label),
                    active_from=active_from,
                    active_to=active_to,
                    identifiers=tuple(identifiers),
                    ticker_aliases=tuple(aliases),
                )
            )
        if not parsed:
            raise PointInTimeIngestionError("securities must contain at least one row")
        return tuple(parsed)

    def _parse_audit_contract(
        self,
        value: Any,
        *,
        row_counts: Mapping[str, int],
        security_ids: set[str],
        window_start: date,
        window_end: date,
    ) -> Mapping[str, Any]:
        contract = _object(value, "manifest audit_contract")
        _check_fields(
            contract,
            required={
                "expected_row_counts",
                "monthly_membership_counts",
                "independent_membership_samples",
            },
            optional=set(),
            label="manifest audit_contract",
        )
        expected = _object(
            contract["expected_row_counts"],
            "manifest audit_contract expected_row_counts",
        )
        if set(expected) != set(REQUIRED_FILE_ROLES):
            raise PointInTimeIngestionError(
                "audit expected_row_counts must contain every bundle file role"
            )
        normalized_counts = {}
        for role in REQUIRED_FILE_ROLES:
            count = _integer_value(
                expected[role], role, "manifest audit_contract expected_row_counts"
            )
            if count is None or count < 0 or count != row_counts[role]:
                raise PointInTimeIngestionError(
                    f"audit expected row count does not match {role}"
                )
            normalized_counts[role] = count

        monthly = _object(
            contract["monthly_membership_counts"],
            "manifest audit_contract monthly_membership_counts",
        )
        required_months = set(_month_keys(window_start, window_end))
        if set(monthly) != required_months:
            raise PointInTimeIngestionError(
                "audit monthly membership counts must cover every universe month"
            )
        normalized_monthly = {}
        for month, value in sorted(monthly.items()):
            count = _integer_value(
                value, month, "manifest audit_contract monthly_membership_counts"
            )
            if count is None or count <= 0:
                raise PointInTimeIngestionError(
                    f"audit monthly membership count must be positive: {month}"
                )
            normalized_monthly[month] = count

        samples = _rows(
            contract["independent_membership_samples"],
            "manifest audit_contract independent_membership_samples",
        )
        if len(samples) < 3:
            raise PointInTimeIngestionError(
                "at least three independent membership samples are required"
            )
        normalized_samples = []
        sample_dates = set()
        for index, sample in enumerate(samples, 1):
            label = f"manifest audit_contract independent sample {index}"
            _check_fields(
                sample,
                required={"as_of_date", "vendor_ids", "source_uri", "source_sha256"},
                optional=set(),
                label=label,
            )
            as_of_date = _date_value(sample["as_of_date"], "as_of_date", label)
            assert as_of_date is not None
            if not window_start <= as_of_date <= window_end or as_of_date in sample_dates:
                raise PointInTimeIngestionError(
                    f"{label}: date must be unique and inside the universe window"
                )
            sample_dates.add(as_of_date)
            vendor_ids = sample["vendor_ids"]
            if not isinstance(vendor_ids, list) or not vendor_ids:
                raise PointInTimeIngestionError(f"{label}: vendor_ids must be non-empty")
            normalized_ids = tuple(
                sorted(
                    {
                        _required_text({"id": item}, "id", label)
                        for item in vendor_ids
                    }
                )
            )
            if len(normalized_ids) != len(vendor_ids) or not set(normalized_ids) <= security_ids:
                raise PointInTimeIngestionError(
                    f"{label}: vendor_ids must be unique known securities"
                )
            source_hash = _required_text(sample, "source_sha256", label)
            if re.fullmatch(r"[0-9a-f]{64}", source_hash) is None:
                raise PointInTimeIngestionError(
                    f"{label}: source_sha256 must be lowercase SHA-256"
                )
            normalized_samples.append(
                {
                    "as_of_date": as_of_date.isoformat(),
                    "vendor_ids": list(normalized_ids),
                    "source_uri": _required_text(sample, "source_uri", label),
                    "source_sha256": source_hash,
                }
            )
        return {
            "expected_row_counts": normalized_counts,
            "monthly_membership_counts": normalized_monthly,
            "independent_membership_samples": normalized_samples,
        }

    def _parse_memberships(
        self, rows: tuple[dict[str, Any], ...], security_ids: set[str]
    ) -> tuple[CanonicalMembership, ...]:
        parsed: list[CanonicalMembership] = []
        keys: set[tuple[str, date]] = set()
        for index, row in enumerate(rows, 1):
            label = f"memberships row {index}"
            _check_fields(
                row,
                required={"vendor_id", "effective_from", "effective_to", "announced_at"},
                optional=set(),
                label=label,
            )
            vendor_id = _required_text(row, "vendor_id", label)
            if vendor_id not in security_ids:
                raise PointInTimeIngestionError(f"{label}: vendor_id does not resolve")
            effective_from = _date_value(row["effective_from"], "effective_from", label)
            effective_to = _date_value(
                row["effective_to"], "effective_to", label, optional=True
            )
            announced_at = _timestamp(row["announced_at"], "announced_at", label)
            assert effective_from is not None and announced_at is not None
            _assert_period(effective_from, effective_to, label)
            key = (vendor_id, effective_from)
            if key in keys:
                raise PointInTimeIngestionError(f"{label}: duplicate membership")
            keys.add(key)
            parsed.append(
                CanonicalMembership(vendor_id, effective_from, effective_to, announced_at)
            )
        return tuple(parsed)

    def _parse_prices(
        self,
        rows: tuple[dict[str, Any], ...],
        security_ids: set[str],
        window_start: date,
        window_end: date,
    ) -> tuple[CanonicalPrice, ...]:
        parsed: list[CanonicalPrice] = []
        keys: set[tuple[str, date]] = set()
        price_fields = {
            "open", "high", "low", "close", "adj_open", "adj_high", "adj_low",
            "adj_close", "volume", "adj_volume",
        }
        for index, row in enumerate(rows, 1):
            label = f"prices row {index}"
            _check_fields(
                row,
                required={"vendor_id", "date", *price_fields},
                optional=set(),
                label=label,
            )
            vendor_id = _required_text(row, "vendor_id", label)
            if vendor_id not in security_ids:
                raise PointInTimeIngestionError(f"{label}: vendor_id does not resolve")
            price_date = _date_value(row["date"], "date", label)
            assert price_date is not None
            if not window_start <= price_date <= window_end:
                raise PointInTimeIngestionError(f"{label}: date is outside the frozen window")
            key = (vendor_id, price_date)
            if key in keys:
                raise PointInTimeIngestionError(f"{label}: duplicate price")
            keys.add(key)
            parsed.append(
                CanonicalPrice(
                    vendor_id=vendor_id,
                    date=price_date,
                    open=_decimal_value(row["open"], "open", label),
                    high=_decimal_value(row["high"], "high", label),
                    low=_decimal_value(row["low"], "low", label),
                    close=_decimal_value(row["close"], "close", label),
                    adj_open=_decimal_value(row["adj_open"], "adj_open", label),
                    adj_high=_decimal_value(row["adj_high"], "adj_high", label),
                    adj_low=_decimal_value(row["adj_low"], "adj_low", label),
                    adj_close=_decimal_value(row["adj_close"], "adj_close", label),
                    volume=_integer_value(row["volume"], "volume", label),
                    adj_volume=_decimal_value(row["adj_volume"], "adj_volume", label),
                )
            )
        return tuple(parsed)

    def _parse_actions(
        self, rows: tuple[dict[str, Any], ...], security_ids: set[str]
    ) -> tuple[CanonicalCorporateAction, ...]:
        parsed: list[CanonicalCorporateAction] = []
        keys: set[tuple[str, str, date]] = set()
        required = {
            "vendor_id", "action_type", "effective_date", "announced_at",
            "cash_amount", "currency", "ratio_from", "ratio_to",
            "related_vendor_id", "details",
        }
        for index, row in enumerate(rows, 1):
            label = f"corporate_actions row {index}"
            _check_fields(row, required=required, optional=set(), label=label)
            vendor_id = _required_text(row, "vendor_id", label)
            if vendor_id not in security_ids:
                raise PointInTimeIngestionError(f"{label}: vendor_id does not resolve")
            related = _optional_text(row, "related_vendor_id", label)
            if related is not None and related not in security_ids:
                raise PointInTimeIngestionError(f"{label}: related_vendor_id does not resolve")
            effective_date = _date_value(row["effective_date"], "effective_date", label)
            announced_at = _timestamp(row["announced_at"], "announced_at", label)
            assert effective_date is not None and announced_at is not None
            action_type = _required_text(row, "action_type", label).lower()
            key = (vendor_id, action_type, effective_date)
            if key in keys:
                raise PointInTimeIngestionError(f"{label}: duplicate corporate action")
            keys.add(key)
            ratio_from = _decimal_value(row["ratio_from"], "ratio_from", label)
            ratio_to = _decimal_value(row["ratio_to"], "ratio_to", label)
            if (ratio_from is None) != (ratio_to is None):
                raise PointInTimeIngestionError(f"{label}: ratio fields must be present together")
            parsed.append(
                CanonicalCorporateAction(
                    vendor_id=vendor_id,
                    action_type=action_type,
                    effective_date=effective_date,
                    announced_at=announced_at,
                    cash_amount=_decimal_value(row["cash_amount"], "cash_amount", label),
                    currency=_optional_text(row, "currency", label),
                    ratio_from=ratio_from,
                    ratio_to=ratio_to,
                    related_vendor_id=related,
                    details=_object(row["details"], f"{label} details"),
                )
            )
        return tuple(parsed)

    def _parse_delistings(
        self, rows: tuple[dict[str, Any], ...], security_ids: set[str]
    ) -> tuple[CanonicalDelisting, ...]:
        parsed: list[CanonicalDelisting] = []
        keys: set[tuple[str, date]] = set()
        required = {
            "vendor_id", "delisting_date", "announced_at", "delisting_return",
            "return_available_at", "reason", "successor_vendor_id",
        }
        for index, row in enumerate(rows, 1):
            label = f"delistings row {index}"
            _check_fields(row, required=required, optional=set(), label=label)
            vendor_id = _required_text(row, "vendor_id", label)
            if vendor_id not in security_ids:
                raise PointInTimeIngestionError(f"{label}: vendor_id does not resolve")
            successor = _optional_text(row, "successor_vendor_id", label)
            if successor is not None and successor not in security_ids:
                raise PointInTimeIngestionError(f"{label}: successor_vendor_id does not resolve")
            delisting_date = _date_value(row["delisting_date"], "delisting_date", label)
            announced_at = _timestamp(row["announced_at"], "announced_at", label)
            return_available_at = _timestamp(
                row["return_available_at"],
                "return_available_at",
                label,
                optional=True,
            )
            delisting_return = _decimal_value(
                row["delisting_return"], "delisting_return", label
            )
            assert delisting_date is not None and announced_at is not None
            if delisting_return is not None and return_available_at is None:
                raise PointInTimeIngestionError(
                    f"{label}: return_available_at is required with delisting_return"
                )
            key = (vendor_id, delisting_date)
            if key in keys:
                raise PointInTimeIngestionError(f"{label}: duplicate delisting")
            keys.add(key)
            parsed.append(
                CanonicalDelisting(
                    vendor_id=vendor_id,
                    delisting_date=delisting_date,
                    announced_at=announced_at,
                    delisting_return=delisting_return,
                    return_available_at=return_available_at,
                    reason=_required_text(row, "reason", label),
                    successor_vendor_id=successor,
                )
            )
        return tuple(parsed)
