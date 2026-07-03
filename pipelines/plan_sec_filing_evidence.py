"""Build a deterministic SEC filing-index plan for availability and SIC evidence."""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEC_ROOT = REPO_ROOT / "data/raw/free-point-in-time/sec-pit-v1"
DEFAULT_OUTPUT = (
    REPO_ROOT / "data/raw/free-point-in-time/sec-filing-evidence-plan-v1.json"
)
ELIGIBLE_FORMS = frozenset(
    {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A"}
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


def _filing_url(cik: str, accession: str) -> str:
    compact = accession.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(cik)}/{compact}/{accession}-index.html"
    )


def build_filing_plan(
    *,
    sec_root: Path,
    expected_registry_hash: str,
    filed_start: str = "2012-01-01",
    filed_end: str = "2025-12-31",
) -> dict[str, Any]:
    registry_path = sec_root / "registry.json"
    registry_body = registry_path.read_bytes()
    if _sha256(registry_body) != expected_registry_hash.lower():
        raise ValueError("SEC registry SHA-256 does not match")
    registry = json.loads(registry_body)
    if registry.get("status") != "complete":
        raise ValueError("SEC source registry is not complete")
    filings: dict[tuple[str, str], dict[str, Any]] = {}
    completion_paths = sorted(sec_root.glob("CIK*/complete.json"))
    if len(completion_paths) != registry["complete_cik_count"]:
        raise ValueError("SEC completion count does not match registry")
    for completion_path in completion_paths:
        completion = json.loads(completion_path.read_text())
        cik = str(completion["cik"])
        source = completion["companyfacts"]
        facts_path = completion_path.parent / source["path"]
        facts_body = facts_path.read_bytes()
        if _sha256(facts_body) != source["sha256"]:
            raise ValueError(f"SEC companyfacts hash mismatch for CIK {cik}")
        document = json.loads(facts_body)
        for taxonomy in document.get("facts", {}).values():
            for concept in taxonomy.values():
                for observations in concept.get("units", {}).values():
                    for item in observations:
                        form = str(item.get("form") or "").upper()
                        filed = str(item.get("filed") or "")
                        accession = str(item.get("accn") or "")
                        if (
                            form not in ELIGIBLE_FORMS
                            or not filed_start <= filed <= filed_end
                            or not accession
                        ):
                            continue
                        key = (cik, accession)
                        prior = filings.get(key)
                        row = {
                            "cik": cik,
                            "accession": accession,
                            "form": form,
                            "filed": filed,
                            "source_url": _filing_url(cik, accession),
                        }
                        if prior is not None and prior != row:
                            raise ValueError(
                                f"conflicting filing metadata for {cik} {accession}"
                            )
                        filings[key] = row
        del document, facts_body
        gc.collect()
    ordered = [filings[key] for key in sorted(filings)]
    return {
        "schema_version": "free-pit-sec-filing-evidence-plan-v1",
        "publication_prohibited": True,
        "sec_registry_sha256": _sha256(registry_body),
        "identifier_registry_sha256": registry["identifier_registry_sha256"],
        "filed_window": {"start": filed_start, "end": filed_end},
        "cik_count": len({row["cik"] for row in ordered}),
        "filing_count": len(ordered),
        "forms": sorted(ELIGIBLE_FORMS),
        "filings": ordered,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Plan SEC filing-index evidence.")
    parser.add_argument("--sec-root", type=Path, default=DEFAULT_SEC_ROOT)
    parser.add_argument("--expected-registry-hash", required=True)
    parser.add_argument("--filed-start", default="2012-01-01")
    parser.add_argument("--filed-end", default="2025-12-31")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        plan = build_filing_plan(
            sec_root=args.sec_root,
            expected_registry_hash=args.expected_registry_hash,
            filed_start=args.filed_start,
            filed_end=args.filed_end,
        )
        body = _json_bytes(plan)
        _atomic_write(args.output, body)
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"SEC filing evidence planning failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"filings={plan['filing_count']} ciks={plan['cik_count']} "
        f"sha256={_sha256(body)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
