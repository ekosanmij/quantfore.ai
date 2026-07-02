"""Canonical Sprint 8 warehouse and evidence fingerprints."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.models import (
    Feature,
    ModelOutcome,
    ModelPrediction,
    MultiFactorPredictionLink,
    MultiFactorScore,
    NormalizationRun,
)
from quantfore_research.validation.point_in_time_fundamentals import (
    audit_point_in_time_fundamentals,
)
from quantfore_research.validation.reproducibility import (
    canonical_json_bytes,
    sha256_bytes,
)


SPRINT8_REPRODUCIBILITY_FIELDS = (
    "fundamental_fact_hash",
    "availability_revision_hash",
    "feature_count",
    "feature_value_hash",
    "monthly_eligible_universe_hash",
    "prediction_count",
    "outcome_count",
    "prediction_outcome_hash",
    "backtest_metrics_hash",
    "canonical_report_hashes",
)


@dataclass(frozen=True)
class Sprint8RebuildFingerprint:
    fundamental_fact_hash: str
    availability_revision_hash: str
    feature_count: int
    feature_value_hash: str
    monthly_eligible_universe_hash: str
    prediction_count: int
    outcome_count: int
    prediction_outcome_hash: str
    backtest_metrics_hash: str
    canonical_report_hashes: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field) for field in SPRINT8_REPRODUCIBILITY_FIELDS
        }


def _hash_document(value: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def build_sprint8_rebuild_fingerprint(
    session: Session,
    *,
    fundamental_source_snapshot_ids: Sequence[str],
    audit_document: Mapping[str, Any],
    backtest_document: Mapping[str, Any],
    comparison_document: Mapping[str, Any],
) -> Sprint8RebuildFingerprint:
    """Fingerprint every invariant required by Sprint 8.8."""

    audit_payload = audit_document.get("audit") or {}
    if (
        audit_document.get("decision") not in {"pass", "review"}
        or audit_payload.get("hard_failure_count") != 0
    ):
        raise ValueError("Sprint 8 closure requires an accepted fundamentals audit")
    for name, document, report_id in (
        ("backtest", backtest_document, "pit_multifactor_baseline_v1"),
        ("comparison", comparison_document, "price-vs-multifactor-v1"),
    ):
        if document.get("report_id") != report_id or document.get("claims_eligible") is not False:
            raise ValueError(f"Sprint 8 {name} report is not canonical")
        lineage = document.get("warehouse_lineage")
        if not isinstance(lineage, dict) or lineage.get("source") != "verified_research_warehouse":
            raise ValueError(f"Sprint 8 {name} report lacks verified warehouse lineage")
    reproduced_audit = audit_point_in_time_fundamentals(
        session,
        source_snapshot_ids=tuple(sorted(set(fundamental_source_snapshot_ids))),
        reconciliation_samples=(),
        enforce_reconciliation_gate=False,
    )
    if (
        reproduced_audit.fact_hash != audit_payload.get("fact_hash")
        or reproduced_audit.availability_revision_hash
        != audit_payload.get("availability_revision_hash")
    ):
        raise ValueError("fundamental audit does not reproduce from closure database")

    features = list(
        session.scalars(
            select(Feature)
            .where(Feature.version == "multifactor-v1")
            .order_by(
                Feature.asof_date,
                Feature.security_id,
                Feature.feature_name,
                Feature.feature_set_id,
            )
        ).all()
    )
    if not features:
        raise ValueError("Sprint 8 closure database has no multi-factor features")
    feature_document = {
        "schema_version": "sprint8_feature_values_v1",
        "rows": [
            {
                "feature_set_id": row.feature_set_id,
                "security_id": row.security_id,
                "asof_date": row.asof_date,
                "feature_name": row.feature_name,
                "value": row.value,
                "raw_value": row.raw_value,
                "status": row.applicability_status,
                "missing_reason": row.missing_reason,
                "inputs": row.inputs_json,
                "source_snapshot_id": row.source_snapshot_id,
                "source_hash": row.source_hash,
            }
            for row in features
        ],
    }
    runs = list(
        session.scalars(
            select(NormalizationRun).order_by(
                NormalizationRun.asof_date, NormalizationRun.normalization_run_id
            )
        ).all()
    )
    scores = list(
        session.scalars(
            select(MultiFactorScore).order_by(
                MultiFactorScore.asof_date,
                MultiFactorScore.security_id,
                MultiFactorScore.normalization_run_id,
            )
        ).all()
    )
    if not runs or not scores:
        raise ValueError("Sprint 8 closure database has no normalized scores")
    eligible_document = {
        "schema_version": "sprint8_monthly_eligible_universe_v1",
        "runs": [
            {
                "normalization_run_id": run.normalization_run_id,
                "universe_id": run.universe_id,
                "asof_date": run.asof_date,
                "input_hash": run.input_hash,
                "eligible_security_ids": sorted(
                    row.security_id
                    for row in scores
                    if row.normalization_run_id == run.normalization_run_id
                    and row.eligible
                ),
            }
            for run in runs
        ],
    }
    links = list(
        session.scalars(
            select(MultiFactorPredictionLink).order_by(
                MultiFactorPredictionLink.prediction_id
            )
        ).all()
    )
    predictions = list(
        session.scalars(
            select(ModelPrediction)
            .where(
                ModelPrediction.model_version.in_(
                    ("multifactor-baseline-v1", "baseline_v0.1")
                )
            )
            .order_by(ModelPrediction.prediction_id)
        ).all()
    )
    prediction_ids = {row.prediction_id for row in predictions}
    outcomes = list(
        session.scalars(
            select(ModelOutcome)
            .where(ModelOutcome.prediction_id.in_(prediction_ids))
            .order_by(ModelOutcome.prediction_id)
        ).all()
    )
    linked_ids = {row.prediction_id for row in links}
    multifactor_ids = {
        row.prediction_id
        for row in predictions
        if row.model_version == "multifactor-baseline-v1"
    }
    if multifactor_ids != linked_ids or len(outcomes) != len(predictions):
        raise ValueError("Sprint 8 closure requires complete prediction outcomes")
    outcome_by_prediction = {row.prediction_id: row for row in outcomes}
    prediction_outcome_document = {
        "schema_version": "sprint8_predictions_outcomes_v1",
        "rows": [
            {
                "prediction_id": row.prediction_id,
                "prediction_hash": row.immutable_hash,
                "outcome_hash": outcome_by_prediction[row.prediction_id].immutable_hash,
            }
            for row in predictions
        ],
    }
    evaluation = backtest_document.get("evaluation")
    if not isinstance(evaluation, dict):
        raise ValueError("Sprint 8 backtest report lacks evaluation metrics")
    report_hashes = {
        "fundamental_audit": sha256_bytes(
            canonical_json_bytes(audit_document, pretty=True)
        ),
        "multifactor_backtest": sha256_bytes(
            canonical_json_bytes(backtest_document, pretty=True)
        ),
        "price_vs_multifactor": sha256_bytes(
            canonical_json_bytes(comparison_document, pretty=True)
        ),
    }
    return Sprint8RebuildFingerprint(
        fundamental_fact_hash=reproduced_audit.fact_hash,
        availability_revision_hash=reproduced_audit.availability_revision_hash,
        feature_count=len(features),
        feature_value_hash=_hash_document(feature_document),
        monthly_eligible_universe_hash=_hash_document(eligible_document),
        prediction_count=len(predictions),
        outcome_count=len(outcomes),
        prediction_outcome_hash=_hash_document(prediction_outcome_document),
        backtest_metrics_hash=_hash_document(evaluation),
        canonical_report_hashes=report_hashes,
    )


def compare_sprint8_rebuilds(
    first: Sprint8RebuildFingerprint,
    second: Sprint8RebuildFingerprint,
) -> dict[str, Any]:
    left = first.to_dict()
    right = second.to_dict()
    checks = {
        field: {
            "matched": left[field] == right[field],
            "first": left[field],
            "second": right[field],
        }
        for field in SPRINT8_REPRODUCIBILITY_FIELDS
    }
    return {
        "all_matched": all(row["matched"] for row in checks.values()),
        "checks": checks,
    }
