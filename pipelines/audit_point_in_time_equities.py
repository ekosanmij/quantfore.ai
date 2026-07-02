"""Audit the Sprint 7 point-in-time US equity panel."""

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
except ModuleNotFoundError:  # Imported through pipelines in tests.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        get_code_revision,
        open_research_database,
    )

from quantfore_research.validation.point_in_time_audit import (
    DEFAULT_MAXIMUM_MONTHLY_MEMBERS,
    DEFAULT_MINIMUM_MONTHLY_MEMBERS,
    HARD,
    PointInTimeEquityPanelAudit,
    audit_point_in_time_equity_panel,
)


DEFAULT_JSON_OUTPUT = Path("reports/data-audits/pit-equity-panel-v1.json")
DEFAULT_MARKDOWN_OUTPUT = Path("reports/data-audits/pit-equity-panel-v1.md")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def build_audit_document(
    audit: PointInTimeEquityPanelAudit,
    *,
    generated_at: datetime,
    code_revision: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "audit_id": "pit-equity-panel-v1",
        "dataset_kind": "proof_candidate_point_in_time",
        "claims_eligible": False,
        "generated_at": _utc(generated_at).isoformat().replace("+00:00", "Z"),
        "code_revision": code_revision or get_code_revision(),
        "decision": audit.status,
        "audit": audit.to_dict(),
    }


def render_markdown(document: dict[str, Any]) -> str:
    audit = document["audit"]
    findings = audit["findings"]
    hard = [row for row in findings if row["severity"] == HARD]
    review = [row for row in findings if row["severity"] != HARD]

    lines = [
        "# Point-in-Time Equity Panel v1 Audit",
        "",
        f"- Decision: `{document['decision']}`",
        f"- Claims eligible: `{str(document['claims_eligible']).lower()}`",
        f"- Generated at: `{document['generated_at']}`",
        f"- Code revision: `{document['code_revision']}`",
        f"- Universe: `{audit['universe_id']}`",
        f"- Window: `{audit['window_start']}` through `{audit['window_end']}`",
        f"- Calendar: `{audit['calendar']}`",
        f"- Hard failures: `{audit['hard_failure_count']}`",
        f"- Review findings: `{audit['review_finding_count']}`",
        "",
        "## Counts",
        "",
        "| Item | Count |",
        "| --- | ---: |",
    ]
    for name, count in audit["counts"].items():
        lines.append(f"| {name.replace('_', ' ').title()} | {count} |")

    def append_findings(title: str, rows: list[dict[str, Any]]) -> None:
        lines.extend(["", f"## {title}", ""])
        if not rows:
            lines.append("None.")
            return
        lines.extend(
            [
                "| Code | Security | Dates | Finding |",
                "| --- | --- | --- | --- |",
            ]
        )
        for row in rows:
            security = row["ticker"] or row["security_id"] or "panel"
            dates = ", ".join(row["dates"]) or "—"
            message = str(row["message"]).replace("|", "\\|")
            lines.append(f"| `{row['code']}` | {security} | {dates} | {message} |")

    append_findings("Hard failures", hard)
    append_findings("Review findings", review)

    lines.extend(["", "## Historical removal evidence", ""])
    removal = audit["historical_removal_evidence"]
    if removal is None:
        lines.append("Unavailable.")
    else:
        lines.extend(
            [
                f"- Security: `{removal['ticker']}` (`{removal['security_id']}`)",
                f"- Membership ended: `{removal['effective_to']}`",
                f"- Membership row: `{removal['membership_id']}`",
                f"- Last member price: `{removal['last_member_price_date']}`",
                f"- First post-removal price: `{removal['first_post_removal_price_date']}`",
                f"- History retained: `{str(removal['security_history_retained']).lower()}`",
                f"- Source snapshot: `{removal['membership_source_snapshot_id']}`",
                f"- Source hash: `{removal['membership_source_hash']}`",
            ]
        )

    lines.extend(["", "## Delisting evidence", ""])
    delisting = audit["delisting_evidence"]
    if delisting is None:
        lines.append("Unavailable.")
    else:
        lines.extend(
            [
                f"- Security: `{delisting['ticker']}` (`{delisting['security_id']}`)",
                f"- Delisting date: `{delisting['delisting_date']}`",
                f"- Final price date: `{delisting['final_price_date']}`",
                f"- Delisting return: `{delisting['delisting_return']}`",
                f"- Return available at: `{delisting['return_available_at']}`",
                f"- Membership closed: `{str(delisting['membership_closed_by_delisting']).lower()}`",
                f"- History retained: `{str(delisting['security_history_retained']).lower()}`",
                f"- Source snapshot: `{delisting['source_snapshot_id']}`",
                f"- Source hash: `{delisting['source_hash']}`",
            ]
        )

    lines.extend(
        [
            "",
            "## Claims boundary",
            "",
            "This audit validates dataset structure and documented exceptions only. "
            "It does not establish model validity or investment performance. "
            "`claims_eligible=false` remains mandatory.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def write_reports(
    document: dict[str, Any],
    *,
    json_output: Path,
    markdown_output: Path,
) -> tuple[str, str]:
    json_payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    markdown_payload = render_markdown(document).encode("utf-8")
    _write_atomic(json_output, json_payload)
    _write_atomic(markdown_output, markdown_payload)
    return (
        hashlib.sha256(json_payload).hexdigest(),
        hashlib.sha256(markdown_payload).hexdigest(),
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a point-in-time equity panel.")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--universe-id", default="sp500-pit-v1")
    parser.add_argument("--calendar", default="XNYS")
    parser.add_argument(
        "--minimum-monthly-members",
        type=int,
        default=DEFAULT_MINIMUM_MONTHLY_MEMBERS,
    )
    parser.add_argument(
        "--maximum-monthly-members",
        type=int,
        default=DEFAULT_MAXIMUM_MONTHLY_MEMBERS,
    )
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument(
        "--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT
    )
    parser.add_argument(
        "--generated-at",
        type=_parse_timestamp,
        help="UTC report timestamp; defaults to now.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    generated_at = args.generated_at or datetime.now(timezone.utc)
    try:
        session_factory = open_research_database(args.database_url)
        with session_factory() as session:
            audit = audit_point_in_time_equity_panel(
                session,
                universe_id=args.universe_id,
                calendar=args.calendar,
                audit_as_of=generated_at,
                minimum_monthly_members=args.minimum_monthly_members,
                maximum_monthly_members=args.maximum_monthly_members,
            )
        document = build_audit_document(audit, generated_at=generated_at)
        json_hash, markdown_hash = write_reports(
            document,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"point-in-time audit failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"decision={audit.status} hard_failures={audit.hard_failure_count} "
        f"review_findings={audit.review_finding_count} "
        f"json_sha256={json_hash} markdown_sha256={markdown_hash}"
    )
    return 2 if audit.hard_failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
