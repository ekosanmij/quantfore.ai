from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import update

import quantfore_research.evaluation.multifactor_warehouse as warehouse
from quantfore_research.db import build_engine, create_schema, make_session_factory
from quantfore_research.models import (
    Feature,
    FeatureSet,
    ModelOutcome,
    ModelPrediction,
    MultiFactorPredictionLink,
    MultiFactorScore,
    NormalizationRun,
    NormalizedFeature,
    ScoreDriver as ScoreDriverRow,
    Security,
    SecurityClassification,
    SourceSnapshot,
    UniverseDefinition,
)
from quantfore_research.scoring.baseline import BaselineScore, ScoreDriver
from quantfore_research.scoring.ledger import immutable_prediction_hash


ASOF = date(2022, 1, 28)
EVALUATED = datetime(2023, 1, 3, tzinfo=timezone.utc)
HASHES = {name: name[0] * 64 for name in ("raw", "class", "universe", "security", "benchmark")}


def _prediction(
    *,
    prediction_id,
    model_version,
    horizon,
    feature_set_id,
    security,
    score,
    drivers,
    valid_hash=True,
):
    value = BaselineScore(
        score=Decimal(score),
        confidence=Decimal("0.90"),
        action_label="neutral",
        drivers=drivers,
    )
    immutable_hash = immutable_prediction_hash(
        model_version=model_version,
        ticker=security.ticker,
        security_id=security.security_id,
        asof_date=ASOF,
        horizon=horizon,
        feature_set_id=feature_set_id,
        score=value,
    )
    return ModelPrediction(
        prediction_id=prediction_id,
        model_version=model_version,
        security_id=security.security_id,
        feature_set_id=feature_set_id,
        asof_date=ASOF,
        horizon=horizon,
        score=value.score,
        confidence=value.confidence,
        action_label=value.action_label,
        immutable_hash=(
            immutable_hash if valid_hash else f"forged-{prediction_id:-<57}"[:64]
        ),
    )


