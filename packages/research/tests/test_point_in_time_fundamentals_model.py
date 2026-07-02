from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
)
from quantfore_research.models import Fundamental, Security, SourceSnapshot


HASH = "f" * 64
AVAILABLE_AT = datetime(2020, 5, 1, 21, 0, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as database_session:
        database_session.add_all(
            [
                SourceSnapshot(
                    snapshot_id="snapshot-fundamentals",
                    vendor="Licensed Test Vendor",
                    dataset="point-in-time-fundamentals-v1",
                    retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    license_tag="test-only",
                    source_hash=HASH,
                    storage_uri="raw/test/fundamentals.json",
                ),
                Security(
                    security_id="security-1",
                    ticker="TST",
                    name="Test Security",
                ),
            ]
        )
        database_session.commit()
        yield database_session


def make_fact(**overrides) -> Fundamental:
    values = {
        "security_id": "security-1",
        "fiscal_period_end": date(2020, 3, 31),
        "fiscal_year": 2020,
        "fiscal_quarter": 1,
        "period_type": "QUARTERLY",
        "form_type": "10-Q",
        "filing_accession": "0000000000-20-000001",
        "filed_at": datetime(2020, 5, 1, tzinfo=timezone.utc),
        "accepted_at": datetime(2020, 5, 1, 20, 30, tzinfo=timezone.utc),
        "public_release_at": datetime(2020, 5, 1, 20, 30, tzinfo=timezone.utc),
        "vendor_available_at": AVAILABLE_AT,
        "model_available_at": AVAILABLE_AT,
        "revision_version": 1,
        "concept": "VendorRevenue",
        "standardized_concept": "revenue",
        "value": Decimal("100.25"),
        "unit": "USD",
        "source_snapshot_id": "snapshot-fundamentals",
        "source_hash": HASH,
    }
    values.update(overrides)
    return Fundamental(**values)


def test_fundamentals_table_has_sprint_8_point_in_time_contract():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("fundamentals")}

    assert {
        "security_id",
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
        "standardized_concept",
        "value",
        "unit",
        "source_snapshot_id",
        "source_hash",
    }.issubset(columns)


def test_fact_is_exactly_reconstructable_from_snapshot_and_revision(session):
    fact = make_fact()
    session.add(fact)
    session.commit()

    reconstructed = session.scalar(
        select(Fundamental).where(
            Fundamental.source_snapshot_id == "snapshot-fundamentals",
            Fundamental.revision_version == 1,
            Fundamental.security_id == "security-1",
            Fundamental.concept == "VendorRevenue",
            Fundamental.fiscal_period_end == date(2020, 3, 31),
        )
    )

    assert reconstructed is not None
    assert reconstructed.value == Decimal("100.250000")
    assert reconstructed.standardized_concept == "revenue"
    assert reconstructed.source_hash == HASH


def test_amendment_adds_revision_without_replacing_original(session):
    original = make_fact(fundamental_id="fact-v1")
    amendment = make_fact(
        fundamental_id="fact-v2",
        filing_accession="0000000000-20-000001-A",
        form_type="10-Q/A",
        revision_version=2,
        value=Decimal("102.50"),
        model_available_at=datetime(2020, 6, 1, 21, 0, tzinfo=timezone.utc),
        vendor_available_at=datetime(2020, 6, 1, 21, 0, tzinfo=timezone.utc),
    )
    session.add_all([original, amendment])
    session.commit()

    versions = session.scalars(
        select(Fundamental)
        .where(Fundamental.concept == "VendorRevenue")
        .order_by(Fundamental.revision_version)
    ).all()

    assert [(item.revision_version, item.value) for item in versions] == [
        (1, Decimal("100.250000")),
        (2, Decimal("102.500000")),
    ]


def test_fundamental_rows_are_append_only(session):
    fact = make_fact()
    session.add(fact)
    session.commit()

    fact.value = Decimal("999")
    with pytest.raises(RuntimeError, match="append-only"):
        session.commit()
    session.rollback()

    session.delete(fact)
    with pytest.raises(RuntimeError, match="append-only"):
        session.commit()


def test_source_hash_and_availability_constraints_are_enforced(session):
    session.add(make_fact(source_hash="0" * 64))
    with pytest.raises(ValueError, match="does not match"):
        session.commit()
    session.rollback()

    session.add(
        make_fact(
            model_available_at=datetime(2020, 4, 30, tzinfo=timezone.utc),
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()
