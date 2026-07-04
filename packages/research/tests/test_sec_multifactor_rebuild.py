import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "pipelines"))

from quantfore_research.db import build_engine, create_schema, make_session_factory
from quantfore_research.models import (
    Security,
    SecurityClassification,
    SecurityIdentifier,
    SourceSnapshot,
)

from pipelines.rebuild_sec_multifactor_evidence import ingest_classifications


def test_classifications_resolve_figi_and_composite_permanent_ids(tmp_path):
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session_factory = make_session_factory(engine)
    retrieved_at = datetime(2026, 7, 3, tzinfo=timezone.utc)
    rows = [
        {
            "classification_system": "SEC_SIC_TO_GICS_V1",
            "effective_from": "2017-01-01",
            "effective_to": "2025-06-30",
            "filing_accession": "0000000001-16-000001",
            "industry": "3571",
            "model_available_at": "2016-10-26T20:42:16Z",
            "sector": "Information Technology",
            "vendor_id": "BBG000000001",
        },
        {
            "classification_system": "SEC_SIC_TO_GICS_V1",
            "effective_from": "2017-01-01",
            "effective_to": "2025-06-30",
            "filing_accession": "0000000002-16-000001",
            "industry": None,
            "model_available_at": "2016-10-27T20:42:16Z",
            "sector": "Unknown",
            "vendor_id": "CIK0000000002:OLD",
        },
    ]
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    body = json.dumps(rows, sort_keys=True).encode()
    (bundle_dir / "classifications.json").write_bytes(body)
    manifest = {
        "fundamentals_file": {"retrieved_at": retrieved_at.isoformat()},
        "classifications_file": {
            "path": "classifications.json",
            "sha256": hashlib.sha256(body).hexdigest(),
        },
    }

    with session_factory() as session:
        source = SourceSnapshot(
            vendor="test",
            dataset="identifiers",
            retrieved_at=retrieved_at,
            license_tag="test-only",
            source_hash="a" * 64,
            storage_uri="raw/test/identifiers.json",
        )
        figi_security = Security(ticker="NEW", name="FIGI Security")
        composite_security = Security(ticker="OLD", name="Composite Security")
        session.add_all([source, figi_security, composite_security])
        session.flush()
        session.add_all(
            [
                SecurityIdentifier(
                    security_id=figi_security.security_id,
                    identifier_type="FIGI_SHARE_CLASS",
                    identifier_value="BBG000000001",
                    valid_from=date(2017, 1, 1),
                    is_permanent=True,
                    source_snapshot_id=source.snapshot_id,
                    source_hash=source.source_hash,
                ),
                SecurityIdentifier(
                    security_id=composite_security.security_id,
                    identifier_type="COMPOSITE_PERMANENT_ID",
                    identifier_value="CIK0000000002:OLD",
                    valid_from=date(2017, 1, 1),
                    is_permanent=True,
                    source_snapshot_id=source.snapshot_id,
                    source_hash=source.source_hash,
                ),
            ]
        )
        session.flush()

        _, inserted = ingest_classifications(
            session,
            bundle_dir=bundle_dir,
            manifest=manifest,
            raw_root=tmp_path / "data" / "raw",
        )

        assert inserted == 2
        stored = session.query(SecurityClassification).all()
        assert {row.security_id for row in stored} == {
            figi_security.security_id,
            composite_security.security_id,
        }
