"""Sealed monthly cohort ledger for untouched forward model evidence."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.models import (
    Feature,
    FeatureSet,
    ModelOutcome,
    ModelPrediction,
    MultiFactorPredictionLink,
    MultiFactorScore,
    NormalizationRun,
    ScoreDriver,
    ShadowOutcomeRecord,
    ShadowPredictionBatch,
    ShadowPredictionRecord,
    SourceSnapshot,
    UniverseDefinition,
)
from quantfore_research.scoring.baseline import BaselineScore, ScoreDriver as Driver
from quantfore_research.scoring.ledger import immutable_prediction_hash
from quantfore_research.validation.leakage import (
    PointInTimeSecurityContext,
    expected_point_in_time_cohort,
    validate_stored_feature_inputs,
)


SHADOW_HORIZONS = ("21d", "63d", "126d", "252d")
LOCKED_SHADOW_DATES = (
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
)
PRIMARY_HORIZON = "126d"
PRODUCT_LABEL_STATUS = "WITHHELD_RESEARCH_ONLY"
SHADOW_ID_NAMESPACE = uuid.UUID("04f75834-117e-5c5a-a10d-8cc4a501cbeb")
REQUIRED_IMPLEMENTATION_BINDINGS = (
    "code_commit",
    "formula_ledger_sha256",
    "classification_ledger_sha256",
    "source_manifest_sha256",
    "evaluation_code_sha256",
    "report_schema_sha256",
    "portfolio_notional_usd",
)


@dataclass(frozen=True)
class ShadowBatchResult:
    batch_id: str
    batch_hash: str
    expected_member_count: int
    scored_count: int
    excluded_count: int
    created: bool


@dataclass(frozen=True)
class _RecordCandidate:
    shadow_prediction_id: str
    security_id: str
    ticker: str
    classification_branch: str
    disposition: str
    research_score: Optional[Decimal]
    research_confidence: Optional[Decimal]
    research_label: Optional[str]
    exclusions: list[dict[str, Any]]
    drivers: list[dict[str, Any]]
    prediction_ids: dict[str, str]
    input_lineage: dict[str, Any]
    record_hash: str


def _utc(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    return value.astimezone(timezone.utc)


def _stored_utc(value: datetime) -> datetime:
    """Normalize database timestamps; SQLite drops the stored timezone marker."""

    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _datetime_text(value: datetime) -> str:
    return _stored_utc(value).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return _decimal_text(value)
    if isinstance(value, datetime):
        return _datetime_text(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _json_ready(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_ready(value), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _is_git_commit(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 40:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _commit_matches(value: Optional[str], *full_commits: str) -> bool:
    if not value or "-dirty-" in value:
        return False
    return any(commit.startswith(value) for commit in full_commits)


def load_executable_shadow_lock(path: Path) -> tuple[dict[str, Any], str]:
    """Load an exact lock file and return its parsed data and byte hash."""

    payload = path.read_bytes()
    try:
        lock = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError(f"executable lock is not valid JSON: {path}") from exc
    if not isinstance(lock, dict):
        raise ValueError("executable lock must contain one JSON object")
    return lock, hashlib.sha256(payload).hexdigest()


def _validate_executable_lock(
    lock: Mapping[str, Any],
    *,
    prediction_date: date,
    universe_id: str,
    normalization_version: str,
    code_commit: str,
) -> dict[str, Any]:
    if lock.get("status") != "EXECUTABLE_LOCKED":
        raise ValueError("shadow predictions require status=EXECUTABLE_LOCKED")
    if lock.get("claims_eligible") is not False:
        raise ValueError("shadow executable lock must retain claims_eligible=false")
    if lock.get("executable_for_shadow_predictions") is not True:
        raise ValueError("lock does not authorize shadow predictions")
    if lock.get("executable_for_outcome_evaluation") is not True:
        raise ValueError("lock must bind and authorize future outcome evaluation code")
    if not _is_sha256(lock.get("design_lock_sha256")):
        raise ValueError("lock must bind the immutable Model V2 design lock")

    model = lock.get("model")
    if not isinstance(model, dict) or not str(model.get("version", "")).strip():
        raise ValueError("lock must declare model.version")
    if model.get("normalization_version") != normalization_version:
        raise ValueError("lock normalization version does not match stored run")
    if tuple(model.get("required_horizons", ())) != SHADOW_HORIZONS:
        raise ValueError(f"lock required_horizons must equal {SHADOW_HORIZONS!r}")
    family_weights = model.get("family_weights")
    if not isinstance(family_weights, dict) or set(family_weights) != {
        "value",
        "quality",
        "growth",
        "momentum",
        "risk",
    }:
        raise ValueError("lock must declare all five factor family weights")
    if any(Decimal(str(value)) != Decimal("0.2") for value in family_weights.values()):
        raise ValueError("shadow Model V2 family weights must remain 20% each")
    if model.get("required_family_count") != 5:
        raise ValueError("shadow Model V2 requires all five factor families")
    if Decimal(str(model.get("minimum_component_coverage"))) != Decimal("0.8"):
        raise ValueError("shadow Model V2 component coverage must remain 80%")

    universe = lock.get("universe")
    if not isinstance(universe, dict) or universe.get("universe_id") != universe_id:
        raise ValueError("lock universe does not match requested shadow universe")

    schedule = lock.get("prediction_schedule")
    if not isinstance(schedule, dict) or not isinstance(schedule.get("dates"), list):
        raise ValueError("lock must contain a fixed prediction_schedule.dates list")
    dates = schedule["dates"]
    if len(dates) != len(set(dates)) or dates != sorted(dates):
        raise ValueError("prediction schedule dates must be unique and sorted")
    if tuple(dates) != LOCKED_SHADOW_DATES:
        raise ValueError("prediction schedule must retain the locked 24-month window")
    if schedule.get("sha256") != _hash_json(dates):
        raise ValueError("prediction schedule hash does not match its dates")
    if prediction_date.isoformat() not in dates:
        raise ValueError("prediction date is outside the executable lock schedule")

    implementation = lock.get("implementation")
    if not isinstance(implementation, dict):
        raise ValueError("lock must contain implementation bindings")
    missing = [
        field
        for field in REQUIRED_IMPLEMENTATION_BINDINGS
        if implementation.get(field) in (None, "")
    ]
    if missing:
        raise ValueError(
            "executable lock has null implementation bindings: "
            + ", ".join(missing)
        )
    if implementation["code_commit"] != code_commit:
        raise ValueError(
            "locked implementation commit does not match executable lock"
        )
    if not _is_git_commit(implementation["code_commit"]):
        raise ValueError("implementation.code_commit must be a full Git commit")
    for field in REQUIRED_IMPLEMENTATION_BINDINGS:
        if field.endswith("_sha256") and not _is_sha256(implementation[field]):
            raise ValueError(f"implementation.{field} must be a SHA-256 hash")
    if Decimal(str(implementation["portfolio_notional_usd"])) <= 0:
        raise ValueError("implementation.portfolio_notional_usd must be positive")
    return dict(model)


def _snapshot_reference(snapshot: SourceSnapshot) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot.snapshot_id,
        "source_hash": snapshot.source_hash,
        "vendor": snapshot.vendor,
        "dataset": snapshot.dataset,
        "retrieved_at": _datetime_text(snapshot.retrieved_at),
        "storage_uri": snapshot.storage_uri,
    }


def _load_source_snapshots(
    session: Session,
    *,
    snapshot_ids: set[str],
    prediction_timestamp: datetime,
) -> dict[str, SourceSnapshot]:
    snapshots = {
        row.snapshot_id: row
        for row in session.scalars(
            select(SourceSnapshot).where(SourceSnapshot.snapshot_id.in_(snapshot_ids))
        ).all()
    }
    missing = sorted(snapshot_ids - set(snapshots))
    if missing:
        raise ValueError(
            f"shadow input references unknown source snapshots: {missing!r}"
        )
    late = sorted(
        row.snapshot_id
        for row in snapshots.values()
        if _stored_utc(row.retrieved_at) > prediction_timestamp
    )
    if late:
        raise ValueError(
            f"source snapshots were retrieved after prediction time: {late!r}"
        )
    return snapshots


def _feature_ledger_hash(rows: Sequence[Feature]) -> str:
    return _hash_json(
        [
            {
                "feature_id": row.feature_id,
                "feature_set_id": row.feature_set_id,
                "feature_name": row.feature_name,
                "value": row.value,
                "raw_value": row.raw_value,
                "available_at": row.available_at,
                "version": row.version,
                "family": row.family,
                "formula_version": row.formula_version,
                "direction": row.direction,
                "applicability_status": row.applicability_status,
                "missing_reason": row.missing_reason,
                "inputs": row.inputs_json,
                "source_snapshot_id": row.source_snapshot_id,
                "source_hash": row.source_hash,
            }
            for row in sorted(
                rows, key=lambda item: (item.feature_name, item.feature_id)
            )
        ]
    )


def _score_hash(score: MultiFactorScore) -> str:
    return _hash_json(
        {
            "multifactor_score_id": score.multifactor_score_id,
            "normalization_run_id": score.normalization_run_id,
            "security_id": score.security_id,
            "asof_date": score.asof_date,
            "eligible": score.eligible,
            "final_score": score.final_score,
            "composite_z": score.composite_z,
            "applicable_component_count": score.applicable_component_count,
            "valid_component_count": score.valid_component_count,
            "component_coverage": score.component_coverage,
            "available_family_count": score.available_family_count,
            "family_z": score.family_z_json,
            "family_scores": score.family_scores_json,
            "family_available": score.family_available_json,
            "weights": score.renormalized_weights_json,
            "missingness": score.missingness_json,
        }
    )


def _exclusions(
    score: MultiFactorScore,
    *,
    required_family_count: int,
    minimum_component_coverage: Decimal,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    missingness = score.missingness_json
    if not isinstance(missingness, dict):
        raise ValueError("score missingness must be a JSON object")
    for feature_name, state in sorted(missingness.items()):
        if not isinstance(state, dict):
            raise ValueError("score missingness entries must be JSON objects")
        status = str(state.get("status", "")).strip()
        reason = str(state.get("reason", "")).strip()
        if status != "APPLICABLE":
            if not status or not reason:
                raise ValueError("unavailable components require status and reason")
            rows.append(
                {
                    "scope": "component",
                    "feature_name": feature_name,
                    "status": status,
                    "reason": reason,
                }
            )
    if score.available_family_count < required_family_count:
        rows.append(
            {
                "scope": "eligibility",
                "reason": "INSUFFICIENT_FAMILY_COVERAGE",
                "actual": score.available_family_count,
                "required": required_family_count,
            }
        )
    if score.component_coverage < minimum_component_coverage:
        rows.append(
            {
                "scope": "eligibility",
                "reason": "INSUFFICIENT_COMPONENT_COVERAGE",
                "actual": score.component_coverage,
                "required": minimum_component_coverage,
            }
        )
    return _json_ready(rows)


def _driver_payload(rows: Sequence[ScoreDriver]) -> list[dict[str, Any]]:
    return [
        {
            "driver_name": row.driver_name,
            "contribution": row.contribution,
            "evidence_uri": row.evidence_uri,
        }
        for row in sorted(rows, key=lambda item: item.driver_name)
    ]


def _prediction_payload(
    session: Session,
    *,
    predictions: Sequence[ModelPrediction],
    context: PointInTimeSecurityContext,
    feature_set_id: str,
    model_version: str,
) -> tuple[dict[str, str], list[dict[str, Any]], ModelPrediction]:
    by_horizon = {row.horizon: row for row in predictions}
    if set(by_horizon) != set(SHADOW_HORIZONS) or len(predictions) != len(
        SHADOW_HORIZONS
    ):
        raise ValueError("eligible shadow member requires all four locked horizons")
    driver_payloads: dict[str, list[dict[str, Any]]] = {}
    for horizon, prediction in by_horizon.items():
        if (
            prediction.model_version != model_version
            or prediction.security_id != context.security.security_id
            or prediction.asof_date != context.prediction_timestamp.date()
            or prediction.feature_set_id != feature_set_id
        ):
            raise ValueError("stored prediction conflicts with shadow batch identity")
        drivers = session.scalars(
            select(ScoreDriver)
            .where(ScoreDriver.prediction_id == prediction.prediction_id)
            .order_by(ScoreDriver.driver_name)
        ).all()
        payload = _driver_payload(drivers)
        driver_payloads[horizon] = payload
        baseline = BaselineScore(
            score=prediction.score,
            confidence=prediction.confidence,
            action_label=prediction.action_label,
            drivers=tuple(
                Driver(
                    driver_name=row["driver_name"],
                    contribution=Decimal(str(row["contribution"])),
                    evidence_uri=row["evidence_uri"],
                )
                for row in payload
            ),
        )
        expected_hash = immutable_prediction_hash(
            model_version=prediction.model_version,
            ticker=context.security.ticker,
            security_id=prediction.security_id,
            asof_date=prediction.asof_date,
            horizon=prediction.horizon,
            feature_set_id=prediction.feature_set_id,
            score=baseline,
        )
        if prediction.immutable_hash != expected_hash:
            raise ValueError("stored prediction immutable hash does not reproduce")
    primary = by_horizon[PRIMARY_HORIZON]
    for prediction in by_horizon.values():
        if (
            prediction.score != primary.score
            or prediction.confidence != primary.confidence
            or prediction.action_label != primary.action_label
        ):
            raise ValueError("shadow horizons do not share one frozen research score")
    canonical_drivers = _json_ready(driver_payloads[PRIMARY_HORIZON])
    if any(
        _json_ready(value) != canonical_drivers
        for value in driver_payloads.values()
    ):
        raise ValueError("shadow horizons do not share the same score drivers")
    expected_driver_names = {
        f"family:{family}"
        for family in ("value", "quality", "growth", "momentum", "risk")
    }
    if {row["driver_name"] for row in canonical_drivers} != expected_driver_names:
        raise ValueError(
            "scored shadow member requires one driver for each factor family"
        )
    return (
        {horizon: by_horizon[horizon].prediction_id for horizon in SHADOW_HORIZONS},
        canonical_drivers,
        primary,
    )


def _record_hash_payload(
    *,
    shadow_prediction_id: str,
    batch_id: str,
    security_id: str,
    ticker: str,
    classification_branch: str,
    disposition: str,
    research_score: Optional[Decimal],
    research_confidence: Optional[Decimal],
    research_label: Optional[str],
    exclusions: list[dict[str, Any]],
    drivers: list[dict[str, Any]],
    prediction_ids: dict[str, str],
    input_lineage: dict[str, Any],
) -> dict[str, Any]:
    return {
        "shadow_prediction_id": shadow_prediction_id,
        "batch_id": batch_id,
        "security_id": security_id,
        "ticker": ticker,
        "classification_branch": classification_branch,
        "disposition": disposition,
        "research_score": research_score,
        "research_confidence": research_confidence,
        "research_label": research_label,
        "product_label": None,
        "product_label_status": PRODUCT_LABEL_STATUS,
        "exclusions": exclusions,
        "drivers": drivers,
        "prediction_ids": prediction_ids,
        "input_lineage": input_lineage,
    }


def create_shadow_prediction_batch(
    session: Session,
    *,
    universe_id: str,
    normalization_run_id: str,
    prediction_timestamp: datetime,
    executable_lock: Mapping[str, Any],
    executable_lock_uri: str,
    executable_lock_hash: str,
    code_commit: str,
    execution_commit: str,
    recorded_at: Optional[datetime] = None,
) -> ShadowBatchResult:
    """Seal one complete monthly shadow cohort or return its identical prior seal."""

    timestamp = _utc(prediction_timestamp, field="prediction_timestamp")
    recorded = _utc(recorded_at or datetime.now(timezone.utc), field="recorded_at")
    if recorded < timestamp:
        raise ValueError("shadow batch cannot be recorded before prediction_timestamp")
    if not executable_lock_uri.strip():
        raise ValueError("executable_lock_uri is required")
    if not _is_sha256(executable_lock_hash):
        raise ValueError("executable_lock_hash must be a SHA-256 hash")
    if not _is_git_commit(code_commit):
        raise ValueError("code_commit must be a full Git commit")
    if not _is_git_commit(execution_commit):
        raise ValueError("execution_commit must be a full Git commit")

    run = session.get(NormalizationRun, normalization_run_id)
    if run is None:
        raise ValueError(f"unknown normalization run: {normalization_run_id}")
    if run.universe_id != universe_id or run.asof_date != timestamp.date():
        raise ValueError("normalization run does not match shadow universe/date")
    if not _commit_matches(run.code_commit, code_commit, execution_commit):
        raise ValueError(
            "normalization run code revision does not match locked or execution commit"
        )
    model = _validate_executable_lock(
        executable_lock,
        prediction_date=timestamp.date(),
        universe_id=universe_id,
        normalization_version=run.version,
        code_commit=code_commit,
    )
    model_version = model["version"]

    universe = session.get(UniverseDefinition, universe_id)
    if universe is None:
        raise ValueError(f"unknown universe: {universe_id}")
    contexts = expected_point_in_time_cohort(
        session,
        universe_id=universe_id,
        prediction_timestamp=timestamp,
    )
    context_by_security = {row.security.security_id: row for row in contexts}
    scores = session.scalars(
        select(MultiFactorScore)
        .where(MultiFactorScore.normalization_run_id == normalization_run_id)
        .order_by(MultiFactorScore.security_id)
    ).all()
    score_by_security = {row.security_id: row for row in scores}
    if len(score_by_security) != len(scores) or set(score_by_security) != set(
        context_by_security
    ):
        raise ValueError(
            "shadow scores must account for every expected member exactly once"
        )

    feature_set_ids = tuple(run.source_feature_set_ids_json)
    if not feature_set_ids or len(feature_set_ids) != len(set(feature_set_ids)):
        raise ValueError("normalization run must bind unique source feature sets")
    feature_sets = {
        row.feature_set_id: row
        for row in session.scalars(
            select(FeatureSet).where(FeatureSet.feature_set_id.in_(feature_set_ids))
        ).all()
    }
    if set(feature_sets) != set(feature_set_ids):
        raise ValueError("normalization run references unknown feature sets")
    feature_rows = session.scalars(
        select(Feature)
        .where(Feature.feature_set_id.in_(feature_set_ids))
        .order_by(Feature.security_id, Feature.feature_name, Feature.feature_id)
    ).all()
    features_by_security: dict[str, list[Feature]] = {}
    feature_set_ids_by_security: dict[str, set[str]] = {}
    for row in feature_rows:
        features_by_security.setdefault(row.security_id, []).append(row)
        feature_set_ids_by_security.setdefault(row.security_id, set()).add(
            row.feature_set_id
        )
    if set(features_by_security) != set(context_by_security):
        raise ValueError(
            "source features must account for every expected shadow member"
        )
    if any(len(values) != 1 for values in feature_set_ids_by_security.values()):
        raise ValueError("each shadow member must bind exactly one feature set")
    for rows in features_by_security.values():
        validate_stored_feature_inputs(rows, prediction_timestamp=timestamp)
    for feature_set in feature_sets.values():
        if feature_set.code_commit and not _commit_matches(
            feature_set.code_commit, code_commit, execution_commit
        ):
            raise ValueError(
                "feature set code revision does not match locked or execution commit"
            )

    score_ids = {row.multifactor_score_id for row in scores}
    links = session.scalars(
        select(MultiFactorPredictionLink)
        .where(MultiFactorPredictionLink.multifactor_score_id.in_(score_ids))
        .order_by(
            MultiFactorPredictionLink.multifactor_score_id,
            MultiFactorPredictionLink.horizon,
        )
    ).all()
    links_by_score: dict[str, list[MultiFactorPredictionLink]] = {}
    for link in links:
        linked_prediction = session.get(ModelPrediction, link.prediction_id)
        if linked_prediction is None or linked_prediction.horizon != link.horizon:
            raise ValueError("multi-factor link horizon conflicts with its prediction")
        links_by_score.setdefault(link.multifactor_score_id, []).append(link)
    prediction_ids = {link.prediction_id for link in links}
    predictions = {
        row.prediction_id: row
        for row in session.scalars(
            select(ModelPrediction).where(
                ModelPrediction.prediction_id.in_(prediction_ids)
            )
        ).all()
    }
    if set(predictions) != prediction_ids:
        raise ValueError("shadow prediction links reference unknown predictions")

    snapshot_ids = {universe.source_snapshot_id}
    snapshot_ids.update(row.membership.source_snapshot_id for row in contexts)
    snapshot_ids.update(row.ticker_alias.source_snapshot_id for row in contexts)
    snapshot_ids.update(row.source_snapshot_id for row in feature_sets.values())
    snapshot_ids.update(row.source_snapshot_id for row in feature_rows)
    nested_snapshot_references: list[tuple[str, str]] = []
    for feature in feature_rows:
        for item in (feature.inputs_json or {}).get("inputs", []):
            if not isinstance(item, Mapping):
                continue
            snapshot_id = item.get("source_snapshot_id")
            source_hash = item.get("source_hash")
            if snapshot_id:
                snapshot_ids.add(str(snapshot_id))
                nested_snapshot_references.append(
                    (str(snapshot_id), str(source_hash or ""))
                )
    snapshots = _load_source_snapshots(
        session,
        snapshot_ids=snapshot_ids,
        prediction_timestamp=timestamp,
    )
    source_hash_references = [
        (universe.source_snapshot_id, universe.source_hash),
        *(
            (row.membership.source_snapshot_id, row.membership.source_hash)
            for row in contexts
        ),
        *(
            (row.ticker_alias.source_snapshot_id, row.ticker_alias.source_hash)
            for row in contexts
        ),
        *((row.source_snapshot_id, row.source_hash) for row in feature_rows),
        *nested_snapshot_references,
    ]
    mismatched_source_hashes = sorted(
        snapshot_id
        for snapshot_id, source_hash in source_hash_references
        if snapshots[snapshot_id].source_hash != source_hash
    )
    if mismatched_source_hashes:
        raise ValueError(
            "source snapshot hashes conflict with stored lineage: "
            f"{mismatched_source_hashes!r}"
        )
    input_manifest = {
        "universe": {
            "universe_id": universe_id,
            "definition_source_snapshot_id": universe.source_snapshot_id,
            "definition_source_hash": universe.source_hash,
        },
        "normalization_run": {
            "normalization_run_id": normalization_run_id,
            "version": run.version,
            "input_hash": run.input_hash,
            "source_feature_set_ids": sorted(feature_set_ids),
            "code_commit": run.code_commit,
        },
        "code": {
            "locked_implementation_commit": code_commit,
            "execution_commit": execution_commit,
        },
        "source_snapshots": [
            _snapshot_reference(snapshots[snapshot_id])
            for snapshot_id in sorted(snapshots)
        ],
        "executable_lock": {
            "uri": executable_lock_uri,
            "sha256": executable_lock_hash,
            "design_lock_sha256": executable_lock["design_lock_sha256"],
        },
    }

    batch_id = "shadow-batch-" + str(
        uuid.uuid5(
            SHADOW_ID_NAMESPACE,
            f"{model_version}|{universe_id}|{timestamp.date()}|{executable_lock_hash}",
        )
    )
    existing_batch = session.get(ShadowPredictionBatch, batch_id)
    if existing_batch is not None:
        recorded = _stored_utc(existing_batch.recorded_at)
    required_family_count = int(model["required_family_count"])
    minimum_component_coverage = Decimal(str(model["minimum_component_coverage"]))
    candidates: list[_RecordCandidate] = []
    for security_id in sorted(context_by_security):
        context = context_by_security[security_id]
        score = score_by_security[security_id]
        if score.asof_date != timestamp.date():
            raise ValueError("score date does not match shadow prediction date")
        feature_set_id = next(iter(feature_set_ids_by_security[security_id]))
        feature_set = feature_sets[feature_set_id]
        if feature_set.asof_date != timestamp.date():
            raise ValueError("feature set date does not match shadow prediction date")
        branch = str(feature_set.config_json.get("classification_branch", "")).strip()
        if not branch:
            raise ValueError(
                "every shadow member requires classification_branch evidence"
            )
        feature_ledger_hash = _feature_ledger_hash(features_by_security[security_id])
        score_ledger_hash = _score_hash(score)
        member_links = links_by_score.get(score.multifactor_score_id, [])
        member_predictions = [predictions[row.prediction_id] for row in member_links]
        exclusions = _exclusions(
            score,
            required_family_count=required_family_count,
            minimum_component_coverage=minimum_component_coverage,
        )
        if score.eligible:
            if score.final_score is None:
                raise ValueError("eligible shadow score is missing final_score")
            if score.available_family_count != required_family_count:
                raise ValueError(
                    "eligible shadow score does not contain all five families"
                )
            if set(score.family_available_json) != set(
                model["family_weights"]
            ) or not all(score.family_available_json.values()):
                raise ValueError(
                    "eligible shadow score family availability is incomplete"
                )
            if score.component_coverage < minimum_component_coverage:
                raise ValueError(
                    "eligible shadow score is below locked component coverage"
                )
            expected_weights = {
                key: Decimal(str(value))
                for key, value in model["family_weights"].items()
            }
            stored_weights = {
                key: Decimal(str(value))
                for key, value in score.renormalized_weights_json.items()
            }
            if stored_weights != expected_weights:
                raise ValueError("eligible shadow score changed locked family weights")
            prediction_map, drivers, primary = _prediction_payload(
                session,
                predictions=member_predictions,
                context=context,
                feature_set_id=feature_set_id,
                model_version=model_version,
            )
            if primary.score != score.final_score:
                raise ValueError("primary prediction score does not match score ledger")
            if primary.confidence != score.component_coverage:
                raise ValueError(
                    "primary prediction confidence does not match coverage"
                )
            disposition = "SCORED"
            research_score = primary.score
            research_confidence = primary.confidence
            research_label = primary.action_label
            exclusions = []
        else:
            if member_predictions:
                raise ValueError("excluded shadow member cannot have model predictions")
            if not exclusions:
                raise ValueError(
                    "excluded shadow member requires explicit reason codes"
                )
            disposition = "EXCLUDED"
            research_score = None
            research_confidence = None
            research_label = None
            prediction_map = {}
            drivers = []
        lineage = {
            "membership": {
                "membership_id": context.membership.membership_id,
                "source_snapshot_id": context.membership.source_snapshot_id,
                "source_hash": context.membership.source_hash,
            },
            "ticker_alias": {
                "ticker_alias_id": context.ticker_alias.ticker_alias_id,
                "source_snapshot_id": context.ticker_alias.source_snapshot_id,
                "source_hash": context.ticker_alias.source_hash,
            },
            "feature_set": {
                "feature_set_id": feature_set_id,
                "source_snapshot_id": feature_set.source_snapshot_id,
                "source_hash": snapshots[feature_set.source_snapshot_id].source_hash,
                "feature_ledger_sha256": feature_ledger_hash,
            },
            "score": {
                "multifactor_score_id": score.multifactor_score_id,
                "score_ledger_sha256": score_ledger_hash,
            },
            "classification": {
                "branch": branch,
                "classification_ledger_sha256": executable_lock["implementation"][
                    "classification_ledger_sha256"
                ],
            },
            "prediction_hashes": {
                horizon: predictions[prediction_id].immutable_hash
                for horizon, prediction_id in prediction_map.items()
            },
        }
        shadow_prediction_id = "shadow-record-" + str(
            uuid.uuid5(SHADOW_ID_NAMESPACE, f"{batch_id}|{security_id}")
        )
        payload = _record_hash_payload(
            shadow_prediction_id=shadow_prediction_id,
            batch_id=batch_id,
            security_id=security_id,
            ticker=context.ticker_alias.ticker,
            classification_branch=branch,
            disposition=disposition,
            research_score=research_score,
            research_confidence=research_confidence,
            research_label=research_label,
            exclusions=exclusions,
            drivers=drivers,
            prediction_ids=prediction_map,
            input_lineage=lineage,
        )
        candidates.append(
            _RecordCandidate(
                shadow_prediction_id=shadow_prediction_id,
                security_id=security_id,
                ticker=context.ticker_alias.ticker,
                classification_branch=branch,
                disposition=disposition,
                research_score=research_score,
                research_confidence=research_confidence,
                research_label=research_label,
                exclusions=exclusions,
                drivers=drivers,
                prediction_ids=prediction_map,
                input_lineage=lineage,
                record_hash=_hash_json(payload),
            )
        )

    scored_count = sum(row.disposition == "SCORED" for row in candidates)
    excluded_count = len(candidates) - scored_count
    batch_payload = {
        "batch_id": batch_id,
        "model_version": model_version,
        "universe_id": universe_id,
        "normalization_run_id": normalization_run_id,
        "prediction_date": timestamp.date(),
        "prediction_timestamp": timestamp,
        "recorded_at": recorded,
        "executable_lock_uri": executable_lock_uri,
        "executable_lock_hash": executable_lock_hash,
        "code_commit": code_commit,
        "execution_commit": execution_commit,
        "input_manifest": input_manifest,
        "expected_member_count": len(candidates),
        "scored_count": scored_count,
        "excluded_count": excluded_count,
        "claims_eligible": False,
        "outcome_evaluation_authorized": True,
        "product_label_policy": PRODUCT_LABEL_STATUS,
        "record_hashes": [row.record_hash for row in candidates],
    }
    batch_hash = _hash_json(batch_payload)
    if existing_batch is not None:
        if existing_batch.batch_hash != batch_hash:
            raise ValueError("shadow batch already exists with different sealed inputs")
        stored_records = session.scalars(
            select(ShadowPredictionRecord)
            .where(ShadowPredictionRecord.batch_id == batch_id)
            .order_by(ShadowPredictionRecord.security_id)
        ).all()
        if len(stored_records) != len(candidates):
            raise ValueError("sealed shadow batch record count no longer reconciles")
        candidate_hashes = [row.record_hash for row in candidates]
        stored_hashes = []
        for row in stored_records:
            reproduced_hash = _hash_json(
                _record_hash_payload(
                    shadow_prediction_id=row.shadow_prediction_id,
                    batch_id=row.batch_id,
                    security_id=row.security_id,
                    ticker=row.ticker,
                    classification_branch=row.classification_branch,
                    disposition=row.disposition,
                    research_score=row.research_score,
                    research_confidence=row.research_confidence,
                    research_label=row.research_label,
                    exclusions=row.exclusions_json,
                    drivers=row.drivers_json,
                    prediction_ids=row.prediction_ids_json,
                    input_lineage=row.input_lineage_json,
                )
            )
            if reproduced_hash != row.record_hash:
                raise ValueError(
                    "sealed shadow member row no longer reproduces its hash"
                )
            stored_hashes.append(row.record_hash)
        if stored_hashes != candidate_hashes:
            raise ValueError("sealed shadow member hashes conflict with current inputs")
        return ShadowBatchResult(
            batch_id=batch_id,
            batch_hash=batch_hash,
            expected_member_count=existing_batch.expected_member_count,
            scored_count=existing_batch.scored_count,
            excluded_count=existing_batch.excluded_count,
            created=False,
        )

    session.add(
        ShadowPredictionBatch(
            batch_id=batch_id,
            model_version=model_version,
            universe_id=universe_id,
            normalization_run_id=normalization_run_id,
            prediction_date=timestamp.date(),
            prediction_timestamp=timestamp,
            recorded_at=recorded,
            executable_lock_uri=executable_lock_uri,
            executable_lock_hash=executable_lock_hash,
            code_commit=code_commit,
            execution_commit=execution_commit,
            input_manifest_json=_json_ready(input_manifest),
            expected_member_count=len(candidates),
            scored_count=scored_count,
            excluded_count=excluded_count,
            claims_eligible=False,
            outcome_evaluation_authorized=True,
            product_label_policy=PRODUCT_LABEL_STATUS,
            batch_hash=batch_hash,
        )
    )
    session.add_all(
        ShadowPredictionRecord(
            shadow_prediction_id=row.shadow_prediction_id,
            batch_id=batch_id,
            security_id=row.security_id,
            ticker=row.ticker,
            classification_branch=row.classification_branch,
            disposition=row.disposition,
            research_score=row.research_score,
            research_confidence=row.research_confidence,
            research_label=row.research_label,
            product_label=None,
            product_label_status=PRODUCT_LABEL_STATUS,
            exclusions_json=row.exclusions,
            drivers_json=row.drivers,
            prediction_ids_json=row.prediction_ids,
            input_lineage_json=_json_ready(row.input_lineage),
            record_hash=row.record_hash,
        )
        for row in candidates
    )
    session.flush()
    return ShadowBatchResult(
        batch_id=batch_id,
        batch_hash=batch_hash,
        expected_member_count=len(candidates),
        scored_count=scored_count,
        excluded_count=excluded_count,
        created=True,
    )


def record_shadow_outcome(
    session: Session,
    *,
    shadow_prediction_id: str,
    horizon: str,
    recorded_at: Optional[datetime] = None,
) -> ShadowOutcomeRecord:
    """Append a hash-bound outcome link only after that exact horizon matures."""

    recorded = _utc(recorded_at or datetime.now(timezone.utc), field="recorded_at")
    record = session.get(ShadowPredictionRecord, shadow_prediction_id)
    if record is None:
        raise ValueError(f"unknown shadow prediction: {shadow_prediction_id}")
    if record.disposition != "SCORED":
        raise ValueError("excluded shadow members never receive outcome records")
    batch = session.get(ShadowPredictionBatch, record.batch_id)
    if batch is None or not batch.outcome_evaluation_authorized:
        raise ValueError("shadow batch lock does not authorize outcome evaluation")
    prediction_id = record.prediction_ids_json.get(horizon)
    if prediction_id is None:
        raise ValueError(f"shadow prediction does not bind horizon: {horizon}")
    prediction = session.get(ModelPrediction, prediction_id)
    if prediction is None or prediction.horizon != horizon:
        raise ValueError("shadow horizon references an invalid model prediction")
    outcome = session.scalar(
        select(ModelOutcome).where(ModelOutcome.prediction_id == prediction_id)
    )
    if outcome is None:
        raise ValueError("shadow outcome is not yet mature or evaluated")
    if outcome.exit_date > recorded.date():
        raise ValueError("shadow outcome cannot be recorded before its exit date")
    outcome_evaluated_at = _stored_utc(outcome.evaluated_at)
    if outcome_evaluated_at > recorded:
        raise ValueError("shadow outcome cannot be recorded before it was evaluated")
    shadow_outcome_id = "shadow-outcome-" + str(
        uuid.uuid5(SHADOW_ID_NAMESPACE, f"{shadow_prediction_id}|{horizon}")
    )
    existing = session.get(ShadowOutcomeRecord, shadow_outcome_id)
    if existing is not None:
        recorded = _stored_utc(existing.recorded_at)
    payload = {
        "shadow_outcome_id": shadow_outcome_id,
        "shadow_prediction_id": shadow_prediction_id,
        "shadow_prediction_hash": record.record_hash,
        "prediction_id": prediction_id,
        "prediction_hash": prediction.immutable_hash,
        "outcome_id": outcome.outcome_id,
        "outcome_hash": outcome.immutable_hash,
        "horizon": horizon,
        "recorded_at": recorded,
    }
    immutable_hash = _hash_json(payload)
    if existing is not None:
        if existing.immutable_hash != immutable_hash:
            raise ValueError(
                "shadow outcome already exists with different sealed inputs"
            )
        return existing
    shadow_outcome = ShadowOutcomeRecord(
        shadow_outcome_id=shadow_outcome_id,
        shadow_prediction_id=shadow_prediction_id,
        prediction_id=prediction_id,
        outcome_id=outcome.outcome_id,
        horizon=horizon,
        recorded_at=recorded,
        immutable_hash=immutable_hash,
    )
    session.add(shadow_outcome)
    session.flush()
    return shadow_outcome
