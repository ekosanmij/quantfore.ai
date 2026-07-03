"""Resumably freeze one Tiingo batch from the private free-source plan."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401

from quantfore_research.ingest.market_prices import (
    MarketPriceError,
    TiingoMarketPriceClient,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "data/raw/free-point-in-time/tiingo-prices-v1"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def _load_plan(path: Path, *, expected_hash: str) -> dict[str, Any]:
    body = path.read_bytes()
    actual = hashlib.sha256(body).hexdigest()
    if actual != expected_hash.lower():
        raise ValueError("private acquisition plan SHA-256 does not match")
    plan = json.loads(body)
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != "free-pit-private-acquisition-plan-v1"
        or plan.get("publication_prohibited") is not True
    ):
        raise ValueError("private acquisition plan has an invalid contract")
    batches = plan.get("safe_acquisition_batches")
    if not isinstance(batches, list) or not batches:
        raise ValueError("private acquisition plan contains no safe batches")
    return plan


def _completed_record(
    path: Path,
    *,
    ticker: str,
    start_date: date,
    end_date: date,
) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or value.get("ticker") != ticker
        or value.get("start_date") != start_date.isoformat()
        or value.get("end_date") != end_date.isoformat()
        or value.get("status") != "complete"
    ):
        raise ValueError(f"conflicting completion record for {ticker}")
    pages = value.get("pages")
    if not isinstance(pages, list) or not pages:
        raise ValueError(f"incomplete page manifest for {ticker}")
    for page in pages:
        raw_path = path.parent / str(page["path"])
        if (
            not raw_path.is_file()
            or hashlib.sha256(raw_path.read_bytes()).hexdigest() != page["sha256"]
        ):
            raise ValueError(f"frozen Tiingo page does not reproduce for {ticker}")
    return value


def _write_registry(
    batch_dir: Path,
    *,
    batch_number: int,
    plan_sha256: str,
    start_date: date,
    end_date: date,
    requested_symbol_count: int,
    completed: list[dict[str, Any]],
    downloaded: int,
    reused: int,
) -> dict[str, Any]:
    complete = len(completed) == requested_symbol_count
    registry = {
        "schema_version": "free-pit-tiingo-batch-v1",
        "status": "complete" if complete else "in_progress",
        "publication_prohibited": True,
        "batch_number": batch_number,
        "acquisition_plan_sha256": plan_sha256,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "requested_symbol_count": requested_symbol_count,
        "complete_symbol_count": len(completed),
        "downloaded_symbol_count": downloaded,
        "reused_symbol_count": reused,
        "tickers": [row["ticker"] for row in completed],
        "ticker_completion_sha256": hashlib.sha256(
            _json_bytes(completed)
        ).hexdigest(),
    }
    _atomic_write(batch_dir / "batch-registry.json", _json_bytes(registry))
    return registry


def acquire_batch(
    *,
    client: TiingoMarketPriceClient,
    plan: dict[str, Any],
    plan_sha256: str,
    batch_number: int,
    start_date: date,
    end_date: date,
    output_root: Path,
    max_symbols: Optional[int] = None,
    request_delay_seconds: float = 0.0,
) -> dict[str, Any]:
    batches = {
        int(row["batch_number"]): row for row in plan["safe_acquisition_batches"]
    }
    if batch_number not in batches:
        raise ValueError(f"unknown acquisition batch: {batch_number}")
    symbols = list(batches[batch_number]["symbols"])
    if len(symbols) != batches[batch_number]["symbol_count"]:
        raise ValueError("acquisition batch symbol count does not reproduce")
    if max_symbols is not None:
        if max_symbols < 1:
            raise ValueError("max_symbols must be positive")
        symbols = symbols[:max_symbols]
    batch_dir = output_root / f"batch-{batch_number:03d}"
    completed = []
    downloaded = 0
    reused = 0
    registry = _write_registry(
        batch_dir,
        batch_number=batch_number,
        plan_sha256=plan_sha256,
        start_date=start_date,
        end_date=end_date,
        requested_symbol_count=len(symbols),
        completed=completed,
        downloaded=downloaded,
        reused=reused,
    )
    for position, ticker in enumerate(symbols, start=1):
        ticker_dir = batch_dir / ticker
        completion_path = ticker_dir / "complete.json"
        prior = _completed_record(
            completion_path,
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
        )
        if prior is not None:
            completed.append(prior)
            reused += 1
            print(
                f"batch={batch_number} symbol={position}/{len(symbols)} "
                f"ticker={ticker} status=reused",
                flush=True,
            )
            registry = _write_registry(
                batch_dir,
                batch_number=batch_number,
                plan_sha256=plan_sha256,
                start_date=start_date,
                end_date=end_date,
                requested_symbol_count=len(symbols),
                completed=completed,
                downloaded=downloaded,
                reused=reused,
            )
            continue
        download = client.download(
            ticker,
            start_date=start_date,
            end_date=end_date,
        )
        page_records = []
        for page_number, page in enumerate(download.pages, start=1):
            relative_path = f"page-{page_number:03d}-{page.source_hash[:16]}.json"
            raw_path = ticker_dir / relative_path
            if raw_path.exists() and raw_path.read_bytes() != page.body:
                raise ValueError(f"frozen path contains different bytes for {ticker}")
            _atomic_write(raw_path, page.body)
            page_records.append(
                {
                    "page_number": page_number,
                    "path": relative_path,
                    "retrieved_at": page.retrieved_at.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "row_count": len(page.prices),
                    "sha256": page.source_hash,
                    "source_url": page.source_url,
                }
            )
        record = {
            "schema_version": "free-pit-tiingo-ticker-download-v1",
            "status": "complete",
            "publication_prohibited": True,
            "ticker": ticker,
            "batch_number": batch_number,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "price_row_count": len(download.prices),
            "first_price_date": download.prices[0].date.isoformat(),
            "last_price_date": download.prices[-1].date.isoformat(),
            "acquisition_plan_sha256": plan_sha256,
            "pages": page_records,
        }
        _atomic_write(completion_path, _json_bytes(record))
        completed.append(record)
        downloaded += 1
        print(
            f"batch={batch_number} symbol={position}/{len(symbols)} "
            f"ticker={ticker} status=downloaded rows={len(download.prices)}",
            flush=True,
        )
        registry = _write_registry(
            batch_dir,
            batch_number=batch_number,
            plan_sha256=plan_sha256,
            start_date=start_date,
            end_date=end_date,
            requested_symbol_count=len(symbols),
            completed=completed,
            downloaded=downloaded,
            reused=reused,
        )
        if request_delay_seconds and position < len(symbols):
            time.sleep(request_delay_seconds)
    return registry


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze one resumable free-tier Tiingo acquisition batch."
    )
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-hash", required=True)
    parser.add_argument("--batch-number", type=int, required=True)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2013, 1, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date.today())
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--max-symbols", type=int)
    parser.add_argument("--request-delay-seconds", type=float, default=0.0)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.start_date > args.end_date:
            raise ValueError("start date is after end date")
        plan = _load_plan(args.plan, expected_hash=args.expected_plan_hash)
        registry = acquire_batch(
            client=TiingoMarketPriceClient.from_env(),
            plan=plan,
            plan_sha256=args.expected_plan_hash.lower(),
            batch_number=args.batch_number,
            start_date=args.start_date,
            end_date=args.end_date,
            output_root=args.output_root,
            max_symbols=args.max_symbols,
            request_delay_seconds=args.request_delay_seconds,
        )
    except (KeyError, OSError, MarketPriceError, RuntimeError, ValueError) as exc:
        print(f"free Tiingo acquisition failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"batch={registry['batch_number']} "
        f"complete={registry['complete_symbol_count']}/"
        f"{registry['requested_symbol_count']} "
        f"registry_sha256={hashlib.sha256(_json_bytes(registry)).hexdigest()}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
