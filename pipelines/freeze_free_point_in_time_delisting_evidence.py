"""Freeze Tiingo listing endpoints and explicit unavailable delisting outcomes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from datetime import date
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401

from quantfore_research.ingest.free_point_in_time import parse_tiingo_supported_tickers


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = REPO_ROOT / "data/raw/free-point-in-time/tiingo-supported-tickers.zip"
DEFAULT_RECONCILED = REPO_ROOT / "data/raw/free-point-in-time/reconciled-lineage-v1.json"
DEFAULT_EXCLUSIONS = REPO_ROOT / "data/raw/free-point-in-time/price-exclusions-v1.json"
DEFAULT_OUTPUT = REPO_ROOT / "data/raw/free-point-in-time/delisting-evidence-v1.json"
DEFAULT_PRICE_ROOTS = (
    REPO_ROOT / "data/raw/free-point-in-time/tiingo-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v2",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v3",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v4",
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


def _inventory_csv(zip_body: bytes) -> bytes:
    with zipfile.ZipFile(BytesIO(zip_body)) as archive:
        if archive.namelist() != ["supported_tickers.csv"]:
            raise ValueError("Tiingo inventory ZIP has unexpected contents")
        return archive.read("supported_tickers.csv")


def _completion_rows(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    completion_body = path.read_bytes()
    completion = json.loads(completion_body)
    rows = []
    for page in completion["pages"]:
        page_path = path.parent / page["path"]
        body = page_path.read_bytes()
        if _sha256(body) != page["sha256"]:
            raise ValueError(f"price page does not reproduce for {completion['ticker']}")
        rows.extend(json.loads(body))
    rows.sort(key=lambda row: row["date"])
    if not rows or rows[0]["date"][:10] != completion["first_price_date"] or rows[-1]["date"][:10] != completion["last_price_date"]:
        raise ValueError(f"price range does not reproduce for {completion['ticker']}")
    completion["_path"] = str(path.resolve())
    completion["_sha256"] = _sha256(completion_body)
    return completion, rows


def _terminal_prices(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    last = rows[-1]
    prior = rows[-2] if len(rows) > 1 else None
    ordinary_return = None
    if prior is not None and Decimal(str(prior["adjClose"])) != 0:
        ordinary_return = str(
            (Decimal(str(last["adjClose"])) / Decimal(str(prior["adjClose"]))) - 1
        )
    return {
        "last_price_date": last["date"][:10],
        "last_close": str(last["close"]),
        "last_adjusted_close": str(last["adjClose"]),
        "prior_price_date": prior["date"][:10] if prior else None,
        "ordinary_last_session_adjusted_return": ordinary_return,
        "delisting_return": None,
        "delisting_return_available": False,
        "delisting_return_reason": "TIINGO_EOD_DOES_NOT_SUPPLY_SEPARATE_DELISTING_RETURN",
    }


def freeze_delisting_evidence(
    *,
    inventory_body: bytes,
    reconciled_body: bytes,
    exclusions_body: bytes,
    price_roots: Sequence[Path],
    evidence_cutoff: date = date(2026, 7, 2),
) -> dict[str, Any]:
    listings = parse_tiingo_supported_tickers(_inventory_csv(inventory_body))
    listings_by_ticker: dict[str, list[Any]] = {}
    for listing in listings:
        listings_by_ticker.setdefault(listing.ticker, []).append(listing)
    reconciled = json.loads(reconciled_body)
    exclusions = json.loads(exclusions_body)

    selected_paths = {
        Path(price["completion_path"])
        for row in reconciled["episodes"]
        if row["status"] == "ready_for_bundle"
        for price in row["selected_identity"]["usable_prices"]
    }
    main_root = price_roots[0].resolve()
    selected_paths.update(
        path.resolve() for path in price_roots[0].glob("batch-*/*/complete.json")
    )
    completions = []
    endpoints = []
    for path in sorted(selected_paths):
        if not path.is_file():
            raise ValueError(f"selected price completion is missing: {path}")
        completion, rows = _completion_rows(path)
        first = date.fromisoformat(completion["first_price_date"])
        last = date.fromisoformat(completion["last_price_date"])
        candidates = [
            listing
            for listing in listings_by_ticker.get(completion["ticker"], [])
            if listing.start_date <= first and listing.end_date >= last
        ]
        if len(candidates) != 1:
            raise ValueError(f"Tiingo listing endpoint is ambiguous for {completion['ticker']}")
        listing = candidates[0]
        completions.append(
            {
                "ticker": completion["ticker"],
                "completion_path": completion["_path"],
                "completion_sha256": completion["_sha256"],
                "listing_start_date": listing.start_date.isoformat(),
                "listing_end_date": listing.end_date.isoformat(),
            }
        )
        if listing.end_date < evidence_cutoff:
            if (listing.end_date - last).days > 7:
                raise ValueError(f"terminal price is too far from listing end for {completion['ticker']}")
            endpoints.append(
                {
                    "ticker": completion["ticker"],
                    "listing_end_date": listing.end_date.isoformat(),
                    "completion_path": completion["_path"],
                    "completion_sha256": completion["_sha256"],
                    **_terminal_prices(rows),
                }
            )
    unavailable = [
        {
            "episode_id": row["episode_id"],
            "ticker": row["ticker"],
            "delisting_date": None,
            "delisting_return": None,
            "reason_code": row["reason_code"],
            "evidence_path": row["evidence_path"],
            "evidence_sha256": row["evidence_sha256"],
        }
        for row in exclusions["exclusions"]
    ]
    return {
        "schema_version": "free-pit-delisting-evidence-v1",
        "status": "complete",
        "publication_prohibited": True,
        "evidence_cutoff": evidence_cutoff.isoformat(),
        "tiingo_inventory_sha256": _sha256(inventory_body),
        "reconciled_lineage_sha256": _sha256(reconciled_body),
        "price_exclusions_sha256": _sha256(exclusions_body),
        "bound_price_completion_count": len(completions),
        "bound_price_completions": completions,
        "ended_listing_count": len(endpoints),
        "ended_listings": endpoints,
        "unavailable_outcome_count": len(unavailable),
        "unavailable_outcomes": unavailable,
        "source_capability": {
            "separate_delisting_return_available": False,
            "policy": "Preserve unavailable returns as null; never infer them from the final ordinary session.",
        },
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--expected-inventory-hash", required=True)
    parser.add_argument("--reconciled", type=Path, default=DEFAULT_RECONCILED)
    parser.add_argument("--exclusions", type=Path, default=DEFAULT_EXCLUSIONS)
    parser.add_argument("--price-root", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        inventory_body = args.inventory.read_bytes()
        if _sha256(inventory_body) != args.expected_inventory_hash.lower():
            raise ValueError("Tiingo inventory SHA-256 does not match")
        document = freeze_delisting_evidence(
            inventory_body=inventory_body,
            reconciled_body=args.reconciled.read_bytes(),
            exclusions_body=args.exclusions.read_bytes(),
            price_roots=tuple(args.price_root or DEFAULT_PRICE_ROOTS),
        )
        _atomic_write(args.output, _json_bytes(document))
    except (KeyError, OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
        print(f"delisting evidence freeze failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"price_completions={document['bound_price_completion_count']} "
        f"ended_listings={document['ended_listing_count']} "
        f"unavailable={document['unavailable_outcome_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