def make_session(*, valid_hash=True, include_outcomes=True):
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session = make_session_factory(engine)()
    for name, digest in HASHES.items():
        session.add(
            SourceSnapshot(
                snapshot_id=f"{name}-snapshot",
                vendor="Test",
                dataset=name,
                license_tag="test",
                source_hash=digest,
                storage_uri=f"raw/test/{name}.json",
                retrieved_at=EVALUATED,
            )
        )
    security = Security(security_id="security-1", ticker="ONE", name="One")
    benchmark = Security(security_id="benchmark-1", ticker="SPY", name="SPY")
    session.add_all([security, benchmark])
    session.flush()
    session.add(
        UniverseDefinition(
            universe_id="universe-1",
            name="Universe",
            version="v1",
            description="test",
            window_start=date(2020, 1, 1),
            window_end=date(2022, 12, 31),
            benchmark_security_id=benchmark.security_id,
            benchmark_excluded_from_rankings=True,
            source_snapshot_id="universe-snapshot",
            source_hash=HASHES["universe"],
        )
    )
    classification = SecurityClassification(
        classification_id="classification-1",
        security_id=security.security_id,
        sector="Industrials",
        industry="Capital Goods",
        classification_system="GICS",
        effective_from=date(2020, 1, 1),
        model_available_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
        source_snapshot_id="class-snapshot",
        source_hash=HASHES["class"],
    )
    session.add(classification)
    classification_config = {
        "classification_id": classification.classification_id,
        "sector": classification.sector,
        "industry": classification.industry,
        "source_snapshot_id": classification.source_snapshot_id,
        "source_hash": classification.source_hash,
    }
    audit_config = {
        "decision": "accepted",
        "audit_sha256": "a" * 64,
        "source_snapshot_hashes": {"raw-snapshot": HASHES["raw"]},
    }
    session.add_all(
        [
            FeatureSet(
                feature_set_id="multifactor-features",
                name="pit_multifactor_raw_features",
                version="multifactor-v1",
                asof_date=ASOF,
                config_json={
                    "security_id": security.security_id,
                    "prediction_timestamp": "2022-01-28T23:59:59Z",
                    "source_snapshot_ids": ["raw-snapshot"],
                    "classification": classification_config,
                    "fundamental_audit": audit_config,
                },
                source_snapshot_id="raw-snapshot",
            ),
            FeatureSet(
                feature_set_id="price-features",
                name="baseline_features",
                version="v0.1",
                asof_date=ASOF,
                config_json={"source_snapshot_ids": ["raw-snapshot"]},
                source_snapshot_id="raw-snapshot",
            ),
        ]
    )
    session.flush()
    session.add(
        Feature(
            feature_id="feature-1",
            feature_set_id="multifactor-features",
            security_id=security.security_id,
            asof_date=ASOF,
            available_at=datetime(2022, 1, 28, tzinfo=timezone.utc),
            feature_name="fcf_yield",
            value=Decimal("0.1"),
            raw_value=Decimal("0.1"),
            version="multifactor-v1",
            family="value",
            formula_version="multifactor-v1",
            formula="fcf / market_cap",
            direction="HIGHER",
            applicability_status="APPLICABLE",
            inputs_json={
                "inputs": [
                    {
                        "record_id": "fact-1",
                        "source_snapshot_id": "raw-snapshot",
                        "source_hash": HASHES["raw"],
                    }
                ]
            },
            source_snapshot_id="raw-snapshot",
            source_hash=HASHES["raw"],
        )
    )
    run = NormalizationRun(
        normalization_run_id="run-1",
        universe_id="universe-1",
        asof_date=ASOF,
        version="multifactor-normalization-v1",
        config_json={},
        source_feature_set_ids_json=["multifactor-features"],
        input_hash="i" * 64,
        code_commit="test",
    )
    score = MultiFactorScore(
        multifactor_score_id="score-1",
        normalization_run_id=run.normalization_run_id,
        security_id=security.security_id,
        asof_date=ASOF,
        eligible=True,
        final_score=Decimal("50"),
        composite_z=Decimal("0.1"),
        applicable_component_count=19,
        valid_component_count=19,
        component_coverage=Decimal("1"),
        available_family_count=5,
        family_z_json={name: "0.1" for name in ("value", "quality", "growth", "momentum", "risk")},
        family_scores_json={name: "53.98" for name in ("value", "quality", "growth", "momentum", "risk")},
        family_available_json={name: True for name in ("value", "quality", "growth", "momentum", "risk")},
        renormalized_weights_json={name: "0.2" for name in ("value", "quality", "growth", "momentum", "risk")},
        missingness_json={},
    )
    session.add(run)
    session.flush()
    session.add(score)
    session.flush()
    session.add(
        NormalizedFeature(
            normalized_feature_id="normalized-1",
            normalization_run_id=run.normalization_run_id,
            feature_id="feature-1",
            security_id=security.security_id,
            feature_name="fcf_yield",
            family="value",
            raw_value=Decimal("0.1"),
            winsorized_value=Decimal("0.1"),
            standardized_value=Decimal("0.2"),
            directed_value=Decimal("0.2"),
            contribution=Decimal("0.04"),
            applicability_status="APPLICABLE",
            normalization_scope="SECTOR",
            group_label="Industrials",
            group_count=20,
            group_mean=Decimal("0.08"),
            group_std=Decimal("0.01"),
        )
    )
    multifactor_drivers = (
        ScoreDriver("family:value", Decimal("0.1"), "normalization:run-1#family=value"),
    )
    predictions = []
    for horizon in ("21d", "63d", "126d", "252d"):
        prediction = _prediction(
            prediction_id=f"multifactor-{horizon}",
            model_version="multifactor-baseline-v1",
            horizon=horizon,
            feature_set_id="multifactor-features",
            security=security,
            score="50",
            drivers=multifactor_drivers,
            valid_hash=valid_hash,
        )
        predictions.append(prediction)
        session.add(prediction)
        session.flush()
        session.add(
            ScoreDriverRow(
                prediction_id=prediction.prediction_id,
                driver_name="family:value",
                contribution=Decimal("0.1"),
                evidence_uri="normalization:run-1#family=value",
            )
        )
        session.add(
            MultiFactorPredictionLink(
                link_id=f"link-{horizon}",
                multifactor_score_id=score.multifactor_score_id,
                prediction_id=prediction.prediction_id,
                horizon=horizon,
            )
        )
    price_drivers = (ScoreDriver("momentum", Decimal("1"), "feature:momentum"),)
    price_prediction = _prediction(
        prediction_id="price-126d",
        model_version="baseline_v0.1",
        horizon="126d",
        feature_set_id="price-features",
        security=security,
        score="40",
        drivers=price_drivers,
    )
    session.add(price_prediction)
    session.flush()
    session.add(
        ScoreDriverRow(
            prediction_id=price_prediction.prediction_id,
            driver_name="momentum",
            contribution=Decimal("1"),
            evidence_uri="feature:momentum",
        )
    )
    if include_outcomes:
        for prediction in (*predictions, price_prediction):
            session.add(
                ModelOutcome(
                    outcome_id=f"outcome-{prediction.prediction_id}",
                    prediction_id=prediction.prediction_id,
                    benchmark_security_id=benchmark.security_id,
                    security_price_snapshot_id="security-snapshot",
                    benchmark_price_snapshot_id="benchmark-snapshot",
                    entry_date=date(2022, 2, 1),
                    exit_date=date(2022, 12, 31),
                    security_entry_price=Decimal("100"),
                    security_exit_price=Decimal("110"),
                    benchmark_entry_price=Decimal("100"),
                    benchmark_exit_price=Decimal("105"),
                    realised_return=Decimal("0.10"),
                    benchmark_return=Decimal("0.05"),
                    excess_return=Decimal("0.05"),
                    max_drawdown=Decimal("-0.10"),
                    evaluated_at=EVALUATED,
                    immutable_hash=f"{prediction.prediction_id:-<64}"[:64],
                )
            )
    session.commit()
    return session


