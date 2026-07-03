"""Freeze explicit price exclusions and prove monthly membership coverage."""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401

from quantfore_research.ingest.free_point_in_time import (
    derive_membership_episodes,
    membership_on,
    parse_membership_history,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIMARY = (
    REPO_ROOT
    / "data/raw/free-point-in-time/primary-b792557e915703398ef9a67e4b583a37c6ec80d5.csv"
)
DEFAULT_RECONCILED = REPO_ROOT / "data/raw/free-point-in-time/reconciled-lineage-v1.json"
DEFAULT_LINEAGE = REPO_ROOT / "data/raw/free-point-in-time/lineage-evidence-v1/registry.json"
DEFAULT_OUTPUT = REPO_ROOT / "data/raw/free-point-in-time/price-exclusions-v1.json"
DEFAULT_PRICE_ROOTS = (
    REPO_ROOT / "data/raw/free-point-in-time/tiingo-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v2",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v3",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v4",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v5",
)


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def _month_ends(start: date, end: date) -> list[date]:
    rows = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        rows.append(date(year, month, calendar.monthrange(year, month)[1]))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return rows


def _reason(lineage: dict[str, Any], reconciled: dict[str, Any]) -> str:
    selected = reconciled.get("selected_identity") or {}
    if selected.get("segments"):
        return "IDENTITY_TRANSITION_PRICE_CHAIN_UNAVAILABLE"
    if lineage.get("status") == "ticker_collision" or (
        lineage.get("metadata_name") and not lineage.get("identity_matches")
    ):
        return "TICKER_REUSED_IDENTITY_COLLISION"
    if lineage.get("identity_matches") and lineage.get("start_date"):
        return "SOURCE_HISTORY_INCOMPLETE"
    return "SOURCE_PRICE_UNAVAILABLE"


def freeze_exclusions(
    *,
    primary_body: bytes,
    reconciled_body: bytes,
    lineage_body: bytes,
    lineage_root: Path,
    price_roots: Sequence[Path] = DEFAULT_PRICE_ROOTS,
    window_start: date = date(2017, 1, 1),
    window_end: date = date(2025, 6, 30),
    minimum_coverage: Decimal = Decimal("0.95"),
) -> dict[str, Any]:
    primary = parse_membership_history(primary_body, label="primary membership")
    reconciled = json.loads(reconciled_body)
    lineage = json.loads(lineage_body)
    if reconciled.get("lineage_registry_sha256") != _sha256(lineage_body):
        raise ValueError("reconciled lineage source hash does not match")
    lineage_by_id = {row["episode_id"]: row for row in lineage["episodes"]}
    reconciled_by_id = {row["episode_id"]: row for row in reconciled["episodes"]}
    completions_by_ticker: dict[str, list[tuple[dict[str, Any], Path]]] = {}
    for root in price_roots:
        for path in root.glob("batch-*/*/complete.json"):
            completion = json.loads(path.read_text())
            completions_by_ticker.setdefault(completion["ticker"], []).append(
                (completion, path)
            )
    exclusions = []
    primary_episodes = derive_membership_episodes(
        primary, window_start=window_start, window_end=window_end
    )
    for episode in primary_episodes:
        episode_id = episode.episode_id
        ticker = episode.ticker.replace(".", "-")
        row = reconciled_by_id.get(episode_id)
        if row is None:
            overlapping = [
                candidate
                for candidate in reconciled["episodes"]
                if candidate["ticker"] == ticker
                and date.fromisoformat(candidate["effective_from"])
                <= episode.effective_to
                and date.fromisoformat(candidate["effective_to"])
                >= episode.effective_from
            ]
            if len(overlapping) > 1:
                raise ValueError(f"ambiguous reconciled lineage overlap for {episode_id}")
            row = overlapping[0] if overlapping else None
        if row is not None and row["status"] == "ready_for_bundle":
            continue
        if row is None:
            covering = [
                (completion, path)
                for completion, path in completions_by_ticker.get(ticker, [])
                if (date.fromisoformat(completion["first_price_date"]) - episode.effective_from).days
                <= 7
                and (episode.effective_to - date.fromisoformat(completion["last_price_date"])).days
                <= 7
            ]
            if covering:
                continue
            candidates = completions_by_ticker.get(ticker, [])
            if not candidates:
                raise ValueError(f"unclassified membership price gap for {episode_id}")
            completion, evidence_path = sorted(
                candidates, key=lambda item: item[0]["first_price_date"]
            )[0]
            evidence_sha256 = _sha256(evidence_path.read_bytes())
            reason_code = "SOURCE_HISTORY_MISMATCH"
        else:
            source = lineage_by_id[row["episode_id"]]
            evidence_path = lineage_root / source["metadata_path"]
            evidence_sha256 = source["metadata_sha256"]
            if not evidence_path.is_file() or _sha256(evidence_path.read_bytes()) != evidence_sha256:
                raise ValueError(f"lineage metadata does not reproduce for {row['ticker']}")
            reason_code = _reason(source, row)
        exclusions.append(
            {
                "episode_id": episode_id,
                "ticker": ticker,
                "effective_from": episode.effective_from.isoformat(),
                "effective_to": episode.effective_to.isoformat(),
                "stage": "prices",
                "reason_code": reason_code,
                "detail": "No identity-safe full-episode price chain is available from the frozen source.",
                "evidence_path": str(evidence_path.resolve()),
                "evidence_sha256": evidence_sha256,
            }
        )
    coverage = []
    for month_end in _month_ends(window_start, window_end):
        members = membership_on(primary, month_end)
        excluded = {
            row["ticker"]
            for row in exclusions
            if date.fromisoformat(row["effective_from"])
            <= month_end
            <= date.fromisoformat(row["effective_to"])
            and row["ticker"] in members
        }
        ratio = Decimal(len(members) - len(excluded)) / Decimal(len(members))
        coverage.append(
            {
                "month_end": month_end.isoformat(),
                "expected_member_count": len(members),
                "excluded_member_count": len(excluded),
                "eligible_member_count": len(members) - len(excluded),
                "coverage": str(ratio.quantize(Decimal("0.000001"))),
                "excluded_tickers": sorted(excluded),
            }
        )
    minimum = min(Decimal(row["coverage"]) for row in coverage)
    failed_months = [
        row["month_end"]
        for row in coverage
        if Decimal(row["coverage"]) < minimum_coverage
    ]
    first_sustained_passing_month = None
    for index, row in enumerate(coverage):
        if all(
            Decimal(candidate["coverage"]) >= minimum_coverage
            for candidate in coverage[index:]
        ):
            first_sustained_passing_month = row["month_end"][:7]
            break
    return {
        "schema_version": "free-pit-price-exclusions-v1",
        "publication_prohibited": True,
        "primary_membership_sha256": _sha256(primary_body),
        "reconciled_lineage_sha256": _sha256(reconciled_body),
        "lineage_registry_sha256": _sha256(lineage_body),
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "minimum_coverage_required": str(minimum_coverage),
        "minimum_monthly_coverage": str(minimum),
        "coverage_gate_passed": minimum >= minimum_coverage,
        "failed_month_count": len(failed_months),
        "failed_months": failed_months,
        "first_sustained_passing_month": first_sustained_passing_month,
        "exclusion_count": len(exclusions),
        "episode_count": len(primary_episodes),
        "ready_episode_count": len(primary_episodes) - len(exclusions),
        "unaccounted_episode_count": 0,
        "exclusions": exclusions,
        "monthly_coverage": coverage,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, default=DEFAULT_PRIMARY)
    parser.add_argument("--expected-primary-hash", required=True)
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument("--lineage", type=Path, default=DEFAULT_LINEAGE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--price-root", type=Path, action="append")
    parser.add_argument("--window-start", type=date.fromisoformat, default=date(2017, 1, 1))
    parser.add_argument("--window-end", type=date.fromisoformat, default=date(2025, 6, 30))
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        primary_body = args.primary.read_bytes()
        if _sha256(primary_body) != args.expected_primary_hash.lower():
            raise ValueError("primary membership SHA-256 does not match")
        document = freeze_exclusions(
            primary_body=primary_body,
            reconciled_body=args.reconciled.read_bytes(),
            lineage_body=args.lineage.read_bytes(),
            lineage_root=args.lineage.parent,
            price_roots=tuple(args.price_root or DEFAULT_PRICE_ROOTS),
            window_start=args.window_start,
            window_end=args.window_end,
        )
        _atomic_write(args.output, _json_bytes(document))
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"price exclusion freeze failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"exclusions={document['exclusion_count']} "
        f"minimum_coverage={document['minimum_monthly_coverage']}"
    )
    return 0 if document["coverage_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
