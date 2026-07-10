"""Run and compare two clean Sprint 8 rebuilds from one frozen bundle.

The full closure takes roughly 48 minutes of continuous CPU. On machines that
system-sleep when idle, launch it wake-locked and detached, e.g.::

    nohup caffeinate -dims env PYTHONPATH=... python \
        pipelines/close_multifactor_sprint.py ... > closure.log 2>&1 &
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import open_research_database
    from close_point_in_time_sprint import DirtyWorktreeError, require_clean_git_worktree
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import open_research_database  # type: ignore
    from pipelines.close_point_in_time_sprint import (  # type: ignore
        DirtyWorktreeError,
        require_clean_git_worktree,
    )

from quantfore_research.validation.reproducibility import (
    canonical_json_bytes,
    sha256_bytes,
)
from quantfore_research.validation.sprint8_reproducibility import (
    Sprint8RebuildFingerprint,
    build_sprint8_rebuild_fingerprint,
    compare_sprint8_rebuilds,
)


DEFAULT_AUDIT = Path("reports/data-audits/pit-fundamentals-v1.json")
DEFAULT_BACKTEST = Path("reports/backtests/pit_multifactor_baseline_v1.json")
DEFAULT_COMPARISON = Path("reports/comparisons/price-vs-multifactor-v1.json")
DEFAULT_CLOSURE_JSON = Path("reports/reproducibility/sprint8-closure-v1.json")
DEFAULT_CLOSURE_MARKDOWN = Path("reports/reproducibility/sprint8-closure-v1.md")


@dataclass(frozen=True)
class Sprint8RebuildArtifacts:
    fingerprint: Sprint8RebuildFingerprint
    audit: Mapping[str, Any]
    backtest: Mapping[str, Any]
    comparison: Mapping[str, Any]


def _read_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"artifact must be a JSON object: {path}")
    return value


def _database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.resolve()}"


def validate_rebuild_program(path: Path, *, expected_sha256: str) -> str:
    """Bind the external rebuild executable to exact bytes before execution."""

    body = path.read_bytes()
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected_sha256.lower():
        raise ValueError("Sprint 8 rebuild program SHA-256 does not match")
    return actual


def _audit_source_snapshot_ids(audit: Mapping[str, Any]) -> tuple[str, ...]:
    source_hashes = audit.get("source_snapshot_hashes")
    if (
        not isinstance(source_hashes, dict)
        or not source_hashes
        or not all(
            isinstance(snapshot_id, str)
            and snapshot_id
            and isinstance(source_hash, str)
            and len(source_hash) == 64
            for snapshot_id, source_hash in source_hashes.items()
        )
    ):
        raise ValueError("Sprint 8 audit lacks frozen source snapshot bindings")
    return tuple(sorted(source_hashes))


def _run_clean_rebuild(
    *,
    rebuild_program: Path,
    expected_rebuild_program_sha256: str,
    bundle_dir: Path,
    expected_manifest_hash: str,
    run_dir: Path,
    evidence_timestamp: datetime,
) -> Sprint8RebuildArtifacts:
    validate_rebuild_program(
        rebuild_program, expected_sha256=expected_rebuild_program_sha256
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    database_path = run_dir / "research.db"
    report_root = run_dir / "reports"
    command = [str(rebuild_program)]
    if rebuild_program.suffix == ".py":
        command.insert(0, sys.executable)
    command.extend(
        [
            "--bundle-dir",
            str(bundle_dir.resolve()),
            "--expected-manifest-hash",
            expected_manifest_hash,
            "--database-url",
            _database_url(database_path),
            "--output-root",
            str(run_dir.resolve()),
            "--generated-at",
            evidence_timestamp.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
        ]
    )
    environment = os.environ.copy()
    environment["QUANTFORE_SPRINT8_CLEAN_REBUILD"] = "1"
    subprocess.run(command, check=True, env=environment)
    audit = _read_json(report_root / "data-audits" / "pit-fundamentals-v1.json")
    backtest = _read_json(
        report_root / "backtests" / "pit_multifactor_baseline_v1.json"
    )
    comparison = _read_json(
        report_root / "comparisons" / "price-vs-multifactor-v1.json"
    )
    factory = open_research_database(_database_url(database_path))
    with factory() as session:
        fingerprint = build_sprint8_rebuild_fingerprint(
            session,
            fundamental_source_snapshot_ids=_audit_source_snapshot_ids(audit),
            audit_document=audit,
            backtest_document=backtest,
            comparison_document=comparison,
        )
    return Sprint8RebuildArtifacts(fingerprint, audit, backtest, comparison)


def validate_sprint7_prerequisite(
    path: Path, *, expected_sha256: str
) -> Mapping[str, Any]:
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected_sha256.lower():
        raise ValueError("Sprint 7 closure SHA-256 does not match")
    document = json.loads(body)
    if (
        not isinstance(document, dict)
        or document.get("schema_version") != "sprint7_reproducibility_closure_v1"
        or document.get("closure_decision") != "pass"
        or document.get("clean_worktree_verified") is not True
        or document.get("claims_eligible") is not False
    ):
        raise ValueError("Sprint 7 passing closure prerequisite is not satisfied")
    return document


def build_sprint8_closure_document(
    *,
    first: Sprint8RebuildArtifacts,
    second: Sprint8RebuildArtifacts,
    git_commit: str,
    bundle_manifest_sha256: str,
    sprint7_closure_sha256: str,
    rebuild_program_sha256: str,
    generated_at: datetime,
) -> dict[str, Any]:
    comparison = compare_sprint8_rebuilds(first.fingerprint, second.fingerprint)
    if not comparison["all_matched"]:
        failed = [
            name for name, row in comparison["checks"].items() if not row["matched"]
        ]
        raise ValueError("Sprint 8 clean rebuilds differ in: " + ", ".join(failed))
    return {
        "schema_version": "sprint8_reproducibility_closure_v1",
        "claims_eligible": False,
        "closure_decision": "pass",
        "clean_worktree_verified": True,
        "git_commit": git_commit,
        "bundle_manifest_sha256": bundle_manifest_sha256,
        "sprint7_closure_sha256": sprint7_closure_sha256,
        "rebuild_program_sha256": rebuild_program_sha256,
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "configuration": {
            "rebuild_count": 2,
            "database_kind": "fresh_temporary_sqlite",
            "source_bundle_reused": True,
        },
        "reproducibility": comparison,
        "canonical_evidence": first.fingerprint.canonical_report_hashes,
        "definition_of_done": (
            "Two fresh databases rebuilt from the same frozen raw bundle produce "
            "identical point-in-time fundamentals, availability/revisions, features, "
            "eligible monthly cohorts, predictions, outcomes, metrics, and reports."
        ),
    }


def render_markdown(document: Mapping[str, Any]) -> str:
    lines = [
        "# Sprint 8 Reproducibility and Closure",
        "",
        "`claims_eligible=false`",
        "",
        f"- Decision: `{document['closure_decision']}`",
        f"- Clean commit: `{document['git_commit']}`",
        f"- Bundle manifest: `{document['bundle_manifest_sha256']}`",
        f"- Sprint 7 closure: `{document['sprint7_closure_sha256']}`",
        f"- Rebuild program: `{document['rebuild_program_sha256']}`",
        "",
        "## Two-rebuild comparison",
        "",
        "| Invariant | Matched |",
        "| --- | --- |",
    ]
    for name, row in document["reproducibility"]["checks"].items():
        lines.append(f"| {name.replace('_', ' ').title()} | `{str(row['matched']).lower()}` |")
    lines.extend(
        [
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


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run two clean Sprint 8 rebuilds and compare every invariant."
    )
    parser.add_argument("bundle_dir", type=Path)
    parser.add_argument("--expected-manifest-hash", required=True)
    parser.add_argument("--rebuild-program", required=True, type=Path)
    parser.add_argument("--expected-rebuild-program-hash", required=True)
    parser.add_argument("--sprint7-closure-json", required=True, type=Path)
    parser.add_argument("--expected-sprint7-closure-hash", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--audit-output", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--backtest-output", type=Path, default=DEFAULT_BACKTEST)
    parser.add_argument("--comparison-output", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--closure-json-output", type=Path, default=DEFAULT_CLOSURE_JSON)
    parser.add_argument(
        "--closure-markdown-output", type=Path, default=DEFAULT_CLOSURE_MARKDOWN
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        git_commit = require_clean_git_worktree()
        manifest_path = args.bundle_dir / "manifest.json"
        manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if manifest_hash != args.expected_manifest_hash.lower():
            raise ValueError("frozen Sprint 8 bundle manifest SHA-256 does not match")
        validate_sprint7_prerequisite(
            args.sprint7_closure_json,
            expected_sha256=args.expected_sprint7_closure_hash,
        )
        rebuild_program_hash = validate_rebuild_program(
            args.rebuild_program,
            expected_sha256=args.expected_rebuild_program_hash,
        )
        generated_at = datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
        if generated_at.tzinfo is None:
            raise ValueError("--generated-at must include a timezone")
        with tempfile.TemporaryDirectory(prefix="quantfore-sprint8-closure-") as root:
            working = Path(root)
            first = _run_clean_rebuild(
                rebuild_program=args.rebuild_program,
                expected_rebuild_program_sha256=rebuild_program_hash,
                bundle_dir=args.bundle_dir,
                expected_manifest_hash=manifest_hash,
                run_dir=working / "first",
                evidence_timestamp=generated_at,
            )
            second = _run_clean_rebuild(
                rebuild_program=args.rebuild_program,
                expected_rebuild_program_sha256=rebuild_program_hash,
                bundle_dir=args.bundle_dir,
                expected_manifest_hash=manifest_hash,
                run_dir=working / "second",
                evidence_timestamp=generated_at,
            )
            closure = build_sprint8_closure_document(
                first=first,
                second=second,
                git_commit=git_commit,
                bundle_manifest_sha256=manifest_hash,
                sprint7_closure_sha256=args.expected_sprint7_closure_hash.lower(),
                rebuild_program_sha256=rebuild_program_hash,
                generated_at=generated_at,
            )
        outputs = (
            (args.audit_output, canonical_json_bytes(first.audit, pretty=True)),
            (args.backtest_output, canonical_json_bytes(first.backtest, pretty=True)),
            (args.comparison_output, canonical_json_bytes(first.comparison, pretty=True)),
            (args.closure_json_output, canonical_json_bytes(closure, pretty=True)),
            (args.closure_markdown_output, render_markdown(closure).encode()),
        )
        for path, payload in outputs:
            _write_atomic(path, payload)
    except (
        DirtyWorktreeError,
        json.JSONDecodeError,
        OSError,
        subprocess.CalledProcessError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"Sprint 8 closure failed: {exc}", file=sys.stderr)
        return 2
    print(
        "Sprint 8 closure complete "
        f"commit={git_commit} closure_sha256="
        f"{sha256_bytes(canonical_json_bytes(closure, pretty=True))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
