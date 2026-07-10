from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
import pandas_market_calendars as mcal
from sqlalchemy import func, inspect, select, update

from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.models import (
    Feature,
    FeatureSet,
    ModelOutcome,
    ModelPrediction,
    MultiFactorPredictionLink,
    MultiFactorScore,
    NormalizationRun,
    ScoreDriver as ScoreDriverRow,
    Security,
    ShadowOutcomeRecord,
    ShadowPredictionBatch,
    ShadowPredictionRecord,
    SourceSnapshot,
    TickerAlias,
    UniverseDefinition,
    UniverseMembership,
)
from quantfore_research.scoring.baseline import BaselineScore, ScoreDriver
from quantfore_research.scoring.ledger import immutable_prediction_hash
from quantfore_research.shadow import (
    LOCKED_SHADOW_DATES,
    SHADOW_HORIZONS,
    create_shadow_prediction_batch,
    record_shadow_outcome,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import create_shadow_predictions as shadow_pipeline  # noqa: E402


PREDICTION_DATE = date(2026, 7, 31)
PREDICTION_TIMESTAMP = datetime(2026, 7, 31, 21, tzinfo=timezone.utc)
RECORDED_AT = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
CODE_COMMIT = "a" * 40
SOURCE_HASH = "b" * 64
SCHEDULE = [
    "2026-07-31",
    "2026-08-31",
    "2026-09-30",
    "2026-10-30",
    "2026-11-30",
    "2026-12-31",
    "2027-01-29",
    "2027-02-26",
    "2027-03-31",
    "2027-04-30",
    "2027-05-28",
    "2027-06-30",
    "2027-07-30",
    "2027-08-31",
    "2027-09-30",
    "2027-10-29",
    "2027-11-30",
    "2027-12-31",
    "2028-01-31",
    "2028-02-29",
    "2028-03-31",
    "2028-04-28",
    "2028-05-31",
    "2028-06-30",
]
FAMILIES = ("value", "quality", "growth", "momentum", "risk")


@dataclass(frozen=True)
class SeededShadowDatabase:
    session_factory: object
    database_url: str
    lock: dict
    lock_hash: str
    scored_security_id: str
    excluded_security_id: str
    benchmark_security_id: str
    source_snapshot_id: str


def _hash_json(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _executable_lock() -> dict:
    return {
        "lock_version": "multifactor-v2-executable-lock-v1",
        "status": "EXECUTABLE_LOCKED",
        "claims_eligible": False,
        "executable_for_shadow_predictions": True,
        "executable_for_outcome_evaluation": True,
        "design_lock_sha256": "d" * 64,
        "model": {
            "version": "multifactor-v2-branch-aware-equal-weight-v1",
            "normalization_version": "multifactor-v2-branch-normalization-v1",
            "required_horizons": list(SHADOW_HORIZONS),
            "family_weights": {family: 0.2 for family in FAMILIES},
            "required_family_count": 5,
            "minimum_component_coverage": 0.8,
        },
        "universe": {"universe_id": "test-shadow-universe"},
        "prediction_schedule": {
            "dates": SCHEDULE,
            "sha256": _hash_json(SCHEDULE),
        },
        "implementation": {
            "code_commit": CODE_COMMIT,
            "formula_ledger_sha256": "1" * 64,
            "classification_ledger_sha256": "2" * 64,
            "source_manifest_sha256": "3" * 64,
            "evaluation_code_sha256": "4" * 64,
            "report_schema_sha256": "5" * 64,
            "portfolio_notional_usd": 1_000_000,
        },
    }


def _feature(
    *,
    feature_set_id: str,
    security_id: str,
    family: str,
    applicable: bool,
) -> Feature:
    return Feature(
        feature_id=f"feature-{security_id}-{family}",
        feature_set_id=feature_set_id,
        security_id=security_id,
        asof_date=PREDICTION_DATE,
        available_at=datetime(2026, 7, 31, 20, tzinfo=timezone.utc),
        feature_name=f"{family}_component",
        value=Decimal("1") if applicable else None,
        raw_value=Decimal("1") if applicable else None,
        version="multifactor-v2-branch-aware-v1",
        family=family,
        formula_version="formula-v1",
        formula=f"synthetic {family}",
        direction="HIGHER",
        applicability_status="APPLICABLE" if applicable else "MISSING",
        missing_reason=None if applicable else "SOURCE_MISSING",
        inputs_json={
            "inputs": [
                {
                    "record_id": f"input-{security_id}-{family}",
                    "model_available_at": "2026-07-31T20:00:00Z",
                    "source_snapshot_id": "shadow-source",
                    "source_hash": SOURCE_HASH,
                }
            ]
        },
        source_snapshot_id="shadow-source",
        source_hash=SOURCE_HASH,
    )


def _score(
    *,
    score_id: str,
    security_id: str,
    eligible: bool,
) -> MultiFactorScore:
    available = {
        family: eligible or family in {"value", "momentum"} for family in FAMILIES
    }
    missingness = {
        f"{family}_component": {
            "status": "APPLICABLE" if available[family] else "MISSING",
            "reason": "APPLICABLE" if available[family] else "SOURCE_MISSING",
        }
        for family in FAMILIES
    }
    return MultiFactorScore(
        multifactor_score_id=score_id,
        normalization_run_id="shadow-normalization-run",
        security_id=security_id,
        asof_date=PREDICTION_DATE,
        eligible=eligible,
        final_score=Decimal("75") if eligible else None,
        composite_z=Decimal("0.7") if eligible else None,
        applicable_component_count=5,
        valid_component_count=5 if eligible else 2,
        component_coverage=Decimal("1") if eligible else Decimal("0.4"),
        available_family_count=5 if eligible else 2,
        family_z_json={
            family: "0.1" if available[family] else None for family in FAMILIES
        },
        family_scores_json={
            family: "53.98" if available[family] else None for family in FAMILIES
        },
        family_available_json=available,
        renormalized_weights_json=(
            {family: "0.2" for family in FAMILIES} if eligible else {}
        ),
        missingness_json=missingness,
    )


def seed_shadow_database(
    *,
    database_url: str = "sqlite+pysqlite:///:memory:",
) -> SeededShadowDatabase:
    engine = build_engine(database_url=database_url)
    create_schema(engine)
    session_factory = make_session_factory(engine)
    lock = _executable_lock()
    lock_hash = _hash_json(lock)

    with session_scope(session_factory) as session:
        snapshot = SourceSnapshot(
            snapshot_id="shadow-source",
            vendor="synthetic-test",
            dataset="shadow monthly inputs",
            retrieved_at=datetime(2026, 7, 31, 20, tzinfo=timezone.utc),
            license_tag="test-only",
            source_hash=SOURCE_HASH,
            storage_uri="test-data/shadow/2026-07-31.json",
        )
        scored = Security(
            security_id="security-scored", ticker="AAA", name="Scored Company"
        )
        excluded = Security(
            security_id="security-excluded", ticker="BBB", name="Excluded Company"
        )
        benchmark = Security(
            security_id="security-benchmark", ticker="SPY", name="Benchmark"
        )
        session.add_all([snapshot, scored, excluded, benchmark])
        session.flush()
        universe = UniverseDefinition(
            universe_id="test-shadow-universe",
            name="Synthetic shadow universe",
            version="v1",
            description="test-only point-in-time cohort",
            window_start=date(2026, 7, 1),
            window_end=date(2028, 6, 30),
            benchmark_security_id=benchmark.security_id,
            benchmark_excluded_from_rankings=True,
            source_snapshot_id=snapshot.snapshot_id,
            source_hash=SOURCE_HASH,
            audit_contract_json={},
        )
        session.add(universe)
        for security in (scored, excluded):
            session.add_all(
                [
                    UniverseMembership(
                        membership_id=f"membership-{security.security_id}",
                        universe_id=universe.universe_id,
                        security_id=security.security_id,
                        effective_from=date(2026, 7, 1),
                        effective_to=None,
                        announced_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                        source_snapshot_id=snapshot.snapshot_id,
                        source_hash=SOURCE_HASH,
                    ),
                    TickerAlias(
                        ticker_alias_id=f"alias-{security.security_id}",
                        security_id=security.security_id,
                        ticker=security.ticker,
                        exchange="NYSE",
                        effective_from=date(2026, 7, 1),
                        effective_to=None,
                        announced_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                        source_snapshot_id=snapshot.snapshot_id,
                        source_hash=SOURCE_HASH,
                    ),
                ]
            )
        feature_sets = {
            scored.security_id: FeatureSet(
                feature_set_id="feature-set-scored",
                name="pit_multifactor_v2_features",
                version="multifactor-v2-branch-aware-v1",
                asof_date=PREDICTION_DATE,
                config_json={
                    "classification_branch": "INDUSTRIAL_GENERAL",
                    "prediction_timestamp": "2026-07-31T21:00:00Z",
                },
                source_snapshot_id=snapshot.snapshot_id,
                code_commit=CODE_COMMIT,
            ),
            excluded.security_id: FeatureSet(
                feature_set_id="feature-set-excluded",
                name="pit_multifactor_v2_features",
                version="multifactor-v2-branch-aware-v1",
                asof_date=PREDICTION_DATE,
                config_json={
                    "classification_branch": "BANK",
                    "prediction_timestamp": "2026-07-31T21:00:00Z",
                },
                source_snapshot_id=snapshot.snapshot_id,
                code_commit=CODE_COMMIT,
            ),
        }
        session.add_all(feature_sets.values())
        session.flush()
        for security in (scored, excluded):
            for family in FAMILIES:
                session.add(
                    _feature(
                        feature_set_id=(
                            feature_sets[security.security_id].feature_set_id
                        ),
                        security_id=security.security_id,
                        family=family,
                        applicable=(
                            security is scored or family in {"value", "momentum"}
                        ),
                    )
                )
        run = NormalizationRun(
            normalization_run_id="shadow-normalization-run",
            universe_id=universe.universe_id,
            asof_date=PREDICTION_DATE,
            version="multifactor-v2-branch-normalization-v1",
            config_json={"shadow": True},
            source_feature_set_ids_json=[
                feature_sets[scored.security_id].feature_set_id,
                feature_sets[excluded.security_id].feature_set_id,
            ],
            input_hash="c" * 64,
            code_commit=CODE_COMMIT,
        )
        scored_score = _score(
            score_id="score-scored", security_id=scored.security_id, eligible=True
        )
        excluded_score = _score(
            score_id="score-excluded", security_id=excluded.security_id, eligible=False
        )
        session.add(run)
        session.flush()
        session.add_all([scored_score, excluded_score])
        session.flush()

        drivers = tuple(
            ScoreDriver(
                driver_name=f"family:{family}",
                contribution=Decimal("0.04"),
                evidence_uri=f"normalization:shadow-normalization-run#family={family}",
            )
            for family in FAMILIES
        )
        baseline_score = BaselineScore(
            score=Decimal("75"),
            confidence=Decimal("1"),
            action_label="research_top_quintile",
            drivers=drivers,
        )
        for horizon in SHADOW_HORIZONS:
            prediction_id = f"prediction-scored-{horizon}"
            prediction = ModelPrediction(
                prediction_id=prediction_id,
                model_version=lock["model"]["version"],
                security_id=scored.security_id,
                feature_set_id=feature_sets[scored.security_id].feature_set_id,
                asof_date=PREDICTION_DATE,
                horizon=horizon,
                score=baseline_score.score,
                confidence=baseline_score.confidence,
                action_label=baseline_score.action_label,
                immutable_hash=immutable_prediction_hash(
                    model_version=lock["model"]["version"],
                    ticker=scored.ticker,
                    security_id=scored.security_id,
                    asof_date=PREDICTION_DATE,
                    horizon=horizon,
                    feature_set_id=feature_sets[scored.security_id].feature_set_id,
                    score=baseline_score,
                ),
            )
            session.add(prediction)
            session.flush()
            session.add_all(
                ScoreDriverRow(
                    prediction_id=prediction_id,
                    driver_name=driver.driver_name,
                    contribution=driver.contribution,
                    evidence_uri=driver.evidence_uri,
                )
                for driver in drivers
            )
            session.add(
                MultiFactorPredictionLink(
                    link_id=f"link-scored-{horizon}",
                    multifactor_score_id=scored_score.multifactor_score_id,
                    prediction_id=prediction_id,
                    horizon=horizon,
                )
            )

    return SeededShadowDatabase(
        session_factory=session_factory,
        database_url=database_url,
        lock=lock,
        lock_hash=lock_hash,
        scored_security_id="security-scored",
        excluded_security_id="security-excluded",
        benchmark_security_id="security-benchmark",
        source_snapshot_id="shadow-source",
    )


def _create_batch(seeded: SeededShadowDatabase):
    with session_scope(seeded.session_factory) as session:
        return create_shadow_prediction_batch(
            session,
            universe_id="test-shadow-universe",
            normalization_run_id="shadow-normalization-run",
            prediction_timestamp=PREDICTION_TIMESTAMP,
            executable_lock=seeded.lock,
            executable_lock_uri="experiments/test-executable-lock.json",
            executable_lock_hash=seeded.lock_hash,
            code_commit=CODE_COMMIT,
            execution_commit=CODE_COMMIT,
            recorded_at=RECORDED_AT,
        )


def test_shadow_schema_has_complete_batch_record_and_outcome_tables():
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    schema = inspect(engine)

    assert {
        "shadow_prediction_batches",
        "shadow_prediction_records",
        "shadow_outcome_records",
    }.issubset(schema.get_table_names())
    batch_columns = {
        row["name"] for row in schema.get_columns("shadow_prediction_batches")
    }
    assert {
        "prediction_timestamp",
        "recorded_at",
        "input_manifest_json",
        "executable_lock_hash",
        "code_commit",
        "execution_commit",
        "batch_hash",
    }.issubset(batch_columns)
    record_columns = {
        row["name"] for row in schema.get_columns("shadow_prediction_records")
    }
    assert {
        "research_score",
        "research_label",
        "product_label",
        "exclusions_json",
        "drivers_json",
        "prediction_ids_json",
        "record_hash",
    }.issubset(record_columns)
    assert tuple(SCHEDULE) == LOCKED_SHADOW_DATES


def test_locked_prediction_dates_are_the_last_regular_nyse_sessions():
    schedule = mcal.get_calendar("NYSE").schedule(
        start_date="2026-07-01", end_date="2028-06-30"
    )
    last_session_by_month = {}
    for timestamp in schedule.index:
        last_session_by_month[(timestamp.year, timestamp.month)] = (
            timestamp.date().isoformat()
        )

    assert tuple(last_session_by_month.values()) == LOCKED_SHADOW_DATES


def test_batch_seals_every_member_with_scores_drivers_or_explicit_exclusions():
    seeded = seed_shadow_database()

    result = _create_batch(seeded)

    assert result.created is True
    assert result.expected_member_count == 2
    assert result.scored_count == 1
    assert result.excluded_count == 1
    assert len(result.batch_hash) == 64
    with seeded.session_factory() as session:
        batch = session.get(ShadowPredictionBatch, result.batch_id)
        rows = session.scalars(
            select(ShadowPredictionRecord).order_by(
                ShadowPredictionRecord.disposition.desc()
            )
        ).all()
        assert batch.claims_eligible is False
        assert batch.product_label_policy == "WITHHELD_RESEARCH_ONLY"
        assert batch.expected_member_count == len(rows)
        assert len(batch.input_manifest_json["source_snapshots"]) == 1
        scored = next(row for row in rows if row.disposition == "SCORED")
        excluded = next(row for row in rows if row.disposition == "EXCLUDED")
        assert scored.research_score == Decimal("75.000000")
        assert scored.product_label is None
        assert scored.product_label_status == "WITHHELD_RESEARCH_ONLY"
        assert set(scored.prediction_ids_json) == set(SHADOW_HORIZONS)
        assert len(scored.drivers_json) == 5
        assert scored.exclusions_json == []
        assert (
            len(
                scored.input_lineage_json["feature_set"][
                    "feature_ledger_sha256"
                ]
            )
            == 64
        )
        assert excluded.research_score is None
        assert excluded.prediction_ids_json == {}
        assert excluded.drivers_json == []
        reasons = {row["reason"] for row in excluded.exclusions_json}
        assert "SOURCE_MISSING" in reasons
        assert "INSUFFICIENT_FAMILY_COVERAGE" in reasons
        assert "INSUFFICIENT_COMPONENT_COVERAGE" in reasons


def test_identical_batch_is_idempotent_and_ledger_rows_are_append_only():
    seeded = seed_shadow_database()
    first = _create_batch(seeded)
    second = _create_batch(seeded)

    assert second.created is False
    assert second.batch_hash == first.batch_hash
    with seeded.session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(ShadowPredictionBatch))
            == 1
        )
        assert (
            session.scalar(select(func.count()).select_from(ShadowPredictionRecord))
            == 2
        )
        row = session.scalar(select(ShadowPredictionRecord).limit(1))
        row.classification_branch = "TAMPERED"
        with pytest.raises(RuntimeError, match="append-only"):
            session.commit()


