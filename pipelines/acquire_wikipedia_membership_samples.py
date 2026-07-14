"""Freeze and reconcile revision-pinned Wikipedia S&P 500 samples."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401

from quantfore_research.ingest.free_point_in_time import (
    membership_on,
    parse_membership_history,
    tiingo_ticker,
)
try:
    from reconcile_free_point_in_time_lineage import SEC_IDENTITY_OVERRIDES
except ModuleNotFoundError:
    from pipelines.reconcile_free_point_in_time_lineage import (  # type: ignore
        SEC_IDENTITY_OVERRIDES,
    )


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRIMARY = (
    REPO_ROOT
    / "data/raw/free-point-in-time/primary-b792557e915703398ef9a67e4b583a37c6ec80d5.csv"
)
DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT / "data/raw/free-point-in-time/wikipedia-membership-samples-v1"
)
SAMPLES = (
    (date(2018, 12, 31), 876091698),
    (date(2022, 12, 31), 1130173030),
    (date(2025, 6, 30), 1295035732),
)
API_URL = "https://en.wikipedia.org/w/api.php"
TICKER_TEMPLATE = re.compile(
    r"\{\{(?:NyseSymbol|NasdaqSymbol|NYSE|NASDAQ|BZX link)\|([^}|]+)", re.I
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


def extract_constituent_tickers(wikitext: str) -> set[str]:
    start = wikitext.find("{|", wikitext.find("=="))
    if start < 0:
        raise ValueError("Wikipedia revision lacks a constituent table")
    table = wikitext[start:]
    table = table[: table.find("|}")]
    tickers = {tiingo_ticker(value.strip()) for value in TICKER_TEMPLATE.findall(table)}
    if not 450 <= len(tickers) <= 550:
        raise ValueError("Wikipedia constituent count is implausible")
    return tickers


def _canonical_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for historical, (_, _, verified_aliases) in SEC_IDENTITY_OVERRIDES.items():
        canonical = min({historical, *verified_aliases})
        for ticker in {historical, *verified_aliases}:
            aliases[ticker] = canonical
    return aliases


def _canonical_set(tickers: set[str]) -> set[str]:
    aliases = _canonical_aliases()
    return {aliases.get(ticker, ticker) for ticker in tickers}


def acquire_samples(
    *,
    primary_body: bytes,
    output_root: Path,
    samples: Sequence[tuple[date, int]] = SAMPLES,
    opener: Callable[..., object] = urllib.request.urlopen,
) -> dict[str, Any]:
    primary = parse_membership_history(primary_body, label="primary membership")
    rows = []
    for as_of_date, revision_id in samples:
        query = urllib.parse.urlencode(
            {
                "action": "parse",
                "format": "json",
                "oldid": revision_id,
                "prop": "wikitext",
            }
        )
        url = f"{API_URL}?{query}"
        response = opener(
            urllib.request.Request(
                url,
                headers={"User-Agent": "QuantforeAIResearch/0.1 research@quantfore.ai"},
            ),
            timeout=60,
        )
        try:
            body = response.read()
        finally:
            response.close()
        document = json.loads(body)
        parsed = document["parse"]
        if int(parsed["revid"]) != revision_id:
            raise ValueError("Wikipedia returned the wrong revision")
        wikipedia = extract_constituent_tickers(parsed["wikitext"]["*"])
        primary_tickers = {tiingo_ticker(value) for value in membership_on(primary, as_of_date)}
        canonical_wikipedia = _canonical_set(wikipedia)
        canonical_primary = _canonical_set(primary_tickers)
        raw_path = output_root / f"revision-{revision_id}.json"
        _atomic_write(raw_path, body)
        rows.append(
            {
                "as_of_date": as_of_date.isoformat(),
                "revision_id": revision_id,
                "source_url": url,
                "path": raw_path.name,
                "sha256": _sha256(body),
                "primary_count": len(primary_tickers),
                "wikipedia_count": len(wikipedia),
                "canonical_primary_count": len(canonical_primary),
                "canonical_wikipedia_count": len(canonical_wikipedia),
                "canonical_primary_only": sorted(canonical_primary - canonical_wikipedia),
                "canonical_wikipedia_only": sorted(canonical_wikipedia - canonical_primary),
                "identity_exact_match": canonical_primary == canonical_wikipedia,
                "canonical_membership_sha256": _sha256(
                    _json_bytes(sorted(canonical_wikipedia))
                ),
            }
        )
    registry = {
        "schema_version": "free-pit-wikipedia-membership-samples-v1",
        "publication_prohibited": True,
        "primary_membership_sha256": _sha256(primary_body),
        "sample_count": len(rows),
        "all_identity_exact_match": all(row["identity_exact_match"] for row in rows),
        "samples": rows,
    }
    _atomic_write(output_root / "registry.json", _json_bytes(registry))
    return registry


def _sample(value: str) -> tuple[date, int]:
    try:
        as_of_text, revision_text = value.split("=", 1)
        as_of_date = date.fromisoformat(as_of_text)
        revision_id = int(revision_text)
    except (TypeError, ValueError) as exc:
        raise argparse.ArgumentTypeError(
            "sample must use AS_OF_DATE=REVISION_ID"
        ) from exc
    if revision_id <= 0:
        raise argparse.ArgumentTypeError("Wikipedia revision ID must be positive")
    return as_of_date, revision_id


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, default=DEFAULT_PRIMARY)
    parser.add_argument("--expected-primary-hash", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--sample",
        action="append",
        type=_sample,
        help="Override default samples; repeat AS_OF_DATE=REVISION_ID as needed.",
    )
    parser.add_argument(
        "--allow-identity-differences",
        action="store_true",
        help=(
            "Return success when dated constituent identities differ. This is only "
            "appropriate when the raw revision is used for non-membership evidence."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        primary_body = args.primary.read_bytes()
        if _sha256(primary_body) != args.expected_primary_hash.lower():
            raise ValueError("primary membership SHA-256 does not match")
        registry = acquire_samples(
            primary_body=primary_body,
            output_root=args.output_root,
            samples=args.sample or SAMPLES,
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Wikipedia membership acquisition failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"samples={registry['sample_count']} "
        f"exact={str(registry['all_identity_exact_match']).lower()}"
    )
    if registry["all_identity_exact_match"] or args.allow_identity_differences:
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
