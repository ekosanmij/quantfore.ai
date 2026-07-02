import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from pipelines.ingest_point_in_time_fundamentals import ingest_bundle
from pipelines.ingest_sec_companyfacts import resolve_sec_security
from quantfore_research.db import build_engine, create_schema, make_session_factory
from quantfore_research.ingest.point_in_time_fundamentals import (
    PointInTimeFundamentalBundleAdapter,
    PointInTimeFundamentalIngestionError,
)
from quantfore_research.models import (
    Fundamental,
    Security,
    SecurityIdentifier,
    SourceSnapshot,
)


HASH = "a" * 64


def vendor_rows():
    base = {
        "issuer": "PERM-1",
        "period": "2020-03-31",
        "fy": 2020,
        "fq": 1,
        "dimension": "quarterly",
        "form": "10-Q",
        "accession": "0001-20-001",
        "filed": "2020-05-01T20:00:00Z",
        "accepted": "2020-05-01T20:30:00Z",
        "released": "2020-05-01T20:30:00Z",
        "vendor_at": "2020-05-01T21:00:00Z",
        "model_at": "2020-05-01T22:00:00Z",
        "revision": 1,
        "tag": "VendorRevenue",
        "amount": "100.25",
        "measure": "USD",
    }
    return [
        base,
        {
            **base,
            "form": "10-Q/A",
            "accession": "0001-20-001-A",
            "filed": "2020-06-01T20:00:00Z",
            "accepted": "2020-06-01T20:30:00Z",
            "released": "2020-06-01T20:30:00Z",
            "vendor_at": "2020-06-01T21:00:00Z",
            "model_at": "2020-06-01T22:00:00Z",
            "revision": 2,
            "amount": "101.50",
        },
    ]


def write_bundle(tmp_path, rows=None):
    rows = vendor_rows() if rows is None else rows
    body = (json.dumps(rows, sort_keys=True) + "\n").encode()
    data_path = tmp_path / "vendor-facts.json"
    data_path.write_bytes(body)
    field_map = {
        "vendor_id": "issuer",
        "fiscal_period_end": "period",
        "fiscal_year": "fy",
        "fiscal_quarter": "fq",
        "period_type": "dimension",
        "form_type": "form",
        "filing_accession": "accession",
        "filed_at": "filed",
        "accepted_at": "accepted",
        "public_release_at": "released",
        "vendor_available_at": "vendor_at",
        "model_available_at": "model_at",
        "revision_version": "revision",
        "concept": "tag",
        "value": "amount",
        "unit": "measure",
    }
    manifest = {
        "schema_version": "point-in-time-fundamentals-bundle-v1",
        "vendor": "Licensed Test Vendor",
        "dataset": "test-pit-fundamentals",
        "license_tag": "test-only",
        "license_evidence_uri": "internal://rights/test-vendor",
        "vendor_identifier_type": "TEST_PERMANENT_ID",
        "concept_map_version": "test-v1",
        "field_map": field_map,
        "concept_map": {"VendorRevenue": "revenue"},
        "fundamentals_file": {
            "path": "vendor-facts.json",
            "sha256": hashlib.sha256(body).hexdigest(),
            "retrieved_at": "2026-01-01T00:00:00Z",
            "source_uri": "vendor://export/facts",
        },
    }
    manifest_body = (json.dumps(manifest, sort_keys=True) + "\n").encode()
    (tmp_path / "manifest.json").write_bytes(manifest_body)
    return hashlib.sha256(manifest_body).hexdigest()


def load_bundle(tmp_path, rows=None):
    manifest_hash = write_bundle(tmp_path, rows)
    return PointInTimeFundamentalBundleAdapter.load(
        tmp_path, expected_manifest_hash=manifest_hash
    )


def make_session():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    return make_session_factory(engine)()


def seed_security(session):
    snapshot = SourceSnapshot(
        snapshot_id="identity-snapshot",
        vendor="Licensed Test Vendor",
        dataset="security-master",
        retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        license_tag="test-only",
        source_hash=HASH,
        storage_uri="raw/test/security-master.json",
    )
    security = Security(
        security_id="security-1",
        ticker="TST",
        name="Test Issuer",
        sector="Industrials",
    )
    session.add_all([snapshot, security])
    session.flush()
    session.add(
        SecurityIdentifier(
            identifier_id="identifier-1",
            security_id=security.security_id,
            identifier_type="TEST_PERMANENT_ID",
            identifier_value="PERM-1",
            valid_from=date(2000, 1, 1),
            valid_to=None,
            is_permanent=True,
            source_snapshot_id=snapshot.snapshot_id,
            source_hash=HASH,
        )
    )
    session.commit()


