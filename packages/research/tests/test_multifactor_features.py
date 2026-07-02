from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import inspect, select

from quantfore_research.db import build_engine, create_schema, make_session_factory
from quantfore_research.features.multifactor import (
    APPLICABLE,
    FINANCIALS_MASK,
    FundamentalBook,
    MULTIFACTOR_FEATURE_VERSION,
    NOT_APPLICABLE,
    construct_multifactor_features,
    select_fundamentals_as_of,
    store_multifactor_features,
)
from quantfore_research.models import (
    Feature,
    Fundamental,
    Price,
    Security,
    SecurityClassification,
    SourceSnapshot,
)
from quantfore_research.validation.fundamental_audit_gate import (
    FundamentalAuditBinding,
)
from quantfore_research.validation.leakage import validate_stored_feature_inputs


PREDICTION = datetime(2021, 2, 1, 23, 59, tzinfo=timezone.utc)
FUNDAMENTAL_HASH = "1" * 64
PRICE_HASH = "2" * 64
BENCHMARK_HASH = "3" * 64
CLASSIFICATION_HASH = "4" * 64


CURRENT_FLOWS = {
    "revenue": "1000",
    "gross_profit": "400",
    "ebit": "200",
    "net_income_common": "100",
    "diluted_eps": "10",
    "cash_from_operations": "150",
    "capital_expenditure": "50",
    "income_tax_expense": "40",
    "pretax_income": "200",
}
PRIOR_FLOWS = {
    "revenue": "800",
    "gross_profit": "320",
    "ebit": "160",
    "net_income_common": "80",
    "diluted_eps": "8",
    "cash_from_operations": "120",
    "capital_expenditure": "40",
    "income_tax_expense": "32",
    "pretax_income": "160",
}


