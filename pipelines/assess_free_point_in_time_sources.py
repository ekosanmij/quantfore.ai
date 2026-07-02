"""Assess whether free sources can support the Sprint 7 PIT evidence run."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import urllib.request
import zipfile
from datetime import date, datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401

from quantfore_research.ingest.free_point_in_time import (
    FreePointInTimeSourceError,
    classify_episode_coverage,
    derive_membership_episodes,
    monthly_membership_counts,
    parse_membership_history,
    parse_tiingo_supported_tickers,
    reconcile_samples,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = REPO_ROOT / "data" / "raw" / "free-point-in-time"
DEFAULT_JSON_REPORT = (
    REPO_ROOT / "reports" / "data-audits" / "free-pit-source-assessment-v1.json"
)
DEFAULT_MARKDOWN_REPORT = DEFAULT_JSON_REPORT.with_suffix(".md")
PRIMARY_COMMIT = "b792557e915703398ef9a67e4b583a37c6ec80d5"
SECONDARY_COMMIT = "a91ef88fad5ace83bed1f3452f451247295bcd18"
PRIMARY_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/"
    f"{PRIMARY_COMMIT}/S%26P%20500%20Historical%20Components%20%26%20Changes%20"
    "%28Updated%29.csv"
)
SECONDARY_URL = (
    "https://raw.githubusercontent.com/hanshof/sp500_constituents/"
    f"{SECONDARY_COMMIT}/sp_500_historical_components.csv"
)
PRIMARY_LICENSE_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/"
    f"{PRIMARY_COMMIT}/LICENSE"
)
SECONDARY_LICENSE_URL = (
    "https://raw.githubusercontent.com/hanshof/sp500_constituents/"
    f"{SECONDARY_COMMIT}/LICENSE"
)
TIINGO_INVENTORY_URL = (
    "https://apimedia.tiingo.com/docs/tiingo/daily/supported_tickers.zip"
)
EXPECTED_PRIMARY_SHA256 = (
    "bc9c2aaed27e70406a247cd3f75513e786b0f173c70c3e05c1e243bf25498787"
)
EXPECTED_SECONDARY_SHA256 = (
    "02f37a12c11f82218fce422ecf7d95fae1074bd96e664c262a5ea42c120d5fe9"
)
EXPECTED_PRIMARY_LICENSE_SHA256 = (
    "a0a71da320f7c856f189569ddb5c46a576cf02caae28ead6c40a27c0de006992"
)
EXPECTED_SECONDARY_LICENSE_SHA256 = (
    "63cbd75acedfefbb0066276292d896d9f94916e85d33c1064d1e1cf4c835475e"
)


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(document: object) -> bytes:
    return (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _fetch(url: str, *, timeout_seconds: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "QuantforeAIResearch/0.1", "Accept": "*/*"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        return response.read()


def _load_or_fetch(
    path: Optional[Path],
    *,
    url: str,
    destination: Path,
    timeout_seconds: int,
) -> bytes:
    if path is not None:
        return path.read_bytes()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return destination.read_bytes()
    body = _fetch(url, timeout_seconds=timeout_seconds)
    destination.write_bytes(body)
    return body


def _require_hash(body: bytes, expected: str, label: str) -> None:
    actual = _sha256(body)
    if actual != expected:
        raise FreePointInTimeSourceError(
            f"{label} hash mismatch: expected {expected}, received {actual}"
        )


def _extract_tiingo_csv(zip_body: bytes) -> bytes:
    try:
        with zipfile.ZipFile(BytesIO(zip_body)) as archive:
            names = archive.namelist()
            if names != ["supported_tickers.csv"]:
                raise FreePointInTimeSourceError(
                    "Tiingo ticker archive must contain only supported_tickers.csv"
                )
            return archive.read(names[0])
    except zipfile.BadZipFile as exc:
        raise FreePointInTimeSourceError(
            "Tiingo ticker inventory is not a valid ZIP archive"
        ) from exc


def build_assessment(
    *,
    primary_body: bytes,
    secondary_body: bytes,
    primary_license_body: bytes,
    secondary_license_body: bytes,
    tiingo_zip_body: bytes,
    window_start: date,
    window_end: date,
    sample_dates: Sequence[date],
    free_symbol_limit: int,
    generated_at: datetime,
) -> tuple[dict[str, object], dict[str, object]]:
    _require_hash(primary_body, EXPECTED_PRIMARY_SHA256, "primary membership source")
    _require_hash(
        secondary_body, EXPECTED_SECONDARY_SHA256, "secondary membership source"
    )
    _require_hash(
        primary_license_body,
        EXPECTED_PRIMARY_LICENSE_SHA256,
        "primary membership license",
    )
    _require_hash(
        secondary_license_body,
        EXPECTED_SECONDARY_LICENSE_SHA256,
        "secondary membership license",
    )
    primary = parse_membership_history(primary_body, label="primary membership source")
    secondary = parse_membership_history(
        secondary_body,
        label="secondary membership source",
        allow_same_date_revisions=True,
    )
    tiingo_csv = _extract_tiingo_csv(tiingo_zip_body)
    listings = parse_tiingo_supported_tickers(tiingo_csv)
    episodes = derive_membership_episodes(
        primary, window_start=window_start, window_end=window_end
    )
    coverage = classify_episode_coverage(episodes, listings)
    status_counts: dict[str, int] = {}
    for row in coverage:
        status_counts[row.status] = status_counts.get(row.status, 0) + 1
    required_symbols = sorted({row.tiingo_ticker for row in coverage} | {"SPY"})
    statuses_by_symbol: dict[str, set[str]] = {}
    for row in coverage:
        statuses_by_symbol.setdefault(row.tiingo_ticker, set()).add(row.status)
    safe_symbols = sorted(
        {symbol for symbol, statuses in statuses_by_symbol.items() if statuses == {"full"}}
        | {"SPY"}
    )
    acquisition_batches = [
        {
            "batch_number": position // free_symbol_limit + 1,
            "symbol_count": len(safe_symbols[position : position + free_symbol_limit]),
            "symbols": safe_symbols[position : position + free_symbol_limit],
        }
        for position in range(0, len(safe_symbols), free_symbol_limit)
    ]
    counts = monthly_membership_counts(
        primary, window_start=window_start, window_end=window_end
    )
    samples = reconcile_samples(primary, secondary, sample_dates)
    sample_summaries = [
        {
            "as_of_date": sample["as_of_date"],
            "primary_count": sample["primary_count"],
            "secondary_count": sample["secondary_count"],
            "matching_count": sample["matching_count"],
            "primary_only_count": len(sample["primary_only"]),
            "secondary_only_count": len(sample["secondary_only"]),
            "exact_match": sample["exact_match"],
        }
        for sample in samples
    ]
    full_episode_count = status_counts.get("full", 0)
    full_episode_fraction = full_episode_count / len(coverage)

    blockers = []
    if len(required_symbols) > free_symbol_limit:
        blockers.append(
            {
                "code": "tiingo_free_monthly_symbol_limit",
                "message": (
                    f"{len(required_symbols)} unique symbols are required but the "
                    f"configured free allowance is {free_symbol_limit} per month"
                ),
            }
        )
    if full_episode_count != len(coverage):
        blockers.append(
            {
                "code": "incomplete_tiingo_episode_resolution",
                "message": (
                    f"only {full_episode_count} of {len(coverage)} membership episodes "
                    "are fully covered by an unambiguous same-ticker Tiingo listing"
                ),
            }
        )
    if not all(bool(sample["exact_match"]) for sample in samples):
        blockers.append(
            {
                "code": "secondary_membership_disagreement",
                "message": "one or more historical membership samples disagree",
            }
        )
    implausible = {
        month: count for month, count in counts.items() if count < 450 or count > 550
    }
    if implausible:
        blockers.append(
            {
                "code": "implausible_membership_count",
                "message": "monthly membership falls outside 450–550",
            }
        )

    private_plan = {
        "schema_version": "free-pit-private-acquisition-plan-v1",
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "publication_prohibited": True,
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
        },
        "source_hashes": {
            "primary_membership": _sha256(primary_body),
            "secondary_membership": _sha256(secondary_body),
            "tiingo_inventory_zip": _sha256(tiingo_zip_body),
            "tiingo_inventory_csv": _sha256(tiingo_csv),
        },
        "secondary_samples": list(samples),
        "safe_acquisition_batches": acquisition_batches,
        "unresolved_episodes": [
            {
                "episode_id": row.episode.episode_id,
                "ticker": row.episode.ticker,
                "tiingo_ticker": row.tiingo_ticker,
                "effective_from": row.episode.effective_from.isoformat(),
                "effective_to": row.episode.effective_to.isoformat(),
                "status": row.status,
                "candidate_ranges": [
                    {
                        "ticker": listing.ticker,
                        "exchange": listing.exchange,
                        "asset_type": listing.asset_type,
                        "start": listing.start_date.isoformat(),
                        "end": listing.end_date.isoformat(),
                    }
                    for listing in row.matching_listings
                ],
            }
            for row in coverage
            if row.status != "full"
        ],
    }
    private_plan_sha256 = _sha256(_json_bytes(private_plan))

    report = {
        "schema_version": "free-pit-source-assessment-v1",
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "claims_eligible": False,
        "decision": "blocked" if blockers else "ready_for_price_download",
        "window": {"start": window_start.isoformat(), "end": window_end.isoformat()},
        "sources": {
            "primary_membership": {
                "url": PRIMARY_URL,
                "commit": PRIMARY_COMMIT,
                "sha256": _sha256(primary_body),
                "license": "MIT",
                "license_url": PRIMARY_LICENSE_URL,
                "license_sha256": _sha256(primary_license_body),
            },
            "secondary_membership": {
                "url": SECONDARY_URL,
                "commit": SECONDARY_COMMIT,
                "sha256": _sha256(secondary_body),
                "license": "MIT",
                "license_url": SECONDARY_LICENSE_URL,
                "license_sha256": _sha256(secondary_license_body),
            },
            "tiingo_inventory": {
                "url": TIINGO_INVENTORY_URL,
                "zip_sha256": _sha256(tiingo_zip_body),
                "csv_sha256": _sha256(tiingo_csv),
                "license": "internal use only; raw data is not committed",
            },
        },
        "membership": {
            "snapshot_count": len(primary),
            "episode_count": len(episodes),
            "unique_ticker_count": len({row.ticker for row in episodes}),
            "monthly_counts": counts,
            "minimum_monthly_count": min(counts.values()),
            "maximum_monthly_count": max(counts.values()),
            "secondary_samples": sample_summaries,
        },
        "tiingo_preflight": {
            "free_unique_symbol_limit_per_month": free_symbol_limit,
            "required_unique_symbol_count": len(required_symbols),
            "safe_unique_symbol_count": len(safe_symbols),
            "minimum_months_for_required_symbols": math.ceil(
                len(required_symbols) / free_symbol_limit
            ),
            "minimum_months_for_safe_symbols": math.ceil(
                len(safe_symbols) / free_symbol_limit
            ),
            "safe_acquisition_batch_counts": [
                row["symbol_count"] for row in acquisition_batches
            ],
            "private_acquisition_plan_sha256": private_plan_sha256,
            "episode_status_counts": dict(sorted(status_counts.items())),
            "full_episode_fraction": round(full_episode_fraction, 8),
        },
        "blockers": blockers,
    }
    return report, private_plan


def render_markdown(report: dict[str, object]) -> str:
    membership = report["membership"]
    preflight = report["tiingo_preflight"]
    assert isinstance(membership, dict) and isinstance(preflight, dict)
    blockers = report["blockers"]
    assert isinstance(blockers, list)
    lines = [
        "# Free Point-in-Time Source Assessment v1",
        "",
        f"Decision: **{str(report['decision']).upper()}**",
        "",
        "This is a source-coverage preflight, not Sprint 7 closure evidence. "
        "`claims_eligible=false` remains in force.",
        "",
        "## Measured coverage",
        "",
        f"- Window: `{report['window']['start']}` through `{report['window']['end']}`.",
        f"- Historical membership episodes: {membership['episode_count']} across "
        f"{membership['unique_ticker_count']} ticker labels.",
        f"- Monthly constituent range: {membership['minimum_monthly_count']}–"
        f"{membership['maximum_monthly_count']}.",
        f"- Required Tiingo symbols: {preflight['required_unique_symbol_count']}; "
        f"configured free monthly allowance: {preflight['free_unique_symbol_limit_per_month']}.",
        f"- Safe unchanged-ticker symbols staged for download: "
        f"{preflight['safe_unique_symbol_count']} across "
        f"{preflight['minimum_months_for_safe_symbols']} free-tier batches.",
        f"- Fully resolved same-ticker episodes: "
        f"{preflight['episode_status_counts'].get('full', 0)} of "
        f"{membership['episode_count']}.",
        "",
        "## Blocking findings",
        "",
    ]
    for blocker in blockers:
        lines.append(f"- `{blocker['code']}`: {blocker['message']}.")
    if not blockers:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The free route is technically viable as a staged acquisition, but it "
            "cannot truthfully close Sprint 7 in one free-tier month. Unresolved "
            "episodes require explicit rename/acquisition lineage or another price "
            "source; blindly querying recycled ticker labels is prohibited.",
            "",
            "The companion JSON contains aggregate source hashes and reconciliation "
            "counts. The symbol-level acquisition and unresolved-episode plan is "
            "content-addressed under `data/raw/` and is deliberately Git-ignored.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preflight free membership and Tiingo coverage for Sprint 7."
    )
    parser.add_argument("--primary-file", type=Path)
    parser.add_argument("--secondary-file", type=Path)
    parser.add_argument("--primary-license-file", type=Path)
    parser.add_argument("--secondary-license-file", type=Path)
    parser.add_argument("--tiingo-inventory-zip", type=Path)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--window-start", type=date.fromisoformat, default=date(2014, 1, 1))
    parser.add_argument("--window-end", type=date.fromisoformat, default=date(2025, 6, 30))
    parser.add_argument(
        "--sample-date",
        action="append",
        type=date.fromisoformat,
        dest="sample_dates",
    )
    parser.add_argument("--free-symbol-limit", type=int, default=500)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_REPORT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_REPORT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.window_start > args.window_end:
        raise ValueError("window-start must not be after window-end")
    if args.free_symbol_limit < 1:
        raise ValueError("free-symbol-limit must be positive")
    sample_dates = tuple(
        args.sample_dates
        or (date(2014, 1, 31), date(2018, 12, 31), date(2025, 6, 30))
    )
    if any(not args.window_start <= row <= args.window_end for row in sample_dates):
        raise ValueError("sample dates must fall inside the requested window")

    primary_body = _load_or_fetch(
        args.primary_file,
        url=PRIMARY_URL,
        destination=args.raw_dir / f"primary-{PRIMARY_COMMIT}.csv",
        timeout_seconds=args.timeout_seconds,
    )
    secondary_body = _load_or_fetch(
        args.secondary_file,
        url=SECONDARY_URL,
        destination=args.raw_dir / f"secondary-{SECONDARY_COMMIT}.csv",
        timeout_seconds=args.timeout_seconds,
    )
    primary_license_body = _load_or_fetch(
        args.primary_license_file,
        url=PRIMARY_LICENSE_URL,
        destination=args.raw_dir / f"primary-license-{PRIMARY_COMMIT}.txt",
        timeout_seconds=args.timeout_seconds,
    )
    secondary_license_body = _load_or_fetch(
        args.secondary_license_file,
        url=SECONDARY_LICENSE_URL,
        destination=args.raw_dir / f"secondary-license-{SECONDARY_COMMIT}.txt",
        timeout_seconds=args.timeout_seconds,
    )
    tiingo_zip_body = _load_or_fetch(
        args.tiingo_inventory_zip,
        url=TIINGO_INVENTORY_URL,
        destination=args.raw_dir / "tiingo-supported-tickers.zip",
        timeout_seconds=args.timeout_seconds,
    )
    report, private_plan = build_assessment(
        primary_body=primary_body,
        secondary_body=secondary_body,
        primary_license_body=primary_license_body,
        secondary_license_body=secondary_license_body,
        tiingo_zip_body=tiingo_zip_body,
        window_start=args.window_start,
        window_end=args.window_end,
        sample_dates=sample_dates,
        free_symbol_limit=args.free_symbol_limit,
        generated_at=datetime.now(timezone.utc),
    )
    private_plan_body = _json_bytes(private_plan)
    private_plan_hash = _sha256(private_plan_body)
    expected_private_hash = report["tiingo_preflight"][
        "private_acquisition_plan_sha256"
    ]
    if private_plan_hash != expected_private_hash:
        raise FreePointInTimeSourceError("private acquisition plan hash changed")
    private_plan_path = (
        args.raw_dir / f"acquisition-plan-v1-{private_plan_hash[:16]}.json"
    )
    if private_plan_path.exists() and private_plan_path.read_bytes() != private_plan_body:
        raise FreePointInTimeSourceError(
            "private acquisition plan path contains different bytes"
        )
    private_plan_path.parent.mkdir(parents=True, exist_ok=True)
    private_plan_path.write_bytes(private_plan_body)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        _json_bytes(report).decode("utf-8"), encoding="utf-8"
    )
    args.markdown_output.write_text(render_markdown(report), encoding="utf-8")
    print(
        f"free PIT assessment decision={report['decision']} "
        f"blockers={len(report['blockers'])} json={args.json_output}"
    )
    return 0 if report["decision"] != "blocked" else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FreePointInTimeSourceError, OSError, ValueError) as exc:
        print(f"free PIT assessment failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
