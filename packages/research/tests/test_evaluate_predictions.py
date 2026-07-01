from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

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
    Price,
    Security,
)
from quantfore_research.snapshots import record_source_snapshot, sha256_text


REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPO_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

from evaluate_predictions import evaluate_prediction, main  # noqa: E402


PREDICTION_DATE = date(2025, 1, 3)  # Friday
EVALUATED_AT = datetime(2025, 7, 1, 12, tzinfo=timezone.utc)
DEFAULT_BENCHMARK_DATES = object()


@dataclass(frozen=True)
class EvaluationDatabase:
    session_factory: object
    database_url: str
    prediction_ids: tuple[str, ...]
    security_id: str
    benchmark_id: str
    security_snapshot_id: str
    benchmark_snapshot_id: str
    evaluation_dates: tuple[date, ...]


def weekday_dates(start: date, count: int) -> list[date]:
    dates: list[date] = []
    candidate = start
    while len(dates) < count:
        if candidate.weekday() < 5:
            dates.append(candidate)
        candidate += timedelta(days=1)
    return dates


def msft_price(interval: int) -> Decimal:
    if interval <= 20:
        return Decimal("100") + Decimal("0.5") * Decimal(interval)
    if interval <= 40:
        return Decimal("110") - Decimal("1.1") * Decimal(interval - 20)
    return Decimal("88") + Decimal("24") * Decimal(interval - 40) / Decimal("86")


def spy_price(interval: int) -> Decimal:
    return Decimal("200") + Decimal("14") * Decimal(interval) / Decimal("126")


def seed_evaluation_database(
    *,
    database_url: str = "sqlite+pysqlite:///:memory:",
    security_dates: list[date] | None = None,
    benchmark_dates=DEFAULT_BENCHMARK_DATES,
    prediction_dates: tuple[date, ...] = (PREDICTION_DATE,),
) -> EvaluationDatabase:
    evaluation_dates = security_dates or weekday_dates(
        PREDICTION_DATE + timedelta(days=1),
        127,
    )
    if benchmark_dates is DEFAULT_BENCHMARK_DATES:
        benchmark_dates = list(evaluation_dates)

    engine = build_engine(database_url=database_url)
    create_schema(engine)
    session_factory = make_session_factory(engine)

    with session_scope(session_factory) as session:
        security_snapshot = record_source_snapshot(
            session,
            vendor="synthetic-test",
            dataset="MSFT evaluation prices",
            license_tag="test-only",
            source_hash=sha256_text("MSFT evaluation prices"),
            storage_uri="test-data/evaluation/MSFT.csv",
            retrieved_at=datetime(2025, 7, 1, 9, tzinfo=timezone.utc),
        )
        benchmark_snapshot = record_source_snapshot(
            session,
            vendor="synthetic-test",
            dataset="SPY evaluation prices",
            license_tag="test-only",
            source_hash=sha256_text("SPY evaluation prices"),
            storage_uri="test-data/evaluation/SPY.csv",
            retrieved_at=datetime(2025, 7, 1, 10, tzinfo=timezone.utc),
        )
        security = Security(ticker="MSFT", name="Microsoft")
        benchmark = Security(ticker="SPY", name="SPDR S&P 500 ETF Trust")
        session.add_all([security, benchmark])
        session.flush()

        for interval, price_date in enumerate(evaluation_dates):
            session.add(
                Price(
                    security_id=security.security_id,
                    date=price_date,
                    adj_close=msft_price(interval),
                    source_snapshot_id=security_snapshot.snapshot_id,
                )
            )
        for interval, price_date in enumerate(benchmark_dates):
            session.add(
                Price(
                    security_id=benchmark.security_id,
                    date=price_date,
                    adj_close=spy_price(interval),
                    source_snapshot_id=benchmark_snapshot.snapshot_id,
                )
            )

        predictions = []
        for index, prediction_date in enumerate(prediction_dates):
            feature_set = FeatureSet(
                feature_set_id=f"evaluation_features_{index}_{prediction_date}",
                name="baseline_features",
                version="v0.1",
                asof_date=prediction_date,
                config_json={"test": True},
                source_snapshot_id=security_snapshot.snapshot_id,
            )
            prediction = ModelPrediction(
                model_version="baseline_v0.1",
                security_id=security.security_id,
                feature_set_id=feature_set.feature_set_id,
                asof_date=prediction_date,
                horizon="126d",
                score=Decimal("82"),
                confidence=Decimal("0.71"),
                action_label="watch_positive",
                immutable_hash=sha256_text(
                    f"prediction|MSFT|{prediction_date}|126d"
                ),
            )
            session.add_all([feature_set, prediction])
            predictions.append(prediction)
        session.flush()

        seeded = EvaluationDatabase(
            session_factory=session_factory,
            database_url=database_url,
            prediction_ids=tuple(
                prediction.prediction_id for prediction in predictions
            ),
            security_id=security.security_id,
            benchmark_id=benchmark.security_id,
            security_snapshot_id=security_snapshot.snapshot_id,
            benchmark_snapshot_id=benchmark_snapshot.snapshot_id,
            evaluation_dates=tuple(evaluation_dates),
        )

    return seeded


