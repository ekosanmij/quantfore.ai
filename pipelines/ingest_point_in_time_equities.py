"""Ingest an immutable point-in-time US equity vendor bundle.

Example:
    python pipelines/ingest_point_in_time_equities.py /private/vendor-export \
      --expected-manifest-hash <sha256>

The bundle format is implemented by
``quantfore_research.ingest.point_in_time_equities``. Raw bytes are copied to
``data/raw`` under content-addressed names and are never committed to Git.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, TypeVar

try:
    import _bootstrap  # noqa: F401
    from _common import DEFAULT_RAW_DIR, open_research_database
except ModuleNotFoundError:  # Imported as pipelines.* in tests.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import DEFAULT_RAW_DIR, open_research_database  # type: ignore

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from quantfore_research.db import session_scope
from quantfore_research.ingest.point_in_time_equities import (
    BundleSource,
    NormalizedPointInTimeBundle,
    PointInTimeEquityBundleAdapter,
    PointInTimeIngestionError,
    deterministic_id,
)
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
from quantfore_research.validation.security_master import validate_security_master


ModelT = TypeVar("ModelT")


@dataclass(frozen=True)
class PointInTimeIngestionResult:
    source_snapshots_inserted: int
    source_snapshots_reused: int
    securities_inserted: int
    securities_reused: int
    identifiers_inserted: int
    ticker_aliases_inserted: int
    universe_definitions_inserted: int
    memberships_inserted: int
    prices_inserted: int
    corporate_actions_inserted: int
    delistings_inserted: int
    duplicate_rows_skipped: int


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "vendor"


def _timestamp_slug(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _storage_uri(vendor: str, source: BundleSource) -> str:
    return (
        f"raw/point-in-time-equities/{_slug(vendor)}/{_slug(source.role)}/"
        f"{_timestamp_slug(source.retrieved_at)}_{source.source_hash}.json"
    )


def _freeze_source(raw_dir: Path, storage_uri: str, body: bytes) -> Path:
    if not storage_uri.startswith("raw/"):
        raise PointInTimeIngestionError("raw storage URI must start with raw/")
    target = raw_dir.parent / storage_uri
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or target.read_bytes() != body:
            raise PointInTimeIngestionError(
                f"immutable raw path already contains different bytes: {storage_uri}"
            )
        return target
    target.write_bytes(body)
    return target


def _normalise_compare(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Decimal):
        return value.normalize()
    if isinstance(value, dict):
        return {key: _normalise_compare(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return tuple(_normalise_compare(item) for item in value)
    return value


def _json_compatible(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_compatible(item) for item in value]
    return value


def _insert_or_match(
    session: Session,
    model: type[ModelT],
    primary_key: str,
    candidate: ModelT,
    fields: Iterable[str],
) -> bool:
    key = getattr(candidate, primary_key)
    existing = session.get(model, key)
    if existing is None:
        session.add(candidate)
        session.flush()
        return True
    conflicts = [
        field
        for field in fields
        if _normalise_compare(getattr(existing, field))
        != _normalise_compare(getattr(candidate, field))
    ]
    if conflicts:
        raise PointInTimeIngestionError(
            f"deterministic {model.__tablename__} key {key} conflicts in fields: "
            + ", ".join(conflicts)
        )
    return False


def _ensure_snapshot(
    session: Session,
    *,
    bundle: NormalizedPointInTimeBundle,
    source: BundleSource,
    storage_uri: str,
) -> tuple[SourceSnapshot, bool]:
    existing = session.scalars(
        select(SourceSnapshot).where(
            SourceSnapshot.vendor == bundle.vendor,
            SourceSnapshot.dataset == source.dataset,
            SourceSnapshot.retrieved_at == source.retrieved_at,
            SourceSnapshot.source_hash == source.source_hash,
        )
    ).first()
    if existing is not None:
        if existing.storage_uri != storage_uri or existing.license_tag != bundle.license_tag:
            raise PointInTimeIngestionError(
                f"existing source snapshot conflicts for {source.role}"
            )
        return existing, False
    snapshot = SourceSnapshot(
        snapshot_id=deterministic_id(
            "source_snapshot",
            bundle.vendor,
            source.dataset,
            source.retrieved_at.isoformat(),
            source.source_hash,
        ),
        vendor=bundle.vendor,
        dataset=source.dataset,
        retrieved_at=source.retrieved_at,
        license_tag=bundle.license_tag,
        source_hash=source.source_hash,
        storage_uri=storage_uri,
        created_at=source.retrieved_at,
        updated_at=source.retrieved_at,
    )
    inserted = _insert_or_match(
        session,
        SourceSnapshot,
        "snapshot_id",
        snapshot,
        (
            "vendor",
            "dataset",
            "retrieved_at",
            "license_tag",
            "source_hash",
            "storage_uri",
        ),
    )
    return snapshot, inserted


def _resolve_security(
    session: Session,
    *,
    bundle: NormalizedPointInTimeBundle,
    row: Any,
    source: SourceSnapshot,
) -> tuple[Security, bool]:
    matches = session.scalars(
        select(Security)
        .join(SecurityIdentifier)
        .where(
            SecurityIdentifier.identifier_type == bundle.vendor_identifier_type,
            SecurityIdentifier.identifier_value == row.vendor_id,
        )
        .distinct()
    ).all()
    if len(matches) > 1:
        raise PointInTimeIngestionError(
            f"vendor identifier {row.vendor_id} maps to multiple securities"
        )
    if matches:
        security = matches[0]
        expected = {
            "ticker": row.ticker,
            "name": row.name,
            "exchange": row.exchange,
            "sector": row.sector,
            "industry": row.industry,
            "cik": row.cik,
            "active_from": row.active_from,
            "active_to": row.active_to,
        }
        conflicts = [
            field
            for field, value in expected.items()
            if _normalise_compare(getattr(security, field)) != _normalise_compare(value)
        ]
        if conflicts:
            raise PointInTimeIngestionError(
                f"security {security.security_id} conflicts with vendor row in: "
                + ", ".join(conflicts)
            )
        return security, False

    deterministic_security_id = deterministic_id(
        "security", bundle.vendor, bundle.vendor_identifier_type, row.vendor_id
    )
    security = session.get(Security, deterministic_security_id)
    if security is None:
        ticker_matches = session.scalars(
            select(Security).where(Security.ticker == row.ticker)
        ).all()
        if len(ticker_matches) == 1:
            security = ticker_matches[0]
            occupied = session.scalars(
                select(SecurityIdentifier).where(
                    SecurityIdentifier.security_id == security.security_id,
                    SecurityIdentifier.is_permanent.is_(True),
                    SecurityIdentifier.identifier_type
                    == bundle.vendor_identifier_type,
                    SecurityIdentifier.identifier_value != row.vendor_id,
                )
            ).first()
            if occupied is not None:
                security = None
            expected_identity = (row.name, row.cik)
            actual_identity = (
                (security.name, security.cik) if security is not None else (None, None)
            )
            if (
                security is not None
                and all(expected_identity)
                and all(actual_identity)
                and expected_identity != actual_identity
            ):
                security = None
            if security is not None:
                security.name = row.name
                security.exchange = row.exchange
                security.sector = row.sector
                security.industry = row.industry
                security.cik = row.cik
                security.active_from = row.active_from
                security.active_to = row.active_to
                security.updated_at = source.retrieved_at
                session.flush()
                return security, False

    candidate = Security(
        security_id=deterministic_security_id,
        ticker=row.ticker,
        name=row.name,
        exchange=row.exchange,
        sector=row.sector,
        industry=row.industry,
        cik=row.cik,
        active_from=row.active_from,
        active_to=row.active_to,
        created_at=source.retrieved_at,
        updated_at=source.retrieved_at,
    )
    inserted = _insert_or_match(
        session,
        Security,
        "security_id",
        candidate,
        (
            "ticker",
            "name",
            "exchange",
            "sector",
            "industry",
            "cik",
            "active_from",
            "active_to",
        ),
    )
    return candidate if inserted else session.get(Security, deterministic_security_id), inserted


def persist_bundle(
    bundle: NormalizedPointInTimeBundle,
    *,
    database_url: Optional[str],
    raw_dir: Path = DEFAULT_RAW_DIR,
) -> PointInTimeIngestionResult:
    """Freeze and atomically persist a fully normalized bundle."""

    all_sources = (bundle.manifest, *(bundle.sources[role] for role in bundle.sources))
    storage_uris: dict[str, str] = {}
    for source in all_sources:
        uri = _storage_uri(bundle.vendor, source)
        _freeze_source(raw_dir, uri, source.body)
        storage_uris[source.role] = uri

    session_factory = open_research_database(database_url)
    inserted_snapshots = 0
    reused_snapshots = 0
    inserted_securities = 0
    reused_securities = 0
    inserted_identifiers = 0
    inserted_aliases = 0
    inserted_universes = 0
    inserted_memberships = 0
    inserted_prices = 0
    inserted_actions = 0
    inserted_delistings = 0
    skipped = 0

    with session_scope(session_factory) as session:
        snapshots: dict[str, SourceSnapshot] = {}
        for source in all_sources:
            snapshot, inserted = _ensure_snapshot(
                session,
                bundle=bundle,
                source=source,
                storage_uri=storage_uris[source.role],
            )
            snapshots[source.role] = snapshot
            inserted_snapshots += int(inserted)
            reused_snapshots += int(not inserted)

        security_snapshot = snapshots["securities"]
        security_by_vendor_id: dict[str, Security] = {}
        for row in bundle.securities:
            security, inserted = _resolve_security(
                session,
                bundle=bundle,
                row=row,
                source=security_snapshot,
            )
            if security is None:  # pragma: no cover - defensive typing guard
                raise PointInTimeIngestionError("failed to resolve security")
            security_by_vendor_id[row.vendor_id] = security
            inserted_securities += int(inserted)
            reused_securities += int(not inserted)

            for identifier in row.identifiers:
                candidate = SecurityIdentifier(
                    identifier_id=deterministic_id(
                        "security_identifier",
                        security.security_id,
                        identifier.identifier_type,
                        identifier.identifier_value,
                        identifier.valid_from,
                        security_snapshot.snapshot_id,
                    ),
                    security_id=security.security_id,
                    identifier_type=identifier.identifier_type,
                    identifier_value=identifier.identifier_value,
                    valid_from=identifier.valid_from,
                    valid_to=identifier.valid_to,
                    is_permanent=identifier.is_permanent,
                    source_snapshot_id=security_snapshot.snapshot_id,
                    source_hash=security_snapshot.source_hash,
                    created_at=security_snapshot.retrieved_at,
                )
                inserted = _insert_or_match(
                    session,
                    SecurityIdentifier,
                    "identifier_id",
                    candidate,
                    (
                        "security_id",
                        "identifier_type",
                        "identifier_value",
                        "valid_from",
                        "valid_to",
                        "is_permanent",
                        "source_snapshot_id",
                        "source_hash",
                    ),
                )
                inserted_identifiers += int(inserted)
                skipped += int(not inserted)

            for alias in row.ticker_aliases:
                candidate = TickerAlias(
                    ticker_alias_id=deterministic_id(
                        "ticker_alias",
                        security.security_id,
                        alias.ticker,
                        alias.effective_from,
                        security_snapshot.snapshot_id,
                    ),
                    security_id=security.security_id,
                    ticker=alias.ticker,
                    exchange=alias.exchange,
                    effective_from=alias.effective_from,
                    effective_to=alias.effective_to,
                    announced_at=alias.announced_at,
                    source_snapshot_id=security_snapshot.snapshot_id,
                    source_hash=security_snapshot.source_hash,
                    created_at=security_snapshot.retrieved_at,
                )
                inserted = _insert_or_match(
                    session,
                    TickerAlias,
                    "ticker_alias_id",
                    candidate,
                    (
                        "security_id",
                        "ticker",
                        "exchange",
                        "effective_from",
                        "effective_to",
                        "announced_at",
                        "source_snapshot_id",
                        "source_hash",
                    ),
                )
                inserted_aliases += int(inserted)
                skipped += int(not inserted)

        membership_snapshot = snapshots["memberships"]
        benchmark = security_by_vendor_id[bundle.benchmark_vendor_id]
        universe = UniverseDefinition(
            universe_id=bundle.universe_id,
            name=bundle.universe_name,
            version=bundle.universe_version,
            description=bundle.universe_description,
            window_start=bundle.window_start,
            window_end=bundle.window_end,
            benchmark_security_id=benchmark.security_id,
            benchmark_excluded_from_rankings=True,
            source_snapshot_id=membership_snapshot.snapshot_id,
            source_hash=membership_snapshot.source_hash,
            audit_contract_json={
                **dict(bundle.audit_contract),
                "role_snapshots": {
                    role: {
                        "snapshot_id": snapshots[role].snapshot_id,
                        "source_hash": snapshots[role].source_hash,
                    }
                    for role in snapshots
                },
                "expected_security_ids": sorted(
                    security.security_id for security in security_by_vendor_id.values()
                ),
                "independent_membership_samples": [
                    {
                        **sample,
                        "security_ids": sorted(
                            security_by_vendor_id[vendor_id].security_id
                            for vendor_id in sample["vendor_ids"]
                        ),
                    }
                    for sample in bundle.audit_contract[
                        "independent_membership_samples"
                    ]
                ],
            },
            created_at=membership_snapshot.retrieved_at,
        )
        inserted = _insert_or_match(
            session,
            UniverseDefinition,
            "universe_id",
            universe,
            (
                "name",
                "version",
                "description",
                "window_start",
                "window_end",
                "benchmark_security_id",
                "benchmark_excluded_from_rankings",
                "source_snapshot_id",
            "source_hash",
            "audit_contract_json",
        ),
        )
        inserted_universes += int(inserted)
        skipped += int(not inserted)

        for row in bundle.memberships:
            security = security_by_vendor_id[row.vendor_id]
            candidate = UniverseMembership(
                membership_id=deterministic_id(
                    "universe_membership",
                    bundle.universe_id,
                    security.security_id,
                    row.effective_from,
                    membership_snapshot.snapshot_id,
                ),
                universe_id=bundle.universe_id,
                security_id=security.security_id,
                effective_from=row.effective_from,
                effective_to=row.effective_to,
                announced_at=row.announced_at,
                source_snapshot_id=membership_snapshot.snapshot_id,
                source_hash=membership_snapshot.source_hash,
                created_at=membership_snapshot.retrieved_at,
            )
            inserted = _insert_or_match(
                session,
                UniverseMembership,
                "membership_id",
                candidate,
                (
                    "universe_id",
                    "security_id",
                    "effective_from",
                    "effective_to",
                    "announced_at",
                    "source_snapshot_id",
                    "source_hash",
                ),
            )
            inserted_memberships += int(inserted)
            skipped += int(not inserted)

        price_snapshot = snapshots["prices"]
        for row in bundle.prices:
            security = security_by_vendor_id[row.vendor_id]
            candidate = Price(
                price_id=deterministic_id(
                    "price", security.security_id, row.date, price_snapshot.snapshot_id
                ),
                security_id=security.security_id,
                date=row.date,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                adj_open=row.adj_open,
                adj_high=row.adj_high,
                adj_low=row.adj_low,
                adj_close=row.adj_close,
                volume=row.volume,
                adj_volume=row.adj_volume,
                source_snapshot_id=price_snapshot.snapshot_id,
                created_at=price_snapshot.retrieved_at,
                updated_at=price_snapshot.retrieved_at,
            )
            inserted = _insert_or_match(
                session,
                Price,
                "price_id",
                candidate,
                (
                    "security_id", "date", "open", "high", "low", "close",
                    "adj_open", "adj_high", "adj_low", "adj_close", "volume",
                    "adj_volume", "source_snapshot_id",
                ),
            )
            inserted_prices += int(inserted)
            skipped += int(not inserted)

        action_snapshot = snapshots["corporate_actions"]
        for row in bundle.corporate_actions:
            security = security_by_vendor_id[row.vendor_id]
            related = (
                security_by_vendor_id[row.related_vendor_id]
                if row.related_vendor_id is not None
                else None
            )
            candidate = CorporateAction(
                corporate_action_id=deterministic_id(
                    "corporate_action",
                    security.security_id,
                    row.action_type,
                    row.effective_date,
                    action_snapshot.snapshot_id,
                ),
                security_id=security.security_id,
                action_type=row.action_type,
                effective_date=row.effective_date,
                announced_at=row.announced_at,
                cash_amount=row.cash_amount,
                currency=row.currency,
                ratio_from=row.ratio_from,
                ratio_to=row.ratio_to,
                related_security_id=related.security_id if related else None,
                details_json=_json_compatible(dict(row.details)),
                source_snapshot_id=action_snapshot.snapshot_id,
                source_hash=action_snapshot.source_hash,
                created_at=action_snapshot.retrieved_at,
            )
            inserted = _insert_or_match(
                session,
                CorporateAction,
                "corporate_action_id",
                candidate,
                (
                    "security_id", "action_type", "effective_date", "announced_at",
                    "cash_amount", "currency", "ratio_from", "ratio_to",
                    "related_security_id", "details_json", "source_snapshot_id",
                    "source_hash",
                ),
            )
            inserted_actions += int(inserted)
            skipped += int(not inserted)

        delisting_snapshot = snapshots["delistings"]
        for row in bundle.delistings:
            security = security_by_vendor_id[row.vendor_id]
            successor = (
                security_by_vendor_id[row.successor_vendor_id]
                if row.successor_vendor_id is not None
                else None
            )
            candidate = DelistingEvent(
                delisting_event_id=deterministic_id(
                    "delisting",
                    security.security_id,
                    row.delisting_date,
                    delisting_snapshot.snapshot_id,
                ),
                security_id=security.security_id,
                delisting_date=row.delisting_date,
                announced_at=row.announced_at,
                delisting_return=row.delisting_return,
                return_available_at=row.return_available_at,
                reason=row.reason,
                successor_security_id=successor.security_id if successor else None,
                source_snapshot_id=delisting_snapshot.snapshot_id,
                source_hash=delisting_snapshot.source_hash,
                created_at=delisting_snapshot.retrieved_at,
            )
            inserted = _insert_or_match(
                session,
                DelistingEvent,
                "delisting_event_id",
                candidate,
                (
                    "security_id", "delisting_date", "announced_at",
                    "delisting_return", "return_available_at", "reason",
                    "successor_security_id", "source_snapshot_id", "source_hash",
                ),
            )
            inserted_delistings += int(inserted)
            skipped += int(not inserted)

        validate_security_master(session)

    return PointInTimeIngestionResult(
        source_snapshots_inserted=inserted_snapshots,
        source_snapshots_reused=reused_snapshots,
        securities_inserted=inserted_securities,
        securities_reused=reused_securities,
        identifiers_inserted=inserted_identifiers,
        ticker_aliases_inserted=inserted_aliases,
        universe_definitions_inserted=inserted_universes,
        memberships_inserted=inserted_memberships,
        prices_inserted=inserted_prices,
        corporate_actions_inserted=inserted_actions,
        delistings_inserted=inserted_delistings,
        duplicate_rows_skipped=skipped,
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest a licensed point-in-time equity JSON bundle."
    )
    parser.add_argument("bundle_dir", type=Path, help="Directory containing manifest.json.")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument(
        "--expected-manifest-hash",
        help="Optional lowercase SHA-256 pin for manifest.json.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        bundle = PointInTimeEquityBundleAdapter(
            args.bundle_dir,
            expected_manifest_hash=args.expected_manifest_hash,
        ).load()
        result = persist_bundle(
            bundle,
            database_url=args.database_url,
            raw_dir=args.raw_dir,
        )
    except (OSError, PointInTimeIngestionError, SQLAlchemyError, ValueError) as exc:
        print(f"point-in-time ingestion failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
