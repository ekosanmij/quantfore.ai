from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect, select

from quantfore_research.db import build_engine, create_schema, make_session_factory, session_scope
from quantfore_research.models import (
    ExperimentRegistry,
    Feature,
    FeatureSet,
    Filing,
    Fundamental,
    MacroSeries,
    ModelOutcome,
    ModelPrediction,
    Price,
    ScoreDriver,
    Security,
)
from quantfore_research.snapshots import record_source_snapshot, sha256_text


def test_minimum_research_tables_exist():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)

    table_names = set(inspect(engine).get_table_names())

    assert {
        "source_snapshots",
        "securities",
        "prices",
        "filings",
        "fundamentals",
        "macro_series",
        "feature_sets",
        "features",
        "model_predictions",
        "score_drivers",
        "model_outcomes",
        "experiment_registry",
    }.issubset(table_names)


def test_research_memory_records_facts_features_predictions_outcomes_and_experiments():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session_factory = make_session_factory(engine)

    with session_scope(session_factory) as session:
        price_snapshot = record_source_snapshot(
            session,
            vendor="market_data_vendor",
            dataset="daily_prices_MSFT",
            license_tag="prototype",
            source_hash=sha256_text("daily_prices_MSFT_2026-06-24"),
            storage_uri="raw/prices/MSFT/2026-06-24.json",
        )
        filing_snapshot = record_source_snapshot(
            session,
            vendor="SEC EDGAR",
            dataset="filing_MSFT_10-Q",
            license_tag="public_source",
            source_hash=sha256_text("filing_MSFT_10-Q_2026-04-25"),
            storage_uri="raw/sec/filings/msft/2026-04-25-10q.json",
        )
        macro_snapshot = record_source_snapshot(
            session,
            vendor="FRED",
            dataset="FEDFUNDS",
            license_tag="public_source",
            source_hash=sha256_text("FEDFUNDS_2026-05-01"),
            storage_uri="raw/fred/FEDFUNDS/2026-05-01.json",
        )

        security = Security(
            ticker="MSFT",
            name="Microsoft",
            sector="Technology",
            exchange="NASDAQ",
            cik="0000789019",
        )
        session.add(security)
        session.flush()

        price = Price(
            security_id=security.security_id,
            date=date(2026, 6, 24),
            open=Decimal("490.00"),
            high=Decimal("496.00"),
            low=Decimal("488.50"),
            close=Decimal("495.00"),
            adj_close=Decimal("495.00"),
            volume=21000000,
            source_snapshot_id=price_snapshot.snapshot_id,
        )
        filing = Filing(
            security_id=security.security_id,
            form_type="10-Q",
            filed_at=datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc),
            period_end=date(2026, 3, 31),
            accession_no="0000789019-26-000001",
            storage_uri="raw/sec/filings/msft/0000789019-26-000001.txt",
            source_url=(
                "https://www.sec.gov/Archives/edgar/data/"
                "789019/000078901926000001/0000789019-26-000001.txt"
            ),
            source_snapshot_id=filing_snapshot.snapshot_id,
        )
        macro_observation = MacroSeries(
            series_id="FEDFUNDS",
            observation_date=date(2026, 5, 1),
            value=Decimal("4.50"),
            source_snapshot_id=macro_snapshot.snapshot_id,
        )
        fundamental = Fundamental(
            security_id=security.security_id,
            fiscal_period="2026-Q3",
            metric="Revenues",
            value=Decimal("70000000000"),
            unit="USD",
            period_end=date(2026, 3, 31),
            filed_at=datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc),
            available_at=datetime(2026, 4, 25, 13, 30, tzinfo=timezone.utc),
            form_type="10-Q",
            accession_no="0000789019-26-000001",
            source_snapshot_id=filing_snapshot.snapshot_id,
        )
        feature_set = FeatureSet(
            feature_set_id="baseline_features_v0.1_2026-06-24",
            name="baseline_features",
            version="v0.1",
            asof_date=date(2026, 6, 24),
            config_json={"lookbacks": [21, 126, 252]},
            source_snapshot_id=price_snapshot.snapshot_id,
            code_commit="abc123",
        )
        feature = Feature(
            feature_set_id="baseline_features_v0.1_2026-06-24",
            security_id=security.security_id,
            asof_date=date(2026, 6, 24),
            available_at=datetime(2026, 6, 24, 8, 0, tzinfo=timezone.utc),
            feature_name="momentum_6_1",
            value=Decimal("0.18"),
            version="v0.1",
            source_snapshot_id=price_snapshot.snapshot_id,
            source_hash=price_snapshot.source_hash,
        )
        prediction = ModelPrediction(
            security_id=security.security_id,
            feature_set_id=feature_set.feature_set_id,
            asof_date=date(2026, 6, 24),
            model_version="baseline_v0.1",
            score=Decimal("82"),
            confidence=Decimal("0.71"),
            action_label="watch_positive",
            immutable_hash=sha256_text(
                "baseline_v0.1|MSFT|2026-06-24|126d|82|0.71|watch_positive"
            ),
        )
        score_driver = ScoreDriver(
            prediction=prediction,
            driver_name="momentum_6_1",
            contribution=Decimal("12.4"),
            evidence_uri="feature:momentum_6_1",
        )
        experiment = ExperimentRegistry(
            experiment_id="exp_001",
            hypothesis_id="H1_revision_momentum",
            data_snapshot_hash="abc123",
            config_json={"feature_version": "v0.1", "universe": "sp500"},
            result_uri="reports/experiments/exp_001.html",
        )
        session.add_all(
            [
                price,
                filing,
                macro_observation,
                fundamental,
                feature_set,
                feature,
                prediction,
                score_driver,
                experiment,
            ]
        )
        session.flush()

        outcome = ModelOutcome(
            prediction_id=prediction.prediction_id,
            realised_return=Decimal("0.07"),
            benchmark_return=Decimal("0.03"),
            excess_return=Decimal("0.04"),
            evaluated_at=datetime(2026, 9, 24, tzinfo=timezone.utc),
        )
        session.add(outcome)

        security_id = security.security_id
        prediction_id = prediction.prediction_id

    with session_factory() as session:
        saved_security = session.scalar(
            select(Security).where(Security.security_id == security_id)
        )
        saved_price = session.scalar(select(Price).where(Price.security_id == security_id))
        saved_filing = session.scalar(
            select(Filing).where(Filing.security_id == security_id)
        )
        saved_macro = session.scalar(
            select(MacroSeries).where(MacroSeries.series_id == "FEDFUNDS")
        )
        saved_fundamental = session.scalar(
            select(Fundamental).where(Fundamental.metric == "Revenues")
        )
        saved_feature = session.scalar(
            select(Feature).where(Feature.feature_name == "momentum_6_1")
        )
        saved_feature_set = session.scalar(
            select(FeatureSet).where(
                FeatureSet.feature_set_id == "baseline_features_v0.1_2026-06-24"
            )
        )
        saved_prediction = session.scalar(
            select(ModelPrediction).where(
                ModelPrediction.prediction_id == prediction_id
            )
        )
        saved_score_driver = session.scalar(
            select(ScoreDriver).where(ScoreDriver.prediction_id == prediction_id)
        )
        saved_outcome = session.scalar(
            select(ModelOutcome).where(ModelOutcome.prediction_id == prediction_id)
        )
        saved_experiment = session.scalar(
            select(ExperimentRegistry).where(
                ExperimentRegistry.experiment_id == "exp_001"
            )
        )

    assert saved_security is not None
    assert saved_security.ticker == "MSFT"
    assert saved_security.cik == "0000789019"
    assert saved_price is not None
    assert saved_price.close == Decimal("495.000000")
    assert saved_price.source_snapshot_id == price_snapshot.snapshot_id
    assert saved_filing is not None
    assert saved_filing.form_type == "10-Q"
    assert saved_filing.storage_uri.startswith("raw/sec/filings/")
    assert saved_filing.source_url.startswith("https://www.sec.gov/Archives/")
    assert saved_filing.source_snapshot_id == filing_snapshot.snapshot_id
    assert saved_macro is not None
    assert saved_macro.value == Decimal("4.50000000")
    assert saved_macro.source_snapshot_id == macro_snapshot.snapshot_id
    assert saved_fundamental is not None
    assert saved_fundamental.value == Decimal("70000000000.000000")
    assert saved_fundamental.source_snapshot_id == filing_snapshot.snapshot_id
    assert saved_feature is not None
    assert saved_feature.value == Decimal("0.1800000000")
    assert saved_feature.feature_set_id == "baseline_features_v0.1_2026-06-24"
    assert saved_feature.available_at is not None
    assert saved_feature.source_snapshot_id == price_snapshot.snapshot_id
    assert saved_feature.source_hash == price_snapshot.source_hash
    assert saved_feature_set is not None
    assert saved_feature_set.name == "baseline_features"
    assert saved_feature_set.source_snapshot_id == price_snapshot.snapshot_id
    assert saved_prediction is not None
    assert saved_prediction.model_version == "baseline_v0.1"
    assert saved_prediction.feature_set_id == saved_feature_set.feature_set_id
    assert saved_prediction.horizon == "126d"
    assert saved_prediction.action_label == "watch_positive"
    assert saved_prediction.immutable_hash is not None
    assert saved_score_driver is not None
    assert saved_score_driver.driver_name == "momentum_6_1"
    assert saved_score_driver.contribution == Decimal("12.4000000000")
    assert saved_score_driver.evidence_uri == "feature:momentum_6_1"
    assert saved_outcome is not None
    assert saved_outcome.excess_return == Decimal("0.04000000")
    assert saved_experiment is not None
    assert saved_experiment.config_json["feature_version"] == "v0.1"