def make_session(*, shares="10"):
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session = make_session_factory(engine)()
    session.add_all(
        [
            SourceSnapshot(
                snapshot_id="fundamentals-snapshot",
                vendor="Licensed Test Vendor",
                dataset="pit-fundamentals",
                license_tag="test",
                source_hash=FUNDAMENTAL_HASH,
                storage_uri="raw/test/fundamentals.json",
            ),
            SourceSnapshot(
                snapshot_id="security-prices",
                vendor="Test Price Vendor",
                dataset="security-prices",
                license_tag="test",
                source_hash=PRICE_HASH,
                storage_uri="raw/test/security-prices.json",
            ),
            SourceSnapshot(
                snapshot_id="benchmark-prices",
                vendor="Test Price Vendor",
                dataset="benchmark-prices",
                license_tag="test",
                source_hash=BENCHMARK_HASH,
                storage_uri="raw/test/benchmark-prices.json",
            ),
            *[
                SourceSnapshot(
                    snapshot_id=f"classification-snapshot-{suffix}",
                    vendor="Test Classification Vendor",
                    dataset=f"point-in-time-classifications-{suffix}",
                    license_tag="test",
                    source_hash=CLASSIFICATION_HASH,
                    storage_uri=f"raw/test/classifications-{suffix}.json",
                )
                for suffix in ("industrials", "financials", "reit")
            ],
            Security(
                security_id="security-1",
                ticker="TST",
                name="Test Issuer",
                sector="Industrials",
            ),
            Security(
                security_id="benchmark-1",
                ticker="SPY",
                name="Benchmark",
            ),
        ]
    )
    session.flush()
    session.add_all(
        [
            SecurityClassification(
                classification_id="classification-industrials",
                security_id="security-1",
                sector="Industrials",
                industry=None,
                classification_system="GICS",
                effective_from=date(2020, 1, 1),
                model_available_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                source_snapshot_id="classification-snapshot-industrials",
                source_hash=CLASSIFICATION_HASH,
            ),
            SecurityClassification(
                classification_id="classification-financials",
                security_id="security-1",
                sector="Financials",
                industry=None,
                classification_system="GICS",
                effective_from=date(2020, 1, 1),
                model_available_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                source_snapshot_id="classification-snapshot-financials",
                source_hash=CLASSIFICATION_HASH,
            ),
            SecurityClassification(
                classification_id="classification-reit",
                security_id="security-1",
                sector="Real Estate",
                industry="601010",
                classification_system="GICS",
                effective_from=date(2020, 1, 1),
                model_available_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
                source_snapshot_id="classification-snapshot-reit",
                source_hash=CLASSIFICATION_HASH,
            ),
        ]
    )

    for period_end, values, label in (
        (date(2019, 12, 31), PRIOR_FLOWS, "prior"),
        (date(2020, 12, 31), CURRENT_FLOWS, "current"),
    ):
        for concept, value in values.items():
            unit = "USD/share" if concept == "diluted_eps" else "USD"
            session.add(
                Fundamental(
                    fundamental_id=f"{label}-{concept}",
                    security_id="security-1",
                    fiscal_period_end=period_end,
                    fiscal_year=period_end.year,
                    fiscal_quarter=None,
                    period_type="TTM",
                    form_type="10-K",
                    filing_accession=f"{label}-accession",
                    filed_at=datetime(
                        period_end.year + 1, 1, 20, tzinfo=timezone.utc
                    ),
                    accepted_at=datetime(
                        period_end.year + 1, 1, 20, tzinfo=timezone.utc
                    ),
                    public_release_at=datetime(
                        period_end.year + 1, 1, 20, tzinfo=timezone.utc
                    ),
                    vendor_available_at=datetime(
                        period_end.year + 1, 1, 21, tzinfo=timezone.utc
                    ),
                    model_available_at=datetime(
                        period_end.year + 1, 1, 21, tzinfo=timezone.utc
                    ),
                    revision_version=1,
                    concept=f"Vendor{concept}",
                    standardized_concept=concept,
                    value=Decimal(value),
                    unit=unit,
                    source_snapshot_id="fundamentals-snapshot",
                    source_hash=FUNDAMENTAL_HASH,
                )
            )

    for period_end, values, label in (
        (
            date(2019, 12, 31),
            {
                "total_assets": "800",
                "total_debt": "240",
                "cash_and_equivalents": "80",
                "shareholders_equity": "480",
            },
            "prior",
        ),
        (
            date(2020, 12, 31),
            {
                "total_assets": "1000",
                "total_debt": "300",
                "cash_and_equivalents": "100",
                "shareholders_equity": "600",
                "common_shares": shares,
            },
            "current",
        ),
    ):
        for concept, value in values.items():
            session.add(
                Fundamental(
                    fundamental_id=f"{label}-{concept}",
                    security_id="security-1",
                    fiscal_period_end=period_end,
                    fiscal_year=period_end.year,
                    fiscal_quarter=None,
                    period_type="ANNUAL",
                    form_type="10-K",
                    filing_accession=f"{label}-balance-accession",
                    filed_at=datetime(
                        period_end.year + 1, 1, 20, tzinfo=timezone.utc
                    ),
                    accepted_at=datetime(
                        period_end.year + 1, 1, 20, tzinfo=timezone.utc
                    ),
                    public_release_at=datetime(
                        period_end.year + 1, 1, 20, tzinfo=timezone.utc
                    ),
                    vendor_available_at=datetime(
                        period_end.year + 1, 1, 21, tzinfo=timezone.utc
                    ),
                    model_available_at=datetime(
                        period_end.year + 1, 1, 21, tzinfo=timezone.utc
                    ),
                    revision_version=1,
                    concept=f"Vendor{concept}",
                    standardized_concept=concept,
                    value=Decimal(value),
                    unit="shares" if concept == "common_shares" else "USD",
                    source_snapshot_id="fundamentals-snapshot",
                    source_hash=FUNDAMENTAL_HASH,
                )
            )

    start = date(2020, 5, 1)
    security_price = Decimal("40")
    benchmark_price = Decimal("100")
    for index in range(260):
        observation_date = start + timedelta(days=index)
        security_price *= Decimal("1") + Decimal("0.0008") + Decimal(index % 5) / Decimal("10000")
        benchmark_price *= Decimal("1") + Decimal("0.0004") + Decimal(index % 7) / Decimal("20000")
        session.add_all(
            [
                Price(
                    price_id=f"security-price-{index}",
                    security_id="security-1",
                    date=observation_date,
                    close=security_price * Decimal("2"),
                    adj_close=security_price,
                    source_snapshot_id="security-prices",
                ),
                Price(
                    price_id=f"benchmark-price-{index}",
                    security_id="benchmark-1",
                    date=observation_date,
                    close=benchmark_price,
                    adj_close=benchmark_price,
                    source_snapshot_id="benchmark-prices",
                ),
            ]
        )
    session.commit()
    return engine, session


