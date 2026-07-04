"""Freeze resumable SEC Companyfacts and submissions for resolved PIT identities."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IDENTIFIERS = (
    REPO_ROOT / "data/raw/free-point-in-time/resolved-identifiers-v1.json"
)
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data/raw/free-point-in-time/sec-pit-v1"
SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT", "QuantforeAIResearch/0.1 research@quantfore.ai"
)


class SecAcquisitionError(RuntimeError):
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


def _resolved_ciks(registry: Mapping[str, Any]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in registry.get("mappings", []):
        cik = str(row.get("cik") or "")
        if row.get("status") != "resolved" or not cik:
            continue
        if len(cik) != 10 or not cik.isdigit():
            raise ValueError(f"invalid resolved CIK: {cik!r}")
        target = grouped.setdefault(
            cik,
            {"cik": cik, "tickers": [], "share_class_figis": []},
        )
        target["tickers"].append(str(row["ticker"]))
        if row.get("share_class_figi"):
            target["share_class_figis"].append(str(row["share_class_figi"]))
    for row in grouped.values():
        row["tickers"] = sorted(set(row["tickers"]))
        row["share_class_figis"] = sorted(set(row["share_class_figis"]))
    return [grouped[cik] for cik in sorted(grouped)]


class SecClient:
    def __init__(
        self,
        *,
        user_agent: str = SEC_USER_AGENT,
        timeout_seconds: int = 30,
        max_retries: int = 5,
        opener: Callable[..., object] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("SEC user agent is required")
        self.user_agent = user_agent.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._opener = opener
        self._sleep = sleep

    def get(self, url: str) -> bytes:
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            try:
                response = self._opener(
                    Request(
                        url,
                        headers={
                            "Accept": "application/json",
                            "User-Agent": self.user_agent,
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
                    raise SecAcquisitionError(f"SEC request failed with HTTP {status}")
                last_error = SecAcquisitionError(
                    f"SEC request failed with HTTP {status}"
                )
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise SecAcquisitionError(
                        f"SEC request failed with HTTP {exc.code}"
                    ) from exc
                last_error = exc
            except (TimeoutError, URLError, OSError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                self._sleep(float(2**attempt))
        raise SecAcquisitionError("SEC request retries exhausted") from last_error


def _validate_payload(body: bytes, *, cik: str, dataset: str) -> None:
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecAcquisitionError(f"SEC {dataset} payload is invalid JSON") from exc
    if not isinstance(value, dict):
        raise SecAcquisitionError(f"SEC {dataset} payload must be an object")
    payload_cik = str(value.get("cik") or "").zfill(10)
    if payload_cik != cik:
        raise SecAcquisitionError(
            f"SEC {dataset} CIK mismatch: expected {cik}, received {payload_cik}"
        )
    if dataset == "companyfacts" and not isinstance(value.get("facts"), dict):
        raise SecAcquisitionError("SEC companyfacts payload lacks facts")
    if dataset == "submissions" and not isinstance(value.get("filings"), dict):
        raise SecAcquisitionError("SEC submissions payload lacks filings")


def _completion_record(path: Path, *, identity_hash: str, cik: str) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    record = json.loads(path.read_text())
    if record.get("identifier_registry_sha256") != identity_hash or record.get("cik") != cik:
        raise ValueError(f"conflicting SEC completion for CIK {cik}")
    for dataset in record.get("datasets", ("companyfacts", "submissions")):
        source = record.get(dataset, {})
        raw_path = path.parent / str(source.get("path", ""))
        if not raw_path.is_file() or _sha256(raw_path.read_bytes()) != source.get("sha256"):
            raise ValueError(f"frozen SEC {dataset} does not reproduce for CIK {cik}")
    return record


def acquire_sec_sources(
    *,
    client: SecClient,
    identifier_body: bytes,
    output_root: Path,
    max_ciks: Optional[int] = None,
    request_delay_seconds: float = 0.12,
    workers: int = 8,
    additional_identities: Sequence[Mapping[str, Any]] = (),
    include_resolved_identities: bool = True,
    include_companyfacts: bool = True,
) -> dict[str, Any]:
    registry = json.loads(identifier_body)
    identity_hash = _sha256(identifier_body)
    identities = _resolved_ciks(registry) if include_resolved_identities else []
    by_cik = {row["cik"]: row for row in identities}
    for supplied in additional_identities:
        cik = str(supplied["cik"]).zfill(10)
        if len(cik) != 10 or not cik.isdigit():
            raise ValueError(f"invalid additional CIK: {cik!r}")
        target = by_cik.setdefault(
            cik,
            {"cik": cik, "tickers": [], "share_class_figis": []},
        )
        target["tickers"] = sorted(
            {*target["tickers"], *[str(value) for value in supplied.get("tickers", [])]}
        )
    identities = [by_cik[cik] for cik in sorted(by_cik)]
    if max_ciks is not None:
        if max_ciks < 1:
            raise ValueError("max_ciks must be positive")
        identities = identities[:max_ciks]
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")
    completed: list[dict[str, Any]] = []
    pending: list[dict[str, Any]] = []
    for identity in identities:
        cik = identity["cik"]
        cik_dir = output_root / f"CIK{cik}"
        completion_path = cik_dir / "complete.json"
        prior = _completion_record(
            completion_path, identity_hash=identity_hash, cik=cik
        )
        if prior is not None:
            completed.append(prior)
        else:
            pending.append(identity)

    reused = len(completed)
    downloaded = 0

    def download(identity: dict[str, Any]) -> dict[str, Any]:
        cik = identity["cik"]
        cik_dir = output_root / f"CIK{cik}"
        sources: dict[str, dict[str, Any]] = {}
        urls = {"submissions": f"https://data.sec.gov/submissions/CIK{cik}.json"}
        if include_companyfacts:
            urls["companyfacts"] = (
                f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
            )
        for dataset, url in urls.items():
            body = client.get(url)
            _validate_payload(body, cik=cik, dataset=dataset)
            digest = _sha256(body)
            raw_path = cik_dir / f"{dataset}-{digest[:16]}.json"
            if raw_path.exists() and raw_path.read_bytes() != body:
                raise ValueError(f"frozen SEC path conflict for CIK {cik}")
            _atomic_write(raw_path, body)
            sources[dataset] = {
                "path": raw_path.name,
                "sha256": digest,
                "source_url": url,
            }
            if request_delay_seconds:
                time.sleep(request_delay_seconds)
        record = {
            "schema_version": "free-pit-sec-company-v1",
            "status": "complete",
            "publication_prohibited": True,
            "cik": cik,
            "tickers": identity["tickers"],
            "share_class_figis": identity["share_class_figis"],
            "identifier_registry_sha256": identity_hash,
            "datasets": sorted(sources),
            "retrieved_at": datetime.now(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            **sources,
        }
        _atomic_write(cik_dir / "complete.json", _json_bytes(record))
        return record

    summary: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download, identity): identity for identity in pending}
        for future in as_completed(futures):
            record = future.result()
            completed.append(record)
            completed.sort(key=lambda row: row["cik"])
            downloaded += 1
            position = len(completed)
            summary = {
                "schema_version": "free-pit-sec-registry-v1",
                "status": (
                    "complete"
                    if len(completed) == len(identities)
                    else "in_progress"
                ),
                "publication_prohibited": True,
                "identifier_registry_sha256": identity_hash,
                "requested_cik_count": len(identities),
                "complete_cik_count": len(completed),
                "downloaded_cik_count": downloaded,
                "reused_cik_count": reused,
                "completion_sha256": _sha256(_json_bytes(completed)),
            }
            _atomic_write(output_root / "registry.json", _json_bytes(summary))
            print(
                f"cik={position}/{len(identities)} value={record['cik']} "
                "status=downloaded",
                flush=True,
            )
    if not pending:
        summary = {
            "schema_version": "free-pit-sec-registry-v1",
            "status": "complete",
            "publication_prohibited": True,
            "identifier_registry_sha256": identity_hash,
            "requested_cik_count": len(identities),
            "complete_cik_count": len(completed),
            "downloaded_cik_count": 0,
            "reused_cik_count": reused,
            "completion_sha256": _sha256(_json_bytes(completed)),
        }
        _atomic_write(output_root / "registry.json", _json_bytes(summary))
    return summary


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Freeze SEC PIT source documents.")
    parser.add_argument("--identifiers", type=Path, default=DEFAULT_IDENTIFIERS)
    parser.add_argument("--expected-identifier-hash", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--max-ciks", type=int)
    parser.add_argument("--request-delay-seconds", type=float, default=0.12)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--user-agent", default=SEC_USER_AGENT)
    parser.add_argument(
        "--additional-cik",
        action="append",
        default=[],
        metavar="CIK:TICKER",
    )
    parser.add_argument("--additional-only", action="store_true")
    parser.add_argument("--submissions-only", action="store_true")
    return parser.parse_args(argv)


def _additional_identities(values: Sequence[str]) -> tuple[dict[str, Any], ...]:
    grouped: dict[str, set[str]] = {}
    for value in values:
        try:
            cik, ticker = value.split(":", 1)
        except ValueError as exc:
            raise ValueError("--additional-cik must use CIK:TICKER") from exc
        if not cik.isdigit() or not ticker.strip():
            raise ValueError("--additional-cik must use CIK:TICKER")
        grouped.setdefault(cik.zfill(10), set()).add(ticker.strip().upper())
    return tuple(
        {"cik": cik, "tickers": sorted(tickers), "share_class_figis": []}
        for cik, tickers in sorted(grouped.items())
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        identifier_body = args.identifiers.read_bytes()
        if _sha256(identifier_body) != args.expected_identifier_hash.lower():
            raise ValueError("identifier registry SHA-256 does not match")
        result = acquire_sec_sources(
            client=SecClient(user_agent=args.user_agent),
            identifier_body=identifier_body,
            output_root=args.output_root,
            max_ciks=args.max_ciks,
            request_delay_seconds=args.request_delay_seconds,
            workers=args.workers,
            additional_identities=_additional_identities(args.additional_cik),
            include_resolved_identities=not args.additional_only,
            include_companyfacts=not args.submissions_only,
        )
    except (KeyError, OSError, SecAcquisitionError, ValueError) as exc:
        print(f"SEC PIT acquisition failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"complete={result['complete_cik_count']}/{result['requested_cik_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
