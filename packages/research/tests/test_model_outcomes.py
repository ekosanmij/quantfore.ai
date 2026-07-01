from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.models import (
    FeatureSet,
    ModelOutcome,
    ModelPrediction,
    Security,
)
from quantfore_research.snapshots import record_source_snapshot, sha256_text


def make_outcome(dependencies: dict[str, str], **overrides) -> ModelOutcome:
    values = {
        "prediction_id": dependencies["prediction_id"],
        "benchmark_security_id": dependencies["benchmark_security_id"],
        "security_price_snapshot_id": dependencies["security_snapshot_id"],
        "benchmark_price_snapshot_id": dependencies["benchmark_snapshot_id"],
        "entry_date": date(2025, 1, 6),
        "exit_date": date(2025, 7, 2),
        "security_entry_price": Decimal("100"),
        "security_exit_price": Decimal("112"),
        "benchmark_entry_price": Decimal("200"),
        "benchmark_exit_price": Decimal("214"),
        "realised_return": Decimal("0.12"),
        "benchmark_return": Decimal("0.07"),
        "excess_return": Decimal("0.05"),
        "max_drawdown": Decimal("-0.08"),
        "evaluated_at": datetime(2025, 7, 3, tzinfo=timezone.utc),
        "immutable_hash": sha256_text("outcome-v0|prediction-1"),
    }
    values.update(overrides)
    return ModelOutcome(**values)


def build_outcome_database():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session_factory = make_session_factory(engine)

    with session_scope(session_factory) as session:
        security_snapshot = record_source_snapshot(
            session,
            vendor="synthetic",
            dataset="MSFT outcome prices",
            license_tag="test-only",
            source_hash=sha256_text("MSFT prices"),
            storage_uri="test-data/MSFT-outcome-prices.csv",
        )
        benchmark_snapshot = record_source_snapshot(
            session,
            vendor="synthetic",
            dataset="SPY outcome prices",
            license_tag="test-only",
            source_hash=sha256_text("SPY prices"),
            storage_uri="test-data/SPY-outcome-prices.csv",
        )
        security = Security(ticker="MSFT", name="Microsoft")
        benchmark = Security(ticker="SPY", name="SPDR S&P 500 ETF Trust")
        session.add_all([security, benchmark])
        session.flush()

        feature_set = FeatureSet(
            feature_set_id="baseline_features_v0.1_2025-01-03",
            name="baseline_features",
            version="v0.1",
            asof_date=date(2025, 1, 3),
            config_json={"lookbacks": [21, 126, 252]},
            source_snapshot_id=security_snapshot.snapshot_id,
        )
        prediction = ModelPrediction(
            model_version="baseline_v0.1",
            security_id=security.security_id,
            feature_set_id=feature_set.feature_set_id,
            asof_date=date(2025, 1, 3),
            horizon="126d",
            score=Decimal("82"),
            confidence=Decimal("0.71"),
            action_label="watch_positive",
            immutable_hash=sha256_text("prediction-v0|MSFT|2025-01-03|126d"),
        )
        session.add_all([feature_set, prediction])
        session.flush()

        dependencies = {
            "prediction_id": prediction.prediction_id,
            "benchmark_security_id": benchmark.security_id,
            "security_snapshot_id": security_snapshot.snapshot_id,
            "benchmark_snapshot_id": benchmark_snapshot.snapshot_id,
        }

    return engine, session_factory, dependencies


def test_model_outcomes_table_has_required_immutable_lineage_contract():
    engine, _, _ = build_outcome_database()
    inspector = inspect(engine)
    columns = {
        column["name"]: column
        for column in inspector.get_columns("model_outcomes")
    }
    required_columns = {
        "prediction_id",
        "benchmark_security_id",
        "security_price_snapshot_id",
        "benchmark_price_snapshot_id",
        "entry_date",
        "exit_date",
        "security_entry_price",
        "security_exit_price",
        "benchmark_entry_price",
        "benchmark_exit_price",
        "realised_return",
        "benchmark_return",
        "excess_return",
        "max_drawdown",
        "evaluated_at",
        "immutable_hash",
        "created_at",
    }

    assert required_columns.issubset(columns)
    assert all(columns[name]["nullable"] is False for name in required_columns)
    assert "updated_at" not in columns

    foreign_keys = {
        tuple(foreign_key["constrained_columns"]): foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("model_outcomes")
    }
    assert foreign_keys[("prediction_id",)] == "model_predictions"
    assert foreign_keys[("benchmark_security_id",)] == "securities"
    assert foreign_keys[("security_price_snapshot_id",)] == "source_snapshots"
    assert foreign_keys[("benchmark_price_snapshot_id",)] == "source_snapshots"


