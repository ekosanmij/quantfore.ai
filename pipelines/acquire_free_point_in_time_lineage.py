"""Freeze Tiingo metadata and classify unresolved historical ticker episodes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = REPO_ROOT / "data/raw/free-point-in-time/acquisition-plan-v1-27755aa00a59a111.json"
DEFAULT_IDENTIFIERS = REPO_ROOT / "data/raw/free-point-in-time/resolved-identifiers-v1.json"
DEFAULT_OPENFIGI = REPO_ROOT / "data/raw/free-point-in-time/openfigi-v3/registry.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data/raw/free-point-in-time/lineage-evidence-v1"
US_EXCHANGES = frozenset({"AMEX", "NASDAQ", "NYSE", "NYSE ARCA", "OTC"})


class LineageAcquisitionError(RuntimeError):
    pass


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def _normalized_name(value: str) -> str:
    suffixes = {
        "CO", "COMPANY", "CORP", "CORPORATION", "INC", "INCORPORATED",
        "LTD", "LIMITED", "LLC", "PLC", "SA", "NV", "NEW", "THE",
    }
    tokens = re.sub(r"[^A-Z0-9]+", " ", value.upper()).split()
    return " ".join(token for token in tokens if token not in suffixes)


def _name_similarity(left: str, right: str) -> float:
    return SequenceMatcher(None, _normalized_name(left), _normalized_name(right)).ratio()


def classify_metadata(
    metadata: Mapping[str, Any],
    *,
    episode: Mapping[str, Any],
    expected_names: Sequence[str],
    tolerance_days: int = 7,
) -> dict[str, Any]:
    name = str(metadata.get("name") or "").strip()
    exchange = str(metadata.get("exchangeCode") or "").strip().upper()
    ticker = str(metadata.get("ticker") or "").strip().upper()
    score = max((_name_similarity(name, value) for value in expected_names), default=0.0)
    try:
        listed_from = date.fromisoformat(str(metadata["startDate"]))
        listed_to = date.fromisoformat(str(metadata["endDate"]))
    except (KeyError, ValueError):
        listed_from = listed_to = None
    episode_from = date.fromisoformat(str(episode["effective_from"]))
    episode_to = date.fromisoformat(str(episode["effective_to"]))
    coverage = bool(
        listed_from
        and listed_to
        and (listed_from - episode_from).days <= tolerance_days
        and (episode_to - listed_to).days <= tolerance_days
    )
    ticker_matches = ticker == str(episode["ticker"]).upper()
    us_listing = exchange in US_EXCHANGES
    identity_matches = bool(name and expected_names and score >= 0.65)
    if ticker_matches and us_listing and identity_matches and coverage:
        status = "direct_ticker_verified"
    elif ticker and not us_listing:
        status = "ticker_collision"
    elif ticker_matches and us_listing and identity_matches:
        status = "partial_history_needs_alias"
    else:
        status = "needs_alias_or_external_evidence"
    return {
        "status": status,
        "metadata_ticker": ticker or None,
        "metadata_name": name or None,
        "exchange": exchange or None,
        "start_date": listed_from.isoformat() if listed_from else None,
        "end_date": listed_to.isoformat() if listed_to else None,
        "name_similarity": round(score, 6),
        "identity_matches": identity_matches,
        "coverage_within_tolerance": coverage,
    }


class TiingoMetadataClient:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: int = 30,
        max_retries: int = 5,
        opener: Callable[..., object] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key.strip():
            raise ValueError("Tiingo API key is required")
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._opener = opener
        self._sleep = sleep

    def get(self, ticker: str) -> bytes:
        url = f"https://api.tiingo.com/tiingo/daily/{ticker}"
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._opener(
                    Request(
                        url,
                        headers={
                            "Accept": "application/json",
                            "Authorization": f"Token {self.api_key}",
                        },
                    ),
                    timeout=self.timeout_seconds,
                )
                try:
                    status = int(getattr(response, "status", response.getcode()))
                    body = response.read()
                finally:
                    response.close()
                if status == 200:
                    return body
                if status not in {429, 500, 502, 503, 504}:
                    raise LineageAcquisitionError(
                        f"Tiingo metadata request failed with HTTP {status}"
                    )
                last_error = LineageAcquisitionError(
                    f"Tiingo metadata request failed with HTTP {status}"
                )
            except HTTPError as exc:
                if exc.code == 404:
                    return exc.read()
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise LineageAcquisitionError(
                        f"Tiingo metadata request failed with HTTP {exc.code}"
                    ) from exc
                last_error = exc
            except (TimeoutError, URLError, OSError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                self._sleep(float(2**attempt))
        raise LineageAcquisitionError("Tiingo metadata retries exhausted") from last_error


def acquire_lineage_metadata(
    *,
    client: TiingoMetadataClient,
    plan_body: bytes,
    identifier_body: bytes,
    openfigi_body: bytes,
    output_root: Path,
    request_delay_seconds: float = 0.05,
) -> dict[str, Any]:
    plan = json.loads(plan_body)
    identifiers = json.loads(identifier_body)
    openfigi = json.loads(openfigi_body)
    episodes = list(plan.get("unresolved_episodes", []))
    identifier_by_ticker = {row["ticker"]: row for row in identifiers["mappings"]}
    figi_by_ticker = {row["ticker"]: row for row in openfigi["mappings"]}
    results = []
    for position, episode in enumerate(episodes, start=1):
        ticker = str(episode["ticker"])
        raw_path = output_root / ticker / "metadata.json"
        if raw_path.is_file():
            body = raw_path.read_bytes()
        else:
            body = client.get(ticker)
            _atomic_write(raw_path, body)
            if request_delay_seconds:
                time.sleep(request_delay_seconds)
        try:
            metadata = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LineageAcquisitionError(
                f"Tiingo metadata is invalid for {ticker}"
            ) from exc
        if not isinstance(metadata, dict):
            raise LineageAcquisitionError(f"Tiingo metadata is not an object for {ticker}")
        identity = identifier_by_ticker[ticker]
        figi = figi_by_ticker[ticker]
        expected_names = sorted(
            {
                *identity.get("historical_name_candidates", []),
                *[
                    str(row.get("name") or "")
                    for row in figi.get("matching_candidates", [])
                    if row.get("name")
                ],
            }
        )
        classification = classify_metadata(
            metadata,
            episode=episode,
            expected_names=expected_names,
        )
        results.append(
            {
                "episode_id": episode["episode_id"],
                "ticker": ticker,
                "effective_from": episode["effective_from"],
                "effective_to": episode["effective_to"],
                "expected_names": expected_names,
                "candidate_share_class_figi": figi.get("share_class_figi"),
                "metadata_path": str(raw_path.relative_to(output_root)),
                "metadata_sha256": _sha256(body),
                **classification,
            }
        )
        print(
            f"lineage={position}/{len(episodes)} ticker={ticker} "
            f"status={classification['status']}",
            flush=True,
        )
    direct_rows = [
        row for row in results if row["status"] == "direct_ticker_verified"
    ]
    direct_price_plan = {
        "schema_version": "free-pit-private-acquisition-plan-v1",
        "publication_prohibited": True,
        "parent_acquisition_plan_sha256": _sha256(plan_body),
        "lineage_evidence": [
            {
                key: row[key]
                for key in (
                    "episode_id",
                    "ticker",
                    "effective_from",
                    "effective_to",
                    "candidate_share_class_figi",
                    "metadata_sha256",
                )
            }
            for row in direct_rows
        ],
        "safe_acquisition_batches": [
            {
                "batch_number": 1,
                "symbol_count": len(direct_rows),
                "symbols": sorted(row["ticker"] for row in direct_rows),
            }
        ],
    }
    direct_plan_body = _json_bytes(direct_price_plan)
    direct_plan_path = output_root / "direct-price-plan.json"
    _atomic_write(direct_plan_path, direct_plan_body)
    registry = {
        "schema_version": "free-pit-lineage-evidence-v1",
        "status": "complete",
        "publication_prohibited": True,
        "acquisition_plan_sha256": _sha256(plan_body),
        "identifier_registry_sha256": _sha256(identifier_body),
        "openfigi_registry_sha256": _sha256(openfigi_body),
        "retrieved_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "episode_count": len(results),
        "direct_ticker_verified_count": sum(
            row["status"] == "direct_ticker_verified" for row in results
        ),
        "needs_review_count": sum(
            row["status"] != "direct_ticker_verified" for row in results
        ),
        "direct_price_plan": {
            "path": direct_plan_path.name,
            "sha256": _sha256(direct_plan_body),
        },
        "episodes": results,
    }
    _atomic_write(output_root / "registry.json", _json_bytes(registry))
    return registry


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze Tiingo metadata for unresolved historical episodes."
    )
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--expected-plan-hash", required=True)
    parser.add_argument("--identifiers", type=Path, default=DEFAULT_IDENTIFIERS)
    parser.add_argument("--expected-identifier-hash", required=True)
    parser.add_argument("--openfigi-registry", type=Path, default=DEFAULT_OPENFIGI)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        plan_body = args.plan.read_bytes()
        identifier_body = args.identifiers.read_bytes()
        if _sha256(plan_body) != args.expected_plan_hash.lower():
            raise ValueError("acquisition plan SHA-256 does not match")
        if _sha256(identifier_body) != args.expected_identifier_hash.lower():
            raise ValueError("identifier registry SHA-256 does not match")
        result = acquire_lineage_metadata(
            client=TiingoMetadataClient(
                api_key=os.environ.get("TIINGO_API_KEY", "")
            ),
            plan_body=plan_body,
            identifier_body=identifier_body,
            openfigi_body=args.openfigi_registry.read_bytes(),
            output_root=args.output_root,
        )
    except (KeyError, OSError, LineageAcquisitionError, ValueError) as exc:
        print(f"lineage metadata acquisition failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"direct={result['direct_ticker_verified_count']}/"
        f"{result['episode_count']} review={result['needs_review_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
