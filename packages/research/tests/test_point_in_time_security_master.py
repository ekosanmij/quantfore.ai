from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from quantfore_research.db import build_engine, create_schema, make_session_factory
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
from quantfore_research.validation.security_master import (
    SecurityMasterValidationError,
    validate_security_master,
)


NOW = datetime(2020, 1, 1, tzinfo=timezone.utc)
SOURCE_HASH = "a" * 64
REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def session():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as database_session:
        yield database_session


def add_snapshot(session) -> SourceSnapshot:
    snapshot = SourceSnapshot(
        vendor="test-vendor",
        dataset="historical-membership",
        retrieved_at=NOW,
        license_tag="test-only",
        source_hash=SOURCE_HASH,
        storage_uri="data/raw/test/membership.json",
    )
    session.add(snapshot)
    session.flush()
    return snapshot


def add_universe(session, snapshot, benchmark) -> UniverseDefinition:
    universe = UniverseDefinition(
        universe_id="sp500-pit-v1",
        name="Historical S&P 500",
        version="v1",
        description="Historical membership by effective date",
        window_start=date(2014, 1, 1),
        window_end=date(2025, 12, 31),
        benchmark_security_id=benchmark.security_id,
        benchmark_excluded_from_rankings=True,
        source_snapshot_id=snapshot.snapshot_id,
        source_hash=SOURCE_HASH,
    )
    session.add(universe)
    session.flush()
    return universe


def test_security_master_tables_exist():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)

    assert {
        "security_identifiers",
        "ticker_aliases",
        "universe_definitions",
        "universe_memberships",
        "corporate_actions",
        "delisting_events",
    }.issubset(inspect(engine).get_table_names())


