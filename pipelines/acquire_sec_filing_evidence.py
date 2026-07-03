"""Resumably freeze SEC filing-index acceptance and dated SIC evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = REPO_ROOT / "data/raw/free-point-in-time/sec-filing-evidence-plan-v1.json"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data/raw/free-point-in-time/sec-filing-evidence-v1"
SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT", "QuantforeAIResearch/0.1 research@quantfore.ai"
)
RETRIABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
EASTERN = ZoneInfo("America/New_York")


class SecFilingEvidenceError(RuntimeError):
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


def _filing_index_url(row: Mapping[str, Any]) -> str:
    accession = str(row["accession"])
    compact = accession.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{int(str(row['cik']))}/{compact}/{accession}-index.html"
    )


def _field(html: str, label: str) -> str:
    pattern = re.compile(
        rf'<div\s+class="infoHead">\s*{re.escape(label)}\s*</div>\s*'
        r'<div\s+class="info">\s*([^<]+?)\s*</div>',
        re.IGNORECASE,
    )
    match = pattern.search(html)
    if match is None:
        raise SecFilingEvidenceError(f"SEC filing index lacks {label}")
    return match.group(1).strip()


def parse_filing_index(
    body: bytes,
    *,
    cik: str,
    accession: str,
    expected_filed: str,
) -> dict[str, Any]:
    """Extract acceptance time and the target filer's dated SIC from SEC HTML."""

    try:
        html = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SecFilingEvidenceError("SEC filing index is not UTF-8") from exc
    if accession not in html:
        raise SecFilingEvidenceError("SEC filing index accession does not match")
    filed = _field(html, "Filing Date")
    filed_matches_plan = filed == expected_filed
    accepted_text = _field(html, "Accepted")
    try:
        accepted_local = datetime.strptime(accepted_text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=EASTERN
        )
    except ValueError as exc:
        raise SecFilingEvidenceError("SEC acceptance timestamp is invalid") from exc

    normalized_cik = f"{int(cik):010d}"
    company_blocks = re.findall(
        r'<span\s+class="companyName">.*?</p>', html, flags=re.IGNORECASE | re.DOTALL
    )
    sic = None
    for block in company_blocks:
        cik_match = re.search(
            r"(?:Central Index Key|CIK).*?([0-9]{10})",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if cik_match is None or cik_match.group(1) != normalized_cik:
            continue
        sic_match = re.search(
            r"Standard Industrial Code.*?SIC=([0-9]{4})",
            block,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if sic_match is not None:
            sic = sic_match.group(1)
        break
    return {
        "filed": filed,
        "planned_filed": expected_filed,
        "filed_matches_plan": filed_matches_plan,
        "accepted_at": accepted_local.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "sic": sic,
        "sic_available": sic is not None,
    }


class SecFilingClient:
    def __init__(
        self,
        *,
        user_agent: str = SEC_USER_AGENT,
        requests_per_second: float = 8.0,
        timeout_seconds: int = 30,
        max_retries: int = 5,
        opener: Callable[..., object] = urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("SEC user agent is required")
        if not 0 < requests_per_second <= 10:
            raise ValueError("SEC request rate must be between 0 and 10 per second")
        self.user_agent = user_agent.strip()
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._opener = opener
        self._sleep = sleep
        self._minimum_interval = 1.0 / requests_per_second
        self._rate_lock = threading.Lock()
        self._next_request_at = 0.0

    def _wait_for_slot(self) -> None:
        with self._rate_lock:
            now = time.monotonic()
            delay = max(0.0, self._next_request_at - now)
            self._next_request_at = max(now, self._next_request_at) + self._minimum_interval
        if delay:
            self._sleep(delay)

    def get(self, url: str) -> bytes:
        last_error: Optional[BaseException] = None
        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            try:
                response = self._opener(
                    Request(
                        url,
                        headers={
                            "Accept": "text/html,application/xhtml+xml",
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
                if status not in RETRIABLE_STATUS_CODES:
                    raise SecFilingEvidenceError(
                        f"SEC filing request failed with HTTP {status}"
                    )
                last_error = SecFilingEvidenceError(
                    f"SEC filing request failed with HTTP {status}"
                )
            except HTTPError as exc:
                if exc.code not in RETRIABLE_STATUS_CODES:
                    raise SecFilingEvidenceError(
                        f"SEC filing request failed with HTTP {exc.code}"
                    ) from exc
                last_error = exc
            except (TimeoutError, URLError, OSError) as exc:
                last_error = exc
            if attempt < self.max_retries:
                self._sleep(float(2**attempt))
        raise SecFilingEvidenceError("SEC filing request retries exhausted") from last_error


def _completion_record(
    path: Path, *, plan_sha256: str, row: Mapping[str, Any]
) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    record = json.loads(path.read_text())
    raw_path = path.parent / str(record.get("path", ""))
    if (
        record.get("filing_plan_sha256") != plan_sha256
        or record.get("cik") != row["cik"]
        or record.get("accession") != row["accession"]
        or not raw_path.is_file()
        or _sha256(raw_path.read_bytes()) != record.get("sha256")
    ):
        raise ValueError(f"frozen SEC filing evidence conflicts: {row['accession']}")
    if "filed_matches_plan" not in record:
        record.update(
            parse_filing_index(
                raw_path.read_bytes(),
                cik=str(row["cik"]),
                accession=str(row["accession"]),
                expected_filed=str(row["filed"]),
            )
        )
        _atomic_write(path, _json_bytes(record))
    return record


def acquire_filing_evidence(
    *,
    client: SecFilingClient,
    plan_body: bytes,
    output_root: Path,
    workers: int = 4,
    max_filings: Optional[int] = None,
) -> dict[str, Any]:
    plan = json.loads(plan_body)
    if plan.get("schema_version") != "free-pit-sec-filing-evidence-plan-v1":
        raise ValueError("SEC filing evidence plan is invalid")
    plan_hash = _sha256(plan_body)
    filings = list(plan["filings"])
    if max_filings is not None:
        if max_filings < 1:
            raise ValueError("max_filings must be positive")
        filings = filings[:max_filings]
    if workers < 1 or workers > 8:
        raise ValueError("workers must be between 1 and 8")

    completed: list[dict[str, Any]] = []
    pending: list[tuple[dict[str, Any], Path]] = []
    for row in filings:
        cik_dir = output_root / f"CIK{row['cik']}"
        completion = cik_dir / f"{row['accession']}.complete.json"
        prior = _completion_record(completion, plan_sha256=plan_hash, row=row)
        if prior is None:
            pending.append((row, completion))
        else:
            completed.append(prior)

    lock = threading.Lock()

    def download(item: tuple[dict[str, Any], Path]) -> dict[str, Any]:
        row, completion = item
        url = _filing_index_url(row)
        body = client.get(url)
        parsed = parse_filing_index(
            body,
            cik=str(row["cik"]),
            accession=str(row["accession"]),
            expected_filed=str(row["filed"]),
        )
        digest = _sha256(body)
        raw_path = completion.parent / f"{row['accession']}-index-{digest[:16]}.html"
        if raw_path.exists() and raw_path.read_bytes() != body:
            raise ValueError(f"frozen SEC filing path conflict: {row['accession']}")
        _atomic_write(raw_path, body)
        record = {
            "schema_version": "free-pit-sec-filing-evidence-v1",
            "publication_prohibited": True,
            "filing_plan_sha256": plan_hash,
            "cik": row["cik"],
            "accession": row["accession"],
            "form": row["form"],
            "source_url": url,
            "path": raw_path.name,
            "sha256": digest,
            **parsed,
        }
        _atomic_write(completion, _json_bytes(record))
        return record

    downloaded = 0
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(download, item): item for item in pending}
        for future in as_completed(futures):
            item = futures[future]
            try:
                record = future.result()
            except (KeyError, OSError, SecFilingEvidenceError, ValueError) as exc:
                failures.append(
                    {
                        "cik": str(item[0]["cik"]),
                        "accession": str(item[0]["accession"]),
                        "error": str(exc),
                    }
                )
                print(
                    f"filing_failed accession={item[0]['accession']} error={exc}",
                    flush=True,
                )
                continue
            with lock:
                completed.append(record)
                downloaded += 1
                position = len(completed)
            if downloaded == 1 or downloaded % 100 == 0 or position == len(filings):
                print(
                    f"filings={position}/{len(filings)} downloaded={downloaded} "
                    f"accession={record['accession']}",
                    flush=True,
                )

    ordered = sorted(completed, key=lambda row: (row["cik"], row["accession"]))
    registry = {
        "schema_version": "free-pit-sec-filing-evidence-registry-v1",
        "status": (
            "complete"
            if len(ordered) == len(filings) and not failures
            else "in_progress"
        ),
        "publication_prohibited": True,
        "filing_plan_sha256": plan_hash,
        "requested_filing_count": len(filings),
        "complete_filing_count": len(ordered),
        "downloaded_filing_count": downloaded,
        "reused_filing_count": len(ordered) - downloaded,
        "sic_available_count": sum(row["sic_available"] for row in ordered),
        "filing_date_mismatch_count": sum(
            not row["filed_matches_plan"] for row in ordered
        ),
        "failed_filing_count": len(failures),
        "failures": sorted(failures, key=lambda row: (row["cik"], row["accession"])),
        "completion_sha256": _sha256(_json_bytes(ordered)),
    }
    _atomic_write(output_root / "registry.json", _json_bytes(registry))
    return registry


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze SEC filing-index acceptance and SIC evidence."
    )
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--expected-plan-hash", required=True)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--requests-per-second", type=float, default=8.0)
    parser.add_argument("--max-filings", type=int)
    parser.add_argument("--user-agent", default=SEC_USER_AGENT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        plan_body = args.plan.read_bytes()
        if _sha256(plan_body) != args.expected_plan_hash.lower():
            raise ValueError("SEC filing evidence plan SHA-256 does not match")
        result = acquire_filing_evidence(
            client=SecFilingClient(
                user_agent=args.user_agent,
                requests_per_second=args.requests_per_second,
            ),
            plan_body=plan_body,
            output_root=args.output_root,
            workers=args.workers,
            max_filings=args.max_filings,
        )
    except (KeyError, OSError, SecFilingEvidenceError, ValueError) as exc:
        print(f"SEC filing evidence acquisition failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"complete={result['complete_filing_count']}/"
        f"{result['requested_filing_count']} sic={result['sic_available_count']}"
    )
    return 0 if result["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