def test_design_lock_and_silent_exclusions_are_rejected():
    seeded = seed_shadow_database()
    design_lock = json.loads(
        (
            REPOSITORY_ROOT
            / "experiments"
            / "multifactor-v2-hypothesis-lock-v1.json"
        ).read_text(encoding="utf-8")
    )
    with seeded.session_factory() as session:
        with pytest.raises(ValueError, match="EXECUTABLE_LOCKED"):
            create_shadow_prediction_batch(
                session,
                universe_id="test-shadow-universe",
                normalization_run_id="shadow-normalization-run",
                prediction_timestamp=PREDICTION_TIMESTAMP,
                executable_lock=design_lock,
                executable_lock_uri=(
                    "experiments/multifactor-v2-hypothesis-lock-v1.json"
                ),
                executable_lock_hash="f" * 64,
                code_commit=CODE_COMMIT,
                execution_commit=CODE_COMMIT,
                recorded_at=RECORDED_AT,
            )

    with session_scope(seeded.session_factory) as session:
        session.execute(
            update(MultiFactorScore)
            .where(MultiFactorScore.multifactor_score_id == "score-excluded")
            .values(
                available_family_count=5,
                component_coverage=Decimal("0.9"),
                missingness_json={},
            )
        )
    with seeded.session_factory() as session:
        with pytest.raises(ValueError, match="explicit reason codes"):
            create_shadow_prediction_batch(
                session,
                universe_id="test-shadow-universe",
                normalization_run_id="shadow-normalization-run",
                prediction_timestamp=PREDICTION_TIMESTAMP,
                executable_lock=seeded.lock,
                executable_lock_uri="experiments/test-executable-lock.json",
                executable_lock_hash=seeded.lock_hash,
                code_commit=CODE_COMMIT,
                execution_commit=CODE_COMMIT,
                recorded_at=RECORDED_AT,
            )