def evaluate_seeded_prediction(
    seeded: EvaluationDatabase,
    *,
    prediction_index: int = 0,
):
    with session_scope(seeded.session_factory) as session:
        prediction = session.get(
            ModelPrediction,
            seeded.prediction_ids[prediction_index],
        )
        benchmark = session.get(Security, seeded.benchmark_id)
        report = evaluate_prediction(
            session,
            prediction=prediction,
            benchmark=benchmark,
            evaluated_at=EVALUATED_AT,
        )
    return report


def saved_outcome(seeded: EvaluationDatabase, *, prediction_index: int = 0):
    with seeded.session_factory() as session:
        return session.scalar(
            select(ModelOutcome).where(
                ModelOutcome.prediction_id
                == seeded.prediction_ids[prediction_index]
            )
        )


def test_pipeline_stores_correct_dates_returns_drawdown_and_snapshot_lineage():
    seeded = seed_evaluation_database()

    report = evaluate_seeded_prediction(seeded)
    outcome = saved_outcome(seeded)

    assert report.status == "evaluated"
    assert outcome.entry_date == date(2025, 1, 6)  # weekend is skipped
    assert outcome.exit_date == seeded.evaluation_dates[126]
    assert outcome.security_entry_price == Decimal("100.000000")
    assert outcome.security_exit_price == Decimal("112.000000")
    assert outcome.benchmark_entry_price == Decimal("200.000000")
    assert outcome.benchmark_exit_price == Decimal("214.000000")
    assert outcome.realised_return == Decimal("0.12000000")
    assert outcome.benchmark_return == Decimal("0.07000000")
    assert outcome.excess_return == Decimal("0.05000000")
    assert outcome.max_drawdown == Decimal("-0.20000000")
    assert outcome.security_price_snapshot_id == seeded.security_snapshot_id
    assert outcome.benchmark_price_snapshot_id == seeded.benchmark_snapshot_id
    assert outcome.benchmark_security_id == seeded.benchmark_id
    assert len(outcome.immutable_hash) == 64


def test_pipeline_safely_skips_an_immature_prediction():
    incomplete_dates = weekday_dates(PREDICTION_DATE + timedelta(days=1), 126)
    seeded = seed_evaluation_database(security_dates=incomplete_dates)

    report = evaluate_seeded_prediction(seeded)

    assert report.status == "immature"
    assert "required_observations=127" in report.lines[0]
    assert "available_observations=126" in report.lines[0]
    assert saved_outcome(seeded) is None


def test_pipeline_rejects_missing_spy_price_data():
    seeded = seed_evaluation_database(benchmark_dates=[])

    with pytest.raises(
        ValueError,
        match="no adjusted-close price snapshots found for benchmark SPY",
    ):
        evaluate_seeded_prediction(seeded)

    assert saved_outcome(seeded) is None


def test_pipeline_rejects_a_missing_benchmark_trading_date():
    security_dates = weekday_dates(PREDICTION_DATE + timedelta(days=1), 127)
    appended_date = weekday_dates(security_dates[-1] + timedelta(days=1), 1)[0]
    benchmark_dates = [*security_dates[:50], *security_dates[51:], appended_date]
    seeded = seed_evaluation_database(
        security_dates=security_dates,
        benchmark_dates=benchmark_dates,
    )

    with pytest.raises(ValueError, match="no complete aligned price snapshot pair"):
        evaluate_seeded_prediction(seeded)

    assert saved_outcome(seeded) is None


