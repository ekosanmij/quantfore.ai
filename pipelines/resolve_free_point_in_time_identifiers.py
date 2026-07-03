"""Resolve safe OpenFIGI candidates with pinned SEC and historical names."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OPENFIGI_REGISTRY = (
    REPO_ROOT / "data/raw/free-point-in-time/openfigi-v3/registry.json"
)
DEFAULT_OUTPUT = (
    REPO_ROOT / "data/raw/free-point-in-time/resolved-identifiers-v1.json"
)
DEFAULT_NAME_SEARCH_DIR = (
    REPO_ROOT / "data/raw/free-point-in-time/openfigi-name-search-v3"
)
HISTORICAL_NAME_OVERRIDES = {"PX": ("Praxair",)}
ADDITIONAL_LINEAGE_REQUIRED = frozenset({"FTR", "XL"})


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def _canonical_ticker(value: str) -> str:
    return value.strip().upper().replace(".", "-").replace("/", "-")


def _name(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", value.upper())).strip()


def _similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _name(left), _name(right)).ratio()


def _sec_identity_match(sec_title: str, candidate_name: str) -> bool:
    sec_tokens = _name(sec_title).split()
    candidate_tokens = _name(candidate_name).split()
    return bool(
        sec_tokens
        and candidate_tokens
        and sec_tokens[0] == candidate_tokens[0]
        and _similarity(sec_title, candidate_name) >= 0.70
    )


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.table: Optional[str] = None
        self.depth = 0
        self.row: Optional[list[str]] = None
        self.cell: Optional[list[str]] = None
        self.tables: dict[str, list[list[str]]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        if tag == "table":
            if self.table is None and attributes.get("id") in {"constituents", "changes"}:
                self.table = str(attributes["id"])
                self.depth = 1
                self.tables[self.table] = []
            elif self.table:
                self.depth += 1
        elif self.table and tag == "tr":
            self.row = []
        elif self.table and tag in {"td", "th"}:
            self.cell = []
        elif self.table and tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_data(self, data: str) -> None:
        if self.table and self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.table:
            return
        if tag in {"td", "th"} and self.cell is not None:
            assert self.row is not None
            self.row.append(re.sub(r"\s+", " ", " ".join(self.cell)).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.tables[self.table].append(self.row)
            self.row = None
        elif tag == "table":
            self.depth -= 1
            if self.depth == 0:
                self.table = None


def parse_wikipedia_names(body: bytes, *, expected_revision: int) -> dict[str, list[str]]:
    document = json.loads(body)
    parsed = document.get("parse", {})
    if parsed.get("revid") != expected_revision:
        raise ValueError("Wikipedia response revision does not match")
    html = parsed.get("text", {}).get("*")
    if not isinstance(html, str):
        raise ValueError("Wikipedia response lacks parsed HTML")
    parser = _TableParser()
    parser.feed(html)
    constituents = parser.tables.get("constituents", [])
    changes = parser.tables.get("changes", [])
    if len(constituents) < 500 or len(changes) < 300:
        raise ValueError("Wikipedia S&P tables are incomplete")
    names: dict[str, set[str]] = {}
    for row in constituents[1:]:
        if len(row) >= 2:
            names.setdefault(_canonical_ticker(row[0]), set()).add(row[1])
    for row in changes[2:]:
        if len(row) >= 6:
            if row[1] and row[2]:
                names.setdefault(_canonical_ticker(row[1]), set()).add(row[2])
            if row[3] and row[4]:
                names.setdefault(_canonical_ticker(row[3]), set()).add(row[4])
    return {ticker: sorted(values) for ticker, values in sorted(names.items())}


def _select_candidate(
    row: dict[str, Any],
    *,
    historical_names: Sequence[str],
    sec_title: Optional[str],
) -> Optional[dict[str, Any]]:
    candidates = row.get("matching_candidates", [])
    by_share_class: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        share_class = str(candidate.get("shareClassFIGI") or "")
        if not share_class:
            continue
        prior = by_share_class.get(share_class)
        candidate_wi = "-W" in str(candidate.get("ticker") or "") or "W/I" in str(
            candidate.get("name") or ""
        )
        prior_wi = (
            "-W" in str(prior.get("ticker") or "")
            or "W/I" in str(prior.get("name") or "")
            if prior is not None
            else True
        )
        if prior is None or (prior_wi and not candidate_wi):
            by_share_class[share_class] = candidate
    scored = []
    for candidate in by_share_class.values():
        candidate_name = str(candidate.get("name") or "")
        historical_score = max(
            (_similarity(name, candidate_name) for name in historical_names),
            default=0.0,
        )
        sec_score = _similarity(sec_title, candidate_name) if sec_title else 0.0
        instrument_bonus = (
            0.05
            if candidate.get("securityType2") in {"Common Stock", "Depositary Receipt"}
            or candidate.get("securityType") == "REIT"
            else 0.0
        )
        when_issued_penalty = (
            0.15
            if "-W" in str(candidate.get("ticker") or "")
            or "W/I" in candidate_name
            else 0.0
        )
        scored.append(
            (
                historical_score + instrument_bonus - when_issued_penalty,
                sec_score,
                candidate,
            )
        )
    if not scored:
        return None
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = scored[0]
    runner_up = scored[1] if len(scored) > 1 else (0.0, 0.0, {})
    minimum_name_score = 0.30 if len(scored) == 1 else 0.40
    if best[0] < minimum_name_score or best[0] - runner_up[0] < 0.10:
        if best[1] < 0.75 or best[1] - runner_up[1] < 0.10:
            return None
    return best[2]


def resolve_identifiers(
    *,
    plan_sha256: str,
    openfigi_body: bytes,
    wikipedia_body: bytes,
    wikipedia_revision: int,
    sec_body: bytes,
    name_search_bodies: Optional[dict[str, bytes]] = None,
) -> dict[str, Any]:
    openfigi = json.loads(openfigi_body)
    if openfigi.get("acquisition_plan_sha256") != plan_sha256:
        raise ValueError("OpenFIGI registry has wrong plan hash")
    historical = parse_wikipedia_names(
        wikipedia_body, expected_revision=wikipedia_revision
    )
    sec_document = json.loads(sec_body)
    sec_by_ticker = {
        _canonical_ticker(str(value["ticker"])): value
        for value in sec_document.values()
        if isinstance(value, dict) and value.get("ticker")
    }
    mappings = []
    for row in openfigi["mappings"]:
        ticker = _canonical_ticker(str(row["ticker"]))
        sec = sec_by_ticker.get(ticker)
        selected = None
        status = row["status"]
        if status == "resolved":
            selected = next(
                (
                    candidate
                    for candidate in row.get("matching_candidates", [])
                    if candidate.get("shareClassFIGI") == row.get("share_class_figi")
                ),
                None,
            )
        elif status == "ambiguous":
            selected = _select_candidate(
                row,
                historical_names=historical.get(ticker, []),
                sec_title=str(sec["title"]) if sec else None,
            )
            if selected is not None:
                status = "resolved"
        elif status == "unresolved" and name_search_bodies:
            search_body = name_search_bodies.get(ticker)
            if search_body is not None:
                search = json.loads(search_body)
                candidates = search.get("data", [])
                if not isinstance(candidates, list):
                    raise ValueError(f"OpenFIGI name search data is invalid for {ticker}")
                search_row = {"matching_candidates": candidates}
                selected = _select_candidate(
                    search_row,
                    historical_names=(
                        *historical.get(ticker, []),
                        *HISTORICAL_NAME_OVERRIDES.get(ticker, ()),
                    ),
                    sec_title=None,
                )
                if selected is not None:
                    status = "resolved"
        if status == "unresolved" and ticker in ADDITIONAL_LINEAGE_REQUIRED:
            status = "needs_lineage"
        cik = None
        if selected is not None and sec is not None:
            if _sec_identity_match(
                str(sec["title"]), str(selected.get("name") or "")
            ):
                cik = f"{int(sec['cik_str']):010d}"
        mappings.append(
            {
                "ticker": ticker,
                "status": status,
                "share_class_figi": (
                    selected.get("shareClassFIGI") if selected is not None else None
                ),
                "composite_figi": (
                    selected.get("compositeFIGI") if selected is not None else None
                ),
                "name": selected.get("name") if selected is not None else None,
                "cik": cik,
                "historical_name_candidates": historical.get(ticker, []),
                "openfigi_candidate_status": row.get("candidate_status"),
            }
        )
    counts = {
        status: sum(row["status"] == status for row in mappings)
        for status in ("resolved", "needs_lineage", "ambiguous", "unresolved")
    }
    return {
        "schema_version": "free-pit-resolved-identifiers-v1",
        "status": "complete" if len(mappings) == openfigi["requested_ticker_count"] else "in_progress",
        "publication_prohibited": True,
        "acquisition_plan_sha256": plan_sha256,
        "requested_ticker_count": openfigi["requested_ticker_count"],
        "processed_ticker_count": len(mappings),
        "resolved_ticker_count": counts["resolved"],
        "lineage_required_ticker_count": counts["needs_lineage"],
        "ambiguous_ticker_count": counts["ambiguous"],
        "unresolved_ticker_count": counts["unresolved"],
        "source_hashes": {
            "openfigi_registry": _sha256(openfigi_body),
            "wikipedia_revision_response": _sha256(wikipedia_body),
            "sec_company_tickers": _sha256(sec_body),
            "openfigi_name_searches": {
                ticker: _sha256(body)
                for ticker, body in sorted((name_search_bodies or {}).items())
            },
        },
        "wikipedia_revision": wikipedia_revision,
        "mappings": mappings,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Resolve safe permanent identifiers.")
    parser.add_argument("--plan-hash", required=True)
    parser.add_argument("--openfigi-registry", type=Path, default=DEFAULT_OPENFIGI_REGISTRY)
    parser.add_argument("--wikipedia-response", type=Path, required=True)
    parser.add_argument("--expected-wikipedia-hash", required=True)
    parser.add_argument("--wikipedia-revision", type=int, required=True)
    parser.add_argument("--sec-company-tickers", type=Path, required=True)
    parser.add_argument("--expected-sec-hash", required=True)
    parser.add_argument(
        "--openfigi-name-search-dir", type=Path, default=DEFAULT_NAME_SEARCH_DIR
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        wikipedia_body = args.wikipedia_response.read_bytes()
        sec_body = args.sec_company_tickers.read_bytes()
        if _sha256(wikipedia_body) != args.expected_wikipedia_hash.lower():
            raise ValueError("Wikipedia source hash does not match")
        if _sha256(sec_body) != args.expected_sec_hash.lower():
            raise ValueError("SEC source hash does not match")
        name_search_bodies = {
            path.name.removesuffix("-common-stock.json"): path.read_bytes()
            for path in args.openfigi_name_search_dir.glob("*-common-stock.json")
        }
        result = resolve_identifiers(
            plan_sha256=args.plan_hash.lower(),
            openfigi_body=args.openfigi_registry.read_bytes(),
            wikipedia_body=wikipedia_body,
            wikipedia_revision=args.wikipedia_revision,
            sec_body=sec_body,
            name_search_bodies=name_search_bodies,
        )
        _atomic_write(args.output, _json_bytes(result))
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"identifier resolution failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"resolved={result['resolved_ticker_count']}/{result['requested_ticker_count']} "
        f"lineage={result['lineage_required_ticker_count']} "
        f"ambiguous={result['ambiguous_ticker_count']} unresolved={result['unresolved_ticker_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