def test_features_table_requires_point_in_time_audit_fields():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("features")}

    assert {
        "feature_set_id",
        "security_id",
        "asof_date",
        "available_at",
        "feature_name",
        "value",
        "version",
        "source_snapshot_id",
        "source_hash",
    }.issubset(columns)


def test_feature_sets_table_records_feature_run_metadata():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("feature_sets")}

    assert {
        "feature_set_id",
        "name",
        "version",
        "asof_date",
        "config_json",
        "source_snapshot_id",
        "code_commit",
        "created_at",
    }.issubset(columns)


def test_filings_table_separates_frozen_storage_uri_from_source_url():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("filings")}

    assert {"storage_uri", "source_url"}.issubset(columns)


def test_model_predictions_table_is_append_only_ledger_shape():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("model_predictions")
    }

    assert {
        "prediction_id",
        "model_version",
        "security_id",
        "feature_set_id",
        "asof_date",
        "horizon",
        "score",
        "confidence",
        "action_label",
        "immutable_hash",
        "created_at",
    }.issubset(columns)
    assert "updated_at" not in columns
    foreign_keys = inspect(engine).get_foreign_keys("model_predictions")
    assert any(
        fk["constrained_columns"] == ["feature_set_id"]
        and fk["referred_table"] == "feature_sets"
        for fk in foreign_keys
    )


