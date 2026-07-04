"""Cryptographic execution gate for audited point-in-time fundamentals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.models import SourceSnapshot
from quantfore_research.validation.point_in_time_fundamentals import (
    audit_point_in_time_fundamentals,
)


@dataclass(frozen=True)
class FundamentalAuditBinding:
    audit_id: str
    audit_sha256: str
    fact_hash: str
    availability_revision_hash: str
    source_snapshot_hashes: Mapping[str, str]
    audit_status: str = "pass"

    def to_dict(self) -> dict[str, object]:
        return {
            "audit_id": self.audit_id,
            "audit_sha256": self.audit_sha256,
            "decision": "accepted",
            "audit_status": self.audit_status,
            "fact_hash": self.fact_hash,
            "availability_revision_hash": self.availability_revision_hash,
            "source_snapshot_hashes": dict(sorted(self.source_snapshot_hashes.items())),
        }


def verify_fundamental_audit(
    session: Session,
    *,
    audit_path: Path,
    expected_audit_sha256: str,
    source_snapshot_ids: Sequence[str],
) -> FundamentalAuditBinding:
    """Require a passing report that exactly reproduces the selected DB facts."""

    body = audit_path.read_bytes()
    actual_hash = hashlib.sha256(body).hexdigest()
    if actual_hash != expected_audit_sha256.lower():
        raise ValueError("fundamental audit SHA-256 does not match")
    document = json.loads(body)
    audit = document.get("audit") if isinstance(document, dict) else None
    if not isinstance(audit, dict):
        raise ValueError("fundamental audit document is missing audit evidence")
    if (
        document.get("audit_id") != "pit-fundamentals-v1"
        or document.get("claims_eligible") is not False
        or document.get("decision") not in {"pass", "review"}
        or audit.get("status") not in {"pass", "review"}
        or audit.get("hard_failure_count") != 0
    ):
        raise ValueError("fundamental audit is not accepted for feature execution")
    reconciliation = audit.get("reconciliation") or {}
    required_sectors = reconciliation.get("required_sectors") or []
    sec_primary = reconciliation.get("evidence_mode") == "sec_primary_source_integrity"
    if sec_primary:
        if reconciliation.get("gate_enforced") is not False:
            raise ValueError("SEC-primary audit has an invalid evidence gate")
    elif (
        reconciliation.get("gate_enforced") is not True
        or reconciliation.get("issuer_period_count", 0)
        < reconciliation.get("minimum_issuer_periods", 30)
        or sorted(reconciliation.get("sectors") or []) != sorted(required_sectors)
    ):
        raise ValueError("fundamental audit did not pass the full SEC reconciliation gate")

    selected_ids = tuple(sorted(set(source_snapshot_ids)))
    audited_ids = tuple(sorted(audit.get("source_snapshot_ids") or ()))
    if not selected_ids or audited_ids != selected_ids:
        raise ValueError("fundamental audit source snapshot IDs do not match feature inputs")
    snapshots = {
        row.snapshot_id: row.source_hash
        for row in session.scalars(
            select(SourceSnapshot).where(SourceSnapshot.snapshot_id.in_(selected_ids))
        ).all()
    }
    if set(snapshots) != set(selected_ids):
        raise ValueError("fundamental audit references an unknown source snapshot")
    reported_snapshots = document.get("source_snapshot_hashes")
    if reported_snapshots != dict(sorted(snapshots.items())):
        raise ValueError("fundamental audit source hashes do not match the warehouse")

    reproduced = audit_point_in_time_fundamentals(
        session,
        source_snapshot_ids=selected_ids,
        reconciliation_samples=(),
        enforce_reconciliation_gate=False,
        require_sec_primary_evidence=sec_primary,
    )
    if reproduced.fact_hash != audit.get("fact_hash"):
        raise ValueError("fundamental audit fact hash does not match the warehouse")
    if reproduced.availability_revision_hash != audit.get("availability_revision_hash"):
        raise ValueError(
            "fundamental audit availability/revision hash does not match the warehouse"
        )
    counts = audit.get("counts") or {}
    if (
        counts.get("facts") != reproduced.fact_count
        or counts.get("securities") != reproduced.security_count
    ):
        raise ValueError("fundamental audit counts do not match the warehouse")
    return FundamentalAuditBinding(
        audit_id="pit-fundamentals-v1",
        audit_sha256=actual_hash,
        fact_hash=reproduced.fact_hash,
        availability_revision_hash=reproduced.availability_revision_hash,
        source_snapshot_hashes=dict(sorted(snapshots.items())),
        audit_status=str(audit["status"]),
    )
