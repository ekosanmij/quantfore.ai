"""Prove Sprint 7 reproducibility with two clean point-in-time rebuilds."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import REPOSITORY_ROOT, open_research_database, repository_relative_path
except ModuleNotFoundError:  # Imported through pipelines in tests.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        REPOSITORY_ROOT,
        open_research_database,
        repository_relative_path,
    )

from quantfore_research.backtest.point_in_time import (
    DEFAULT_MINIMUM_COHORT_COVERAGE,
    build_dynamic_universe_report,
    run_dynamic_universe_backtest,
)
from quantfore_research.db import session_scope
from quantfore_research.ingest.point_in_time_equities import (
    NormalizedPointInTimeBundle,
    PointInTimeEquityBundleAdapter,
    PointInTimeIngestionError,
)
from quantfore_research.validation.point_in_time_audit import (
    audit_point_in_time_equity_panel,
)
from quantfore_research.validation.reproducibility import (
    RebuildFingerprint,
    build_rebuild_fingerprint,
    canonical_json_bytes,
    compare_rebuild_fingerprints,
    sha256_bytes,
)
from pipelines.audit_point_in_time_equities import (
    build_audit_document,
    render_markdown as render_audit_markdown,
)
from pipelines.ingest_point_in_time_equities import persist_bundle
from pipelines.run_point_in_time_backtest import (
    render_markdown as render_backtest_markdown,
)


DEFAULT_AUDIT_JSON_OUTPUT = Path("reports/data-audits/pit-equity-panel-v1.json")
DEFAULT_AUDIT_MARKDOWN_OUTPUT = Path("reports/data-audits/pit-equity-panel-v1.md")
DEFAULT_BACKTEST_JSON_OUTPUT = Path("reports/backtests/pit_baseline_v0_1.json")
DEFAULT_BACKTEST_MARKDOWN_OUTPUT = Path("reports/backtests/pit_baseline_v0_1.md")
DEFAULT_BACKTEST_LINEAGE_OUTPUT = Path(
    "reports/backtests/pit_baseline_v0_1.lineage.json"
)
DEFAULT_CLOSURE_JSON_OUTPUT = Path("reports/reproducibility/sprint7-closure-v1.json")
DEFAULT_CLOSURE_MARKDOWN_OUTPUT = Path("reports/reproducibility/sprint7-closure-v1.md")


class DirtyWorktreeError(RuntimeError):
    """Closure evidence cannot be generated from uncommitted source state."""


@dataclass(frozen=True)
class RebuildArtifacts:
    fingerprint: RebuildFingerprint
    audit_document: Mapping[str, Any]
    audit_json: bytes
    audit_markdown: bytes
    backtest_report: Mapping[str, Any]
    backtest_json: bytes
    backtest_markdown: bytes
    backtest_lineage: Mapping[str, Any]
    lineage_json: bytes


def require_clean_git_worktree(repository_root: Path = REPOSITORY_ROOT) -> str:
    """Return the exact commit only when the complete Git worktree is clean."""

    try:
        top_level = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repository_root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        status = subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ],
            cwd=repository_root,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise DirtyWorktreeError("Sprint 7 closure requires a valid Git checkout") from exc
    if Path(top_level).resolve() != repository_root.resolve():
        raise DirtyWorktreeError("repository root does not match the Git checkout root")
    if status:
        changed = [line[3:] for line in status.splitlines()[:5]]
        detail = ", ".join(changed)
        suffix = "" if len(status.splitlines()) <= 5 else ", ..."
        raise DirtyWorktreeError(
            f"Sprint 7 closure refuses a dirty Git worktree: {detail}{suffix}"
        )
    if not commit:
        raise DirtyWorktreeError("Sprint 7 closure could not resolve HEAD")
    return commit


def _database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.resolve()}"


def _run_clean_rebuild(
    *,
    bundle: NormalizedPointInTimeBundle,
    run_directory: Path,
    universe_id: str,
    calendar: str,
    start_date: date,
    end_date: date,
    experiment_id: str,
    minimum_coverage: Decimal,
    source_snapshot_id: Optional[str],
    evidence_timestamp: datetime,
    git_commit: str,
) -> RebuildArtifacts:
    database_path = run_directory / "research.db"
    raw_directory = run_directory / "data" / "raw"
    database_url = _database_url(database_path)
    persist_bundle(bundle, database_url=database_url, raw_dir=raw_directory)

    session_factory = open_research_database(database_url)
    with session_factory() as session:
        audit = audit_point_in_time_equity_panel(
            session,
            universe_id=universe_id,
            calendar=calendar,
            audit_as_of=evidence_timestamp,
        )
    audit_document = build_audit_document(
        audit,
        generated_at=evidence_timestamp,
        code_revision=git_commit,
    )
    if audit.hard_failure_count:
        raise ValueError(
            f"clean rebuild audit failed with {audit.hard_failure_count} hard findings"
        )
    audit_json = canonical_json_bytes(audit_document, pretty=True)
    audit_hash = sha256_bytes(audit_json)
    audited_price_snapshots = {
        security_id: value["snapshot_id"]
        for security_id, value in audit.snapshot_binding[
            "price_snapshots_by_security"
        ].items()
    }

    with session_scope(session_factory) as session:
        result = run_dynamic_universe_backtest(
            session,
            experiment_id=experiment_id,
            universe_id=universe_id,
            start_date=start_date,
            end_date=end_date,
            price_source_snapshot_id=source_snapshot_id,
            price_snapshot_ids_by_security=audited_price_snapshots,
            minimum_coverage=minimum_coverage,
            code_commit=git_commit,
            evaluated_at=evidence_timestamp,
            result_uri=repository_relative_path(DEFAULT_BACKTEST_JSON_OUTPUT),
            audit_sha256=audit_hash,
        )
        report = build_dynamic_universe_report(session, result=result)
        lineage = result.to_manifest()
    if not result.coverage_gate_passed:
        raise ValueError("clean rebuild failed the point-in-time cohort coverage gate")

    with session_factory() as session:
        fingerprint = build_rebuild_fingerprint(
            session,
            universe_id=universe_id,
            audit_document=audit_document,
            backtest_report=report,
            backtest_lineage=lineage,
        )
    backtest_json = canonical_json_bytes(report, pretty=True)
    lineage_json = canonical_json_bytes(lineage, pretty=True)
    return RebuildArtifacts(
        fingerprint=fingerprint,
        audit_document=audit_document,
        audit_json=audit_json,
        audit_markdown=render_audit_markdown(dict(audit_document)).encode("utf-8"),
        backtest_report=report,
        backtest_json=backtest_json,
        backtest_markdown=render_backtest_markdown(report).encode("utf-8"),
        backtest_lineage=lineage,
        lineage_json=lineage_json,
    )


def build_closure_document(
    *,
    first: RebuildArtifacts,
    second: RebuildArtifacts,
    git_commit: str,
    bundle_manifest_sha256: str,
    universe_id: str,
    start_date: date,
    end_date: date,
    experiment_id: str,
    evidence_timestamp: datetime,
    price_exclusions_sha256: str = "0" * 64,
    price_exclusion_count: int = 0,
    minimum_monthly_price_coverage: str = "1",
) -> dict[str, Any]:
    comparison = compare_rebuild_fingerprints(
        first.fingerprint, second.fingerprint
    )
    if not comparison["all_matched"]:
        failed = [
            name
            for name, check in comparison["checks"].items()
            if not check["matched"]
        ]
        raise ValueError("clean rebuilds differ in: " + ", ".join(failed))
    return {
        "schema_version": "sprint7_reproducibility_closure_v1",
        "claims_eligible": False,
        "closure_decision": "pass",
        "clean_worktree_verified": True,
        "git_commit": git_commit,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "price_exclusions_sha256": price_exclusions_sha256,
        "configuration": {
            "universe_id": universe_id,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "experiment_id": experiment_id,
            "evidence_timestamp": evidence_timestamp.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "rebuild_count": 2,
            "database_kind": "fresh_temporary_sqlite",
            "price_exclusion_count": price_exclusion_count,
            "minimum_monthly_price_coverage": minimum_monthly_price_coverage,
        },
        "reproducibility": comparison,
        "canonical_evidence": {
            "audit_sha256": first.fingerprint.canonical_audit_sha256,
            "backtest_report_sha256": (
                first.fingerprint.canonical_report_sha256
            ),
            "backtest_lineage_sha256": sha256_bytes(first.lineage_json),
        },
        "definition_of_done": (
            "For any historical monthly date, the system reconstructs the "
            "securities that were eligible then, uses only data available then, "
            "retains companies that later disappeared, and produces a "
            "deterministic baseline evaluation."
        ),
    }


def render_markdown(document: Mapping[str, Any]) -> str:
    config = document["configuration"]
    checks = document["reproducibility"]["checks"]
    lines = [
        "# Sprint 7 Reproducibility and Closure",
        "",
        "`claims_eligible=false`",
        "",
        f"- Decision: `{document['closure_decision']}`",
        f"- Clean commit: `{document['git_commit']}`",
        f"- Bundle manifest: `{document['bundle_manifest_sha256']}`",
        f"- Price exclusions: `{document['price_exclusions_sha256']}`",
        f"- Minimum full-universe price coverage: `{config['minimum_monthly_price_coverage']}`",
        f"- Universe: `{config['universe_id']}`",
        f"- Backtest window: `{config['start_date']}` through `{config['end_date']}`",
        f"- Evidence timestamp: `{config['evidence_timestamp']}`",
        "",
        "## Clean rebuild comparison",
        "",
        "| Invariant | Matched | Rebuild value |",
        "| --- | --- | --- |",
    ]
    labels = {
        "universe_membership_hash": "Universe membership hash",
        "security_count_by_month": "Security count by month",
        "prediction_count": "Prediction count",
        "outcome_count": "Outcome count",
        "dataset_audit_decision": "Dataset audit decision",
        "backtest_metrics": "Backtest metrics",
        "canonical_report_sha256": "Canonical report hash",
        "canonical_audit_sha256": "Canonical audit hash",
    }
    for field, check in checks.items():
        value = check["first"]
        if isinstance(value, dict):
            value = "see JSON evidence"
        lines.append(
            f"| {labels[field]} | `{str(check['matched']).lower()}` | `{value}` |"
        )
    evidence = document["canonical_evidence"]
    lines.extend(
        [
            "",
            "## Canonical evidence",
            "",
            f"- Audit SHA-256: `{evidence['audit_sha256']}`",
            f"- Backtest report SHA-256: `{evidence['backtest_report_sha256']}`",
            f"- Backtest lineage SHA-256: `{evidence['backtest_lineage_sha256']}`",
            "",
            "## Definition of done",
            "",
            f"> {document['definition_of_done']}",
            "",
        ]
    )
    return "\n".join(lines)


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("evidence timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run two clean rebuilds and close Sprint 7 reproducibility."
    )
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--expected-manifest-hash", required=True)
    parser.add_argument("--price-exclusions", type=Path)
    parser.add_argument("--expected-price-exclusions-hash")
    parser.add_argument("--universe-id", default="sp500-pit-v1")
    parser.add_argument("--calendar", default="XNYS")
    parser.add_argument("--start-date", type=_date, required=True)
    parser.add_argument("--end-date", type=_date, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--evidence-timestamp", type=_timestamp, required=True)
    parser.add_argument("--source-snapshot-id")
    parser.add_argument(
        "--minimum-coverage",
        type=Decimal,
        default=DEFAULT_MINIMUM_COHORT_COVERAGE,
    )
    parser.add_argument(
        "--audit-json-output", type=Path, default=DEFAULT_AUDIT_JSON_OUTPUT
    )
    parser.add_argument(
        "--audit-markdown-output",
        type=Path,
        default=DEFAULT_AUDIT_MARKDOWN_OUTPUT,
    )
    parser.add_argument(
        "--backtest-json-output", type=Path, default=DEFAULT_BACKTEST_JSON_OUTPUT
    )
    parser.add_argument(
        "--backtest-markdown-output",
        type=Path,
        default=DEFAULT_BACKTEST_MARKDOWN_OUTPUT,
    )
    parser.add_argument(
        "--backtest-lineage-output",
        type=Path,
        default=DEFAULT_BACKTEST_LINEAGE_OUTPUT,
    )
    parser.add_argument(
        "--closure-json-output", type=Path, default=DEFAULT_CLOSURE_JSON_OUTPUT
    )
    parser.add_argument(
        "--closure-markdown-output",
        type=Path,
        default=DEFAULT_CLOSURE_MARKDOWN_OUTPUT,
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        git_commit = require_clean_git_worktree()
        if args.price_exclusions is None or not args.expected_price_exclusions_hash:
            raise ValueError(
                "--price-exclusions and --expected-price-exclusions-hash are required"
            )
        bundle = PointInTimeEquityBundleAdapter(
            args.bundle_dir,
            expected_manifest_hash=args.expected_manifest_hash,
        ).load()
        if bundle.universe_id != args.universe_id:
            raise ValueError("bundle universe does not match --universe-id")
        if args.start_date != bundle.window_start or args.end_date != bundle.window_end:
            raise ValueError("closure dates must exactly match the bundle window")
        exclusion_body = args.price_exclusions.read_bytes()
        exclusion_hash = sha256_bytes(exclusion_body)
        if exclusion_hash != args.expected_price_exclusions_hash.lower():
            raise ValueError("price exclusion SHA-256 does not match")
        exclusion_document = json.loads(exclusion_body)
        if not (
            exclusion_document.get("schema_version") == "free-pit-price-exclusions-v1"
            and exclusion_document.get("coverage_gate_passed") is True
            and exclusion_document.get("unaccounted_episode_count") == 0
            and exclusion_document.get("window_start") == bundle.window_start.isoformat()
            and exclusion_document.get("window_end") == bundle.window_end.isoformat()
        ):
            raise ValueError("price exclusion coverage evidence is invalid")
        with tempfile.TemporaryDirectory(prefix="quantfore-sprint7-closure-") as root:
            working = Path(root)
            first = _run_clean_rebuild(
                bundle=bundle,
                run_directory=working / "first",
                universe_id=args.universe_id,
                calendar=args.calendar,
                start_date=args.start_date,
                end_date=args.end_date,
                experiment_id=args.experiment_id,
                minimum_coverage=args.minimum_coverage,
                source_snapshot_id=args.source_snapshot_id,
                evidence_timestamp=args.evidence_timestamp,
                git_commit=git_commit,
            )
            second = _run_clean_rebuild(
                bundle=bundle,
                run_directory=working / "second",
                universe_id=args.universe_id,
                calendar=args.calendar,
                start_date=args.start_date,
                end_date=args.end_date,
                experiment_id=args.experiment_id,
                minimum_coverage=args.minimum_coverage,
                source_snapshot_id=args.source_snapshot_id,
                evidence_timestamp=args.evidence_timestamp,
                git_commit=git_commit,
            )
            closure = build_closure_document(
                first=first,
                second=second,
                git_commit=git_commit,
                bundle_manifest_sha256=bundle.manifest.source_hash,
                universe_id=args.universe_id,
                start_date=args.start_date,
                end_date=args.end_date,
                experiment_id=args.experiment_id,
                evidence_timestamp=args.evidence_timestamp,
                price_exclusions_sha256=exclusion_hash,
                price_exclusion_count=int(exclusion_document["exclusion_count"]),
                minimum_monthly_price_coverage=str(
                    exclusion_document["minimum_monthly_coverage"]
                ),
            )
        outputs = (
            (args.audit_json_output, first.audit_json),
            (args.audit_markdown_output, first.audit_markdown),
            (args.backtest_json_output, first.backtest_json),
            (args.backtest_markdown_output, first.backtest_markdown),
            (args.backtest_lineage_output, first.lineage_json),
            (args.closure_json_output, canonical_json_bytes(closure, pretty=True)),
            (args.closure_markdown_output, render_markdown(closure).encode("utf-8")),
        )
        for path, payload in outputs:
            _write_atomic(path, payload)
    except (
        DirtyWorktreeError,
        OSError,
        PointInTimeIngestionError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"Sprint 7 closure failed: {exc}", file=sys.stderr)
        return 2
    closure_hash = sha256_bytes(canonical_json_bytes(closure, pretty=True))
    print(
        "Sprint 7 closure complete "
        f"commit={git_commit} report_sha256="
        f"{first.fingerprint.canonical_report_sha256} "
        f"closure_sha256={closure_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
