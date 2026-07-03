"""Ingest an immutable vendor-neutral point-in-time fundamental bundle."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import DEFAULT_RAW_DIR, get_code_revision, open_research_database
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        DEFAULT_RAW_DIR,
        get_code_revision,
        open_research_database,
    )

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantfore_research.db import session_scope
from quantfore_research.ingest.point_in_time_fundamentals import (
    CanonicalFundamental,
    NormalizedFundamentalBundle,
    PointInTimeFundamentalBundleAdapter,
    PointInTimeFundamentalIngestionError,
    deterministic_fundamental_id,
)
from quantfore_research.models import (
    Fundamental,
    SecurityIdentifier,
    SourceSnapshot,
)
from quantfore_research.snapshots import record_source_snapshot


DEFAULT_RESULT_OUTPUT = Path(
    "reports/data-audits/pit-fundamentals-ingestion-v1.json"
)


@dataclass(frozen=True)
class FundamentalIngestionResult:
    source_snapshots_inserted: int
    source_snapshots_reused: int
    facts_inserted: int
    facts_reused: int
    security_count: int
    manifest_hash: str
    fundamental_source_hash: str


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "vendor"


def _freeze(raw_dir: Path, storage_uri: str, body: bytes) -> None:
    target = raw_dir.parent / storage_uri
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if not target.is_file() or target.read_bytes() != body:
            raise PointInTimeFundamentalIngestionError(
                f"immutable raw path contains different bytes: {storage_uri}"
            )
        return
    target.write_bytes(body)


def _snapshot(
    session: Session,
    *,
    vendor: str,
    dataset: str,
    retrieved_at: datetime,
    license_tag: str,
    source_hash: str,
    storage_uri: str,
) -> tuple[SourceSnapshot, bool]:
    existing = session.scalar(
        select(SourceSnapshot).where(SourceSnapshot.storage_uri == storage_uri)
    )
    if existing is not None:
        expected = (
            vendor,
            dataset,
            license_tag,
            source_hash,
            retrieved_at.astimezone(timezone.utc),
        )
        actual = (
            existing.vendor,
            existing.dataset,
            existing.license_tag,
            existing.source_hash,
            _utc(existing.retrieved_at),
        )
        if actual != expected:
            raise PointInTimeFundamentalIngestionError(
                f"source snapshot conflict at {storage_uri}"
            )
        return existing, False
    return (
        record_source_snapshot(
            session,
            vendor=vendor,
            dataset=dataset,
            retrieved_at=retrieved_at,
            license_tag=license_tag,
            source_hash=source_hash,
            storage_uri=storage_uri,
        ),
        True,
    )


def _resolve_security_ids(
    session: Session,
    bundle: NormalizedFundamentalBundle,
) -> dict[str, str]:
    values = sorted({fact.vendor_id for fact in bundle.facts})
    rows = session.scalars(
        select(SecurityIdentifier).where(
            func.upper(SecurityIdentifier.identifier_type)
            == bundle.vendor_identifier_type,
            SecurityIdentifier.is_permanent.is_(True),
            func.upper(SecurityIdentifier.identifier_value).in_(
                [value.upper() for value in values]
            ),
        )
    ).all()
    by_value: dict[str, list[SecurityIdentifier]] = {}
    for row in rows:
        by_value.setdefault(row.identifier_value.upper(), []).append(row)

    failures: list[str] = []
    result: dict[str, str] = {}
    for value in values:
        identifier_rows = by_value.get(value.upper(), [])
        facts = [fact for fact in bundle.facts if fact.vendor_id == value]
        security_ids: set[str] = set()
        missing_dates = []
        ambiguous_dates = []
        for fact in facts:
            eligible_rows = [
                row
                for row in identifier_rows
                if row.is_permanent
                or (
                    row.valid_from <= fact.fiscal_period_end
                    and (row.valid_to is None or fact.fiscal_period_end <= row.valid_to)
                )
            ]
            eligible_ids = {row.security_id for row in eligible_rows}
            if not eligible_ids:
                missing_dates.append(fact.fiscal_period_end)
            elif len(eligible_ids) > 1:
                ambiguous_dates.append(fact.fiscal_period_end)
            security_ids.update(eligible_ids)
        if missing_dates:
            failures.append(
                f"vendor_id={value!r} has no {bundle.vendor_identifier_type} "
                f"mapping on {sorted(set(missing_dates))!r}"
            )
        elif ambiguous_dates or len(security_ids) > 1:
            failures.append(
                f"vendor_id={value!r} maps ambiguously to {sorted(security_ids)!r}"
            )
        else:
            result[value] = next(iter(security_ids))
    if failures:
        raise PointInTimeFundamentalIngestionError(
            "security identifier resolution failed:\n- " + "\n- ".join(failures)
        )
    return result


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_fact(
    existing: Fundamental,
    fact: CanonicalFundamental,
    *,
    security_id: str,
    source_snapshot_id: str,
    source_hash: str,
) -> bool:
    return all(
        (
            existing.fiscal_period_end == fact.fiscal_period_end,
            existing.security_id == security_id,
            existing.fiscal_year == fact.fiscal_year,
            existing.fiscal_quarter == fact.fiscal_quarter,
            existing.period_type == fact.period_type,
            existing.form_type == fact.form_type,
            existing.filing_accession == fact.filing_accession,
            _utc(existing.filed_at) == fact.filed_at,
            (
                None
                if existing.accepted_at is None
                else _utc(existing.accepted_at)
            )
            == fact.accepted_at,
            (
                None
                if existing.public_release_at is None
                else _utc(existing.public_release_at)
            )
            == fact.public_release_at,
            _utc(existing.vendor_available_at) == fact.vendor_available_at,
            _utc(existing.model_available_at) == fact.model_available_at,
            existing.revision_version == fact.revision_version,
            existing.concept == fact.concept,
            existing.standardized_concept == fact.standardized_concept,
            existing.value == fact.value,
            existing.unit == fact.unit,
            existing.source_snapshot_id == source_snapshot_id,
            existing.source_hash == source_hash,
        )
    )


def ingest_bundle(
    session: Session,
    bundle: NormalizedFundamentalBundle,
    *,
    raw_dir: Path = DEFAULT_RAW_DIR,
) -> FundamentalIngestionResult:
    """Resolve identities and append all facts atomically and idempotently."""

    security_ids = _resolve_security_ids(session, bundle)
    vendor_slug = _slug(bundle.vendor)
    source_uri = (
        f"raw/point-in-time-fundamentals/{vendor_slug}/facts/"
        f"{bundle.source.source_hash}.json"
    )
    manifest_uri = (
        f"raw/point-in-time-fundamentals/{vendor_slug}/manifests/"
        f"{bundle.manifest_hash}.json"
    )
    manifest_body = bundle.manifest_body
    _freeze(raw_dir, source_uri, bundle.source.body)
    _freeze(raw_dir, manifest_uri, manifest_body)

    inserted_snapshots = 0
    reused_snapshots = 0
    data_snapshot, inserted = _snapshot(
        session,
        vendor=bundle.vendor,
        dataset=f"{bundle.dataset}@{bundle.concept_map_version}",
        retrieved_at=bundle.source.retrieved_at,
        license_tag=bundle.license_tag,
        source_hash=bundle.source.source_hash,
        storage_uri=source_uri,
    )
    inserted_snapshots += int(inserted)
    reused_snapshots += int(not inserted)
    _, inserted = _snapshot(
        session,
        vendor=bundle.vendor,
        dataset=f"{bundle.dataset}_manifest",
        retrieved_at=bundle.source.retrieved_at,
        license_tag=bundle.license_tag,
        source_hash=bundle.manifest_hash,
        storage_uri=manifest_uri,
    )
    inserted_snapshots += int(inserted)
    reused_snapshots += int(not inserted)

    inserted_facts = 0
    reused_facts = 0
    for fact in bundle.facts:
        fundamental_id = deterministic_fundamental_id(
            bundle.vendor, bundle.source.source_hash, fact
        )
        existing = session.get(Fundamental, fundamental_id)
        if existing is not None:
            if not _same_fact(
                existing,
                fact,
                security_id=security_ids[fact.vendor_id],
                source_snapshot_id=data_snapshot.snapshot_id,
                source_hash=data_snapshot.source_hash,
            ):
                raise PointInTimeFundamentalIngestionError(
                    f"fundamental ID conflict for source row {fact.source_row_number}"
                )
            reused_facts += 1
            continue
        session.add(
            Fundamental(
                fundamental_id=fundamental_id,
                security_id=security_ids[fact.vendor_id],
                fiscal_period_end=fact.fiscal_period_end,
                fiscal_year=fact.fiscal_year,
                fiscal_quarter=fact.fiscal_quarter,
                period_type=fact.period_type,
                form_type=fact.form_type,
                filing_accession=fact.filing_accession,
                filed_at=fact.filed_at,
                accepted_at=fact.accepted_at,
                public_release_at=fact.public_release_at,
                vendor_available_at=fact.vendor_available_at,
                model_available_at=fact.model_available_at,
                revision_version=fact.revision_version,
                concept=fact.concept,
                standardized_concept=fact.standardized_concept,
                value=fact.value,
                unit=fact.unit,
                source_snapshot_id=data_snapshot.snapshot_id,
                source_hash=data_snapshot.source_hash,
            )
        )
        inserted_facts += 1
    session.flush()
    return FundamentalIngestionResult(
        source_snapshots_inserted=inserted_snapshots,
        source_snapshots_reused=reused_snapshots,
        facts_inserted=inserted_facts,
        facts_reused=reused_facts,
        security_count=len(set(security_ids.values())),
        manifest_hash=bundle.manifest_hash,
        fundamental_source_hash=bundle.source.source_hash,
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _write_report(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(document, indent=2, sort_keys=True, default=_json_value) + "\n").encode()
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest a point-in-time fundamental vendor bundle."
    )
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--expected-manifest-hash", required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--result-output", type=Path, default=DEFAULT_RESULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    generated_at = datetime.now(timezone.utc)
    try:
        bundle = PointInTimeFundamentalBundleAdapter.load(
            args.bundle_dir,
            expected_manifest_hash=args.expected_manifest_hash,
        )
        session_factory = open_research_database(args.database_url)
        with session_scope(session_factory) as session:
            result = ingest_bundle(session, bundle, raw_dir=args.raw_dir)
        document = {
            "ingestion_id": "pit-fundamentals-ingestion-v1",
            "decision": "pass",
            "claims_eligible": False,
            "generated_at": generated_at,
            "code_revision": get_code_revision(),
            "vendor": bundle.vendor,
            "dataset": bundle.dataset,
            "vendor_identifier_type": bundle.vendor_identifier_type,
            "concept_map_version": bundle.concept_map_version,
            "license_evidence_uri": bundle.license_evidence_uri,
            "result": asdict(result),
            "failures": [],
        }
        exit_code = 0
    except (OSError, RuntimeError, ValueError) as exc:
        document = {
            "ingestion_id": "pit-fundamentals-ingestion-v1",
            "decision": "fail",
            "claims_eligible": False,
            "generated_at": generated_at,
            "code_revision": get_code_revision(),
            "result": None,
            "failures": [{"code": "INGESTION_REJECTED", "message": str(exc)}],
        }
        exit_code = 2
    _write_report(args.result_output, document)
    if exit_code:
        print(document["failures"][0]["message"], file=sys.stderr)
    else:
        print(
            f"facts_inserted={result.facts_inserted} facts_reused={result.facts_reused} "
            f"securities={result.security_count} source_hash={result.fundamental_source_hash}"
        )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
