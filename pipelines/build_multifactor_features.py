"""Build and store Sprint 8 point-in-time raw multi-factor features."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import get_code_revision, open_research_database
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import get_code_revision, open_research_database  # type: ignore

from sqlalchemy import select

from quantfore_research.db import session_scope
from quantfore_research.features.multifactor import (
    APPLICABLE,
    MULTIFACTOR_FEATURE_VERSION,
    construct_multifactor_features,
    store_multifactor_features,
)
from quantfore_research.models import Security
from quantfore_research.validation.fundamental_audit_gate import (
    verify_fundamental_audit,
)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _resolve_security(session, *, security_id: Optional[str], ticker: Optional[str]) -> Security:
    if security_id:
        security = session.get(Security, security_id)
        if security is None:
            raise ValueError(f"unknown security: {security_id}")
        return security
    normalized = (ticker or "").strip().upper()
    rows = list(
        session.scalars(select(Security).where(Security.ticker == normalized)).all()
    )
    if len(rows) != 1:
        raise ValueError(
            f"ticker {normalized!r} must resolve to exactly one security; use an ID"
        )
    return rows[0]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build Sprint 8 point-in-time raw multi-factor features."
    )
    security = parser.add_mutually_exclusive_group(required=True)
    security.add_argument("--security-id")
    security.add_argument("--ticker")
    benchmark = parser.add_mutually_exclusive_group(required=False)
    benchmark.add_argument("--benchmark-security-id")
    benchmark.add_argument("--benchmark-ticker", default="SPY")
    parser.add_argument("--prediction-timestamp", required=True, type=_timestamp)
    parser.add_argument(
        "--classification-id",
        help="Optional exact dated classification record; otherwise resolve as-of.",
    )
    parser.add_argument(
        "--fundamental-source-snapshot-id",
        action="append",
        required=True,
        help="Primary vendor snapshot; repeat for immutable partitions.",
    )
    parser.add_argument("--security-price-snapshot-id", required=True)
    parser.add_argument("--benchmark-price-snapshot-id", required=True)
    parser.add_argument("--fundamental-audit-json", required=True, type=Path)
    parser.add_argument("--expected-fundamental-audit-hash", required=True)
    parser.add_argument("--feature-set-id")
    parser.add_argument("--database-url")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        session_factory = open_research_database(args.database_url)
        with session_scope(session_factory) as session:
            audit_binding = verify_fundamental_audit(
                session,
                audit_path=args.fundamental_audit_json,
                expected_audit_sha256=args.expected_fundamental_audit_hash,
                source_snapshot_ids=args.fundamental_source_snapshot_id,
            )
            security = _resolve_security(
                session, security_id=args.security_id, ticker=args.ticker
            )
            benchmark = _resolve_security(
                session,
                security_id=args.benchmark_security_id,
                ticker=args.benchmark_ticker,
            )
            batch = construct_multifactor_features(
                session,
                security_id=security.security_id,
                benchmark_security_id=benchmark.security_id,
                prediction_timestamp=args.prediction_timestamp,
                classification_id=args.classification_id,
                fundamental_source_snapshot_ids=(
                    args.fundamental_source_snapshot_id
                ),
                security_price_snapshot_id=args.security_price_snapshot_id,
                benchmark_price_snapshot_id=args.benchmark_price_snapshot_id,
            )
            feature_set_id = args.feature_set_id or (
                f"pit_{MULTIFACTOR_FEATURE_VERSION}_{security.security_id}_"
                f"{args.prediction_timestamp.date().isoformat()}"
            )
            store_multifactor_features(
                session,
                batch=batch,
                feature_set_id=feature_set_id,
                fundamental_audit=audit_binding,
                code_commit=get_code_revision(),
            )
        valid = sum(row.status == APPLICABLE for row in batch.features)
        unavailable = len(batch.features) - valid
        print(
            f"feature_set_id={feature_set_id} security_id={security.security_id} "
            f"valid={valid} unavailable={unavailable} "
            f"source_snapshots={len(batch.source_snapshot_ids)}"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"multi-factor feature build failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
