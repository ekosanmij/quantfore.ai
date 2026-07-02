from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quantfore_research.db import build_engine, create_schema, make_session_factory
from quantfore_research.models import (
    DelistingEvent,
    Feature,
    Price,
    Security,
    SourceSnapshot,
    TickerAlias,
    UniverseDefinition,
    UniverseMembership,
)
from quantfore_research.validation.leakage import (
    PointInTimeInputEvidence,
    PointInTimeLeakageError,
    construct_point_in_time_baseline_features,
    expected_point_in_time_cohort,
    resolve_point_in_time_security,
    validate_candidate_price_inputs,
    validate_point_in_time_cohort,
    validate_point_in_time_evidence,
    validate_stored_feature_inputs,
)


PREDICTION_TIMESTAMP = datetime(2020, 6, 30, 23, 59, tzinfo=timezone.utc)
HASH = "c" * 64


def violation_codes(exc: PointInTimeLeakageError) -> set[str]:
    return {violation.code for violation in exc.violations}


@pytest.fixture
def session():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    factory = make_session_factory(engine)
    with factory() as database_session:
        snapshot = SourceSnapshot(
            snapshot_id="snapshot-prices",
            vendor="Licensed Test Vendor",
            dataset="pit-prices",
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            license_tag="test",
            source_hash=HASH,
            storage_uri="raw/test/pit-prices.json",
        )
        meta = Security(
            security_id="security-meta",
            ticker="META",
            name="Meta Platforms",
            active_from=date(2012, 5, 18),
        )
        old = Security(
            security_id="security-old",
            ticker="OLD",
            name="Later Delisted Company",
            active_from=date(2000, 1, 1),
            active_to=date(2024, 3, 1),
        )
        future = Security(
            security_id="security-future",
            ticker="FUT",
            name="Future Constituent",
            active_from=date(2010, 1, 1),
        )
        spy = Security(
            security_id="security-spy",
            ticker="SPY",
            name="SPDR S&P 500 ETF Trust",
            active_from=date(1993, 1, 22),
        )
        database_session.add_all([snapshot, meta, old, future, spy])
        database_session.flush()
        database_session.add(
            UniverseDefinition(
                universe_id="sp500-pit-v1",
                name="Historical S&P 500",
                version="v1",
                description="Leakage test universe",
                window_start=date(2014, 1, 1),
                window_end=date(2025, 12, 31),
                benchmark_security_id=spy.security_id,
                benchmark_excluded_from_rankings=True,
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=HASH,
            )
        )
        database_session.add_all(
            [
                TickerAlias(
                    ticker_alias_id="alias-fb",
                    security_id=meta.security_id,
                    ticker="FB",
                    effective_from=date(2012, 5, 18),
                    effective_to=date(2022, 6, 8),
                    announced_at=datetime(2012, 5, 1, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
                TickerAlias(
                    ticker_alias_id="alias-meta",
                    security_id=meta.security_id,
                    ticker="META",
                    effective_from=date(2022, 6, 9),
                    effective_to=None,
                    announced_at=datetime(2022, 5, 31, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
                TickerAlias(
                    ticker_alias_id="alias-old",
                    security_id=old.security_id,
                    ticker="OLD",
                    effective_from=date(2000, 1, 1),
                    effective_to=date(2024, 3, 1),
                    announced_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
                TickerAlias(
                    ticker_alias_id="alias-future",
                    security_id=future.security_id,
                    ticker="FUT",
                    effective_from=date(2010, 1, 1),
                    effective_to=None,
                    announced_at=datetime(2010, 1, 1, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
                UniverseMembership(
                    membership_id="membership-meta",
                    universe_id="sp500-pit-v1",
                    security_id=meta.security_id,
                    effective_from=date(2013, 12, 23),
                    effective_to=None,
                    announced_at=datetime(2013, 12, 18, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
                UniverseMembership(
                    membership_id="membership-old",
                    universe_id="sp500-pit-v1",
                    security_id=old.security_id,
                    effective_from=date(2010, 1, 1),
                    effective_to=date(2020, 12, 31),
                    announced_at=datetime(2009, 12, 20, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
                UniverseMembership(
                    membership_id="membership-future",
                    universe_id="sp500-pit-v1",
                    security_id=future.security_id,
                    effective_from=date(2021, 1, 1),
                    effective_to=None,
                    announced_at=datetime(2020, 12, 20, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
                DelistingEvent(
                    delisting_event_id="delisting-old",
                    security_id=old.security_id,
                    delisting_date=date(2024, 3, 1),
                    announced_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
                    delisting_return=Decimal("-0.40"),
                    return_available_at=datetime(
                        2024, 3, 4, tzinfo=timezone.utc
                    ),
                    reason="bankruptcy",
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
            ]
        )
        start = date(2019, 7, 1)
        for index in range(366):
            observation_date = start + timedelta(days=index)
            if observation_date.weekday() >= 5:
                continue
            price = Decimal("100") + Decimal(index) / Decimal("10")
            database_session.add(
                Price(
                    price_id=f"price-meta-{observation_date.isoformat()}",
                    security_id=meta.security_id,
                    date=observation_date,
                    open=price,
                    high=price + Decimal("1"),
                    low=price - Decimal("1"),
                    close=price,
                    adj_open=price,
                    adj_high=price + Decimal("1"),
                    adj_low=price - Decimal("1"),
                    adj_close=price,
                    volume=1000,
                    adj_volume=Decimal("1000"),
                    source_snapshot_id=snapshot.snapshot_id,
                )
            )
        database_session.commit()
        yield database_session


def test_positive_constructor_proves_membership_alias_price_and_availability(session):
    result = construct_point_in_time_baseline_features(
        session,
        universe_id="sp500-pit-v1",
        ticker="FB",
        prediction_timestamp=PREDICTION_TIMESTAMP,
    )

    assert result.inputs.context.security.security_id == "security-meta"
    assert result.inputs.context.ticker_alias.ticker == "FB"
    assert len(result.inputs.prices) >= 253
    assert set(result.values) == {
        "momentum_6_1",
        "momentum_12_1",
        "return_21d",
        "volatility_126d",
    }
    assert all(
        item.model_available_at <= PREDICTION_TIMESTAMP
        for item in result.inputs.evidence
    )
    assert all(
        item.price_date is None
        or item.price_date <= PREDICTION_TIMESTAMP.date()
        for item in result.inputs.evidence
    )


def test_future_constituent_fails(session):
    with pytest.raises(PointInTimeLeakageError) as raised:
        resolve_point_in_time_security(
            session,
            universe_id="sp500-pit-v1",
            ticker="FUT",
            prediction_timestamp=PREDICTION_TIMESTAMP,
        )

    assert "MEMBERSHIP_NOT_EFFECTIVE" in violation_codes(raised.value)


def test_future_price_and_future_model_availability_fail(session):
    future = Price(
        price_id="future-price",
        security_id="security-meta",
        date=date(2020, 7, 1),
        adj_close=Decimal("999"),
        source_snapshot_id="snapshot-prices",
    )
    with pytest.raises(PointInTimeLeakageError) as raised:
        validate_candidate_price_inputs(
            [future], prediction_timestamp=PREDICTION_TIMESTAMP
        )
    assert "PRICE_FROM_FUTURE" in violation_codes(raised.value)

    historical = session.get(Price, "price-meta-2020-06-30")
    with pytest.raises(PointInTimeLeakageError) as availability_raised:
        validate_candidate_price_inputs(
            [historical],
            prediction_timestamp=PREDICTION_TIMESTAMP,
            model_available_at={
                historical.price_id: datetime(
                    2020, 7, 2, tzinfo=timezone.utc
                )
            },
        )
    assert "INPUT_AVAILABLE_IN_FUTURE" in violation_codes(
        availability_raised.value
    )


def test_ticker_known_only_after_rename_fails(session):
    with pytest.raises(PointInTimeLeakageError) as raised:
        resolve_point_in_time_security(
            session,
            universe_id="sp500-pit-v1",
            ticker="META",
            prediction_timestamp=PREDICTION_TIMESTAMP,
        )

    assert "TICKER_NOT_EFFECTIVE" in violation_codes(raised.value)


def test_revised_membership_announced_after_prediction_fails(session):
    snapshot = session.get(SourceSnapshot, "snapshot-prices")
    revised = Security(
        security_id="security-revised",
        ticker="REV",
        name="Revised Constituent",
        active_from=date(2010, 1, 1),
    )
    session.add(revised)
    session.flush()
    session.add_all(
        [
            TickerAlias(
                ticker_alias_id="alias-revised",
                security_id=revised.security_id,
                ticker="REV",
                effective_from=date(2010, 1, 1),
                effective_to=None,
                announced_at=datetime(2010, 1, 1, tzinfo=timezone.utc),
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=HASH,
            ),
            UniverseMembership(
                membership_id="membership-revised",
                universe_id="sp500-pit-v1",
                security_id=revised.security_id,
                effective_from=date(2019, 1, 1),
                effective_to=None,
                announced_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=HASH,
            ),
        ]
    )
    session.flush()

    with pytest.raises(PointInTimeLeakageError) as raised:
        resolve_point_in_time_security(
            session,
            universe_id="sp500-pit-v1",
            ticker="REV",
            prediction_timestamp=PREDICTION_TIMESTAMP,
        )

    assert "REVISED_MEMBERSHIP_UNAVAILABLE" in violation_codes(raised.value)


def test_later_delisted_company_must_remain_in_historical_cohort(session):
    expected = expected_point_in_time_cohort(
        session,
        universe_id="sp500-pit-v1",
        prediction_timestamp=PREDICTION_TIMESTAMP,
    )
    expected_ids = {context.security.security_id for context in expected}

    assert expected_ids == {"security-meta", "security-old"}
    with pytest.raises(PointInTimeLeakageError) as raised:
        validate_point_in_time_cohort(
            session,
            universe_id="sp500-pit-v1",
            prediction_timestamp=PREDICTION_TIMESTAMP,
            candidate_security_ids=["security-meta"],
        )

    assert "COHORT_MISSING_SECURITY" in violation_codes(raised.value)
    assert raised.value.violations[0].security_id == "security-old"


def test_all_four_contract_inequalities_are_hard_failures():
    evidence = (
        PointInTimeInputEvidence(
            input_type="membership",
            record_id="membership",
            security_id="security",
            model_available_at=datetime(2020, 7, 1, tzinfo=timezone.utc),
            membership_effective_from=date(2020, 7, 1),
            membership_effective_to=date(2020, 6, 1),
        ),
        PointInTimeInputEvidence(
            input_type="price",
            record_id="price",
            security_id="security",
            model_available_at=datetime(2020, 6, 30, tzinfo=timezone.utc),
            price_date=date(2020, 7, 1),
        ),
    )

    with pytest.raises(PointInTimeLeakageError) as raised:
        validate_point_in_time_evidence(
            evidence, prediction_timestamp=PREDICTION_TIMESTAMP
        )

    assert violation_codes(raised.value) == {
        "INPUT_AVAILABLE_IN_FUTURE",
        "MEMBERSHIP_STARTS_IN_FUTURE",
        "MEMBERSHIP_ENDED_BEFORE_PREDICTION",
        "PRICE_FROM_FUTURE",
    }


def test_prediction_scoring_rejects_feature_available_after_prediction():
    feature = Feature(
        feature_id="future-feature",
        feature_set_id="feature-set",
        security_id="security-meta",
        asof_date=PREDICTION_TIMESTAMP.date(),
        available_at=datetime(2020, 7, 1, tzinfo=timezone.utc),
        feature_name="momentum_6_1",
        value=Decimal("0.1"),
        version="v0.1",
        source_snapshot_id="snapshot-prices",
        source_hash=HASH,
    )

    with pytest.raises(PointInTimeLeakageError) as raised:
        validate_stored_feature_inputs(
            [feature], prediction_timestamp=PREDICTION_TIMESTAMP
        )

    assert violation_codes(raised.value) == {"FEATURE_AVAILABLE_IN_FUTURE"}