def test_model_outcomes_table_has_requested_uniqueness_indexes_and_checks():
    engine, _, _ = build_outcome_database()
    inspector = inspect(engine)

    unique_constraints = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("model_outcomes")
    }
    assert ("prediction_id",) in unique_constraints

    indexes = {
        tuple(index["column_names"])
        for index in inspector.get_indexes("model_outcomes")
    }
    assert ("prediction_id",) in indexes
    assert ("benchmark_security_id",) in indexes
    assert ("exit_date",) in indexes

    check_names = {
        constraint["name"]
        for constraint in inspector.get_check_constraints("model_outcomes")
    }
    assert {
        "ck_model_outcomes_prediction_id_nonempty",
        "ck_model_outcomes_benchmark_security_id_nonempty",
        "ck_model_outcomes_security_snapshot_id_nonempty",
        "ck_model_outcomes_benchmark_snapshot_id_nonempty",
        "ck_model_outcomes_immutable_hash_nonempty",
    }.issubset(check_names)


def test_model_outcome_persists_snapshot_lineage_and_prices():
    _, session_factory, dependencies = build_outcome_database()
    with session_scope(session_factory) as session:
        outcome = make_outcome(dependencies)
        session.add(outcome)
        session.flush()
        outcome_id = outcome.outcome_id

    with session_factory() as session:
        saved = session.get(ModelOutcome, outcome_id)
        assert saved.prediction_id == dependencies["prediction_id"]
        assert saved.benchmark_security_id == dependencies["benchmark_security_id"]
        assert saved.security_price_snapshot_id == dependencies["security_snapshot_id"]
        assert saved.benchmark_price_snapshot_id == dependencies["benchmark_snapshot_id"]
        assert saved.entry_date == date(2025, 1, 6)
        assert saved.exit_date == date(2025, 7, 2)
        assert saved.security_entry_price == Decimal("100.000000")
        assert saved.security_exit_price == Decimal("112.000000")
        assert saved.benchmark_entry_price == Decimal("200.000000")
        assert saved.benchmark_exit_price == Decimal("214.000000")
        assert saved.immutable_hash == sha256_text("outcome-v0|prediction-1")


def test_model_outcomes_allow_only_one_outcome_per_prediction():
    _, session_factory, dependencies = build_outcome_database()
    with session_scope(session_factory) as session:
        session.add(make_outcome(dependencies))

    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as session:
            session.add(
                make_outcome(
                    dependencies,
                    immutable_hash=sha256_text("conflicting outcome"),
                )
            )


def test_model_outcomes_reject_blank_immutable_hashes():
    _, session_factory, dependencies = build_outcome_database()

    with pytest.raises(IntegrityError):
        with session_scope(session_factory) as session:
            session.add(make_outcome(dependencies, immutable_hash="   "))


def test_model_outcomes_reject_update_and_delete_attempts():
    _, session_factory, dependencies = build_outcome_database()
    with session_scope(session_factory) as session:
        outcome = make_outcome(dependencies)
        session.add(outcome)
        session.flush()
        outcome_id = outcome.outcome_id

    with session_factory() as session:
        saved = session.get(ModelOutcome, outcome_id)
        saved.realised_return = Decimal("0.13")
        with pytest.raises(RuntimeError, match="append-only"):
            session.commit()
        session.rollback()

    with session_factory() as session:
        saved = session.get(ModelOutcome, outcome_id)
        session.delete(saved)
        with pytest.raises(RuntimeError, match="append-only"):
            session.commit()
        session.rollback()
