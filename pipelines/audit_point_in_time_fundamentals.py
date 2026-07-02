"""Audit point-in-time fundamentals and independent SEC reconciliation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import get_code_revision, open_research_database
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import get_code_revision, open_research_database  # type: ignore

from quantfore_research.validation.point_in_time_fundamentals import (
    HARD,
    PointInTimeFundamentalAudit,
    SecReconciliationSample,
    audit_point_in_time_fundamentals,
    derive_sec_reconciliation_samples,
)


DEFAULT_JSON_OUTPUT = Path("reports/data-audits/pit-fundamentals-v1.json")
DEFAULT_MARKDOWN_OUTPUT = Path("reports/data-audits/pit-fundamentals-v1.md")


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def load_reconciliation_samples(path: Path) -> tuple[SecReconciliationSample, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document.get("samples") if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise ValueError("reconciliation JSON must be an array or contain samples[]")
    return tuple(SecReconciliationSample.from_mapping(row) for row in rows)


def load_candidate_fact_ids(path: Optional[Path]) -> Optional[tuple[str, ...]]:
    if path is None:
        return None
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, list) or not all(
        isinstance(value, str) and value.strip() for value in values
    ):
        raise ValueError("candidate fact JSON must be an array of IDs")
    return tuple(value.strip() for value in values)


def build_audit_document(
    audit: PointInTimeFundamentalAudit,
    *,
    generated_at: datetime,
    code_revision: Optional[str] = None,
    source_snapshot_hashes: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    return {
        "audit_id": "pit-fundamentals-v1",
        "dataset_kind": "proof_candidate_point_in_time",
        "claims_eligible": False,
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "code_revision": code_revision or get_code_revision(),
        "source_snapshot_hashes": dict(sorted((source_snapshot_hashes or {}).items())),
        "decision": audit.status,
        "audit": audit.to_dict(),
    }


def render_markdown(document: dict[str, Any]) -> str:
    audit = document["audit"]
    reconciliation = audit["reconciliation"]
    findings = audit["findings"]
    hard = [row for row in findings if row["severity"] == HARD]
    review = [row for row in findings if row["severity"] != HARD]
    lines = [
        "# Point-in-Time Fundamentals v1 Audit",
        "",
        f"- Decision: `{document['decision']}`",
        f"- Claims eligible: `{str(document['claims_eligible']).lower()}`",
        f"- Generated at: `{document['generated_at']}`",
        f"- Code revision: `{document['code_revision']}`",
        f"- Facts: `{audit['counts']['facts']}`",
        f"- Securities: `{audit['counts']['securities']}`",
        f"- Hard failures: `{audit['hard_failure_count']}`",
        f"- Review findings: `{audit['review_finding_count']}`",
        f"- Fundamental fact hash: `{audit['fact_hash']}`",
        f"- Availability/revision hash: `{audit['availability_revision_hash']}`",
        "",
        "## SEC reconciliation",
        "",
        f"- Evidence rows: `{reconciliation['sample_count']}`",
        f"- Unique issuer-period samples: `{reconciliation['issuer_period_count']}` / `30`",
        f"- Sectors covered: `{len(reconciliation['sectors'])}` / `11`",
        "",
    ]

    def append_findings(title: str, rows: list[dict[str, Any]]) -> None:
        lines.extend([f"## {title}", ""])
        if not rows:
            lines.extend(["None.", ""])
            return
        lines.extend(
            [
                "| Code | Security | Facts | Finding |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in rows:
            fact_ids = ", ".join(row["fundamental_ids"]) or "—"
            message = row["message"].replace("|", "\\|")
            lines.append(
                f"| `{row['code']}` | {row['security_id'] or 'panel'} | "
                f"{fact_ids} | {message} |"
            )
        lines.append("")

    append_findings("Hard failures", hard)
    append_findings("Review findings and unresolved differences", review)
    lines.extend(
        [
            "## Claims boundary",
            "",
            "This audit preserves unresolved differences and validates data structure only. "
            "It does not establish model performance. `claims_eligible=false` remains mandatory.",
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(
    document: dict[str, Any],
    *,
    json_output: Path,
    markdown_output: Path,
) -> tuple[str, str]:
    json_payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    markdown_payload = render_markdown(document).encode()
    for path, payload in (
        (json_output, json_payload),
        (markdown_output, markdown_payload),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)
    return hashlib.sha256(json_payload).hexdigest(), hashlib.sha256(
        markdown_payload
    ).hexdigest()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit point-in-time fundamentals and SEC samples."
    )
    parser.add_argument("--database-url")
    parser.add_argument("--source-snapshot-id", action="append", default=None)
    parser.add_argument("--prediction-timestamp", type=_timestamp)
    parser.add_argument("--candidate-fact-ids", type=Path)
    parser.add_argument("--reconciliation-json", type=Path)
    parser.add_argument("--allow-incomplete-reconciliation", action="store_true")
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument("--generated-at", type=_timestamp)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    generated_at = args.generated_at or datetime.now(timezone.utc)
    try:
        candidate_ids = load_candidate_fact_ids(args.candidate_fact_ids)
        if candidate_ids is not None and args.prediction_timestamp is None:
            raise ValueError(
                "--candidate-fact-ids requires --prediction-timestamp"
            )
        session_factory = open_research_database(args.database_url)
        with session_factory() as session:
            samples = (
                load_reconciliation_samples(args.reconciliation_json)
                if args.reconciliation_json is not None
                else derive_sec_reconciliation_samples(
                    session,
                    vendor_source_snapshot_ids=args.source_snapshot_id,
                )
            )
            audit = audit_point_in_time_fundamentals(
                session,
                source_snapshot_ids=args.source_snapshot_id,
                prediction_timestamp=args.prediction_timestamp,
                candidate_fact_ids=candidate_ids,
                reconciliation_samples=samples,
                enforce_reconciliation_gate=not args.allow_incomplete_reconciliation,
            )
            from quantfore_research.models import SourceSnapshot
            from sqlalchemy import select

            source_hashes = {
                row.snapshot_id: row.source_hash
                for row in session.scalars(
                    select(SourceSnapshot).where(
                        SourceSnapshot.snapshot_id.in_(audit.source_snapshot_ids)
                    )
                ).all()
            }
        document = build_audit_document(
            audit,
            generated_at=generated_at,
            source_snapshot_hashes=source_hashes,
        )
        json_hash, markdown_hash = write_reports(
            document,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"point-in-time fundamental audit failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"decision={audit.status} hard_failures={audit.hard_failure_count} "
        f"review_findings={audit.review_finding_count} "
        f"json_sha256={json_hash} markdown_sha256={markdown_hash}"
    )
    return 2 if audit.hard_failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
