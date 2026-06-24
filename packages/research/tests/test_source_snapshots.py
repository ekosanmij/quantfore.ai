import pytest
from sqlalchemy import inspect, select

from quantfore_research.db import build_engine, create_schema, make_session_factory, session_scope
from quantfore_research.models import SourceSnapshot
from quantfore_research.snapshots import record_source_snapshot, sha256_text


def test_record_source_snapshot_persists_required_audit_fields():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session_factory = make_session_factory(engine)
    source_hash = sha256_text("fred:DGS10:2026-06-24")

    with session_scope(session_factory) as session:
        snapshot = record_source_snapshot(
            session,
            vendor="fred",
            dataset="macro/series/DGS10",
            license_tag="public-fred",
            source_hash=source_hash,
            storage_uri="s3://quantfore-raw/fred/DGS10/2026-06-24.json",
        )
        snapshot_id = snapshot.snapshot_id

    with session_factory() as session:
        saved = session.scalar(
            select(SourceSnapshot).where(SourceSnapshot.snapshot_id == snapshot_id)
        )

    assert saved is not None
    assert saved.vendor == "fred"
    assert saved.dataset == "macro/series/DGS10"
    assert saved.license_tag == "public-fred"
    assert saved.source_hash == source_hash
    assert saved.storage_uri == "s3://quantfore-raw/fred/DGS10/2026-06-24.json"
    assert saved.retrieved_at is not None


def test_source_snapshots_table_contract_matches_audit_log_fields():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)

    columns = {
        column["name"]
        for column in inspect(engine).get_columns(SourceSnapshot.__tablename__)
    }

    assert {
        "snapshot_id",
        "vendor",
        "dataset",
        "retrieved_at",
        "license_tag",
        "hash",
        "storage_uri",
        "created_at",
        "updated_at",
    }.issubset(columns)


def test_same_source_hash_can_be_recorded_for_multiple_retrieval_events():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session_factory = make_session_factory(engine)
    source_hash = sha256_text("same-sec-companyfacts-payload")

    with session_scope(session_factory) as session:
        first = record_source_snapshot(
            session,
            vendor="SEC EDGAR",
            dataset="companyfacts_MSFT",
            license_tag="public_source",
            source_hash=source_hash,
            storage_uri="raw/sec/companyfacts/MSFT/2026-06-24T13-42-00.json",
        )
        second = record_source_snapshot(
            session,
            vendor="SEC EDGAR",
            dataset="companyfacts_MSFT",
            license_tag="public_source",
            source_hash=source_hash,
            storage_uri="raw/sec/companyfacts/MSFT/2026-06-25T09-00-00.json",
        )

    assert first.snapshot_id != second.snapshot_id


def test_blank_audit_fields_are_rejected_before_insert():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session_factory = make_session_factory(engine)

    with session_factory() as session:
        with pytest.raises(ValueError, match="vendor is required"):
            record_source_snapshot(
                session,
                vendor=" ",
                dataset="companyfacts_MSFT",
                license_tag="public_source",
                source_hash="a91f3c",
                storage_uri="raw/sec/companyfacts/MSFT/2026-06-24.json",
            )
