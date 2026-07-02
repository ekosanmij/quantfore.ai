"""Canonical fingerprints for Sprint 7 clean-rebuild closure evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.models import UniverseMembership


REPRODUCIBILITY_FIELDS = (
    "universe_membership_hash",
    "security_count_by_month",
    "prediction_count",
    "outcome_count",
    "dataset_audit_decision",
    "backtest_metrics",
    "canonical_report_sha256",
)


def _canonical_value(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_value(item) for item in value]
    return value


def canonical_json_bytes(document: Mapping[str, Any], *, pretty: bool = False) -> bytes:
    """Serialize a document deterministically for hashing or publication."""

    normalized = _canonical_value(document)
    if pretty:
        body = json.dumps(normalized, indent=2, sort_keys=True)
    else:
        body = json.dumps(normalized, separators=(",", ":"), sort_keys=True)
    return (body + "\n").encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def universe_membership_hash(session: Session, *, universe_id: str) -> str:
    """Hash normalized membership business content, excluding warehouse times."""

    rows = session.scalars(
        select(UniverseMembership)
        .where(UniverseMembership.universe_id == universe_id)
        .order_by(
            UniverseMembership.security_id,
            UniverseMembership.effective_from,
            UniverseMembership.effective_to,
            UniverseMembership.membership_id,
        )
    ).all()
    if not rows:
        raise ValueError(f"universe has no memberships: {universe_id}")
    document = {
        "schema_version": "universe_membership_content_v1",
        "universe_id": universe_id,
        "memberships": [
            {
                "security_id": row.security_id,
                "effective_from": row.effective_from,
                "effective_to": row.effective_to,
                "announced_at": row.announced_at,
                "source_snapshot_id": row.source_snapshot_id,
                "source_hash": row.source_hash,
            }
            for row in rows
        ],
    }
    return sha256_bytes(canonical_json_bytes(document))


@dataclass(frozen=True)
class RebuildFingerprint:
    universe_membership_hash: str
    security_count_by_month: Mapping[str, int]
    prediction_count: int
    outcome_count: int
    dataset_audit_decision: str
    backtest_metrics: Mapping[str, Any]
    canonical_report_sha256: str
    canonical_audit_sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_membership_hash": self.universe_membership_hash,
            "security_count_by_month": dict(self.security_count_by_month),
            "prediction_count": self.prediction_count,
            "outcome_count": self.outcome_count,
            "dataset_audit_decision": self.dataset_audit_decision,
            "backtest_metrics": dict(self.backtest_metrics),
            "canonical_report_sha256": self.canonical_report_sha256,
            "canonical_audit_sha256": self.canonical_audit_sha256,
        }


def build_rebuild_fingerprint(
    session: Session,
    *,
    universe_id: str,
    audit_document: Mapping[str, Any],
    backtest_report: Mapping[str, Any],
    backtest_lineage: Mapping[str, Any],
) -> RebuildFingerprint:
    """Build the required closure fingerprint from one clean database run."""

    if backtest_report.get("manifest") != backtest_lineage:
        raise ValueError("backtest report manifest does not match lineage")
    cohorts = backtest_report.get("cohorts")
    if not isinstance(cohorts, list) or not cohorts:
        raise ValueError("backtest report must contain monthly cohorts")
    counts = {}
    for cohort in cohorts:
        if not isinstance(cohort, dict):
            raise ValueError("backtest cohort must be an object")
        prediction_date = cohort.get("prediction_date")
        expected_count = cohort.get("expected_count")
        if not isinstance(prediction_date, str) or not isinstance(expected_count, int):
            raise ValueError("backtest cohort lacks date or expected security count")
        if prediction_date in counts:
            raise ValueError(f"duplicate backtest cohort date: {prediction_date}")
        counts[prediction_date] = expected_count
    metrics = backtest_report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("backtest report must contain metrics")
    decision = audit_document.get("decision")
    if not isinstance(decision, str):
        raise ValueError("audit document must contain a decision")
    prediction_count = backtest_lineage.get("prediction_count")
    outcome_count = backtest_lineage.get("outcome_count")
    if not isinstance(prediction_count, int) or not isinstance(outcome_count, int):
        raise ValueError("backtest lineage must contain prediction and outcome counts")
    report_payload = canonical_json_bytes(backtest_report, pretty=True)
    audit_payload = canonical_json_bytes(audit_document, pretty=True)
    return RebuildFingerprint(
        universe_membership_hash=universe_membership_hash(
            session, universe_id=universe_id
        ),
        security_count_by_month=dict(sorted(counts.items())),
        prediction_count=prediction_count,
        outcome_count=outcome_count,
        dataset_audit_decision=decision,
        backtest_metrics=dict(metrics),
        canonical_report_sha256=sha256_bytes(report_payload),
        canonical_audit_sha256=sha256_bytes(audit_payload),
    )


def compare_rebuild_fingerprints(
    first: RebuildFingerprint, second: RebuildFingerprint
) -> dict[str, Any]:
    """Return an explicit equality decision for every Sprint 7.8 invariant."""

    left = first.to_dict()
    right = second.to_dict()
    fields = (*REPRODUCIBILITY_FIELDS, "canonical_audit_sha256")
    checks = {
        field: {
            "matched": left[field] == right[field],
            "first": left[field],
            "second": right[field],
        }
        for field in fields
    }
    return {
        "all_matched": all(check["matched"] for check in checks.values()),
        "checks": checks,
    }
