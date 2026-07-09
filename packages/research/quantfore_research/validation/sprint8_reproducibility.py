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
    SourceSnapshot,
)
from quantfore_research.validation.point_in_time_fundamentals import (
    audit_point_in_time_fundamentals,
    derive_sec_reconciliation_samples,
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


def _require_report_metadata(
    document: Mapping[str, Any], *, report_id: str, report_name: str
) -> dict[str, Any]:
    if (
        document.get("report_id") != report_id
        or document.get("claims_eligible") is not False
    ):
        raise ValueError(f"Sprint 8 {report_name} report is not canonical")
    generated_at = document.get("generated_at")
    code_revision = document.get("code_revision")
    lock_hash = document.get("holdout_lock_sha256")
    if not isinstance(generated_at, str) or not generated_at:
        raise ValueError(f"Sprint 8 {report_name} report lacks generated_at")
    if not isinstance(code_revision, str) or not code_revision:
        raise ValueError(f"Sprint 8 {report_name} report lacks code_revision")
    if not isinstance(lock_hash, str) or len(lock_hash) != 64:
        raise ValueError(f"Sprint 8 {report_name} report lacks a holdout lock hash")
    return {
        "report_id": report_id,
        "claims_eligible": False,
        "generated_at": generated_at,
        "code_revision": code_revision,
        "holdout_lock_sha256": lock_hash,
    }


def _require_exact_document(
    supplied: Mapping[str, Any], calculated: Mapping[str, Any], *, report_name: str
) -> None:
    if canonical_json_bytes(supplied) != canonical_json_bytes(calculated):
        raise ValueError(
            f"Sprint 8 {report_name} report does not reproduce from closure database"
        )


def build_sprint8_rebuild_fingerprint(
    session: Session,
    *,
    fundamental_source_snapshot_ids: Sequence[str],
    audit_document: Mapping[str, Any],
    backtest_document: Mapping[str, Any],
    comparison_document: Mapping[str, Any],
) -> Sprint8RebuildFingerprint:
    """Fingerprint every invariant required by Sprint 8.8."""

    # Imported lazily to keep validation package initialization independent of
    # the scoring/features import graph.
    from quantfore_research.evaluation.multifactor import (
        evaluate_multifactor_baseline,
    )
    from quantfore_research.evaluation.multifactor_comparison import (
        build_multifactor_comparison,
    )
    from quantfore_research.evaluation.multifactor_warehouse import (
        load_verified_comparison_ledger,
        load_verified_evaluation_ledger,
    )

    source_ids = tuple(sorted(set(fundamental_source_snapshot_ids)))
    if not isinstance(audit_document.get("generated_at"), str) or not isinstance(
        audit_document.get("code_revision"), str
    ):
        raise ValueError("Sprint 8 fundamentals audit lacks canonical metadata")
    requested_snapshots = list(
        session.scalars(
            select(SourceSnapshot).where(SourceSnapshot.snapshot_id.in_(source_ids))
        ).all()
    )
    if len(requested_snapshots) != len(source_ids):
        raise ValueError("Sprint 8 fundamental source snapshots are incomplete")
    sec_primary = bool(requested_snapshots) and all(
        "SEC EDGAR PRIMARY" in row.vendor.upper() for row in requested_snapshots
    )
    reconciliation_samples = (
        ()
        if sec_primary
        else derive_sec_reconciliation_samples(
            session,
            vendor_source_snapshot_ids=source_ids,
        )
    )
    reproduced_audit = audit_point_in_time_fundamentals(
        session,
        source_snapshot_ids=source_ids,
        reconciliation_samples=reconciliation_samples,
        enforce_reconciliation_gate=not sec_primary,
        require_sec_primary_evidence=sec_primary,
    )
    if reproduced_audit.hard_failure_count:
        raise ValueError("Sprint 8 closure requires a passing fundamentals audit")
    source_hashes = {
        row.snapshot_id: row.source_hash
        for row in session.scalars(
            select(SourceSnapshot).where(
                SourceSnapshot.snapshot_id.in_(reproduced_audit.source_snapshot_ids)
            )
        ).all()
    }
    calculated_audit = {
        "audit_id": "pit-fundamentals-v1",
        "dataset_kind": "proof_candidate_point_in_time",
        "claims_eligible": False,
        "generated_at": audit_document.get("generated_at"),
        "code_revision": audit_document.get("code_revision"),
        "source_snapshot_hashes": dict(sorted(source_hashes.items())),
        "decision": reproduced_audit.status,
        "audit": reproduced_audit.to_dict(),
    }
    _require_exact_document(
        audit_document, calculated_audit, report_name="fundamentals audit"
    )

    evaluation_ledger = load_verified_evaluation_ledger(session)
    comparison_ledger = load_verified_comparison_ledger(session)
    calculated_backtest = {
        **_require_report_metadata(
            backtest_document,
            report_id="pit_multifactor_baseline_v1",
            report_name="backtest",
        ),
        "warehouse_lineage": evaluation_ledger.lineage_dict(),
        "evaluation": evaluate_multifactor_baseline(evaluation_ledger.observations),
    }
    calculated_comparison = {
        **_require_report_metadata(
            comparison_document,
            report_id="price-vs-multifactor-v1",
            report_name="comparison",
        ),
        "warehouse_lineage": comparison_ledger.lineage_dict(),
        "comparison": build_multifactor_comparison(comparison_ledger.observations),
    }
    _require_exact_document(
        backtest_document, calculated_backtest, report_name="backtest"
    )
    _require_exact_document(
        comparison_document, calculated_comparison, report_name="comparison"
    )

    # Core rows: every load below reads plain mapped columns through the same
    # type processors as the ORM entities they replace, so document contents
    # and hashes are identical while avoiding full-object hydration of the
    # ~1M-row feature/score/prediction tables.
    features = list(
        session.execute(
            select(Feature.__table__)
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
        session.execute(
            select(NormalizationRun.__table__).order_by(
                NormalizationRun.asof_date, NormalizationRun.normalization_run_id
            )
        ).all()
    )
    scores = list(
        session.execute(
            select(MultiFactorScore.__table__).order_by(
                MultiFactorScore.asof_date,
                MultiFactorScore.security_id,
                MultiFactorScore.normalization_run_id,
            )
        ).all()
    )
    if not runs or not scores:
        raise ValueError("Sprint 8 closure database has no normalized scores")
    eligible_by_run: dict[str, list[str]] = {}
    for row in scores:
        if row.eligible:
            eligible_by_run.setdefault(row.normalization_run_id, []).append(
                row.security_id
            )
    eligible_document = {
        "schema_version": "sprint8_monthly_eligible_universe_v1",
        "runs": [
            {
                "normalization_run_id": run.normalization_run_id,
                "universe_id": run.universe_id,
                "asof_date": run.asof_date,
                "input_hash": run.input_hash,
                "eligible_security_ids": sorted(
                    eligible_by_run.get(run.normalization_run_id, ())
                ),
            }
            for run in runs
        ],
    }
    links = list(
        session.execute(
            select(MultiFactorPredictionLink.__table__).order_by(
                MultiFactorPredictionLink.prediction_id
            )
        ).all()
    )
    predictions = list(
        session.execute(
            select(ModelPrediction.__table__)
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
        session.execute(
            select(ModelOutcome.__table__)
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
    if multifactor_ids != linked_ids:
        raise ValueError("Sprint 8 closure requires complete prediction links")
    outcome_by_prediction = {row.prediction_id: row for row in outcomes}
    prediction_outcome_document = {
        "schema_version": "sprint8_predictions_outcomes_v1",
        "rows": [
            {
                "prediction_id": row.prediction_id,
                "prediction_hash": row.immutable_hash,
                "outcome_hash": (
                    outcome_by_prediction[row.prediction_id].immutable_hash
                    if row.prediction_id in outcome_by_prediction
                    else None
                ),
            }
            for row in predictions
        ],
    }
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
        backtest_metrics_hash=_hash_document(calculated_backtest["evaluation"]),
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
