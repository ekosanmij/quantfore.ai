"""Freeze resumable OpenFIGI v3 mappings for the private PIT acquisition plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data/raw/free-point-in-time/openfigi-v3"
OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
OPENFIGI_API_KEY_ENV = "OPENFIGI_API_KEY"
ANONYMOUS_MAX_JOBS = 5


class IdentifierAcquisitionError(RuntimeError):
    """Raised when an identifier response cannot be safely accepted."""


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _load_plan(path: Path, *, expected_hash: str) -> dict[str, Any]:
    body = path.read_bytes()
    if _sha256(body) != expected_hash.lower():
        raise ValueError("private acquisition plan SHA-256 does not match")
    plan = json.loads(body)
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != "free-pit-private-acquisition-plan-v1"
        or plan.get("publication_prohibited") is not True
    ):
        raise ValueError("private acquisition plan has an invalid contract")
    return plan


def _plan_tickers(plan: Mapping[str, Any]) -> list[str]:
    tickers = {
        str(ticker)
        for batch in plan.get("safe_acquisition_batches", [])
        for ticker in batch.get("symbols", [])
    }
    tickers.update(
        str(row["ticker"]) for row in plan.get("unresolved_episodes", [])
    )
    if not tickers or any(not ticker.strip() for ticker in tickers):
        raise ValueError("private acquisition plan contains invalid tickers")
    return sorted(tickers)


def _openfigi_ticker(ticker: str) -> str:
    match = re.fullmatch(r"([A-Z0-9]+)[.-]([A-Z])", ticker.upper())
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    return ticker.upper()


def _canonical_ticker(ticker: str) -> str:
    return ticker.strip().upper().replace("/", ".").replace("-", ".")


def _jobs(tickers: Sequence[str]) -> list[dict[str, Any]]:
    return [
        {
            "idType": "TICKER",
            "idValue": _openfigi_ticker(ticker),
            "exchCode": "US",
            "marketSecDes": "Equity",
            "includeUnlistedEquities": True,
        }
        for ticker in tickers
    ]


def _retry_seconds(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())


class OpenFigiClient:
    def __init__(
        self,
        *,
        api_key: str = "",
        timeout_seconds: int = 30,
        max_retries: int = 4,
        opener: Callable[..., object] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._opener = opener
        self._sleep = sleep

    @classmethod
    def from_env(cls, **kwargs: object) -> "OpenFigiClient":
        return cls(api_key=os.environ.get(OPENFIGI_API_KEY_ENV, ""), **kwargs)

    @property
    def max_jobs(self) -> int:
        return 100 if self.api_key else ANONYMOUS_MAX_JOBS

    def map(self, jobs: Sequence[Mapping[str, Any]]) -> bytes:
        if not jobs or len(jobs) > self.max_jobs:
            raise ValueError(f"OpenFIGI request must contain 1-{self.max_jobs} jobs")
        body = json.dumps(list(jobs), separators=(",", ":")).encode()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "QuantforeAIResearch/0.1",
        }
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            retry_after = None
            try:
                response = self._opener(
                    Request(OPENFIGI_URL, data=body, headers=headers, method="POST"),
                    timeout=self.timeout_seconds,
                )
                try:
                    status = int(getattr(response, "status", response.getcode()))
                    result = response.read()
                    response_headers = getattr(response, "headers", {})
                finally:
                    response.close()
                if status == 200:
                    return result
                if status not in {429, 500, 502, 503, 504}:
                    raise IdentifierAcquisitionError(
                        f"OpenFIGI request failed with HTTP {status}"
                    )
                last_error = IdentifierAcquisitionError(
                    f"OpenFIGI request failed with HTTP {status}"
                )
                retry_after = response_headers.get("Retry-After")
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise IdentifierAcquisitionError(
                        f"OpenFIGI request failed with HTTP {exc.code}"
                    ) from exc
                last_error = exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
            except (TimeoutError, URLError, OSError) as exc:
                last_error = exc
            if attempt >= self.max_retries:
                break
            delay = _retry_seconds(retry_after)
            self._sleep(delay if delay is not None else float(2**attempt))
        raise IdentifierAcquisitionError("OpenFIGI request retries exhausted") from last_error


def _parse_response(
    body: bytes,
    tickers: Sequence[str],
    *,
    lineage_required: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IdentifierAcquisitionError("OpenFIGI response is not valid JSON") from exc
    if not isinstance(value, list) or len(value) != len(tickers):
        raise IdentifierAcquisitionError("OpenFIGI response count does not match request")
    parsed = []
    for ticker, result in zip(tickers, value):
        if not isinstance(result, dict):
            raise IdentifierAcquisitionError("OpenFIGI result must be an object")
        candidates = []
        data = result.get("data", [])
        if data is not None and not isinstance(data, list):
            raise IdentifierAcquisitionError("OpenFIGI data must be an array")
        for candidate in data or []:
            if not isinstance(candidate, dict):
                raise IdentifierAcquisitionError("OpenFIGI candidate must be an object")
            if (
                candidate.get("marketSector") == "Equity"
                and _canonical_ticker(str(candidate.get("ticker", "")))
                == _canonical_ticker(ticker)
                and isinstance(candidate.get("shareClassFIGI"), str)
                and candidate["shareClassFIGI"].strip()
            ):
                candidates.append(candidate)
        share_classes = sorted({row["shareClassFIGI"] for row in candidates})
        candidate_status = (
            "unresolved" if not share_classes else (
                "ambiguous" if len(share_classes) > 1 else "unique"
            )
        )
        if ticker in lineage_required:
            status = "needs_lineage"
        elif candidate_status == "unique":
            status = "resolved"
        else:
            status = candidate_status
        parsed.append(
            {
                "ticker": ticker,
                "status": status,
                "candidate_status": candidate_status,
                "share_class_figi": share_classes[0] if len(share_classes) == 1 else None,
                "matching_candidate_count": len(candidates),
                "distinct_share_class_count": len(share_classes),
                "matching_candidates": [
                    {
                        key: candidate.get(key)
                        for key in (
                            "figi",
                            "name",
                            "ticker",
                            "exchCode",
                            "compositeFIGI",
                            "shareClassFIGI",
                            "securityType",
                            "securityType2",
                        )
                    }
                    for candidate in candidates
                ],
                "warning": result.get("warning"),
            }
        )
    return parsed


def acquire_identifiers(
    *,
    client: OpenFigiClient,
    plan: Mapping[str, Any],
    plan_sha256: str,
    output_root: Path,
    max_tickers: Optional[int] = None,
    request_delay_seconds: float = 2.5,
) -> dict[str, Any]:
    tickers = _plan_tickers(plan)
    lineage_required = frozenset(
        str(row["ticker"]) for row in plan.get("unresolved_episodes", [])
    )
    if max_tickers is not None:
        if max_tickers < 1:
            raise ValueError("max_tickers must be positive")
        tickers = tickers[:max_tickers]
    batches = [tickers[i : i + client.max_jobs] for i in range(0, len(tickers), client.max_jobs)]
    mappings: list[dict[str, Any]] = []
    downloaded = 0
    reused = 0
    for number, batch_tickers in enumerate(batches, start=1):
        jobs = _jobs(batch_tickers)
        request_hash = _sha256(_json_bytes(jobs))
        prefix = f"mapping-{number:04d}-{request_hash[:16]}"
        response_path = output_root / f"{prefix}.json"
        completion_path = output_root / f"{prefix}.complete.json"
        if completion_path.exists():
            completion = json.loads(completion_path.read_text())
            if (
                completion.get("request_sha256") != request_hash
                or completion.get("tickers") != list(batch_tickers)
                or not response_path.exists()
                or _sha256(response_path.read_bytes()) != completion.get("response_sha256")
            ):
                raise ValueError(f"frozen OpenFIGI mapping {number} does not reproduce")
            parsed = _parse_response(
                response_path.read_bytes(),
                batch_tickers,
                lineage_required=lineage_required,
            )
            reused += 1
        else:
            body = client.map(jobs)
            parsed = _parse_response(
                body,
                batch_tickers,
                lineage_required=lineage_required,
            )
            _atomic_write(response_path, body)
            completion = {
                "schema_version": "free-pit-openfigi-mapping-v1",
                "publication_prohibited": True,
                "acquisition_plan_sha256": plan_sha256,
                "tickers": list(batch_tickers),
                "request_sha256": request_hash,
                "response_sha256": _sha256(body),
                "path": response_path.name,
            }
            _atomic_write(completion_path, _json_bytes(completion))
            downloaded += 1
        mappings.extend(parsed)
        registry = {
            "schema_version": "free-pit-openfigi-registry-v1",
            "status": "complete" if number == len(batches) else "in_progress",
            "publication_prohibited": True,
            "acquisition_plan_sha256": plan_sha256,
            "requested_ticker_count": len(tickers),
            "processed_ticker_count": len(mappings),
            "resolved_ticker_count": sum(row["status"] == "resolved" for row in mappings),
            "lineage_required_ticker_count": sum(
                row["status"] == "needs_lineage" for row in mappings
            ),
            "ambiguous_ticker_count": sum(row["status"] == "ambiguous" for row in mappings),
            "unresolved_ticker_count": sum(row["status"] == "unresolved" for row in mappings),
            "downloaded_request_count": downloaded,
            "reused_request_count": reused,
            "mappings": mappings,
        }
        _atomic_write(output_root / "registry.json", _json_bytes(registry))
        print(
            f"request={number}/{len(batches)} tickers={len(mappings)}/{len(tickers)} "
            f"resolved={registry['resolved_ticker_count']}",
            flush=True,
        )
        if downloaded and request_delay_seconds and number < len(batches):
            time.sleep(request_delay_seconds)
    return registry


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze OpenFIGI mappings for a PIT plan.")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-hash", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--max-tickers", type=int)
    parser.add_argument("--request-delay-seconds", type=float, default=2.5)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        plan = _load_plan(args.plan, expected_hash=args.expected_plan_hash)
        registry = acquire_identifiers(
            client=OpenFigiClient.from_env(),
            plan=plan,
            plan_sha256=args.expected_plan_hash.lower(),
            output_root=args.output_root,
            max_tickers=args.max_tickers,
            request_delay_seconds=args.request_delay_seconds,
        )
    except (IdentifierAcquisitionError, OSError, ValueError) as exc:
        print(f"OpenFIGI acquisition failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"complete={registry['processed_ticker_count']}/{registry['requested_ticker_count']} "
        f"resolved={registry['resolved_ticker_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
