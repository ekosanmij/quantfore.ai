"""Freeze dated Wikidata ticker, exchange, CIK, and alias evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional, Sequence
from urllib.parse import urlencode
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LINEAGE_REGISTRY = (
    REPO_ROOT / "data/raw/free-point-in-time/lineage-evidence-v1/registry.json"
)
DEFAULT_OUTPUT_ROOT = (
    REPO_ROOT / "data/raw/free-point-in-time/wikidata-lineage-v1"
)
ENDPOINT = "https://query.wikidata.org/sparql"


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def build_query(tickers: Sequence[str]) -> str:
    values = " ".join(json.dumps(ticker) for ticker in sorted(set(tickers)))
    return f"""SELECT ?targetTicker ?company ?companyLabel ?cik
?targetExchangeLabel ?targetStart ?targetEnd ?aliasTicker
?aliasExchangeLabel ?aliasStart ?aliasEnd WHERE {{
  VALUES ?targetTicker {{ {values} }}
  ?company p:P414 ?targetStatement.
  ?targetStatement pq:P249 ?targetTicker; ps:P414 ?targetExchange.
  OPTIONAL {{ ?targetStatement pq:P580 ?targetStart. }}
  OPTIONAL {{ ?targetStatement pq:P582 ?targetEnd. }}
  OPTIONAL {{ ?company wdt:P5531 ?cik. }}
  OPTIONAL {{
    ?company p:P414 ?aliasStatement.
    ?aliasStatement pq:P249 ?aliasTicker; ps:P414 ?aliasExchange.
    OPTIONAL {{ ?aliasStatement pq:P580 ?aliasStart. }}
    OPTIONAL {{ ?aliasStatement pq:P582 ?aliasEnd. }}
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}} ORDER BY ?targetTicker ?company ?aliasStart"""


def _value(binding: dict[str, Any], key: str) -> Optional[str]:
    value = binding.get(key, {}).get("value")
    return str(value) if value is not None else None


def normalize_response(body: bytes) -> list[dict[str, Optional[str]]]:
    document = json.loads(body)
    bindings = document.get("results", {}).get("bindings")
    if not isinstance(bindings, list):
        raise ValueError("Wikidata response lacks result bindings")
    fields = (
        "targetTicker",
        "company",
        "companyLabel",
        "cik",
        "targetExchangeLabel",
        "targetStart",
        "targetEnd",
        "aliasTicker",
        "aliasExchangeLabel",
        "aliasStart",
        "aliasEnd",
    )
    return [
        {field: _value(binding, field) for field in fields}
        for binding in bindings
    ]


def acquire_wikidata_lineage(
    *,
    lineage_body: bytes,
    output_root: Path,
    opener: Callable[..., object] = urlopen,
) -> dict[str, Any]:
    lineage = json.loads(lineage_body)
    tickers = sorted({str(row["ticker"]) for row in lineage["episodes"]})
    query = build_query(tickers)
    url = ENDPOINT + "?" + urlencode({"query": query})
    response = opener(
        Request(
            url,
            headers={
                "Accept": "application/sparql-results+json",
                "User-Agent": "QuantforeAIResearch/0.1 research@quantfore.ai",
            },
        ),
        timeout=60,
    )
    try:
        body = response.read()
    finally:
        response.close()
    rows = normalize_response(body)
    digest = _sha256(body)
    raw_path = output_root / f"query-{digest[:16]}.json"
    _atomic_write(raw_path, body)
    represented = {row["targetTicker"] for row in rows}
    registry = {
        "schema_version": "free-pit-wikidata-lineage-v1",
        "publication_prohibited": True,
        "lineage_registry_sha256": _sha256(lineage_body),
        "retrieved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "query_sha256": _sha256(query.encode()),
        "source_url": ENDPOINT,
        "response_path": raw_path.name,
        "response_sha256": digest,
        "requested_ticker_count": len(tickers),
        "represented_ticker_count": len(represented),
        "unrepresented_tickers": sorted(set(tickers) - represented),
        "binding_count": len(rows),
        "bindings": rows,
    }
    _atomic_write(output_root / "registry.json", _json_bytes(registry))
    return registry


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze Wikidata lineage evidence.")
    parser.add_argument("--lineage-registry", type=Path, default=DEFAULT_LINEAGE_REGISTRY)
    parser.add_argument("--expected-lineage-hash", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        lineage_body = args.lineage_registry.read_bytes()
        if _sha256(lineage_body) != args.expected_lineage_hash.lower():
            raise ValueError("lineage registry SHA-256 does not match")
        result = acquire_wikidata_lineage(
            lineage_body=lineage_body,
            output_root=args.output_root,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Wikidata lineage acquisition failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"represented={result['represented_ticker_count']}/"
        f"{result['requested_ticker_count']} bindings={result['binding_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