def build(session, *, sector="Industrials", industry=None):
    classification_id = {
        ("Industrials", None): "classification-industrials",
        ("Financials", None): "classification-financials",
        ("Real Estate", "601010"): "classification-reit",
    }[(sector, industry)]
    return construct_multifactor_features(
        session,
        security_id="security-1",
        benchmark_security_id="benchmark-1",
        prediction_timestamp=PREDICTION,
        classification_id=classification_id,
        fundamental_source_snapshot_ids=["fundamentals-snapshot"],
        security_price_snapshot_id="security-prices",
        benchmark_price_snapshot_id="benchmark-prices",
    )


def test_all_five_feature_families_calculate_with_frozen_formulas():
    _, session = make_session()
    batch = build(session)
    features = batch.by_name()

    assert len(features) == 19
    assert {row.definition.family for row in batch.features} == {
        "value",
        "quality",
        "growth",
        "momentum",
        "risk",
    }
    assert all(row.status == APPLICABLE for row in batch.features)
    assert features["revenue_growth"].value == Decimal("0.25")
    assert features["eps_growth"].value == Decimal("0.25")
    assert features["fcf_growth"].value == Decimal("0.25")
    assert features["fcf_conversion"].value == Decimal("1")
    assert features["inverse_leverage"].value == Decimal("-0.3333333333333333333333333333")
    assert features["roic"].value == Decimal("0.2222222222222222222222222222")
    assert features["maximum_drawdown_252d"].value <= 0
    assert features["volatility_126d"].definition.direction == "LOWER"
    assert features["momentum_12_1"].inputs


def test_market_cap_uses_raw_close_not_total_return_adjusted_close():
    _, session = make_session()
    features = build(session).by_name()
    fcf_yield = features["fcf_yield"]

    price_input = next(item for item in fcf_yield.inputs if item.input_type == "raw_close")
    latest = session.scalar(
        select(Price)
        .where(Price.security_id == "security-1")
        .order_by(Price.date.desc())
        .limit(1)
    )
    assert price_input.value == latest.close
    assert latest.close != latest.adj_close
    assert price_input.input_name == "raw_close"


def test_ttm_can_be_constructed_only_from_consecutive_quarters():
    period_ends = (
        date(2019, 3, 31),
        date(2019, 6, 30),
        date(2019, 9, 30),
        date(2019, 12, 31),
        date(2020, 3, 31),
        date(2020, 6, 30),
        date(2020, 9, 30),
        date(2020, 12, 31),
    )
    facts = []
    for index, period_end in enumerate(period_ends):
        quarter = index % 4 + 1
        facts.append(
            Fundamental(
                fundamental_id=f"quarter-{index}",
                security_id="security-1",
                fiscal_period_end=period_end,
                fiscal_year=period_end.year,
                fiscal_quarter=quarter,
                period_type="QUARTERLY",
                form_type="10-Q",
                filing_accession=f"quarter-accession-{index}",
                filed_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
                accepted_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
                public_release_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
                vendor_available_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
                model_available_at=datetime(2021, 1, 1, tzinfo=timezone.utc),
                revision_version=1,
                concept="VendorRevenue",
                standardized_concept="revenue",
                value=Decimal(index + 1),
                unit="USD",
                source_snapshot_id="fundamentals-snapshot",
                source_hash=FUNDAMENTAL_HASH,
            )
        )

    current, prior, reason = FundamentalBook(facts).ttm_pair("revenue")

    assert current.value == Decimal("26")
    assert prior.value == Decimal("10")
    assert reason is None