def test_complete_security_master_resolves_rename_to_one_permanent_security(session):
    snapshot = add_snapshot(session)
    company = Security(ticker="META", name="Meta Platforms")
    benchmark = Security(ticker="SPY", name="SPDR S&P 500 ETF Trust")
    successor = Security(ticker="NEW", name="Successor")
    session.add_all([company, benchmark, successor])
    session.flush()
    universe = add_universe(session, snapshot, benchmark)

    session.add_all(
        [
            SecurityIdentifier(
                security_id=company.security_id,
                identifier_type="SHARADAR_PERMATICKER",
                identifier_value="12345",
                valid_from=date(2012, 5, 18),
                is_permanent=True,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
            TickerAlias(
                security_id=company.security_id,
                ticker="FB",
                effective_from=date(2012, 5, 18),
                effective_to=date(2022, 6, 8),
                announced_at=NOW,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
            TickerAlias(
                security_id=company.security_id,
                ticker="META",
                effective_from=date(2022, 6, 9),
                announced_at=NOW,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
            UniverseMembership(
                universe_id=universe.universe_id,
                security_id=company.security_id,
                effective_from=date(2013, 12, 23),
                effective_to=date(2025, 12, 31),
                announced_at=NOW,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
            CorporateAction(
                security_id=company.security_id,
                action_type="symbol_change",
                effective_date=date(2022, 6, 9),
                announced_at=NOW,
                details_json={"from": "FB", "to": "META"},
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
            DelistingEvent(
                security_id=company.security_id,
                delisting_date=date(2025, 12, 31),
                announced_at=NOW,
                delisting_return=Decimal("-0.25"),
                return_available_at=NOW,
                reason="test acquisition",
                successor_security_id=successor.security_id,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
        ]
    )

    summary = validate_security_master(session)

    assert summary.security_count == 3
    assert summary.identifier_count == 1
    assert summary.ticker_alias_count == 2
    assert summary.membership_count == 1
    assert summary.universe_count == 1
    assert summary.corporate_action_count == 1
    assert summary.delisting_event_count == 1


def test_overlapping_memberships_fail_validation(session):
    snapshot = add_snapshot(session)
    company = Security(ticker="ABC", name="Example")
    benchmark = Security(ticker="SPY", name="SPDR S&P 500 ETF Trust")
    session.add_all([company, benchmark])
    session.flush()
    universe = add_universe(session, snapshot, benchmark)
    session.add_all(
        [
            UniverseMembership(
                universe_id=universe.universe_id,
                security_id=company.security_id,
                effective_from=date(2019, 1, 1),
                effective_to=date(2020, 6, 30),
                announced_at=NOW,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
            UniverseMembership(
                universe_id=universe.universe_id,
                security_id=company.security_id,
                effective_from=date(2020, 6, 30),
                effective_to=date(2021, 1, 1),
                announced_at=NOW,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
        ]
    )

    with pytest.raises(SecurityMasterValidationError, match="overlapping universe"):
        validate_security_master(session)


def test_ambiguous_ticker_mapping_fails_validation_case_insensitively(session):
    snapshot = add_snapshot(session)
    first = Security(ticker="ABC", name="First")
    second = Security(ticker="abc", name="Second")
    session.add_all([first, second])
    session.flush()
    session.add_all(
        [
            TickerAlias(
                security_id=first.security_id,
                ticker="ABC",
                effective_from=date(2020, 1, 1),
                effective_to=date(2020, 12, 31),
                announced_at=NOW,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
            TickerAlias(
                security_id=second.security_id,
                ticker="abc",
                effective_from=date(2020, 6, 1),
                effective_to=date(2021, 1, 1),
                announced_at=NOW,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
        ]
    )

    with pytest.raises(SecurityMasterValidationError, match="ambiguous ticker"):
        validate_security_master(session)


def test_same_ticker_can_be_reused_in_non_overlapping_periods(session):
    snapshot = add_snapshot(session)
    first = Security(ticker="OLD-ABC", name="First")
    second = Security(ticker="NEW-ABC", name="Second")
    session.add_all([first, second])
    session.flush()
    session.add_all(
        [
            TickerAlias(
                security_id=first.security_id,
                ticker="ABC",
                effective_from=date(2018, 1, 1),
                effective_to=date(2019, 12, 31),
                announced_at=NOW,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
            TickerAlias(
                security_id=second.security_id,
                ticker="ABC",
                effective_from=date(2020, 1, 1),
                announced_at=NOW,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            ),
        ]
    )

    assert validate_security_master(session).ticker_alias_count == 2


def test_ambiguous_permanent_identifier_fails_validation(session):
    snapshot = add_snapshot(session)
    first = Security(ticker="ONE", name="First")
    second = Security(ticker="TWO", name="Second")
    session.add_all([first, second])
    session.flush()
    for security in (first, second):
        session.add(
            SecurityIdentifier(
                security_id=security.security_id,
                identifier_type="sharadar_permaticker",
                identifier_value="123",
                valid_from=date(2014, 1, 1),
                is_permanent=True,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=SOURCE_HASH,
            )
        )

    with pytest.raises(SecurityMasterValidationError, match="ambiguous security identifier"):
        validate_security_master(session)


def test_unresolved_membership_is_rejected_by_foreign_key(session):
    snapshot = add_snapshot(session)
    benchmark = Security(ticker="SPY", name="SPDR S&P 500 ETF Trust")
    session.add(benchmark)
    session.flush()
    universe = add_universe(session, snapshot, benchmark)
    session.add(
        UniverseMembership(
            universe_id=universe.universe_id,
            security_id="missing-security",
            effective_from=date(2020, 1, 1),
            effective_to=date(2020, 12, 31),
            announced_at=NOW,
            source_snapshot_id=snapshot.snapshot_id,
            source_hash=SOURCE_HASH,
        )
    )

    with pytest.raises(IntegrityError):
        validate_security_master(session)


def test_contract_freezes_dates_universe_benchmark_lineage_and_licensing():
    contract = (
        REPO_ROOT / "docs" / "data" / "point-in-time-equity-panel-v1.md"
    ).read_text(encoding="utf-8")

    assert "window_start: 2014-01-01" in contract
    assert "window_end: 2025-12-31" in contract
    assert "historical_sp_500_constituents_by_effective_date" in contract
    assert "benchmark: SPY" in contract
    assert "benchmark_rank_eligible: false" in contract
    assert "Removed, acquired, merged, bankrupt" in contract
    assert "`securities.security_id` is Quantfore's permanent" in contract
    assert "source_snapshot_ids: assigned_during_ingestion" in contract
    assert "source_hashes: assigned_from_exact_raw_bytes_during_ingestion" in contract
    assert "written licence evidence" in contract
    assert "claims_eligible: false" in contract
