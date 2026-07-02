from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from pipelines.audit_point_in_time_equities import main as audit_main
from quantfore_research.db import build_engine, create_schema, make_session_factory
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
from quantfore_research.validation.point_in_time_audit import (
    HARD,
    REVIEW,
    audit_point_in_time_equity_panel,
)


RETRIEVED_AT = datetime(2020, 1, 11, 12, tzinfo=timezone.utc)
HASH = "b" * 64
SESSIONS = (
    date(2020, 1, 2),
    date(2020, 1, 3),
    date(2020, 1, 6),
    date(2020, 1, 7),
    date(2020, 1, 8),
    date(2020, 1, 9),
    date(2020, 1, 10),
)


def seed_valid_panel(database_url: str):
    engine = build_engine(database_url=database_url)
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory.begin() as session:
        snapshot = SourceSnapshot(
            snapshot_id="snapshot-pit-audit",
            vendor="Licensed Test Vendor",
            dataset="pit-audit-fixture",
            retrieved_at=RETRIEVED_AT,
            license_tag="test-only",
            source_hash=HASH,
            storage_uri="raw/test/pit-audit-fixture.json",
            created_at=RETRIEVED_AT,
            updated_at=RETRIEVED_AT,
        )
        old = Security(
            security_id="security-old",
            ticker="OLD",
            name="Removed and Delisted Company",
            active_from=date(2010, 1, 1),
            active_to=date(2020, 1, 8),
        )
        new = Security(
            security_id="security-new",
            ticker="NEW",
            name="Replacement Company",
            active_from=date(2020, 1, 9),
        )
        spy = Security(
            security_id="security-spy",
            ticker="SPY",
            name="SPDR S&P 500 ETF Trust",
            active_from=date(1993, 1, 22),
        )
        session.add_all([snapshot, old, new, spy])
        session.flush()
        universe = UniverseDefinition(
            universe_id="sp500-pit-v1",
            name="Historical S&P 500",
            version="v1",
            description="Short deterministic audit fixture",
            window_start=SESSIONS[0],
            window_end=SESSIONS[-1],
            benchmark_security_id=spy.security_id,
            benchmark_excluded_from_rankings=True,
            source_snapshot_id=snapshot.snapshot_id,
            source_hash=HASH,
            audit_contract_json={
                "expected_row_counts": {
                    "securities": 3,
                    "memberships": 2,
                    "prices": 14,
                    "corporate_actions": 2,
                    "delistings": 1,
                },
                "monthly_membership_counts": {"2020-01": 1},
                "independent_membership_samples": [
                    {
                        "as_of_date": "2020-01-02",
                        "security_ids": ["security-old"],
                        "source_uri": "private://independent/one",
                        "source_sha256": "1" * 64,
                    },
                    {
                        "as_of_date": "2020-01-08",
                        "security_ids": ["security-old"],
                        "source_uri": "private://independent/two",
                        "source_sha256": "2" * 64,
                    },
                    {
                        "as_of_date": "2020-01-10",
                        "security_ids": ["security-new"],
                        "source_uri": "private://independent/three",
                        "source_sha256": "3" * 64,
                    },
                ],
                "role_snapshots": {
                    role: {"snapshot_id": snapshot.snapshot_id, "source_hash": HASH}
                    for role in (
                        "manifest",
                        "securities",
                        "memberships",
                        "prices",
                        "corporate_actions",
                        "delistings",
                    )
                },
                "expected_security_ids": [
                    "security-old",
                    "security-new",
                    "security-spy",
                ],
            },
            created_at=RETRIEVED_AT,
        )
        session.add(universe)
        for security, value, start, end in (
            (old, "100", date(2010, 1, 1), date(2020, 1, 8)),
            (new, "200", date(2020, 1, 9), None),
            (spy, "300", date(1993, 1, 22), None),
        ):
            session.add_all(
                [
                    SecurityIdentifier(
                        identifier_id=f"identifier-{security.ticker}",
                        security_id=security.security_id,
                        identifier_type="TEST_PERMATICKER",
                        identifier_value=value,
                        valid_from=start,
                        valid_to=end,
                        is_permanent=True,
                        source_snapshot_id=snapshot.snapshot_id,
                        source_hash=HASH,
                        created_at=RETRIEVED_AT,
                    ),
                    TickerAlias(
                        ticker_alias_id=f"alias-{security.ticker}",
                        security_id=security.security_id,
                        ticker=security.ticker,
                        effective_from=start,
                        effective_to=end,
                        announced_at=RETRIEVED_AT,
                        source_snapshot_id=snapshot.snapshot_id,
                        source_hash=HASH,
                        created_at=RETRIEVED_AT,
                    ),
                ]
            )
        session.add_all(
            [
                UniverseMembership(
                    membership_id="membership-old",
                    universe_id=universe.universe_id,
                    security_id=old.security_id,
                    effective_from=SESSIONS[0],
                    effective_to=date(2020, 1, 8),
                    announced_at=datetime(2019, 12, 20, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                    created_at=RETRIEVED_AT,
                ),
                UniverseMembership(
                    membership_id="membership-new",
                    universe_id=universe.universe_id,
                    security_id=new.security_id,
                    effective_from=date(2020, 1, 9),
                    effective_to=None,
                    announced_at=datetime(2019, 12, 20, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                    created_at=RETRIEVED_AT,
                ),
            ]
        )

        def add_price(security, day, raw_close, adjusted_close):
            session.add(
                Price(
                    price_id=f"price-{security.ticker}-{day.isoformat()}",
                    security_id=security.security_id,
                    date=day,
                    open=raw_close,
                    high=raw_close + Decimal("1"),
                    low=raw_close - Decimal("1"),
                    close=raw_close,
                    adj_open=adjusted_close,
                    adj_high=adjusted_close + Decimal("1"),
                    adj_low=adjusted_close - Decimal("1"),
                    adj_close=adjusted_close,
                    volume=1000,
                    adj_volume=Decimal("1000"),
                    source_snapshot_id=snapshot.snapshot_id,
                    created_at=RETRIEVED_AT,
                    updated_at=RETRIEVED_AT,
                )
            )

        for day in SESSIONS:
            add_price(spy, day, Decimal("300"), Decimal("300"))
        for day in SESSIONS[:5]:
            raw = Decimal("100") if day < date(2020, 1, 6) else Decimal("50")
            add_price(old, day, raw, Decimal("50"))
        for day in SESSIONS[5:]:
            add_price(new, day, Decimal("10"), Decimal("10"))

        session.add_all(
            [
                CorporateAction(
                    corporate_action_id="action-split-old",
                    security_id=old.security_id,
                    action_type="split",
                    effective_date=date(2020, 1, 6),
                    announced_at=datetime(2019, 12, 1, tzinfo=timezone.utc),
                    ratio_from=Decimal("1"),
                    ratio_to=Decimal("2"),
                    details_json={},
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                    created_at=RETRIEVED_AT,
                ),
                CorporateAction(
                    corporate_action_id="action-dividend-old",
                    security_id=old.security_id,
                    action_type="cash_dividend",
                    effective_date=date(2020, 1, 7),
                    announced_at=datetime(2019, 12, 15, tzinfo=timezone.utc),
                    cash_amount=Decimal("0.25"),
                    currency="USD",
                    details_json={},
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                    created_at=RETRIEVED_AT,
                ),
                DelistingEvent(
                    delisting_event_id="delisting-old",
                    security_id=old.security_id,
                    delisting_date=date(2020, 1, 8),
                    announced_at=datetime(2019, 12, 20, tzinfo=timezone.utc),
                    delisting_return=Decimal("-0.20"),
                    return_available_at=datetime(
                        2020, 1, 9, 22, tzinfo=timezone.utc
                    ),
                    reason="acquired",
                    successor_security_id=new.security_id,
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                    created_at=RETRIEVED_AT,
                ),
            ]
        )
    return factory


@pytest.fixture
def valid_session(tmp_path):
    factory = seed_valid_panel(f"sqlite+pysqlite:///{tmp_path / 'audit.db'}")
    with factory() as session:
        yield session


def test_complete_panel_passes_and_demonstrates_removal_and_delisting(valid_session):
    audit = audit_point_in_time_equity_panel(
        valid_session,
        audit_as_of=RETRIEVED_AT,
        minimum_monthly_members=1,
        maximum_monthly_members=2,
    )

    assert audit.status == "pass"
    assert audit.hard_failure_count == 0
    assert audit.review_finding_count == 0
    assert audit.historical_removal is not None
    assert audit.historical_removal.ticker == "OLD"
    assert audit.historical_removal.last_member_price_date == date(2020, 1, 8)
    assert audit.delisting is not None
    assert audit.delisting.ticker == "OLD"
    assert audit.delisting.delisting_return == Decimal("-0.20")
    assert audit.delisting.membership_closed_by_delisting is True


def test_tiny_sp500_panel_fails_default_monthly_plausibility_gate(valid_session):
    audit = audit_point_in_time_equity_panel(
        valid_session, audit_as_of=RETRIEVED_AT
    )

    assert audit.status == "fail"
    assert "implausible_monthly_membership_count" in {
        row.code for row in audit.findings if row.severity == HARD
    }


def test_independent_membership_sample_mismatch_is_a_hard_failure(valid_session):
    universe = valid_session.get(UniverseDefinition, "sp500-pit-v1")
    contract = dict(universe.audit_contract_json)
    samples = [dict(row) for row in contract["independent_membership_samples"]]
    samples[0]["security_ids"] = ["security-new"]
    contract["independent_membership_samples"] = samples
    universe.audit_contract_json = contract
    valid_session.flush()

    audit = audit_point_in_time_equity_panel(
        valid_session,
        audit_as_of=RETRIEVED_AT,
        minimum_monthly_members=1,
        maximum_monthly_members=2,
    )

    assert "independent_membership_sample_mismatch" in {
        row.code for row in audit.findings if row.severity == HARD
    }


def test_calendar_gap_and_missing_delisting_return_are_explicit_review_findings(
    valid_session,
):
    missing_price = valid_session.get(Price, "price-OLD-2020-01-07")
    valid_session.delete(missing_price)
    event = valid_session.get(DelistingEvent, "delisting-old")
    event.delisting_return = None
    event.return_available_at = None
    valid_session.flush()

    audit = audit_point_in_time_equity_panel(
        valid_session,
        audit_as_of=RETRIEVED_AT,
        minimum_monthly_members=1,
        maximum_monthly_members=2,
    )
    review_codes = {
        row.code for row in audit.findings if row.severity == REVIEW
    }

    assert audit.status == "fail"
    assert "vendor_row_count_mismatch" in {
        row.code for row in audit.findings if row.severity == HARD
    }
    assert "exchange_calendar_gaps" in review_codes
    assert "missing_delisting_return" in review_codes


def test_overlaps_conflicting_identifiers_impossible_ohlc_and_post_delisting_fail(
    valid_session,
):
    snapshot = valid_session.get(SourceSnapshot, "snapshot-pit-audit")
    valid_session.add_all(
        [
            UniverseMembership(
                membership_id="membership-overlap",
                universe_id="sp500-pit-v1",
                security_id="security-old",
                effective_from=date(2020, 1, 7),
                effective_to=date(2020, 1, 8),
                announced_at=RETRIEVED_AT,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=HASH,
            ),
            SecurityIdentifier(
                identifier_id="identifier-conflict",
                security_id="security-new",
                identifier_type="TEST_PERMATICKER",
                identifier_value="100",
                valid_from=date(2020, 1, 1),
                valid_to=None,
                is_permanent=True,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=HASH,
            ),
            Price(
                price_id="price-old-after-delisting",
                security_id="security-old",
                date=date(2020, 1, 9),
                open=Decimal("50"),
                high=Decimal("40"),
                low=Decimal("49"),
                close=Decimal("50"),
                adj_open=Decimal("50"),
                adj_high=Decimal("51"),
                adj_low=Decimal("49"),
                adj_close=Decimal("50"),
                volume=1000,
                adj_volume=Decimal("1000"),
                source_snapshot_id=snapshot.snapshot_id,
            ),
        ]
    )
    valid_session.flush()

    audit = audit_point_in_time_equity_panel(
        valid_session,
        audit_as_of=RETRIEVED_AT,
        minimum_monthly_members=1,
        maximum_monthly_members=2,
    )
    hard_codes = {row.code for row in audit.findings if row.severity == HARD}

    assert audit.status == "fail"
    assert {
        "overlapping_memberships",
        "conflicting_identifier_mapping",
        "impossible_ohlc_or_volume",
        "unexpected_post_delisting_prices",
        "price_outside_listing_boundary",
    }.issubset(hard_codes)


def test_inactive_member_without_delisting_is_a_hard_failure(valid_session):
    event = valid_session.get(DelistingEvent, "delisting-old")
    valid_session.delete(event)
    valid_session.flush()

    audit = audit_point_in_time_equity_panel(
        valid_session,
        audit_as_of=RETRIEVED_AT,
        minimum_monthly_members=1,
        maximum_monthly_members=2,
    )
    hard_codes = {row.code for row in audit.findings if row.severity == HARD}

    assert "missing_delisting_event" in hard_codes
    assert "missing_delisting_evidence" in hard_codes


def test_pipeline_writes_deterministic_json_and_markdown_reports(tmp_path):
    db_path = tmp_path / "audit.db"
    seed_valid_panel(f"sqlite+pysqlite:///{db_path}")
    json_output = tmp_path / "pit-equity-panel-v1.json"
    markdown_output = tmp_path / "pit-equity-panel-v1.md"
    args = [
        "--database-url",
        f"sqlite+pysqlite:///{db_path}",
        "--json-output",
        str(json_output),
        "--markdown-output",
        str(markdown_output),
        "--generated-at",
        "2020-01-11T12:00:00Z",
        "--minimum-monthly-members",
        "1",
        "--maximum-monthly-members",
        "2",
    ]

    first_exit = audit_main(args)
    first_json = json_output.read_bytes()
    first_markdown = markdown_output.read_bytes()
    second_exit = audit_main(args)

    assert first_exit == second_exit == 0
    assert json_output.read_bytes() == first_json
    assert markdown_output.read_bytes() == first_markdown
    assert b'"decision": "pass"' in first_json
    assert b'"historical_removal_evidence"' in first_json
    assert b'"delisting_evidence"' in first_json
    assert b"## Historical removal evidence" in first_markdown
    assert b"## Delisting evidence" in first_markdown