def test_pipeline_rejects_mismatched_benchmark_trading_dates():
    security_dates = weekday_dates(PREDICTION_DATE + timedelta(days=1), 127)
    benchmark_dates = [PREDICTION_DATE + timedelta(days=1), *security_dates]
    seeded = seed_evaluation_database(
        security_dates=security_dates,
        benchmark_dates=benchmark_dates,
    )

    with pytest.raises(ValueError, match="no complete aligned price snapshot pair"):
        evaluate_seeded_prediction(seeded)

    assert saved_outcome(seeded) is None


def test_pipeline_identical_rerun_is_a_noop():
    seeded = seed_evaluation_database()
    first_report = evaluate_seeded_prediction(seeded)
    original = saved_outcome(seeded)

    second_report = evaluate_seeded_prediction(seeded)
    unchanged = saved_outcome(seeded)

    assert first_report.status == "evaluated"
    assert second_report.status == "identical"
    assert "outcome already exists; skipping" in second_report.lines[0]
    assert unchanged.outcome_id == original.outcome_id
    assert unchanged.immutable_hash == original.immutable_hash
    assert unchanged.evaluated_at == original.evaluated_at
    with seeded.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelOutcome)) == 1


def test_pipeline_rejects_a_conflicting_rerun_without_overwriting():
    seeded = seed_evaluation_database()
    evaluate_seeded_prediction(seeded)
    original = saved_outcome(seeded)

    with session_scope(seeded.session_factory) as session:
        exit_price = session.scalar(
            select(Price)
            .where(Price.security_id == seeded.security_id)
            .where(Price.source_snapshot_id == seeded.security_snapshot_id)
            .where(Price.date == seeded.evaluation_dates[-1])
        )
        exit_price.adj_close = Decimal("113")

    with pytest.raises(ValueError, match="conflicting outcome rerun"):
        evaluate_seeded_prediction(seeded)

    unchanged = saved_outcome(seeded)
    assert unchanged.outcome_id == original.outcome_id
    assert unchanged.security_exit_price == original.security_exit_price
    assert unchanged.realised_return == original.realised_return
    assert unchanged.immutable_hash == original.immutable_hash


def test_pipeline_outcome_rejects_update_and_delete_after_storage():
    seeded = seed_evaluation_database()
    evaluate_seeded_prediction(seeded)

    with seeded.session_factory() as session:
        outcome = session.scalar(select(ModelOutcome))
        outcome.excess_return = Decimal("0.06")
        with pytest.raises(RuntimeError, match="append-only"):
            session.commit()
        session.rollback()

    with seeded.session_factory() as session:
        outcome = session.scalar(select(ModelOutcome))
        session.delete(outcome)
        with pytest.raises(RuntimeError, match="append-only"):
            session.commit()
        session.rollback()


def test_batch_evaluates_mature_predictions_and_skips_immature_ones(
    tmp_path,
    capsys,
):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'evaluation.db'}"
    evaluation_dates = weekday_dates(PREDICTION_DATE + timedelta(days=1), 127)
    seeded = seed_evaluation_database(
        database_url=database_url,
        security_dates=evaluation_dates,
        prediction_dates=(PREDICTION_DATE, evaluation_dates[4]),
    )

    result = main(["--benchmark", "SPY", "--database-url", database_url])
    output = capsys.readouterr().out

    assert result == 0
    assert "evaluated prediction ticker=MSFT horizon=126d" in output
    assert "entry_date=2025-01-06" in output
    assert "prediction immature; skipping" in output
    with seeded.session_factory() as session:
        outcomes = list(session.scalars(select(ModelOutcome)))
    assert len(outcomes) == 1
    assert outcomes[0].prediction_id == seeded.prediction_ids[0]


def test_single_prediction_cli_prints_the_readable_result(tmp_path, capsys):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'evaluation.db'}"
    seeded = seed_evaluation_database(database_url=database_url)

    result = main(
        [
            "--prediction-id",
            seeded.prediction_ids[0],
            "--benchmark",
            "SPY",
            "--database-url",
            database_url,
        ]
    )
    output = capsys.readouterr().out

    assert result == 0
    assert "evaluated prediction ticker=MSFT horizon=126d" in output
    assert "entry_date=2025-01-06" in output
    assert "realised_return=0.12 benchmark_return=0.07" in output
    assert "excess_return=0.05 max_drawdown=-0.2" in output


def test_cli_rejects_an_unknown_benchmark(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'evaluation.db'}"
    seed_evaluation_database(database_url=database_url)

    with pytest.raises(ValueError, match="unknown benchmark: QQQ"):
        main(["--benchmark", "QQQ", "--database-url", database_url])