def test_later_restatement_is_not_selected_or_exposed_in_lineage():
    _, session = make_session()
    session.add(
        Fundamental(
            fundamental_id="future-revenue-restatement",
            security_id="security-1",
            fiscal_period_end=date(2020, 12, 31),
            fiscal_year=2020,
            period_type="TTM",
            form_type="10-K/A",
            filing_accession="future-amendment",
            filed_at=datetime(2021, 3, 1, tzinfo=timezone.utc),
            accepted_at=datetime(2021, 3, 1, tzinfo=timezone.utc),
            public_release_at=datetime(2021, 3, 1, tzinfo=timezone.utc),
            vendor_available_at=datetime(2021, 3, 1, tzinfo=timezone.utc),
            model_available_at=datetime(2021, 3, 1, tzinfo=timezone.utc),
            revision_version=2,
            concept="Vendorrevenue",
            standardized_concept="revenue",
            value=Decimal("9999"),
            unit="USD",
            source_snapshot_id="fundamentals-snapshot",
            source_hash=FUNDAMENTAL_HASH,
        )
    )
    session.commit()

    selected = select_fundamentals_as_of(
        session,
        security_id="security-1",
        prediction_timestamp=PREDICTION,
        source_snapshot_ids=["fundamentals-snapshot"],
    )
    batch = build(session)

    assert "future-revenue-restatement" not in {
        row.fundamental_id for row in selected
    }
    assert batch.by_name()["revenue_growth"].value == Decimal("0.25")
    assert "future-revenue-restatement" not in {
        item.record_id
        for feature in batch.features
        for item in feature.inputs
    }


def test_invalid_denominators_become_missing_instead_of_zero():
    _, session = make_session(shares="0")
    features = build(session).by_name()

    assert features["fcf_yield"].value is None
    assert features["fcf_yield"].missing_reason == "INVALID_DENOMINATOR"
    assert features["earnings_yield"].value is None
    assert features["earnings_yield"].missing_reason == "INVALID_DENOMINATOR"


def test_financials_and_reits_receive_explicit_applicability_masks():
    _, session = make_session()
    financials = build(session, sector="Financials").by_name()
    reit = build(session, sector="Real Estate", industry="601010").by_name()

    assert {
        name for name, row in financials.items() if row.status == NOT_APPLICABLE
    } == FINANCIALS_MASK
    assert financials["earnings_yield"].status == APPLICABLE
    assert reit["fcf_yield"].status == NOT_APPLICABLE
    assert reit["inverse_leverage"].status == APPLICABLE


def test_feature_store_persists_raw_values_formulas_inputs_and_missingness():
    engine, session = make_session()
    batch = build(session, sector="Financials")
    store_multifactor_features(
        session,
        batch=batch,
        feature_set_id="multifactor-test-2021-02-01",
        fundamental_audit=FundamentalAuditBinding(
            audit_id="pit-fundamentals-v1",
            audit_sha256="a" * 64,
            fact_hash="b" * 64,
            availability_revision_hash="c" * 64,
            source_snapshot_hashes={"fundamentals-snapshot": FUNDAMENTAL_HASH},
        ),
        code_commit="test-commit",
    )
    session.commit()
    reused = store_multifactor_features(
        session,
        batch=batch,
        feature_set_id="multifactor-test-2021-02-01",
        fundamental_audit=FundamentalAuditBinding(
            audit_id="pit-fundamentals-v1",
            audit_sha256="a" * 64,
            fact_hash="b" * 64,
            availability_revision_hash="c" * 64,
            source_snapshot_hashes={"fundamentals-snapshot": FUNDAMENTAL_HASH},
        ),
        code_commit="test-commit",
    )

    rows = session.scalars(
        select(Feature).where(
            Feature.feature_set_id == "multifactor-test-2021-02-01"
        )
    ).all()
    by_name = {row.feature_name: row for row in rows}

    assert len(rows) == 19
    assert reused.feature_set_id == "multifactor-test-2021-02-01"
    assert by_name["revenue_growth"].raw_value == Decimal("0.250000000000")
    assert by_name["revenue_growth"].formula_version == MULTIFACTOR_FEATURE_VERSION
    assert by_name["revenue_growth"].inputs_json["inputs"]
    assert by_name["fcf_yield"].value is None
    assert by_name["fcf_yield"].applicability_status == NOT_APPLICABLE
    assert by_name["fcf_yield"].missing_reason == "NOT_APPLICABLE"
    assert reused.config_json["classification"]["classification_id"] == (
        "classification-financials"
    )
    assert reused.config_json["fundamental_audit"]["audit_sha256"] == "a" * 64
    validate_stored_feature_inputs(rows, prediction_timestamp=PREDICTION)

    columns = {column["name"] for column in inspect(engine).get_columns("features")}
    assert {
        "raw_value",
        "family",
        "formula_version",
        "formula",
        "direction",
        "applicability_status",
        "missing_reason",
        "inputs_json",
    }.issubset(columns)