def test_score_drivers_table_explains_prediction_scores():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("score_drivers")}

    assert {
        "driver_id",
        "prediction_id",
        "driver_name",
        "contribution",
        "evidence_uri",
        "created_at",
    }.issubset(columns)


def test_model_predictions_reject_update_and_delete_attempts():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session_factory = make_session_factory(engine)

    with session_scope(session_factory) as session:
        price_snapshot = record_source_snapshot(
            session,
            vendor="market_data_vendor",
            dataset="daily_prices_MSFT",
            license_tag="prototype",
            source_hash=sha256_text("daily_prices_MSFT_2026-06-24"),
            storage_uri="raw/prices/MSFT/2026-06-24.json",
        )
        security = Security(
            ticker="MSFT",
            name="Microsoft",
            sector="Technology",
            exchange="NASDAQ",
            cik="0000789019",
        )
        session.add(security)
        session.flush()
        feature_set = FeatureSet(
            feature_set_id="baseline_features_v0.1_2026-06-24",
            name="baseline_features",
            version="v0.1",
            asof_date=date(2026, 6, 24),
            config_json={"lookbacks": [21, 126, 252]},
            source_snapshot_id=price_snapshot.snapshot_id,
            code_commit="abc123",
        )
        session.add(feature_set)
        prediction = ModelPrediction(
            security_id=security.security_id,
            feature_set_id=feature_set.feature_set_id,
            asof_date=date(2026, 6, 24),
            model_version="baseline_v0.1",
            score=Decimal("82"),
            confidence=Decimal("0.71"),
            action_label="watch_positive",
            immutable_hash=sha256_text(
                "baseline_v0.1|MSFT|2026-06-24|126d|82|0.71|watch_positive"
            ),
        )
        session.add(prediction)
        session.flush()
        prediction_id = prediction.prediction_id

    with session_factory() as session:
        saved_prediction = session.scalar(
            select(ModelPrediction).where(
                ModelPrediction.prediction_id == prediction_id
            )
        )
        saved_prediction.score = Decimal("83")
        with pytest.raises(RuntimeError, match="append-only"):
            session.commit()

    with session_factory() as session:
        saved_prediction = session.scalar(
            select(ModelPrediction).where(
                ModelPrediction.prediction_id == prediction_id
            )
        )
        session.delete(saved_prediction)
        with pytest.raises(RuntimeError, match="append-only"):
            session.commit()
