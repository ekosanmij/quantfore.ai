"""Deterministic synthetic fixture for rehearsing the shadow ledger."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.models import (
    Feature,
    FeatureSet,
    ModelPrediction,
    MultiFactorPredictionLink,
    MultiFactorScore,
    NormalizationRun,
    ScoreDriver as ScoreDriverRow,
    Security,
    SourceSnapshot,
    TickerAlias,
    UniverseDefinition,
    UniverseMembership,
)
from quantfore_research.scoring.baseline import BaselineScore, ScoreDriver
from quantfore_research.scoring.ledger import immutable_prediction_hash
from quantfore_research.shadow.ledger import (
    LOCKED_SHADOW_DATES,
    SHADOW_HORIZONS,
    ShadowBatchResult,
    create_shadow_prediction_batch,
)


FIXTURE_PREDICTION_DATE = date(2026, 7, 31)
FIXTURE_PREDICTION_TIMESTAMP = datetime(
    2026, 7, 31, 21, tzinfo=timezone.utc
)
FIXTURE_RECORDED_AT = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
FIXTURE_CODE_COMMIT = "a" * 40
FIXTURE_SOURCE_HASH = "b" * 64
FIXTURE_UNIVERSE_ID = "test-shadow-universe"
FIXTURE_NORMALIZATION_RUN_ID = "shadow-normalization-run"
FIXTURE_LOCK_URI = "experiments/synthetic-shadow-executable-lock-v1.json"
FIXTURE_FAMILIES = ("value", "quality", "growth", "momentum", "risk")


@dataclass(frozen=True)
class SeededShadowRehearsal:
    session_factory: object
    database_url: str
    executable_lock: dict
    executable_lock_hash: str
    scored_security_id: str
    excluded_security_id: str
    benchmark_security_id: str
    source_snapshot_id: str


def hash_json(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def synthetic_executable_lock() -> dict:
    """Return a clearly synthetic lock that can never represent production evidence."""

    schedule = list(LOCKED_SHADOW_DATES)
    return {
        "lock_version": "synthetic-shadow-rehearsal-executable-lock-v1",
        "fixture_only": True,
        "status": "EXECUTABLE_LOCKED",
        "claims_eligible": False,
        "executable_for_shadow_predictions": True,
        "executable_for_outcome_evaluation": True,
        "design_lock_sha256": "d" * 64,
        "model": {
            "version": "multifactor-v2-branch-aware-equal-weight-v1",
            "normalization_version": "multifactor-v2-branch-normalization-v1",
            "required_horizons": list(SHADOW_HORIZONS),
            "family_weights": {family: 0.2 for family in FIXTURE_FAMILIES},
            "required_family_count": 5,
            "minimum_component_coverage": 0.8,
        },
        "universe": {"universe_id": FIXTURE_UNIVERSE_ID},
        "prediction_schedule": {
            "dates": schedule,
            "sha256": hash_json(schedule),
        },
        "implementation": {
            "code_commit": FIXTURE_CODE_COMMIT,
            "formula_ledger_sha256": "1" * 64,
            "classification_ledger_sha256": "2" * 64,
            "source_manifest_sha256": "3" * 64,
            "evaluation_code_sha256": "4" * 64,
            "report_schema_sha256": "5" * 64,
            "portfolio_notional_usd": 1_000_000,
        },
    }


def _feature(
    *, feature_set_id: str, security_id: str, family: str, applicable: bool
) -> Feature:
    return Feature(
        feature_id=f"feature-{security_id}-{family}",
        feature_set_id=feature_set_id,
        security_id=security_id,
        asof_date=FIXTURE_PREDICTION_DATE,
        available_at=datetime(2026, 7, 31, 20, tzinfo=timezone.utc),
        feature_name=f"{family}_component",
        value=Decimal("1") if applicable else None,
        raw_value=Decimal("1") if applicable else None,
        version="multifactor-v2-branch-aware-v1",
        family=family,
        formula_version="synthetic-rehearsal-formula-v1",
        formula=f"synthetic fixture {family}",
        direction="HIGHER",
        applicability_status="APPLICABLE" if applicable else "MISSING",
        missing_reason=None if applicable else "SOURCE_MISSING",
        inputs_json={
            "inputs": [
                {
                    "record_id": f"input-{security_id}-{family}",
                    "model_available_at": "2026-07-31T20:00:00Z",
                    "source_snapshot_id": "shadow-source",
                    "source_hash": FIXTURE_SOURCE_HASH,
                }
            ]
        },
        source_snapshot_id="shadow-source",
        source_hash=FIXTURE_SOURCE_HASH,
    )


def _score(
    *, score_id: str, security_id: str, eligible: bool
) -> MultiFactorScore:
    available = {
        family: eligible or family in {"value", "momentum"}
        for family in FIXTURE_FAMILIES
    }
    missingness = {
        f"{family}_component": {
            "status": "APPLICABLE" if available[family] else "MISSING",
            "reason": "APPLICABLE" if available[family] else "SOURCE_MISSING",
        }
        for family in FIXTURE_FAMILIES
    }
    return MultiFactorScore(
        multifactor_score_id=score_id,
        normalization_run_id=FIXTURE_NORMALIZATION_RUN_ID,
        security_id=security_id,
        asof_date=FIXTURE_PREDICTION_DATE,
        eligible=eligible,
        final_score=Decimal("75") if eligible else None,
        composite_z=Decimal("0.7") if eligible else None,
        applicable_component_count=5,
        valid_component_count=5 if eligible else 2,
        component_coverage=Decimal("1") if eligible else Decimal("0.4"),
        available_family_count=5 if eligible else 2,
        family_z_json={
            family: "0.1" if available[family] else None
            for family in FIXTURE_FAMILIES
        },
        family_scores_json={
            family: "53.98" if available[family] else None
            for family in FIXTURE_FAMILIES
        },
        family_available_json=available,
        renormalized_weights_json=(
            {family: "0.2" for family in FIXTURE_FAMILIES} if eligible else {}
        ),
        missingness_json=missingness,
    )


def seed_shadow_rehearsal_database(
    *, database_url: str = "sqlite+pysqlite:///:memory:"
) -> SeededShadowRehearsal:
    """Seed two nonbenchmark members and one benchmark using synthetic-only data."""

    engine = build_engine(database_url=database_url)
    create_schema(engine)
    session_factory = make_session_factory(engine)
    executable_lock = synthetic_executable_lock()
    executable_lock_hash = hash_json(executable_lock)

    with session_scope(session_factory) as session:
        snapshot = SourceSnapshot(
            snapshot_id="shadow-source",
            vendor="synthetic-rehearsal",
            dataset="fixture shadow monthly inputs",
            retrieved_at=datetime(2026, 7, 31, 20, tzinfo=timezone.utc),
            license_tag="test-only",
            source_hash=FIXTURE_SOURCE_HASH,
            storage_uri="fixture://shadow/2026-07-31.json",
        )
        scored = Security(
            security_id="security-scored", ticker="AAA", name="Scored Fixture"
        )
        excluded = Security(
            security_id="security-excluded",
            ticker="BBB",
            name="Excluded Fixture",
        )
        benchmark = Security(
            security_id="security-benchmark", ticker="SPY", name="Fixture Benchmark"
        )
        session.add_all([snapshot, scored, excluded, benchmark])
        session.flush()

        universe = UniverseDefinition(
            universe_id=FIXTURE_UNIVERSE_ID,
            name="Synthetic rehearsal universe",
            version="fixture-v1",
            description="synthetic-only point-in-time cohort",
            window_start=date(2026, 7, 1),
            window_end=date(2028, 6, 30),
            benchmark_security_id=benchmark.security_id,
            benchmark_excluded_from_rankings=True,
            source_snapshot_id=snapshot.snapshot_id,
            source_hash=FIXTURE_SOURCE_HASH,
            audit_contract_json={"fixture_only": True},
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
                        source_hash=FIXTURE_SOURCE_HASH,
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
                        source_hash=FIXTURE_SOURCE_HASH,
                    ),
                ]
            )

        feature_sets = {
            scored.security_id: FeatureSet(
                feature_set_id="feature-set-scored",
                name="pit_multifactor_v2_features",
                version="multifactor-v2-branch-aware-v1",
                asof_date=FIXTURE_PREDICTION_DATE,
                config_json={
                    "classification_branch": "INDUSTRIAL_GENERAL",
                    "prediction_timestamp": "2026-07-31T21:00:00Z",
                    "fixture_only": True,
                },
                source_snapshot_id=snapshot.snapshot_id,
                code_commit=FIXTURE_CODE_COMMIT,
            ),
            excluded.security_id: FeatureSet(
                feature_set_id="feature-set-excluded",
                name="pit_multifactor_v2_features",
                version="multifactor-v2-branch-aware-v1",
                asof_date=FIXTURE_PREDICTION_DATE,
                config_json={
                    "classification_branch": "BANK",
                    "prediction_timestamp": "2026-07-31T21:00:00Z",
                    "fixture_only": True,
                },
                source_snapshot_id=snapshot.snapshot_id,
                code_commit=FIXTURE_CODE_COMMIT,
            ),
        }
        session.add_all(feature_sets.values())
        session.flush()
        for security in (scored, excluded):
            for family in FIXTURE_FAMILIES:
                session.add(
                    _feature(
                        feature_set_id=feature_sets[
                            security.security_id
                        ].feature_set_id,
                        security_id=security.security_id,
                        family=family,
                        applicable=(
                            security is scored or family in {"value", "momentum"}
                        ),
                    )
                )

        run = NormalizationRun(
            normalization_run_id=FIXTURE_NORMALIZATION_RUN_ID,
            universe_id=universe.universe_id,
            asof_date=FIXTURE_PREDICTION_DATE,
            version="multifactor-v2-branch-normalization-v1",
            config_json={"shadow": True, "fixture_only": True},
            source_feature_set_ids_json=[
                feature_sets[scored.security_id].feature_set_id,
                feature_sets[excluded.security_id].feature_set_id,
            ],
            input_hash="c" * 64,
            code_commit=FIXTURE_CODE_COMMIT,
        )
        scored_score = _score(
            score_id="score-scored", security_id=scored.security_id, eligible=True
        )
        excluded_score = _score(
            score_id="score-excluded",
            security_id=excluded.security_id,
            eligible=False,
        )
        session.add(run)
        session.flush()
        session.add_all([scored_score, excluded_score])
        session.flush()

        drivers = tuple(
            ScoreDriver(
                driver_name=f"family:{family}",
                contribution=Decimal("0.04"),
                evidence_uri=(
                    f"normalization:{FIXTURE_NORMALIZATION_RUN_ID}#family={family}"
                ),
            )
            for family in FIXTURE_FAMILIES
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
                model_version=executable_lock["model"]["version"],
                security_id=scored.security_id,
                feature_set_id=feature_sets[scored.security_id].feature_set_id,
                asof_date=FIXTURE_PREDICTION_DATE,
                horizon=horizon,
                score=baseline_score.score,
                confidence=baseline_score.confidence,
                action_label=baseline_score.action_label,
                immutable_hash=immutable_prediction_hash(
                    model_version=executable_lock["model"]["version"],
                    ticker=scored.ticker,
                    security_id=scored.security_id,
                    asof_date=FIXTURE_PREDICTION_DATE,
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

    return SeededShadowRehearsal(
        session_factory=session_factory,
        database_url=database_url,
        executable_lock=executable_lock,
        executable_lock_hash=executable_lock_hash,
        scored_security_id="security-scored",
        excluded_security_id="security-excluded",
        benchmark_security_id="security-benchmark",
        source_snapshot_id="shadow-source",
    )


def create_fixture_batch(
    seeded: SeededShadowRehearsal,
    *,
    executable_lock: Optional[dict] = None,
    executable_lock_hash: Optional[str] = None,
    executable_lock_uri: str = FIXTURE_LOCK_URI,
    recorded_at: datetime = FIXTURE_RECORDED_AT,
) -> ShadowBatchResult:
    """Seal or reopen the deterministic fixture cohort."""

    with session_scope(seeded.session_factory) as session:
        return create_shadow_prediction_batch(
            session,
            universe_id=FIXTURE_UNIVERSE_ID,
            normalization_run_id=FIXTURE_NORMALIZATION_RUN_ID,
            prediction_timestamp=FIXTURE_PREDICTION_TIMESTAMP,
            executable_lock=executable_lock or seeded.executable_lock,
            executable_lock_uri=executable_lock_uri,
            executable_lock_hash=(
                executable_lock_hash or seeded.executable_lock_hash
            ),
            code_commit=FIXTURE_CODE_COMMIT,
            execution_commit=FIXTURE_CODE_COMMIT,
            recorded_at=recorded_at,
        )