def test_warehouse_loader_builds_scores_returns_and_attribution_from_records(monkeypatch):
    session = make_session()
    monkeypatch.setattr(
        warehouse,
        "_verify_outcome",
        lambda *args, **kwargs: {HASHES["security"], HASHES["benchmark"]},
    )

    evaluation = warehouse.load_verified_evaluation_ledger(
        session, normalization_run_ids=["run-1"]
    )
    comparison = warehouse.load_verified_comparison_ledger(
        session, normalization_run_ids=["run-1"]
    )

    assert len(evaluation.observations) == 4
    assert len(evaluation.prediction_ids) == 4
    assert len(evaluation.outcome_ids) == 4
    assert set(evaluation.source_snapshot_hashes) >= set(HASHES.values())
    assert len(comparison.observations) == 1
    assert comparison.observations[0].price_score == Decimal("40.000000")
    assert comparison.observations[0].multifactor_score == Decimal("50.000000")
    assert comparison.observations[0].components[0].evidence_refs == (
        f"record:fact-1",
        f"snapshot:raw-snapshot#sha256={HASHES['raw']}",
    )


def test_warehouse_loader_rejects_a_forged_prediction_hash(monkeypatch):
    session = make_session(valid_hash=False)
    monkeypatch.setattr(warehouse, "_verify_outcome", lambda *args, **kwargs: set())

    with pytest.raises(ValueError, match="immutable hash"):
        warehouse.load_verified_evaluation_ledger(session)


def test_preoutcome_lock_inputs_are_built_before_any_holdout_result_exists():
    session = make_session(include_outcomes=False)

    inputs = warehouse.build_preoutcome_lock_inputs(
        session,
        normalization_run_ids=["run-1"],
        outcome_source_snapshot_ids=["security-snapshot", "benchmark-snapshot"],
    )

    assert inputs.normalization_run_ids == ("run-1",)
    assert len(inputs.prediction_ids) == 4
    assert len(inputs.score_ledger_sha256) == 64
    assert set(inputs.source_snapshot_hashes) >= set(HASHES.values())

    late = make_session(include_outcomes=True)
    with pytest.raises(ValueError, match="already exists"):
        warehouse.build_preoutcome_lock_inputs(
            late,
            normalization_run_ids=["run-1"],
            outcome_source_snapshot_ids=["security-snapshot", "benchmark-snapshot"],
        )


def test_evaluation_rejects_dates_after_frozen_mature_cutoff(monkeypatch):
    session = make_session()
    monkeypatch.setattr(warehouse, "_verify_outcome", lambda *args, **kwargs: set())

    with pytest.raises(ValueError, match="exceeds frozen cutoff 2025-06-30"):
        warehouse.load_verified_evaluation_ledger(
            session,
            end_date=date(2025, 12, 31),
        )

    session.execute(
        update(MultiFactorScore).values(asof_date=date(2025, 7, 31))
    )
    session.commit()
    with pytest.raises(ValueError, match="exceeds frozen cutoff 2025-06-30"):
        warehouse.load_verified_evaluation_ledger(session)