def test_vendor_field_map_normalizes_revisions_and_preserves_original_concept(tmp_path):
    bundle = load_bundle(tmp_path)

    assert bundle.vendor == "Licensed Test Vendor"
    assert [fact.revision_version for fact in bundle.facts] == [1, 2]
    assert bundle.facts[0].period_type == "QUARTERLY"
    assert bundle.facts[0].concept == "VendorRevenue"
    assert bundle.facts[0].standardized_concept == "revenue"
    assert bundle.facts[1].form_type == "10-Q/A"
    assert bundle.source.source_hash == hashlib.sha256(
        (tmp_path / "vendor-facts.json").read_bytes()
    ).hexdigest()


def test_adapter_rejects_revision_gaps_and_hash_changes(tmp_path):
    rows = vendor_rows()
    rows[1]["revision"] = 3
    with pytest.raises(PointInTimeFundamentalIngestionError, match="contiguous"):
        load_bundle(tmp_path, rows)

    tmp_path_2 = tmp_path / "second"
    tmp_path_2.mkdir()
    manifest_hash = write_bundle(tmp_path_2)
    (tmp_path_2 / "vendor-facts.json").write_text("[]\n", encoding="utf-8")
    with pytest.raises(PointInTimeFundamentalIngestionError, match="SHA-256"):
        PointInTimeFundamentalBundleAdapter.load(
            tmp_path_2, expected_manifest_hash=manifest_hash
        )


def test_ingestion_links_permanent_security_and_is_idempotent(tmp_path):
    session = make_session()
    seed_security(session)
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    bundle = load_bundle(bundle_dir)

    first = ingest_bundle(session, bundle, raw_dir=tmp_path / "raw")
    session.commit()
    second = ingest_bundle(session, bundle, raw_dir=tmp_path / "raw")
    session.commit()

    assert first.facts_inserted == 2
    assert second.facts_reused == 2
    assert session.scalar(select(func.count()).select_from(Fundamental)) == 2
    facts = session.scalars(
        select(Fundamental).order_by(Fundamental.revision_version)
    ).all()
    assert {fact.security_id for fact in facts} == {"security-1"}
    assert [fact.value for fact in facts] == [
        Decimal("100.250000"),
        Decimal("101.500000"),
    ]
    assert len(
        list((tmp_path / "raw/point-in-time-fundamentals").rglob("*.json"))
    ) == 2


def test_ingestion_rejects_missing_or_nonpermanent_identifier(tmp_path):
    session = make_session()
    snapshot = SourceSnapshot(
        snapshot_id="identity-snapshot",
        vendor="Test",
        dataset="security-master",
        license_tag="test",
        source_hash=HASH,
        storage_uri="raw/test/security.json",
    )
    security = Security(
        security_id="security-1", ticker="TST", name="Test Issuer"
    )
    session.add_all([snapshot, security])
    session.flush()
    session.add(
        SecurityIdentifier(
            security_id="security-1",
            identifier_type="TEST_PERMANENT_ID",
            identifier_value="PERM-1",
            valid_from=date(2000, 1, 1),
            is_permanent=False,
            source_snapshot_id="identity-snapshot",
            source_hash=HASH,
        )
    )
    session.commit()
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    bundle = load_bundle(bundle_dir)

    with pytest.raises(PointInTimeFundamentalIngestionError, match="no TEST_PERMANENT_ID"):
        ingest_bundle(session, bundle, raw_dir=tmp_path / "raw")


def test_sec_reconciliation_prefers_existing_cik_identity_over_ticker():
    session = make_session()
    security = Security(
        security_id="security-existing",
        ticker="OLD",
        name="Existing Sprint 7 Security",
        cik="0000123456",
    )
    session.add(security)
    session.commit()

    resolved = resolve_sec_security(
        session,
        ticker="NEW",
        name="Renamed Issuer",
        cik="0000123456",
    )

    assert resolved.security_id == "security-existing"
    assert session.scalar(select(func.count()).select_from(Security)) == 1
