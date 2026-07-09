"""Rebuild the amended SEC-primary Sprint 8 warehouse and evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import get_code_revision, open_research_database
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import get_code_revision, open_research_database  # type: ignore

from sqlalchemy import select

from pipelines.audit_point_in_time_fundamentals import (
    build_audit_document,
    write_reports,
)
from pipelines.evaluate_predictions import evaluate_prediction
from pipelines.ingest_point_in_time_equities import persist_bundle
from pipelines.ingest_point_in_time_fundamentals import ingest_bundle
from pipelines.normalize_multifactor_features import load_stored_cohort
from quantfore_research.backtest.point_in_time import run_dynamic_universe_backtest
from quantfore_research.db import session_scope
from quantfore_research.features.multifactor import (
    MULTIFACTOR_FEATURE_VERSION,
    construct_multifactor_features,
    store_multifactor_features,
)
from quantfore_research.ingest.point_in_time_equities import (
    PointInTimeEquityBundleAdapter,
    deterministic_id,
)
from quantfore_research.ingest.point_in_time_fundamentals import (
    PointInTimeFundamentalBundleAdapter,
)
from quantfore_research.models import (
    ModelPrediction,
    Price,
    Security,
    SecurityClassification,
    SecurityIdentifier,
    SourceSnapshot,
    UniverseDefinition,
)
from quantfore_research.scoring.multifactor import (
    NORMALIZATION_VERSION,
    normalize_multifactor_cohort,
    store_multifactor_cohort_scores,
    store_multifactor_predictions,
)
from quantfore_research.snapshots import record_source_snapshot
from quantfore_research.validation.fundamental_audit_gate import (
    FundamentalAuditBinding,
)
from quantfore_research.validation.leakage import expected_point_in_time_cohort
from quantfore_research.validation.point_in_time_fundamentals import (
    audit_point_in_time_fundamentals,
)
from quantfore_research.validation.price_quality import exchange_sessions


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EQUITY_BUNDLE = (
    REPO_ROOT / "data/raw/free-point-in-time/composite-equity-bundle-v1"
)
DEFAULT_LOCK = REPO_ROOT / "experiments/multifactor-holdout-lock-v1.json"
CLASSIFICATION_NAMESPACE = uuid.UUID("57545967-b440-5d11-aad2-27c28a0648e4")
UNIVERSE_ID = "sp500-pit-v1"
WINDOW_START = date(2017, 1, 1)
WINDOW_END = date(2025, 6, 30)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _classification_id(row: Mapping[str, Any]) -> str:
    return str(
        uuid.uuid5(
            CLASSIFICATION_NAMESPACE,
            "|".join(
                str(row.get(key) or "")
                for key in (
                    "vendor_id",
                    "classification_system",
                    "effective_from",
                    "effective_to",
                    "sector",
                    "industry",
                )
            ),
        )
    )


def ingest_classifications(
    session,
    *,
    bundle_dir: Path,
    manifest: Mapping[str, Any],
    raw_root: Path,
) -> tuple[str, int]:
    metadata = manifest.get("classifications_file")
    if not isinstance(metadata, dict):
        raise ValueError("SEC bundle lacks classifications_file")
    source_path = bundle_dir / str(metadata["path"])
    body = source_path.read_bytes()
    source_hash = hashlib.sha256(body).hexdigest()
    if source_hash != str(metadata["sha256"]):
        raise ValueError("SEC classification source SHA-256 does not match")
    rows = json.loads(body)
    if not isinstance(rows, list) or not rows:
        raise ValueError("SEC classification source is empty")
    vendor_ids = {str(row["vendor_id"]) for row in rows}
    identifiers = list(
        session.scalars(
            select(SecurityIdentifier).where(
                SecurityIdentifier.identifier_type.in_(
                    ("FIGI_SHARE_CLASS", "COMPOSITE_PERMANENT_ID")
                ),
                SecurityIdentifier.identifier_value.in_(vendor_ids),
                SecurityIdentifier.is_permanent.is_(True),
            )
        ).all()
    )
    by_vendor: dict[str, set[str]] = {}
    for identifier in identifiers:
        by_vendor.setdefault(identifier.identifier_value, set()).add(
            identifier.security_id
        )
    unresolved = sorted(
        vendor_id
        for vendor_id in vendor_ids
        if len(by_vendor.get(vendor_id, ())) != 1
    )
    if unresolved:
        raise ValueError(f"classification identifiers are unresolved: {unresolved[:5]!r}")

    retrieved_at = _timestamp(str(manifest["fundamentals_file"]["retrieved_at"]))
    storage_uri = f"raw/sec-primary/classifications/{source_hash}.json"
    raw_path = raw_root.parent / storage_uri
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_path.exists() and raw_path.read_bytes() != body:
        raise ValueError("classification raw storage contains conflicting bytes")
    raw_path.write_bytes(body)
    snapshot = record_source_snapshot(
        session,
        vendor="SEC EDGAR Primary",
        dataset="filing_index_sic_classifications_v1",
        retrieved_at=retrieved_at,
        license_tag="public_source_internal_research",
        source_hash=source_hash,
        storage_uri=storage_uri,
        # Deterministic ID: classification lineage is copied into stored
        # rows, so two clean rebuilds must mint the same value.
        snapshot_id=deterministic_id(
            "source_snapshot",
            "SEC EDGAR Primary",
            "filing_index_sic_classifications_v1",
            retrieved_at.isoformat(),
            source_hash,
        ),
    )
    inserted = 0
    for row in rows:
        security_id = next(iter(by_vendor[str(row["vendor_id"])]))
        classification_id = _classification_id(row)
        existing = session.get(SecurityClassification, classification_id)
        expected = {
            "security_id": security_id,
            "sector": str(row["sector"]),
            "industry": row.get("industry"),
            "classification_system": str(row["classification_system"]),
            "effective_from": date.fromisoformat(str(row["effective_from"])),
            "effective_to": (
                date.fromisoformat(str(row["effective_to"]))
                if row.get("effective_to")
                else None
            ),
            "model_available_at": _timestamp(str(row["model_available_at"])),
            "source_snapshot_id": snapshot.snapshot_id,
            "source_hash": source_hash,
        }
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in expected.items()):
                raise ValueError(f"classification conflict: {classification_id}")
            continue
        session.add(
            SecurityClassification(
                classification_id=classification_id,
                **expected,
            )
        )
        inserted += 1
    session.flush()
    return snapshot.snapshot_id, inserted


def _prediction_dates(session) -> tuple[date, ...]:
    universe = session.get(UniverseDefinition, UNIVERSE_ID)
    if universe is None:
        raise ValueError("Sprint 8 universe is missing")
    rows = list(
        session.scalars(
            select(Price)
            .where(Price.security_id == universe.benchmark_security_id)
            .where(Price.date >= WINDOW_START)
            .where(Price.date <= WINDOW_END)
            .order_by(Price.date)
        ).all()
    )
    available = {row.date for row in rows}
    final_by_month: dict[tuple[int, int], date] = {}
    for day in exchange_sessions(WINDOW_START, WINDOW_END, calendar_name="XNYS"):
        if day in available:
            final_by_month[(day.year, day.month)] = day
    return tuple(final_by_month[key] for key in sorted(final_by_month))


def _price_snapshots(session) -> dict[str, str]:
    rows = session.execute(
        select(Price.security_id, Price.source_snapshot_id).distinct()
    ).all()
    grouped: dict[str, set[str]] = {}
    for security_id, snapshot_id in rows:
        grouped.setdefault(str(security_id), set()).add(str(snapshot_id))
    ambiguous = [key for key, values in grouped.items() if len(values) != 1]
    if ambiguous:
        raise ValueError(f"ambiguous price snapshots: {ambiguous[:5]!r}")
    return {key: next(iter(values)) for key, values in grouped.items()}


def build_monthly_multifactor_ledger(
    *,
    session_factory,
    audit_binding: FundamentalAuditBinding,
    fundamental_snapshot_ids: Sequence[str],
    code_commit: str,
) -> tuple[str, ...]:
    with session_factory() as session:
        universe = session.get(UniverseDefinition, UNIVERSE_ID)
        if universe is None:
            raise ValueError("Sprint 8 universe is missing")
        benchmark_id = universe.benchmark_security_id
        dates = _prediction_dates(session)
        price_snapshots = _price_snapshots(session)
    run_ids = []
    # One session (and one commit) for every month: each month's statements
    # run in the same order as the previous per-month transactions, and the
    # shared session lets the immutable fundamental and price histories be
    # loaded once per security instead of once per security-month.
    with session_scope(session_factory) as session:
        for position, prediction_date in enumerate(dates, start=1):
            prediction_timestamp = datetime.combine(
                prediction_date, datetime.max.time(), tzinfo=timezone.utc
            )
            contexts = expected_point_in_time_cohort(
                session,
                universe_id=UNIVERSE_ID,
                prediction_timestamp=prediction_timestamp,
            )
            for context in contexts:
                security_id = context.security.security_id
                batch = construct_multifactor_features(
                    session,
                    security_id=security_id,
                    benchmark_security_id=benchmark_id,
                    prediction_timestamp=prediction_timestamp,
                    fundamental_source_snapshot_ids=fundamental_snapshot_ids,
                    security_price_snapshot_id=price_snapshots.get(security_id),
                    benchmark_price_snapshot_id=price_snapshots.get(benchmark_id),
                )
                store_multifactor_features(
                    session,
                    batch=batch,
                    feature_set_id=(
                        f"pit_{MULTIFACTOR_FEATURE_VERSION}_{security_id}_"
                        f"{prediction_date.isoformat()}"
                    ),
                    fundamental_audit=audit_binding,
                    code_commit=code_commit,
                )
            batches, raw_feature_ids, feature_set_ids = load_stored_cohort(
                session,
                universe_id=UNIVERSE_ID,
                prediction_timestamp=prediction_timestamp,
            )
            result = normalize_multifactor_cohort(batches)
            run_id = (
                f"{NORMALIZATION_VERSION}_{UNIVERSE_ID}_{prediction_date.isoformat()}"
            )
            store_multifactor_cohort_scores(
                session,
                result=result,
                normalization_run_id=run_id,
                universe_id=UNIVERSE_ID,
                raw_feature_ids=raw_feature_ids,
                source_feature_set_ids=feature_set_ids,
                code_commit=code_commit,
            )
            store_multifactor_predictions(
                session,
                result=result,
                normalization_run_id=run_id,
                raw_feature_ids=raw_feature_ids,
            )
            run_ids.append(run_id)
            if position == 1 or position % 12 == 0 or position == len(dates):
                print(f"multifactor_months={position}/{len(dates)}", flush=True)
    return tuple(run_ids)


def _evaluate_multifactor_predictions(
    session_factory, *, evaluated_at: datetime
) -> tuple[int, int]:
    # One session (and one commit) for the whole ledger: each prediction's
    # evaluation is independent and deterministic, and the shared session lets
    # the immutable per-security price series be read once instead of once per
    # prediction. The iteration order is unchanged.
    evaluated = 0
    skipped = 0
    with session_scope(session_factory) as session:
        benchmark = session.scalar(select(Security).where(Security.ticker == "SPY"))
        if benchmark is None:
            raise ValueError("SPY benchmark is missing")
        prediction_ids = list(
            session.scalars(
                select(ModelPrediction.prediction_id)
                .where(ModelPrediction.model_version == "multifactor-baseline-v1")
                .order_by(ModelPrediction.asof_date, ModelPrediction.prediction_id)
            ).all()
        )
        for position, prediction_id in enumerate(prediction_ids, start=1):
            prediction = session.get(ModelPrediction, prediction_id)
            if prediction is None:
                raise ValueError("prediction evaluation lineage disappeared")
            report = evaluate_prediction(
                session,
                prediction=prediction,
                benchmark=benchmark,
                evaluated_at=evaluated_at,
            )
            if report.status == "evaluated":
                evaluated += 1
            else:
                skipped += 1
            if position % 10000 == 0 or position == len(prediction_ids):
                print(
                    f"multifactor_outcomes={position}/{len(prediction_ids)} "
                    f"evaluated={evaluated} skipped={skipped}",
                    flush=True,
                )
    return evaluated, skipped


def _run_report(command: Sequence[str]) -> None:
    subprocess.run(list(command), cwd=REPO_ROOT, check=True)


def rebuild(
    *,
    bundle_dir: Path,
    expected_manifest_hash: str,
    equity_bundle: Path,
    database_url: str,
    output_root: Path,
    generated_at: datetime,
    prepare_only: bool,
) -> dict[str, Any]:
    code_commit = get_code_revision()
    manifest_body = (bundle_dir / "manifest.json").read_bytes()
    if hashlib.sha256(manifest_body).hexdigest() != expected_manifest_hash.lower():
        raise ValueError("SEC fundamental manifest SHA-256 does not match")
    manifest = json.loads(manifest_body)
    equity_hash = str(manifest["amended_contract"]["equity_manifest_sha256"])
    equity = PointInTimeEquityBundleAdapter(
        equity_bundle,
        expected_manifest_hash=equity_hash,
    ).load()
    raw_dir = output_root / "data" / "raw"
    persist_bundle(equity, database_url=database_url, raw_dir=raw_dir)

    fundamental = PointInTimeFundamentalBundleAdapter.load(
        bundle_dir,
        expected_manifest_hash=expected_manifest_hash,
    )
    session_factory = open_research_database(database_url)
    with session_scope(session_factory) as session:
        ingestion = ingest_bundle(session, fundamental, raw_dir=raw_dir)
        classification_snapshot_id, classification_count = ingest_classifications(
            session,
            bundle_dir=bundle_dir,
            manifest=manifest,
            raw_root=raw_dir,
        )
    with session_factory() as session:
        fundamental_snapshot = session.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.vendor == "SEC EDGAR Primary",
                SourceSnapshot.source_hash == fundamental.source.source_hash,
            )
        )
        if fundamental_snapshot is None:
            raise ValueError("SEC fundamental source snapshot is missing")
        source_ids = (fundamental_snapshot.snapshot_id,)
        audit = audit_point_in_time_fundamentals(
            session,
            source_snapshot_ids=source_ids,
            reconciliation_samples=(),
            enforce_reconciliation_gate=False,
            require_sec_primary_evidence=True,
        )
    if audit.hard_failure_count:
        raise ValueError(
            f"SEC-primary fundamental audit has {audit.hard_failure_count} hard failures"
        )
    audit_document = build_audit_document(
        audit,
        generated_at=generated_at,
        code_revision=code_commit,
        source_snapshot_hashes={
            fundamental_snapshot.snapshot_id: fundamental_snapshot.source_hash
        },
    )
    audit_json = output_root / "reports/data-audits/pit-fundamentals-v1.json"
    audit_markdown = output_root / "reports/data-audits/pit-fundamentals-v1.md"
    audit_hash, _ = write_reports(
        audit_document,
        json_output=audit_json,
        markdown_output=audit_markdown,
    )
    binding = FundamentalAuditBinding(
        audit_id="pit-fundamentals-v1",
        audit_sha256=audit_hash,
        fact_hash=audit.fact_hash,
        availability_revision_hash=audit.availability_revision_hash,
        source_snapshot_hashes={
            fundamental_snapshot.snapshot_id: fundamental_snapshot.source_hash
        },
        audit_status=audit.status,
    )
    run_ids = build_monthly_multifactor_ledger(
        session_factory=session_factory,
        audit_binding=binding,
        fundamental_snapshot_ids=source_ids,
        code_commit=code_commit,
    )
    if not prepare_only:
        with session_scope(session_factory) as session:
            run_dynamic_universe_backtest(
                session,
                experiment_id="pit_baseline_v0_1",
                universe_id=UNIVERSE_ID,
                start_date=WINDOW_START,
                end_date=WINDOW_END,
                minimum_coverage=Decimal("0.95"),
                code_commit=code_commit,
                evaluated_at=generated_at,
                result_uri="reports/backtests/pit_baseline_v0_1.json",
                audit_sha256=hashlib.sha256(
                    (REPO_ROOT / "reports/data-audits/pit-equity-panel-v1.json").read_bytes()
                ).hexdigest(),
            )
        evaluated, skipped = _evaluate_multifactor_predictions(
            session_factory, evaluated_at=generated_at
        )
        lock_hash = hashlib.sha256(DEFAULT_LOCK.read_bytes()).hexdigest()
        report_root = output_root / "reports"
        _run_report(
            (
                sys.executable,
                str(REPO_ROOT / "pipelines/evaluate_multifactor_baseline.py"),
                "--database-url",
                database_url,
                "--universe-id",
                UNIVERSE_ID,
                "--lock-json",
                str(DEFAULT_LOCK),
                "--expected-lock-hash",
                lock_hash,
                "--output",
                str(report_root / "backtests/pit_multifactor_baseline_v1.json"),
                "--generated-at",
                generated_at.isoformat().replace("+00:00", "Z"),
            )
        )
        _run_report(
            (
                sys.executable,
                str(REPO_ROOT / "pipelines/compare_price_vs_multifactor.py"),
                "--database-url",
                database_url,
                "--universe-id",
                UNIVERSE_ID,
                "--lock-json",
                str(DEFAULT_LOCK),
                "--expected-lock-hash",
                lock_hash,
                "--output",
                str(report_root / "comparisons/price-vs-multifactor-v1.json"),
                "--generated-at",
                generated_at.isoformat().replace("+00:00", "Z"),
            )
        )
    else:
        evaluated = skipped = 0
    return {
        "fundamental_source_snapshot_id": fundamental_snapshot.snapshot_id,
        "classification_source_snapshot_id": classification_snapshot_id,
        "facts": ingestion.facts_inserted + ingestion.facts_reused,
        "classifications": classification_count,
        "normalization_runs": len(run_ids),
        "evaluated_outcomes": evaluated,
        "skipped_outcomes": skipped,
        "audit_status": audit.status,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--expected-manifest-hash", required=True)
    parser.add_argument("--equity-bundle", type=Path, default=DEFAULT_EQUITY_BUNDLE)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--generated-at", type=_timestamp, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        result = rebuild(
            bundle_dir=args.bundle_dir,
            expected_manifest_hash=args.expected_manifest_hash,
            equity_bundle=args.equity_bundle,
            database_url=args.database_url,
            output_root=args.output_root,
            generated_at=args.generated_at,
            prepare_only=args.prepare_only,
        )
    except (KeyError, OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"SEC multi-factor rebuild failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
