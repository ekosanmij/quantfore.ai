"""Reconcile a deterministic Tiingo sample against an independent CSV export."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import (
        DEFAULT_RAW_DIR,
        get_code_revision,
        open_research_database,
        repository_relative_path,
        write_raw_payload,
    )
    from audit_price_panel import load_trial_panel, sha256_file
    from ingest_market_prices import DEFAULT_UNIVERSE_FILE, read_universe
except ModuleNotFoundError:  # Imported through the pipelines package.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        DEFAULT_RAW_DIR,
        get_code_revision,
        open_research_database,
        repository_relative_path,
        write_raw_payload,
    )
    from pipelines.audit_price_panel import (  # type: ignore
        load_trial_panel,
        sha256_file,
    )
    from pipelines.ingest_market_prices import (  # type: ignore
        DEFAULT_UNIVERSE_FILE,
        read_universe,
    )

from quantfore_research.ingest.market_prices import TIINGO_VENDOR
from quantfore_research.validation.price_reconciliation import (
    ReconciliationConfig,
    ReconciliationResult,
    deterministic_sample,
    parse_independent_csv,
    primary_prices,
    reconcile_sample,
)


DEFAULT_START_DATE = date(2020, 1, 1)
DEFAULT_END_DATE = date(2025, 12, 31)
DEFAULT_PRICE_AUDIT = Path(
    "reports/data-audits/us-equity-trial-v0-price-quality.json"
)
DEFAULT_JSON_OUTPUT = Path("reports/data-audits/us-equity-trial-v0.json")
DEFAULT_MARKDOWN_OUTPUT = Path("reports/data-audits/us-equity-trial-v0.md")


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_source_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return normalized or "independent-source"


def freeze_independent_export(
    *,
    payload: bytes,
    source: str,
    raw_dir: Path,
) -> str:
    source_hash = hashlib.sha256(payload).hexdigest()
    storage_uri = (
        f"raw/reconciliation/{_safe_source_name(source)}/"
        f"us-equity-trial-v0_{source_hash}.csv"
    )
    write_raw_payload(raw_dir, storage_uri, payload)
    return storage_uri


def load_price_quality_audit(
    path: Path,
    *,
    expected_universe_sha256: str,
    expected_snapshot_hashes: Sequence[str],
) -> tuple[Optional[str], Optional[dict[str, int]], Optional[dict[str, object]]]:
    if not path.exists():
        return None, None, None
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("dataset_kind") != "prototype_real":
        raise ValueError("price-quality audit dataset_kind must be prototype_real")
    if document.get("claims_eligible") is not False:
        raise ValueError("price-quality audit must set claims_eligible=false")
    if document.get("universe_file_sha256") != expected_universe_sha256:
        raise ValueError("price-quality audit universe hash does not match")
    source_snapshots = document.get("source_snapshots")
    if not isinstance(source_snapshots, list):
        raise ValueError("price-quality audit is missing source snapshots")
    audit_snapshot_hashes = []
    for item in source_snapshots:
        if not isinstance(item, dict) or not isinstance(item.get("sha256"), str):
            raise ValueError("price-quality audit has invalid source snapshot lineage")
        audit_snapshot_hashes.append(item["sha256"])
    if sorted(audit_snapshot_hashes) != sorted(expected_snapshot_hashes):
        raise ValueError("price-quality audit source snapshot hashes do not match")
    if document.get("source_snapshot_count") != len(audit_snapshot_hashes):
        raise ValueError("price-quality audit source snapshot count does not match")
    audit = document.get("audit")
    if not isinstance(audit, dict):
        raise ValueError("price-quality audit is missing its audit object")
    status = audit.get("status")
    if status not in {"pass", "review", "fail"}:
        raise ValueError("price-quality audit has an invalid status")
    securities = audit.get("securities")
    if not isinstance(securities, list):
        raise ValueError("price-quality audit is missing securities")
    missing_counts = {}
    for item in securities:
        if not isinstance(item, dict) or not isinstance(item.get("ticker"), str):
            raise ValueError("price-quality audit has an invalid security entry")
        issue_counts = item.get("issue_counts")
        if not isinstance(issue_counts, dict):
            raise ValueError("price-quality audit is missing issue counts")
        missing_counts[item["ticker"]] = int(
            issue_counts.get("missing_expected_sessions", 0)
        )
    metadata = {
        "path": repository_relative_path(path),
        "sha256": sha256_file(path),
        "status": status,
        "universe_file_sha256": expected_universe_sha256,
        "source_snapshot_hashes": sorted(audit_snapshot_hashes),
    }
    return status, missing_counts, metadata


def build_report_document(
    *,
    result: ReconciliationResult,
    generated_at: datetime,
    universe_file: Path,
    config: ReconciliationConfig,
    primary_snapshots: Sequence[dict[str, object]],
    independent_metadata: Optional[dict[str, object]],
    price_quality_metadata: Optional[dict[str, object]],
) -> dict[str, object]:
    return {
        "audit_id": "us-equity-trial-v0",
        "dataset_kind": "prototype_real",
        "claims_eligible": False,
        "generated_at": _utc_iso(generated_at),
        "code_revision": get_code_revision(),
        "primary_vendor": TIINGO_VENDOR,
        "universe_file": repository_relative_path(universe_file),
        "universe_file_sha256": sha256_file(universe_file),
        "tolerances": {
            "raw_price_bps": str(config.raw_price_tolerance_bps),
            "adjusted_price_bps": str(config.adjusted_price_tolerance_bps),
            "volume_percent": str(config.volume_tolerance_percent),
        },
        "primary_source_snapshots": list(primary_snapshots),
        "independent_source": independent_metadata,
        "price_quality_audit": price_quality_metadata,
        "reconciliation": result.to_dict(),
    }


def _format_optional(value: object) -> str:
    return "not available" if value is None else str(value)


def render_markdown(document: dict[str, object]) -> str:
    reconciliation = document["reconciliation"]
    assert isinstance(reconciliation, dict)
    decision = str(reconciliation["decision"])
    securities = reconciliation["securities"]
    assert isinstance(securities, list)
    comparisons = reconciliation["comparisons"]
    assert isinstance(comparisons, list)
    rows_received = reconciliation["rows_received"]
    assert isinstance(rows_received, dict)
    sample = reconciliation["sample"]
    assert isinstance(sample, list)

    lines = [
        "# US Equity Trial v0 — Independent Reconciliation",
        "",
        "**PROTOTYPE REAL-DATA TRIAL — NOT ELIGIBLE FOR PERFORMANCE CLAIMS**",
        "",
        f"**Decision:** `{decision.upper()}`  ",
        f"**Generated:** `{document['generated_at']}`  ",
        f"**Primary vendor:** `{document['primary_vendor']}`  ",
        "",
        "## Scope",
        "",
        (
            "The deterministic sample contains five equities and twenty XNYS "
            "sessions per equity. It includes documented split windows and "
            "volatile periods. Vendor values are compared as received; this "
            "workflow never repairs either source."
        ),
        "",
        "## Deterministic sample",
        "",
        "| Ticker | Anchor | Event | Dates | Selection reason |",
        "| --- | --- | --- | ---: | --- |",
    ]
    sample_summary: dict[str, dict[str, object]] = {}
    for point in sample:
        assert isinstance(point, dict)
        ticker = str(point["ticker"])
        summary = sample_summary.setdefault(
            ticker,
            {
                "anchor_date": point["anchor_date"],
                "event_type": point["event_type"],
                "selection_reason": point["selection_reason"],
                "dates": 0,
            },
        )
        summary["dates"] = int(summary["dates"]) + 1
    for ticker, summary in sample_summary.items():
        lines.append(
            f"| {ticker} | {summary['anchor_date']} | "
            f"{summary['event_type']} | {summary['dates']} | "
            f"{summary['selection_reason']} |"
        )
    lines.extend(
        [
            "",
            "## Rows received and accepted",
            "",
            f"- Primary rows received: {rows_received['primary']}",
            f"- Independent rows received: {rows_received['independent']}",
            (
                "- Matched rows accepted for comparison: "
                f"{reconciliation['rows_accepted']}"
            ),
            f"- Requested sample rows: {reconciliation['sample_size']}",
            "",
            "## Security coverage",
            "",
            (
                "| Ticker | Primary | Independent | Compared | Coverage | "
                "Missing sessions | Failed | Review | Status |"
            ),
            (
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: | "
                "---: | --- |"
            ),
        ]
    )
    for item in securities:
        assert isinstance(item, dict)
        lines.append(
            f"| {item['ticker']} | {item['primary_rows_received']} | "
            f"{item['independent_rows_received']} | {item['rows_accepted']} | "
            f"{item['coverage_percentage']}% | "
            f"{_format_optional(item['missing_session_count'])} | "
            f"{item['failed_comparisons']} | {item['review_comparisons']} | "
            f"{item['status']} |"
        )

    exceptions = [
        item for item in comparisons if isinstance(item, dict) and item["status"] != "pass"
    ]
    lines.extend(
        [
            "",
            "## Price and adjustment differences",
            "",
            f"- Comparison exceptions: {len(exceptions)}",
            (
                "- Adjustment differences requiring review: "
                f"{reconciliation['adjustment_difference_count']}"
            ),
        ]
    )
    if exceptions:
        lines.extend(
            [
                "",
                "| Ticker | Date | Status | Notes |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in exceptions[:50]:
            notes = item.get("notes", [])
            note_text = "; ".join(str(note) for note in notes)
            lines.append(
                f"| {item['ticker']} | {item['date']} | {item['status']} | {note_text} |"
            )
        if len(exceptions) > 50:
            lines.append(
                "\nOnly the first 50 exceptions are shown; the JSON contains "
                f"all {len(exceptions)}."
            )

    failed = reconciliation["failed_securities"]
    blockers = reconciliation["blocking_reasons"]
    notes = reconciliation["manual_review_notes"]
    lines.extend(
        [
            "",
            "## Failed securities",
            "",
            ", ".join(failed) if failed else "None.",
            "",
            "## Blocking reasons",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in blockers)
    if not blockers:
        lines.append("- None.")
    lines.extend(["", "## Manual-review notes", ""])
    lines.extend(f"- {item}" for item in notes)
    if not notes:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Claims boundary",
            "",
            (
                "This reconciliation does not establish model validity, "
                "point-in-time universe validity, or investment performance. "
                "`claims_eligible=false`."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_reports(
    *,
    document: dict[str, object],
    json_path: Path,
    markdown_path: Path,
) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_temp = json_path.with_suffix(json_path.suffix + ".tmp")
    markdown_temp = markdown_path.with_suffix(markdown_path.suffix + ".tmp")
    json_temp.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    markdown_temp.write_text(render_markdown(document), encoding="utf-8")
    json_temp.replace(json_path)
    markdown_temp.replace(markdown_path)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reconcile a deterministic Tiingo sample independently."
    )
    parser.add_argument(
        "--universe-file", type=Path, default=DEFAULT_UNIVERSE_FILE
    )
    parser.add_argument("--independent-file", type=Path)
    parser.add_argument("--price-audit", type=Path, default=DEFAULT_PRICE_AUDIT)
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument(
        "--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE
    )
    parser.add_argument("--end-date", type=date.fromisoformat, default=DEFAULT_END_DATE)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument(
        "--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT
    )
    parser.add_argument(
        "--raw-price-tolerance-bps", type=Decimal, default=Decimal("10")
    )
    parser.add_argument(
        "--adjusted-price-tolerance-bps", type=Decimal, default=Decimal("25")
    )
    parser.add_argument(
        "--volume-tolerance-percent", type=Decimal, default=Decimal("1")
    )
    parser.add_argument(
        "--manual-review-note", action="append", default=[]
    )
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    universe = read_universe(args.universe_file)
    sample = deterministic_sample()
    sample_tickers = {point.ticker for point in sample}
    universe_tickers = {entry.ticker for entry in universe}
    if not sample_tickers.issubset(universe_tickers):
        missing = ",".join(sorted(sample_tickers - universe_tickers))
        raise ValueError(f"sample tickers are absent from universe: {missing}")
    config = ReconciliationConfig(
        raw_price_tolerance_bps=args.raw_price_tolerance_bps,
        adjusted_price_tolerance_bps=args.adjusted_price_tolerance_bps,
        volume_tolerance_percent=args.volume_tolerance_percent,
    )

    session_factory = open_research_database(args.database_url)
    panel, primary_snapshots = load_trial_panel(
        session_factory=session_factory,
        universe=universe,
        start_date=args.start_date,
        end_date=args.end_date,
        vendor=TIINGO_VENDOR,
    )
    primary_observations = [
        row
        for ticker in sample_tickers
        for row in panel.get(ticker, ())
        if any(point.ticker == ticker and point.date == row.date for point in sample)
    ]
    primary = primary_prices(primary_observations)
    blockers = []
    if not primary_snapshots:
        blockers.append("no Tiingo source snapshots were found")
    if not primary:
        blockers.append("no primary sample rows were available")

    independent = ()
    independent_metadata = None
    if args.independent_file is None:
        blockers.append("independent comparison export was not supplied")
    elif not args.independent_file.exists():
        blockers.append(
            f"independent comparison export does not exist: {args.independent_file}"
        )
    else:
        payload = args.independent_file.read_bytes()
        try:
            independent = parse_independent_csv(payload)
        except ValueError as exc:
            blockers.append(f"independent comparison export is invalid: {exc}")
        else:
            source = independent[0].source
            storage_uri = freeze_independent_export(
                payload=payload,
                source=source,
                raw_dir=args.raw_dir,
            )
            independent_metadata = {
                "source": source,
                "source_urls": sorted({row.source_url for row in independent}),
                "retrieved_at": sorted(
                    {_utc_iso(row.retrieved_at) for row in independent}
                ),
                "license_tags": sorted({row.license_tag for row in independent}),
                "sha256": hashlib.sha256(payload).hexdigest(),
                "storage_uri": storage_uri,
                "rows": len(independent),
            }

    try:
        quality_status, missing_counts, quality_metadata = (
            load_price_quality_audit(
                args.price_audit,
                expected_universe_sha256=sha256_file(args.universe_file),
                expected_snapshot_hashes=[
                    str(snapshot["sha256"]) for snapshot in primary_snapshots
                ],
            )
        )
    except (ValueError, json.JSONDecodeError) as exc:
        quality_status, missing_counts, quality_metadata = None, None, None
        blockers.append(f"price-quality audit is invalid: {exc}")

    result = reconcile_sample(
        sample=sample,
        primary=primary,
        independent=independent,
        missing_session_counts=missing_counts,
        price_quality_status=quality_status,
        config=config,
        prerequisite_blockers=blockers,
        manual_review_notes=args.manual_review_note,
    )
    document = build_report_document(
        result=result,
        generated_at=datetime.now(timezone.utc),
        universe_file=args.universe_file,
        config=config,
        primary_snapshots=primary_snapshots,
        independent_metadata=independent_metadata,
        price_quality_metadata=quality_metadata,
    )
    write_reports(
        document=document,
        json_path=args.json_output,
        markdown_path=args.markdown_output,
    )
    print(
        f"reconciliation decision={result.decision} "
        f"rows_accepted={result.rows_accepted} "
        f"failed_securities={len(result.failed_securities)} "
        f"json={args.json_output} markdown={args.markdown_output}"
    )
    return 1 if args.require_pass and result.decision != "pass" else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"price reconciliation failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
