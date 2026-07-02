"""Run the unchanged baseline over historical point-in-time memberships."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import (
        get_code_revision,
        open_research_database,
        repository_relative_path,
    )
except ModuleNotFoundError:  # Imported through pipelines in tests.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        get_code_revision,
        open_research_database,
        repository_relative_path,
    )

from quantfore_research.backtest.point_in_time import (
    DEFAULT_MINIMUM_COHORT_COVERAGE,
    build_dynamic_universe_report,
    run_dynamic_universe_backtest,
)
from quantfore_research.db import session_scope
from quantfore_research.models import (
    Price,
    SourceSnapshot,
    UniverseDefinition,
    UniverseMembership,
)
from quantfore_research.validation.reproducibility import universe_membership_hash
from sqlalchemy import select


DEFAULT_AUDIT = Path("reports/data-audits/pit-equity-panel-v1.json")
DEFAULT_JSON_OUTPUT = Path("reports/backtests/pit_baseline_v0_1.json")
DEFAULT_MARKDOWN_OUTPUT = Path("reports/backtests/pit_baseline_v0_1.md")
DEFAULT_LINEAGE_OUTPUT = Path("reports/backtests/pit_baseline_v0_1.lineage.json")


def _date(value: str):
    from datetime import date

    return date.fromisoformat(value)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class PassingAuditBinding:
    audit_sha256: str
    membership_content_hash: str
    universe_snapshot_id: str
    universe_source_hash: str
    membership_snapshots: Mapping[str, str]
    price_snapshots_by_security: Mapping[str, str]
    price_snapshot_hashes_by_security: Mapping[str, str]


def load_passing_audit(path: Path, *, universe_id: str) -> PassingAuditBinding:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read point-in-time audit: {path}") from exc
    audit = document.get("audit")
    if not isinstance(audit, dict):
        raise ValueError("point-in-time audit document is missing audit details")
    if audit.get("universe_id") != universe_id:
        raise ValueError("point-in-time audit universe does not match the run")
    if audit.get("hard_failure_count") != 0 or document.get("decision") == "fail":
        raise ValueError("point-in-time backtest requires an audit with no hard failures")
    if document.get("claims_eligible") is not False:
        raise ValueError("point-in-time audit must retain claims_eligible=false")
    binding = audit.get("dataset_binding")
    if not isinstance(binding, dict):
        raise ValueError("point-in-time audit is missing its dataset binding")
    membership_hash = binding.get("membership_content_hash")
    universe_snapshot = binding.get("universe_snapshot")
    membership_snapshots = binding.get("membership_snapshots")
    price_snapshots = binding.get("price_snapshots_by_security")
    if not isinstance(membership_hash, str) or len(membership_hash) != 64:
        raise ValueError("point-in-time audit has an invalid membership content hash")
    if not isinstance(universe_snapshot, dict):
        raise ValueError("point-in-time audit lacks a universe snapshot binding")
    if not isinstance(membership_snapshots, dict) or not membership_snapshots:
        raise ValueError("point-in-time audit lacks membership snapshot bindings")
    if not isinstance(price_snapshots, dict) or not price_snapshots:
        raise ValueError("point-in-time audit lacks price snapshot bindings")
    normalized_prices = {}
    normalized_price_hashes = {}
    for security_id, snapshot in price_snapshots.items():
        if not isinstance(snapshot, dict):
            raise ValueError("point-in-time audit has an invalid price snapshot binding")
        snapshot_id = snapshot.get("snapshot_id")
        source_hash = snapshot.get("source_hash")
        if not isinstance(snapshot_id, str) or not isinstance(source_hash, str):
            raise ValueError("point-in-time audit has an invalid price snapshot binding")
        normalized_prices[security_id] = snapshot_id
        normalized_price_hashes[security_id] = source_hash
    universe_snapshot_id = universe_snapshot.get("snapshot_id")
    universe_source_hash = universe_snapshot.get("source_hash")
    if (
        not isinstance(universe_snapshot_id, str)
        or not universe_snapshot_id
        or not isinstance(universe_source_hash, str)
        or len(universe_source_hash) != 64
    ):
        raise ValueError("point-in-time audit has an invalid universe snapshot binding")
    return PassingAuditBinding(
        audit_sha256=_sha256(path),
        membership_content_hash=membership_hash,
        universe_snapshot_id=universe_snapshot_id,
        universe_source_hash=universe_source_hash,
        membership_snapshots={str(key): str(value) for key, value in membership_snapshots.items()},
        price_snapshots_by_security=normalized_prices,
        price_snapshot_hashes_by_security=normalized_price_hashes,
    )


def validate_audit_binding(
    session,
    *,
    universe_id: str,
    binding: PassingAuditBinding,
) -> None:
    universe = session.get(UniverseDefinition, universe_id)
    if universe is None:
        raise ValueError(f"unknown universe: {universe_id}")
    if (
        universe.source_snapshot_id != binding.universe_snapshot_id
        or universe.source_hash != binding.universe_source_hash
    ):
        raise ValueError("database universe snapshot differs from the passing audit")
    if (
        universe_membership_hash(session, universe_id=universe_id)
        != binding.membership_content_hash
    ):
        raise ValueError("database membership content differs from the passing audit")
    memberships = session.scalars(
        select(UniverseMembership).where(
            UniverseMembership.universe_id == universe_id
        )
    ).all()
    actual_membership_snapshots = {}
    for row in memberships:
        snapshot = session.get(SourceSnapshot, row.source_snapshot_id)
        if snapshot is None:
            raise ValueError("database membership snapshot is missing")
        actual_membership_snapshots[snapshot.snapshot_id] = snapshot.source_hash
    if actual_membership_snapshots != dict(binding.membership_snapshots):
        raise ValueError("database membership snapshots differ from the passing audit")
    required_security_ids = {
        universe.benchmark_security_id,
        *(row.security_id for row in memberships),
    }
    if set(binding.price_snapshots_by_security) != required_security_ids:
        raise ValueError("audit price bindings do not cover the exact universe securities")
    for security_id, snapshot_id in binding.price_snapshots_by_security.items():
        snapshot = session.get(SourceSnapshot, snapshot_id)
        exists = session.scalar(
            select(Price.price_id)
            .where(Price.security_id == security_id)
            .where(Price.source_snapshot_id == snapshot_id)
            .limit(1)
        )
        if (
            snapshot is None
            or exists is None
            or snapshot.source_hash
            != binding.price_snapshot_hashes_by_security.get(security_id)
        ):
            raise ValueError("an audited price snapshot is absent from the database")


def render_markdown(report: Mapping[str, Any]) -> str:
    config = report["configuration"]
    counts = report["observation_counts"]
    lines = [
        "# Point-in-Time Dynamic-Universe Baseline v0.1",
        "",
        "`claims_eligible=false`",
        "",
        f"- Experiment: `{config['experiment_id']}`",
        f"- Universe: `{config['universe_id']}`",
        f"- Model: `{config['model_version']}`",
        f"- Horizon: `{config['horizon']}`",
        f"- Minimum cohort coverage: `{config['minimum_cohort_coverage']}`",
        f"- Coverage gate passed: `{str(report['coverage_gate_passed']).lower()}`",
        "",
        "## Observation counts",
        "",
        f"- Expected: `{counts['expected']}`",
        f"- Features built: `{counts['features_built']}`",
        f"- Evaluated: `{counts['evaluated']}`",
        f"- Delisted outcomes: `{counts['delisted_outcomes']}`",
        "",
        "## Monthly cohorts",
        "",
        "| Date | Expected | Features | Evaluated | Coverage | Delisted |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for cohort in report["cohorts"]:
        lines.append(
            f"| {cohort['prediction_date']} | {cohort['expected_count']} | "
            f"{cohort['feature_count']} | {cohort['evaluated_count']} | "
            f"{cohort['coverage']} | {cohort['delisted_outcome_count']} |"
        )
    lines.extend(["", "## Exclusions", ""])
    exclusions = [
        row for cohort in report["cohorts"] for row in cohort["exclusions"]
    ]
    if not exclusions:
        lines.append("None.")
    else:
        lines.extend(
            [
                "| Date | Ticker | Stage | Reason | Detail |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for row in exclusions:
            detail = row["detail"].replace("|", "\\|")
            lines.append(
                f"| {row['prediction_date']} | {row['ticker']} | {row['stage']} | "
                f"`{row['reason_code']}` | {detail} |"
            )
    lines.extend(
        [
            "",
            "## Claims boundary",
            "",
            "This is the unchanged baseline evaluated on a survivorship-aware "
            "historical universe. It remains ineligible for model or investment "
            "claims until every Sprint 7 gate passes.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def write_reports(
    report: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    json_output: Path,
    markdown_output: Path,
    lineage_output: Path,
) -> None:
    _write_atomic(
        json_output,
        (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    _write_atomic(markdown_output, render_markdown(report).encode("utf-8"))
    _write_atomic(
        lineage_output,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the Sprint 7 dynamic-universe baseline backtest."
    )
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--universe-id", default="sp500-pit-v1")
    parser.add_argument("--start-date", type=_date, required=True)
    parser.add_argument("--end-date", type=_date, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--source-snapshot-id")
    parser.add_argument(
        "--minimum-coverage",
        type=Decimal,
        default=DEFAULT_MINIMUM_COHORT_COVERAGE,
    )
    parser.add_argument("--audit-json", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument(
        "--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT
    )
    parser.add_argument("--lineage-output", type=Path, default=DEFAULT_LINEAGE_OUTPUT)
    parser.add_argument("--evaluated-at", type=_timestamp)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        audit_binding = load_passing_audit(
            args.audit_json, universe_id=args.universe_id
        )
        session_factory = open_research_database(args.database_url)
        with session_scope(session_factory) as session:
            validate_audit_binding(
                session,
                universe_id=args.universe_id,
                binding=audit_binding,
            )
            result = run_dynamic_universe_backtest(
                session,
                experiment_id=args.experiment_id,
                universe_id=args.universe_id,
                start_date=args.start_date,
                end_date=args.end_date,
                price_source_snapshot_id=args.source_snapshot_id,
                price_snapshot_ids_by_security=(
                    audit_binding.price_snapshots_by_security
                ),
                minimum_coverage=args.minimum_coverage,
                code_commit=get_code_revision(),
                evaluated_at=args.evaluated_at,
                result_uri=repository_relative_path(args.json_output),
                audit_sha256=audit_binding.audit_sha256,
            )
            report = build_dynamic_universe_report(session, result=result)
            manifest = result.to_manifest()
        write_reports(
            report,
            manifest=manifest,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
            lineage_output=args.lineage_output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"point-in-time backtest failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"point-in-time backtest complete experiment={result.experiment_id} "
        f"cohorts={len(result.cohorts)} coverage_gate_passed="
        f"{str(result.coverage_gate_passed).lower()} json_report={args.json_output}"
    )
    return 0 if result.coverage_gate_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