def test_late_input_snapshot_is_rejected_before_any_batch_is_written():
    seeded = seed_shadow_database()
    with session_scope(seeded.session_factory) as session:
        session.execute(
            update(SourceSnapshot)
            .where(SourceSnapshot.snapshot_id == seeded.source_snapshot_id)
            .values(retrieved_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
        )

    with seeded.session_factory() as session:
        with pytest.raises(ValueError, match="retrieved after prediction"):
            create_shadow_prediction_batch(
                session,
                universe_id="test-shadow-universe",
                normalization_run_id="shadow-normalization-run",
                prediction_timestamp=PREDICTION_TIMESTAMP,
                executable_lock=seeded.lock,
                executable_lock_uri="experiments/test-executable-lock.json",
                executable_lock_hash=seeded.lock_hash,
                code_commit=CODE_COMMIT,
                execution_commit=CODE_COMMIT,
                recorded_at=RECORDED_AT,
            )
        assert (
            session.scalar(select(func.count()).select_from(ShadowPredictionBatch))
            == 0
        )


def test_outcome_link_is_appended_only_after_exact_horizon_is_mature():
    seeded = seed_shadow_database()
    batch = _create_batch(seeded)
    with seeded.session_factory() as session:
        scored = session.scalar(
            select(ShadowPredictionRecord).where(
                ShadowPredictionRecord.batch_id == batch.batch_id,
                ShadowPredictionRecord.disposition == "SCORED",
            )
        )
        excluded = session.scalar(
            select(ShadowPredictionRecord).where(
                ShadowPredictionRecord.batch_id == batch.batch_id,
                ShadowPredictionRecord.disposition == "EXCLUDED",
            )
        )
        with pytest.raises(ValueError, match="not yet mature or evaluated"):
            record_shadow_outcome(
                session,
                shadow_prediction_id=scored.shadow_prediction_id,
                horizon="126d",
                recorded_at=datetime(2027, 2, 1, tzinfo=timezone.utc),
            )
        with pytest.raises(ValueError, match="excluded"):
            record_shadow_outcome(
                session,
                shadow_prediction_id=excluded.shadow_prediction_id,
                horizon="126d",
                recorded_at=datetime(2027, 2, 1, tzinfo=timezone.utc),
            )

    with session_scope(seeded.session_factory) as session:
        scored = session.scalar(
            select(ShadowPredictionRecord).where(
                ShadowPredictionRecord.disposition == "SCORED"
            )
        )
        prediction_id = scored.prediction_ids_json["126d"]
        session.add(
            ModelOutcome(
                outcome_id="outcome-scored-126d",
                prediction_id=prediction_id,
                benchmark_security_id=seeded.benchmark_security_id,
                security_price_snapshot_id=seeded.source_snapshot_id,
                benchmark_price_snapshot_id=seeded.source_snapshot_id,
                entry_date=date(2026, 8, 3),
                exit_date=date(2027, 1, 29),
                security_entry_price=Decimal("100"),
                security_exit_price=Decimal("110"),
                benchmark_entry_price=Decimal("200"),
                benchmark_exit_price=Decimal("210"),
                realised_return=Decimal("0.10"),
                benchmark_return=Decimal("0.05"),
                excess_return=Decimal("0.05"),
                max_drawdown=Decimal("-0.08"),
                evaluated_at=datetime(2027, 1, 30, tzinfo=timezone.utc),
                immutable_hash="e" * 64,
            )
        )

    with session_scope(seeded.session_factory) as session:
        scored = session.scalar(
            select(ShadowPredictionRecord).where(
                ShadowPredictionRecord.disposition == "SCORED"
            )
        )
        with pytest.raises(ValueError, match="before its exit date"):
            record_shadow_outcome(
                session,
                shadow_prediction_id=scored.shadow_prediction_id,
                horizon="126d",
                recorded_at=datetime(2027, 1, 28, tzinfo=timezone.utc),
            )
        outcome = record_shadow_outcome(
            session,
            shadow_prediction_id=scored.shadow_prediction_id,
            horizon="126d",
            recorded_at=datetime(2027, 2, 1, tzinfo=timezone.utc),
        )
        assert outcome.outcome_id == "outcome-scored-126d"
        assert len(outcome.immutable_hash) == 64

    with seeded.session_factory() as session:
        assert (
            session.scalar(select(func.count()).select_from(ShadowOutcomeRecord))
            == 1
        )


def test_monthly_cli_creates_and_idempotently_reopens_the_batch(
    tmp_path, monkeypatch, capsys
):
    database_path = tmp_path / "shadow.db"
    database_url = f"sqlite+pysqlite:///{database_path}"
    seeded = seed_shadow_database(database_url=database_url)
    lock_path = tmp_path / "executable-lock.json"
    lock_path.write_text(
        json.dumps(seeded.lock, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        shadow_pipeline,
        "committed_lock_evidence",
        lambda path, locked_code_commit: (
            "experiments/test-executable-lock.json",
            CODE_COMMIT,
        ),
    )
    argv = [
        "--universe-id",
        "test-shadow-universe",
        "--prediction-timestamp",
        "2026-07-31T21:00:00Z",
        "--normalization-run-id",
        "shadow-normalization-run",
        "--executable-lock",
        str(lock_path),
        "--database-url",
        database_url,
    ]

    assert shadow_pipeline.main(argv, recorded_at=RECORDED_AT) == 0
    assert "shadow_batch_status=created" in capsys.readouterr().out
    assert shadow_pipeline.main(argv, recorded_at=RECORDED_AT) == 0
    assert "shadow_batch_status=already_sealed" in capsys.readouterr().out


def test_cli_rejects_an_executable_lock_outside_the_committed_repository(tmp_path):
    external_lock = tmp_path / "lock.json"
    external_lock.write_text("{}", encoding="utf-8")

    with pytest.raises(RuntimeError, match="committed inside the repository"):
        shadow_pipeline.committed_lock_evidence(
            external_lock, locked_code_commit=CODE_COMMIT
        )


def test_cli_accepts_only_a_lock_only_commit_after_implementation(tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "shadow-test@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Shadow Test"],
        cwd=repository,
        check=True,
    )
    source = repository / "model.py"
    source.write_text("MODEL = 'locked'\n", encoding="utf-8")
    subprocess.run(["git", "add", "model.py"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "Lock implementation"],
        cwd=repository,
        check=True,
    )
    implementation_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repository, text=True
    ).strip()
    experiments = repository / "experiments"
    experiments.mkdir()
    lock_path = experiments / "model-lock.json"
    lock_path.write_text(
        json.dumps({"implementation": {"code_commit": implementation_commit}}),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", "experiments/model-lock.json"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "Commit lock"],
        cwd=repository,
        check=True,
    )

    uri, execution_commit = shadow_pipeline.committed_lock_evidence(
        lock_path,
        locked_code_commit=implementation_commit,
        repository_root=repository,
    )

    assert uri == "experiments/model-lock.json"
    assert execution_commit != implementation_commit
    source.write_text("MODEL = 'changed'\n", encoding="utf-8")
    subprocess.run(["git", "add", "model.py"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "Change model"],
        cwd=repository,
        check=True,
    )
    with pytest.raises(RuntimeError, match="source changed"):
        shadow_pipeline.committed_lock_evidence(
            lock_path,
            locked_code_commit=implementation_commit,
            repository_root=repository,
        )


def test_shadow_ledger_document_locks_workflow_schedule_and_claims_boundary():
    contract = (
        REPOSITORY_ROOT / "docs" / "research" / "shadow-ledger-v1.md"
    ).read_text(encoding="utf-8")

    for heading in (
        "## Storage contract",
        "## Timestamp rules",
        "## Executable lock required by the CLI",
        "## Monthly command",
        "## Monthly operating procedure",
        "## Failure policy",
        "## Blinding and claims boundary",
    ):
        assert heading in contract
    assert "create_shadow_predictions.py" in contract
    assert "expected_member_count = scored_count + excluded_count" in contract
    assert "product_label IS NULL" in contract
    assert "claims_eligible=false" in contract
    assert all(prediction_date in contract for prediction_date in LOCKED_SHADOW_DATES)
