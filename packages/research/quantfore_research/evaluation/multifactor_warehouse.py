"""Warehouse-verified observations for canonical Sprint 8 evaluation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.evaluation.multifactor import MultiFactorEvaluationObservation
from quantfore_research.evaluation.multifactor_contract import (
    HOLDOUT_END,
    HOLDOUT_END_TEXT,
    HOLDOUT_START,
    reject_after_frozen_cutoff,
)
from quantfore_research.evaluation.multifactor_comparison import (
    AttributionComponent,
    MultiModelObservation,
)
from quantfore_research.models import (
    DelistingEvent,
    Feature,
    FeatureSet,
    ModelOutcome,
    ModelPrediction,
    MultiFactorPredictionLink,
    MultiFactorScore,
    NormalizedFeature,
    NormalizationRun,
    ScoreDriver,
    Security,
    SecurityClassification,
    SourceSnapshot,
    UniverseDefinition,
)
from quantfore_research.scoring.ledger import immutable_prediction_hash
from quantfore_research.scoring.multifactor import MULTIFACTOR_MODEL_VERSION


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decimal_mapping(values: dict[str, Any]) -> dict[str, Optional[Decimal]]:
    return {
        str(key): (None if value is None else Decimal(str(value)))
        for key, value in values.items()
    }


@dataclass(frozen=True)
class VerifiedEvaluationLedger:
    observations: tuple[MultiFactorEvaluationObservation, ...]
    normalization_run_ids: tuple[str, ...]
    prediction_ids: tuple[str, ...]
    outcome_ids: tuple[str, ...]
    source_snapshot_hashes: tuple[str, ...]
    score_ledger_sha256: str
    earliest_holdout_evaluated_at: Optional[datetime]

    def lineage_dict(self) -> dict[str, Any]:
        return {
            "source": "verified_research_warehouse",
            "normalization_run_ids": list(self.normalization_run_ids),
            "prediction_ids": list(self.prediction_ids),
            "outcome_ids": list(self.outcome_ids),
            "source_snapshot_hashes": list(self.source_snapshot_hashes),
            "score_ledger_sha256": self.score_ledger_sha256,
            "observation_count": len(self.observations),
        }


@dataclass(frozen=True)
class VerifiedComparisonLedger:
    observations: tuple[MultiModelObservation, ...]
    evaluation_ledger: VerifiedEvaluationLedger
    price_prediction_ids: tuple[str, ...]
    price_outcome_ids: tuple[str, ...]

    def lineage_dict(self) -> dict[str, Any]:
        value = self.evaluation_ledger.lineage_dict()
        value.update(
            {
                "price_prediction_ids": list(self.price_prediction_ids),
                "price_outcome_ids": list(self.price_outcome_ids),
                "comparison_observation_count": len(self.observations),
            }
        )
        return value


@dataclass(frozen=True)
class PreOutcomeLockInputs:
    normalization_run_ids: tuple[str, ...]
    source_snapshot_hashes: tuple[str, ...]
    score_ledger_sha256: str
    prediction_ids: tuple[str, ...]


def _verify_prediction(
    session: Session,
    *,
    prediction: ModelPrediction,
    score: MultiFactorScore,
    security: Security,
) -> None:
    if (
        prediction.model_version != MULTIFACTOR_MODEL_VERSION
        or prediction.security_id != score.security_id
        or prediction.asof_date != score.asof_date
        or score.final_score is None
        or prediction.score != score.final_score
    ):
        raise ValueError("multi-factor prediction does not match its stored score")
    _verify_prediction_hash(session, prediction=prediction, security=security)


def _verify_prediction_hash(
    session: Session,
    *,
    prediction: ModelPrediction,
    security: Security,
) -> None:
    drivers = tuple(
        session.scalars(
            select(ScoreDriver)
            .where(ScoreDriver.prediction_id == prediction.prediction_id)
            .order_by(ScoreDriver.driver_name)
        ).all()
    )
    expected = immutable_prediction_hash(
        model_version=prediction.model_version,
        ticker=security.ticker,
        security_id=prediction.security_id,
        asof_date=prediction.asof_date,
        horizon=prediction.horizon,
        feature_set_id=prediction.feature_set_id,
        score=SimpleNamespace(
            score=prediction.score,
            confidence=prediction.confidence,
            action_label=prediction.action_label,
            drivers=drivers,
        ),
    )
    if expected != prediction.immutable_hash:
        raise ValueError("stored prediction immutable hash is invalid")


def _feature_set_source_hashes(
    session: Session, *, feature_set_id: str
) -> set[str]:
    feature_set = session.get(FeatureSet, feature_set_id)
    if feature_set is None:
        raise ValueError("stored prediction feature set is missing")
    snapshots = {
        feature_set.source_snapshot_id,
        *feature_set.config_json.get("source_snapshot_ids", []),
    }
    rows = list(
        session.scalars(
            select(SourceSnapshot).where(SourceSnapshot.snapshot_id.in_(snapshots))
        ).all()
    )
    if {row.snapshot_id for row in rows} != snapshots:
        raise ValueError("prediction feature source snapshots are incomplete")
    feature_rows = list(
        session.scalars(
            select(Feature).where(Feature.feature_set_id == feature_set_id)
        ).all()
    )
    snapshots_by_id = {row.snapshot_id: row for row in rows}
    if any(
        snapshots_by_id[row.source_snapshot_id].source_hash != row.source_hash
        for row in feature_rows
    ):
        raise ValueError("stored feature source hash no longer reproduces")
    return {row.source_hash for row in rows} | {row.source_hash for row in feature_rows}


def _verify_feature_lineage(
    session: Session,
    *,
    prediction: ModelPrediction,
    run: NormalizationRun,
    score: MultiFactorScore,
) -> tuple[str, set[str]]:
    if prediction.feature_set_id not in run.source_feature_set_ids_json:
        raise ValueError("prediction feature set is outside its normalization run")
    feature_set = session.get(FeatureSet, prediction.feature_set_id)
    if feature_set is None:
        raise ValueError("prediction references a missing feature set")
    config = feature_set.config_json
    if config.get("security_id") != score.security_id:
        raise ValueError("prediction feature set has conflicting security lineage")
    audit = config.get("fundamental_audit")
    if not isinstance(audit, dict) or audit.get("decision") != "accepted":
        raise ValueError("prediction feature set lacks a passing fundamental audit")
    audit_hash = audit.get("audit_sha256")
    if not isinstance(audit_hash, str) or len(audit_hash) != 64:
        raise ValueError("prediction feature set has invalid audit lineage")
    classification = config.get("classification")
    if not isinstance(classification, dict):
        raise ValueError("prediction feature set lacks classification lineage")
    classification_row = session.get(
        SecurityClassification, classification.get("classification_id")
    )
    if classification_row is None or (
        classification_row.security_id != score.security_id
        or classification_row.sector != classification.get("sector")
        or classification_row.industry != classification.get("industry")
        or classification_row.source_snapshot_id
        != classification.get("source_snapshot_id")
        or classification_row.source_hash != classification.get("source_hash")
        or classification_row.effective_from > score.asof_date
        or (
            classification_row.effective_to is not None
            and classification_row.effective_to < score.asof_date
        )
    ):
        raise ValueError("stored point-in-time classification does not reproduce")
    timestamp_text = config.get("prediction_timestamp")
    try:
        prediction_timestamp = datetime.fromisoformat(
            str(timestamp_text).replace("Z", "+00:00")
        )
    except ValueError as exc:
        raise ValueError("feature set prediction timestamp is invalid") from exc
    if _utc(classification_row.model_available_at) > _utc(prediction_timestamp):
        raise ValueError("classification was not available at prediction time")
    audit_snapshots = audit.get("source_snapshot_hashes")
    if not isinstance(audit_snapshots, dict) or not audit_snapshots:
        raise ValueError("feature set audit source hashes are missing")
    stored_audit_snapshots = {
        row.snapshot_id: row.source_hash
        for row in session.scalars(
            select(SourceSnapshot).where(
                SourceSnapshot.snapshot_id.in_(set(audit_snapshots))
            )
        ).all()
    }
    if stored_audit_snapshots != audit_snapshots:
        raise ValueError("feature set audit source hashes do not reproduce")
    hashes = set(audit_snapshots.values())
    hashes.add(classification_row.source_hash)
    feature_snapshot_ids = set(config.get("source_snapshot_ids") or ())
    feature_snapshots = session.scalars(
        select(SourceSnapshot).where(SourceSnapshot.snapshot_id.in_(feature_snapshot_ids))
    ).all()
    if {row.snapshot_id for row in feature_snapshots} != feature_snapshot_ids:
        raise ValueError("feature set references unknown source snapshots")
    snapshots_by_id = {row.snapshot_id: row for row in feature_snapshots}
    feature_rows = list(
        session.scalars(
            select(Feature).where(Feature.feature_set_id == feature_set.feature_set_id)
        ).all()
    )
    if any(
        snapshots_by_id[row.source_snapshot_id].source_hash != row.source_hash
        for row in feature_rows
    ):
        raise ValueError("multi-factor feature source hash does not reproduce")
    hashes.update(row.source_hash for row in feature_snapshots)
    return classification_row.sector, hashes


def _verify_outcome(
    session: Session,
    *,
    prediction: ModelPrediction,
    security: Security,
    outcome: ModelOutcome,
) -> set[str]:
    benchmark = session.get(Security, outcome.benchmark_security_id)
    if benchmark is None:
        raise ValueError("stored outcome benchmark is missing")
    # Reuses the canonical verifier: immutable hash, snapshot timing, and a
    # full recalculation from the exact stored adjusted-close observations.
    from pipelines.evaluate_predictions import validate_existing_outcome

    validate_existing_outcome(
        session,
        prediction=prediction,
        ticker=security.ticker,
        benchmark=benchmark,
        existing=outcome,
    )
    snapshots = [
        session.get(SourceSnapshot, outcome.security_price_snapshot_id),
        session.get(SourceSnapshot, outcome.benchmark_price_snapshot_id),
    ]
    if any(row is None for row in snapshots):
        raise ValueError("stored outcome source snapshot is missing")
    return {row.source_hash for row in snapshots if row is not None}


def build_preoutcome_lock_inputs(
    session: Session,
    *,
    outcome_source_snapshot_ids: Sequence[str],
    normalization_run_ids: Optional[Sequence[str]] = None,
    universe_id: Optional[str] = None,
) -> PreOutcomeLockInputs:
    """Verify locked scores/sources while refusing pre-existing holdout outcomes."""

    requested_runs = set(normalization_run_ids or ())
    used_runs: set[str] = set()
    source_hashes: set[str] = set()
    prediction_ids = []
    score_rows = []
    uses_holdout = False
    links = list(
        session.scalars(
            select(MultiFactorPredictionLink).order_by(
                MultiFactorPredictionLink.prediction_id
            )
        ).all()
    )
    for link in links:
        score = session.get(MultiFactorScore, link.multifactor_score_id)
        prediction = session.get(ModelPrediction, link.prediction_id)
        if score is None or prediction is None:
            raise ValueError("multi-factor prediction link is orphaned")
        run = session.get(NormalizationRun, score.normalization_run_id)
        security = session.get(Security, score.security_id)
        if run is None or security is None:
            raise ValueError("multi-factor score lineage is incomplete")
        if requested_runs and run.normalization_run_id not in requested_runs:
            continue
        if universe_id and run.universe_id != universe_id:
            continue
        _verify_prediction(
            session, prediction=prediction, score=score, security=security
        )
        _, feature_hashes = _verify_feature_lineage(
            session, prediction=prediction, run=run, score=score
        )
        source_hashes.update(feature_hashes)
        universe = session.get(UniverseDefinition, run.universe_id)
        if universe is None:
            raise ValueError("normalization run universe is missing")
        universe_snapshot = session.get(SourceSnapshot, universe.source_snapshot_id)
        if universe_snapshot is None or universe_snapshot.source_hash != universe.source_hash:
            raise ValueError("normalization universe source does not reproduce")
        source_hashes.add(universe.source_hash)
        reject_after_frozen_cutoff(score.asof_date)
        if HOLDOUT_START <= score.asof_date <= HOLDOUT_END:
            uses_holdout = True
            existing = session.scalar(
                select(ModelOutcome).where(
                    ModelOutcome.prediction_id == prediction.prediction_id
                )
            )
            if existing is not None:
                raise ValueError(
                    "holdout outcome already exists; the experiment can no longer be locked"
                )
        # Sprint 7 is already independently frozen by its passing closure.
        # Its comparison predictions are rebuilt only after this Sprint 8 lock,
        # so the lock binds the new multi-factor scores without re-reading the
        # previously evaluated price-only holdout.
        used_runs.add(run.normalization_run_id)
        prediction_ids.append(prediction.prediction_id)
        score_rows.append(
            {
                "multifactor_score_id": score.multifactor_score_id,
                "normalization_run_id": run.normalization_run_id,
                "prediction_id": prediction.prediction_id,
                "prediction_hash": prediction.immutable_hash,
                "score": str(score.final_score),
                "family_scores": score.family_scores_json,
                "component_coverage": str(score.component_coverage),
            }
        )
    if not score_rows:
        raise ValueError("no verified multi-factor predictions are available to lock")
    if not uses_holdout:
        raise ValueError(
            f"holdout lock inputs contain no {HOLDOUT_START.isoformat()} through "
            f"{HOLDOUT_END_TEXT} predictions"
        )
    if requested_runs - used_runs:
        raise ValueError("requested normalization runs are missing from lock inputs")
    outcome_ids = set(outcome_source_snapshot_ids)
    outcome_snapshots = list(
        session.scalars(
            select(SourceSnapshot).where(SourceSnapshot.snapshot_id.in_(outcome_ids))
        ).all()
    )
    if not outcome_ids or {row.snapshot_id for row in outcome_snapshots} != outcome_ids:
        raise ValueError("locked outcome source snapshots are missing")
    source_hashes.update(row.source_hash for row in outcome_snapshots)
    score_hash = hashlib.sha256(
        json.dumps(score_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return PreOutcomeLockInputs(
        normalization_run_ids=tuple(sorted(used_runs)),
        source_snapshot_hashes=tuple(sorted(source_hashes)),
        score_ledger_sha256=score_hash,
        prediction_ids=tuple(sorted(prediction_ids)),
    )


def load_verified_evaluation_ledger(
    session: Session,
    *,
    normalization_run_ids: Optional[Sequence[str]] = None,
    universe_id: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> VerifiedEvaluationLedger:
    """Load only database-backed scores, immutable predictions and outcomes."""

    if end_date is not None:
        reject_after_frozen_cutoff(end_date)

    query = select(MultiFactorPredictionLink).order_by(
        MultiFactorPredictionLink.prediction_id
    )
    links = list(session.scalars(query).all())
    requested_runs = set(normalization_run_ids or ())
    observations = []
    used_runs: set[str] = set()
    prediction_ids = []
    outcome_ids = []
    source_hashes: set[str] = set()
    score_rows = []
    holdout_times = []
    expected_by_cohort: dict[tuple[date, str], int] = {}
    evaluated_by_cohort: dict[tuple[date, str], int] = {}
    for link in links:
        score = session.get(MultiFactorScore, link.multifactor_score_id)
        prediction = session.get(ModelPrediction, link.prediction_id)
        if score is None or prediction is None:
            raise ValueError("multi-factor prediction link is orphaned")
        run = session.get(NormalizationRun, score.normalization_run_id)
        security = session.get(Security, score.security_id)
        if run is None or security is None:
            raise ValueError("multi-factor score lineage is incomplete")
        if requested_runs and run.normalization_run_id not in requested_runs:
            continue
        if universe_id and run.universe_id != universe_id:
            continue
        if start_date and score.asof_date < start_date:
            continue
        if end_date and score.asof_date > end_date:
            continue
        reject_after_frozen_cutoff(score.asof_date)
        if link.horizon != prediction.horizon:
            raise ValueError("multi-factor prediction link horizon conflicts")
        _verify_prediction(
            session, prediction=prediction, score=score, security=security
        )
        sector, feature_hashes = _verify_feature_lineage(
            session, prediction=prediction, run=run, score=score
        )
        source_hashes.update(feature_hashes)
        universe = session.get(UniverseDefinition, run.universe_id)
        if universe is None:
            raise ValueError("normalization run universe is missing")
        universe_snapshot = session.get(SourceSnapshot, universe.source_snapshot_id)
        if universe_snapshot is None or universe_snapshot.source_hash != universe.source_hash:
            raise ValueError("normalization universe source does not reproduce")
        source_hashes.add(universe.source_hash)
        cohort_key = (score.asof_date, prediction.horizon)
        expected_by_cohort[cohort_key] = expected_by_cohort.get(cohort_key, 0) + 1
        used_runs.add(run.normalization_run_id)
        prediction_ids.append(prediction.prediction_id)
        score_rows.append(
            {
                "multifactor_score_id": score.multifactor_score_id,
                "normalization_run_id": run.normalization_run_id,
                "prediction_id": prediction.prediction_id,
                "prediction_hash": prediction.immutable_hash,
                "score": str(score.final_score),
                "family_scores": score.family_scores_json,
                "component_coverage": str(score.component_coverage),
            }
        )
        outcome = session.scalar(
            select(ModelOutcome).where(ModelOutcome.prediction_id == prediction.prediction_id)
        )
        if outcome is None:
            continue
        evaluated_by_cohort[cohort_key] = evaluated_by_cohort.get(cohort_key, 0) + 1
        if prediction.horizon == "126d":
            price_prediction = session.scalar(
                select(ModelPrediction).where(
                    ModelPrediction.model_version == "baseline_v0.1",
                    ModelPrediction.security_id == score.security_id,
                    ModelPrediction.asof_date == score.asof_date,
                    ModelPrediction.horizon == "126d",
                )
            )
            if price_prediction is None:
                raise ValueError(
                    "frozen experiment is missing its Sprint 7 comparison prediction"
                )
            _verify_prediction_hash(
                session, prediction=price_prediction, security=security
            )
            source_hashes.update(
                _feature_set_source_hashes(
                    session, feature_set_id=price_prediction.feature_set_id
                )
            )
            price_outcome = session.scalar(
                select(ModelOutcome).where(
                    ModelOutcome.prediction_id == price_prediction.prediction_id
                )
            )
            if price_outcome is not None:
                source_hashes.update(
                    _verify_outcome(
                        session,
                        prediction=price_prediction,
                        security=security,
                        outcome=price_outcome,
                    )
                )
        source_hashes.update(
            _verify_outcome(
                session,
                prediction=prediction,
                security=security,
                outcome=outcome,
            )
        )
        outcome_ids.append(outcome.outcome_id)
        if HOLDOUT_START <= score.asof_date <= HOLDOUT_END:
            holdout_times.append(_utc(outcome.evaluated_at))
        delisted = False
        delisted = session.scalar(
            select(DelistingEvent.delisting_event_id)
            .where(DelistingEvent.security_id == score.security_id)
            .where(DelistingEvent.delisting_date <= outcome.exit_date)
            .limit(1)
        ) is not None
        missing_reasons = tuple(
            sorted(
                {
                    str(value.get("reason"))
                    for value in score.missingness_json.values()
                    if isinstance(value, dict) and value.get("reason")
                }
            )
        )
        observations.append(
            MultiFactorEvaluationObservation(
                security_id=score.security_id,
                ticker=security.ticker,
                prediction_date=score.asof_date,
                sector=sector,
                score=score.final_score,
                family_scores=_decimal_mapping(score.family_scores_json),
                component_coverage=score.component_coverage,
                missing_reasons=missing_reasons,
                horizon=prediction.horizon,
                excess_return=outcome.excess_return,
                realised_return=outcome.realised_return,
                benchmark_return=outcome.benchmark_return,
                max_drawdown=outcome.max_drawdown,
                delisted_outcome=delisted,
            )
        )
    failed_coverage = [
        (key, evaluated_by_cohort.get(key, 0), expected)
        for key, expected in sorted(expected_by_cohort.items())
        if Decimal(evaluated_by_cohort.get(key, 0)) / Decimal(expected)
        < Decimal("0.95")
    ]
    if failed_coverage:
        key, evaluated, expected = failed_coverage[0]
        raise ValueError(
            "multi-factor outcome coverage is below 0.95 for "
            f"{key[0].isoformat()} {key[1]}: {evaluated}/{expected}"
        )
    if not observations:
        raise ValueError("no verified multi-factor warehouse observations matched")
    if requested_runs - used_runs:
        raise ValueError(
            "requested normalization runs have no verified predictions: "
            + ", ".join(sorted(requested_runs - used_runs))
        )
    score_hash = hashlib.sha256(
        json.dumps(score_rows, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return VerifiedEvaluationLedger(
        observations=tuple(observations),
        normalization_run_ids=tuple(sorted(used_runs)),
        prediction_ids=tuple(sorted(prediction_ids)),
        outcome_ids=tuple(sorted(outcome_ids)),
        source_snapshot_hashes=tuple(sorted(source_hashes)),
        score_ledger_sha256=score_hash,
        earliest_holdout_evaluated_at=min(holdout_times) if holdout_times else None,
    )


def _component_evidence_refs(feature: Feature) -> tuple[str, ...]:
    values = []
    for row in feature.inputs_json.get("inputs", []):
        if not isinstance(row, dict):
            continue
        record_id = row.get("record_id")
        snapshot_id = row.get("source_snapshot_id")
        source_hash = row.get("source_hash")
        if record_id:
            values.append(f"record:{record_id}")
        if snapshot_id and source_hash:
            values.append(f"snapshot:{snapshot_id}#sha256={source_hash}")
    return tuple(sorted(set(values)))


def _verify_equivalent_outcomes(
    left: ModelOutcome, right: ModelOutcome
) -> None:
    fields = (
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
    )
    if any(getattr(left, field) != getattr(right, field) for field in fields):
        raise ValueError("price and multi-factor outcomes are not identical")


def load_verified_comparison_ledger(
    session: Session,
    *,
    normalization_run_ids: Optional[Sequence[str]] = None,
    universe_id: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    price_model_version: str = "baseline_v0.1",
) -> VerifiedComparisonLedger:
    """Build the three-model comparison from exact verified DB intersections."""

    evaluation = load_verified_evaluation_ledger(
        session,
        normalization_run_ids=normalization_run_ids,
        universe_id=universe_id,
        start_date=start_date,
        end_date=end_date,
    )
    primary = [row for row in evaluation.observations if row.horizon == "126d"]
    if not primary:
        raise ValueError("comparison requires verified 126d multi-factor outcomes")
    output = []
    price_prediction_ids = []
    price_outcome_ids = []
    extra_source_hashes = set(evaluation.source_snapshot_hashes)
    for row in primary:
        price_prediction = session.scalar(
            select(ModelPrediction).where(
                ModelPrediction.model_version == price_model_version,
                ModelPrediction.security_id == row.security_id,
                ModelPrediction.asof_date == row.prediction_date,
                ModelPrediction.horizon == "126d",
            )
        )
        if price_prediction is None:
            continue
        security = session.get(Security, row.security_id)
        if security is None:
            raise ValueError("comparison security is missing")
        price_drivers = tuple(
            session.scalars(
                select(ScoreDriver)
                .where(ScoreDriver.prediction_id == price_prediction.prediction_id)
                .order_by(ScoreDriver.driver_name)
            ).all()
        )
        price_hash = immutable_prediction_hash(
            model_version=price_prediction.model_version,
            ticker=security.ticker,
            security_id=price_prediction.security_id,
            asof_date=price_prediction.asof_date,
            horizon=price_prediction.horizon,
            feature_set_id=price_prediction.feature_set_id,
            score=SimpleNamespace(
                score=price_prediction.score,
                confidence=price_prediction.confidence,
                action_label=price_prediction.action_label,
                drivers=price_drivers,
            ),
        )
        if price_hash != price_prediction.immutable_hash:
            raise ValueError("price-only prediction immutable hash is invalid")
        price_outcome = session.scalar(
            select(ModelOutcome).where(
                ModelOutcome.prediction_id == price_prediction.prediction_id
            )
        )
        if price_outcome is None:
            continue
        extra_source_hashes.update(
            _verify_outcome(
                session,
                prediction=price_prediction,
                security=security,
                outcome=price_outcome,
            )
        )
        multifactor_link = session.scalar(
            select(MultiFactorPredictionLink)
            .join(
                MultiFactorScore,
                MultiFactorPredictionLink.multifactor_score_id
                == MultiFactorScore.multifactor_score_id,
            )
            .join(
                ModelPrediction,
                MultiFactorPredictionLink.prediction_id
                == ModelPrediction.prediction_id,
            )
            .where(
                MultiFactorScore.security_id == row.security_id,
                MultiFactorScore.asof_date == row.prediction_date,
                MultiFactorPredictionLink.horizon == "126d",
            )
        )
        if multifactor_link is None:
            raise ValueError("multi-factor comparison prediction link is missing")
        score = session.get(MultiFactorScore, multifactor_link.multifactor_score_id)
        multifactor_outcome = session.scalar(
            select(ModelOutcome).where(
                ModelOutcome.prediction_id == multifactor_link.prediction_id
            )
        )
        if score is None or multifactor_outcome is None:
            raise ValueError("multi-factor comparison lineage is incomplete")
        _verify_equivalent_outcomes(price_outcome, multifactor_outcome)
        normalized_rows = list(
            session.scalars(
                select(NormalizedFeature).where(
                    NormalizedFeature.normalization_run_id
                    == score.normalization_run_id,
                    NormalizedFeature.security_id == row.security_id,
                )
            ).all()
        )
        loaded_features = {
            row.feature_id: row
            for row in session.scalars(
                select(Feature).where(
                    Feature.feature_id.in_(
                        [value.feature_id for value in normalized_rows]
                    )
                )
            )
        } if normalized_rows else {}
        features = {
            value.feature_id: loaded_features.get(value.feature_id)
            for value in normalized_rows
        }
        components = tuple(
            AttributionComponent(
                name=value.feature_name,
                family=value.family,
                contribution=value.contribution,
                raw_value=value.raw_value,
                directed_value=value.directed_value,
                normalization_scope=value.normalization_scope,
                group_label=value.group_label,
                group_count=value.group_count,
                group_mean=value.group_mean,
                group_std=value.group_std,
                missing_reason=value.missing_reason,
                evidence_refs=_component_evidence_refs(features[value.feature_id]),
            )
            for value in normalized_rows
            if features[value.feature_id] is not None
        )
        output.append(
            MultiModelObservation(
                security_id=row.security_id,
                ticker=row.ticker,
                prediction_date=row.prediction_date,
                sector=row.sector,
                price_score=price_prediction.score,
                multifactor_score=score.final_score,
                family_z=_decimal_mapping(score.family_z_json),
                family_scores=_decimal_mapping(score.family_scores_json),
                missing_data_flags=dict(score.missingness_json),
                components=components,
                excess_return=row.excess_return,
                realised_return=row.realised_return,
                benchmark_return=row.benchmark_return,
                max_drawdown=row.max_drawdown,
                delisted_outcome=row.delisted_outcome,
            )
        )
        price_prediction_ids.append(price_prediction.prediction_id)
        price_outcome_ids.append(price_outcome.outcome_id)
    rebound = VerifiedEvaluationLedger(
        observations=evaluation.observations,
        normalization_run_ids=evaluation.normalization_run_ids,
        prediction_ids=evaluation.prediction_ids,
        outcome_ids=evaluation.outcome_ids,
        source_snapshot_hashes=tuple(sorted(extra_source_hashes)),
        score_ledger_sha256=evaluation.score_ledger_sha256,
        earliest_holdout_evaluated_at=evaluation.earliest_holdout_evaluated_at,
    )
    return VerifiedComparisonLedger(
        observations=tuple(output),
        evaluation_ledger=rebound,
        price_prediction_ids=tuple(sorted(price_prediction_ids)),
        price_outcome_ids=tuple(sorted(price_outcome_ids)),
    )
