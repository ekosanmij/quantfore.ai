"""Ingest the frozen US equity trial panel from Tiingo.

Example:
    TIINGO_API_KEY=... python pipelines/ingest_market_prices.py \
      --universe-file config/universes/us-equity-trial-v0.csv \
      --start-date 2020-01-01 --end-date 2025-12-31
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import (
        DEFAULT_RAW_DIR,
        get_or_create_security,
        open_research_database,
        sha256_bytes,
        timestamp_slug,
        write_raw_payload,
    )
except ModuleNotFoundError:  # Imported as pipelines.ingest_market_prices in tests.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        DEFAULT_RAW_DIR,
        get_or_create_security,
        open_research_database,
        sha256_bytes,
        timestamp_slug,
        write_raw_payload,
    )

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.db import session_scope
from quantfore_research.ingest.market_prices import (
    TIINGO_API_KEY_ENV,
    TIINGO_VENDOR,
    CanonicalPrice,
    MarketPriceError,
    RawPage,
    TickerDownload,
    TiingoMarketPriceClient,
    load_api_key,
)
from quantfore_research.models import Price, Security, SourceSnapshot
from quantfore_research.snapshots import record_source_snapshot


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_UNIVERSE_FILE = (
    REPO_ROOT / "config" / "universes" / "us-equity-trial-v0.csv"
)
DEFAULT_START_DATE = date(2020, 1, 1)
DEFAULT_END_DATE = date(2025, 12, 31)
DEFAULT_LICENSE_TAG = "tiingo_internal_research_trial_v0"
UNIVERSE_FIELDS = (
    "ticker",
    "company_name",
    "cik",
    "exchange",
    "sector",
    "active_from",
    "active_to",
    "is_benchmark",
    "selection_reason",
)


@dataclass(frozen=True)
class UniverseSecurity:
    ticker: str
    company_name: str
    cik: str
    exchange: str
    sector: str
    active_from: date
    active_to: date
    is_benchmark: bool
    selection_reason: str


@dataclass(frozen=True)
class IngestionResult:
    tickers: int
    pages: int
    received_rows: int
    inserted_rows: int
    skipped_rows: int
    created_snapshots: int
    reused_snapshots: int


def _required(row: dict[str, str], field: str, row_number: int) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise ValueError(f"universe row {row_number}: {field} is required")
    return value


def _universe_date(row: dict[str, str], field: str, row_number: int) -> date:
    value = _required(row, field, row_number)
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"universe row {row_number}: {field} must be an ISO date"
        ) from exc


def read_universe(path: Path) -> list[UniverseSecurity]:
    """Read and strictly validate the frozen universe contract."""

    with path.open(encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if tuple(reader.fieldnames or ()) != UNIVERSE_FIELDS:
            raise ValueError(
                "universe fields must exactly match: " + ",".join(UNIVERSE_FIELDS)
            )
        rows = []
        seen_tickers = set()
        for row_number, row in enumerate(reader, start=2):
            ticker = _required(row, "ticker", row_number).upper()
            if ticker in seen_tickers:
                raise ValueError(f"universe contains duplicate ticker {ticker}")
            seen_tickers.add(ticker)
            benchmark_value = _required(
                row, "is_benchmark", row_number
            ).lower()
            if benchmark_value not in {"true", "false"}:
                raise ValueError(
                    f"universe row {row_number}: is_benchmark must be true or false"
                )
            active_from = _universe_date(row, "active_from", row_number)
            active_to = _universe_date(row, "active_to", row_number)
            if active_from > active_to:
                raise ValueError(
                    f"universe row {row_number}: active_from is after active_to"
                )
            rows.append(
                UniverseSecurity(
                    ticker=ticker,
                    company_name=_required(row, "company_name", row_number),
                    cik=_required(row, "cik", row_number),
                    exchange=_required(row, "exchange", row_number),
                    sector=_required(row, "sector", row_number),
                    active_from=active_from,
                    active_to=active_to,
                    is_benchmark=benchmark_value == "true",
                    selection_reason=_required(
                        row, "selection_reason", row_number
                    ),
                )
            )

    if not rows:
        raise ValueError("universe must contain at least one security")
    benchmarks = [row.ticker for row in rows if row.is_benchmark]
    if benchmarks != ["SPY"]:
        raise ValueError("universe must contain SPY as its only benchmark")
    return rows


def download_panel(
    client: TiingoMarketPriceClient,
    universe: Sequence[UniverseSecurity],
    *,
    start_date: date,
    end_date: date,
) -> list[TickerDownload]:
    """Download the entire panel before allowing any persistence."""

    downloads = []
    for security in universe:
        effective_start = max(start_date, security.active_from)
        effective_end = min(end_date, security.active_to)
        if effective_start > effective_end:
            raise ValueError(
                f"{security.ticker}: requested range does not overlap active range"
            )
        downloads.append(
            client.download(
                security.ticker,
                start_date=effective_start,
                end_date=effective_end,
            )
        )
    return downloads


def _utc_iso(value: datetime) -> str:
    normalized = value
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _dataset_name(
    ticker: str,
    start_date: date,
    end_date: date,
    page_number: int,
) -> str:
    return (
        f"tiingo_eod_prices_{ticker}_{start_date.isoformat()}_"
        f"{end_date.isoformat()}_page_{page_number:03d}"
    )


def _storage_uri(
    ticker: str,
    start_date: date,
    end_date: date,
    page_number: int,
    page: RawPage,
) -> str:
    return (
        f"raw/prices/tiingo/{ticker}/"
        f"{start_date.isoformat()}_{end_date.isoformat()}/"
        f"{timestamp_slug(page.retrieved_at)}_page-{page_number:03d}_"
        f"{page.source_hash[:16]}.json"
    )


def _metadata_uri(storage_uri: str) -> str:
    if not storage_uri.endswith(".json"):
        raise ValueError("raw Tiingo storage URI must end with .json")
    return storage_uri[:-5] + ".metadata.json"


def freeze_raw_page(
    *,
    raw_dir: Path,
    storage_uri: str,
    dataset: str,
    license_tag: str,
    page: RawPage,
) -> None:
    """Freeze the exact body plus a non-secret provenance sidecar."""

    if sha256_bytes(page.body) != page.source_hash:
        raise ValueError("raw response hash changed before persistence")
    write_raw_payload(raw_dir, storage_uri, page.body)
    metadata = {
        "dataset": dataset,
        "license_tag": license_tag,
        "retrieved_at": _utc_iso(page.retrieved_at),
        "sha256": page.source_hash,
        "source_url": page.source_url,
        "storage_uri": storage_uri,
        "vendor": TIINGO_VENDOR,
    }
    metadata_payload = (
        json.dumps(metadata, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    write_raw_payload(raw_dir, _metadata_uri(storage_uri), metadata_payload)


def _existing_snapshot(
    session: Session,
    *,
    dataset: str,
    source_hash: str,
) -> Optional[SourceSnapshot]:
    return session.scalars(
        select(SourceSnapshot)
        .where(
            SourceSnapshot.vendor == TIINGO_VENDOR,
            SourceSnapshot.dataset == dataset,
            SourceSnapshot.source_hash == source_hash,
        )
        .order_by(SourceSnapshot.retrieved_at)
    ).first()


def _canonical_security(
    session: Session, definition: UniverseSecurity
) -> Security:
    security = get_or_create_security(
        session,
        ticker=definition.ticker,
        name=definition.company_name,
        cik=definition.cik,
        exchange=definition.exchange,
        sector=definition.sector,
    )
    security.name = definition.company_name
    security.cik = definition.cik
    security.exchange = definition.exchange
    security.sector = definition.sector
    security.active_from = definition.active_from
    security.active_to = definition.active_to
    session.flush()
    return security


def _new_price(
    *,
    security_id: str,
    snapshot_id: str,
    row: CanonicalPrice,
) -> Price:
    return Price(
        security_id=security_id,
        date=row.date,
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
        adj_open=row.adj_open,
        adj_high=row.adj_high,
        adj_low=row.adj_low,
        adj_close=row.adj_close,
        adj_volume=row.adj_volume,
        source_snapshot_id=snapshot_id,
    )


def persist_downloads(
    downloads: Sequence[TickerDownload],
    universe: Sequence[UniverseSecurity],
    *,
    start_date: date,
    end_date: date,
    database_url: Optional[str],
    raw_dir: Path,
    license_tag: str = DEFAULT_LICENSE_TAG,
) -> IngestionResult:
    """Atomically register a fully downloaded panel and its canonical rows."""

    if not license_tag.strip():
        raise ValueError("license_tag is required")
    definitions = {entry.ticker: entry for entry in universe}
    if len(downloads) != len(universe) or set(definitions) != {
        download.ticker for download in downloads
    }:
        raise ValueError("downloaded tickers do not exactly match the universe")
    for download in downloads:
        if not download.pages or not download.prices:
            raise ValueError(f"{download.ticker}: incomplete empty download")
        if len(download.prices) != len(download.price_page_numbers):
            raise ValueError(
                f"{download.ticker}: price-to-page lineage is incomplete"
            )
        if any(
            page_number < 1 or page_number > len(download.pages)
            for page_number in download.price_page_numbers
        ):
            raise ValueError(f"{download.ticker}: invalid price page lineage")

    session_factory = open_research_database(database_url)
    inserted_rows = 0
    skipped_rows = 0
    created_snapshots = 0
    reused_snapshots = 0

    with session_scope(session_factory) as session:
        for download in downloads:
            definition = definitions[download.ticker]
            security = _canonical_security(session, definition)
            page_snapshots: dict[int, SourceSnapshot] = {}

            for page_number, page in enumerate(download.pages, start=1):
                dataset = _dataset_name(
                    download.ticker, start_date, end_date, page_number
                )
                snapshot = _existing_snapshot(
                    session, dataset=dataset, source_hash=page.source_hash
                )
                if snapshot is None:
                    storage_uri = _storage_uri(
                        download.ticker,
                        start_date,
                        end_date,
                        page_number,
                        page,
                    )
                    freeze_raw_page(
                        raw_dir=raw_dir,
                        storage_uri=storage_uri,
                        dataset=dataset,
                        license_tag=license_tag,
                        page=page,
                    )
                    snapshot = record_source_snapshot(
                        session,
                        vendor=TIINGO_VENDOR,
                        dataset=dataset,
                        retrieved_at=page.retrieved_at,
                        license_tag=license_tag,
                        source_hash=page.source_hash,
                        storage_uri=storage_uri,
                    )
                    created_snapshots += 1
                else:
                    reused_snapshots += 1
                page_snapshots[page_number] = snapshot

            existing_price_keys = set(
                session.execute(
                    select(Price.date, Price.source_snapshot_id).where(
                        Price.security_id == security.security_id,
                        Price.source_snapshot_id.in_(
                            [
                                snapshot.snapshot_id
                                for snapshot in page_snapshots.values()
                            ]
                        ),
                    )
                ).all()
            )
            for row, page_number in zip(
                download.prices, download.price_page_numbers
            ):
                snapshot = page_snapshots[page_number]
                key = (row.date, snapshot.snapshot_id)
                if key in existing_price_keys:
                    skipped_rows += 1
                    continue
                session.add(
                    _new_price(
                        security_id=security.security_id,
                        snapshot_id=snapshot.snapshot_id,
                        row=row,
                    )
                )
                existing_price_keys.add(key)
                inserted_rows += 1

    received_rows = sum(len(download.prices) for download in downloads)
    return IngestionResult(
        tickers=len(downloads),
        pages=sum(len(download.pages) for download in downloads),
        received_rows=received_rows,
        inserted_rows=inserted_rows,
        skipped_rows=skipped_rows,
        created_snapshots=created_snapshots,
        reused_snapshots=reused_snapshots,
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingest the frozen real-market price panel from Tiingo."
    )
    parser.add_argument(
        "--universe-file", type=Path, default=DEFAULT_UNIVERSE_FILE
    )
    parser.add_argument(
        "--start-date", type=date.fromisoformat, default=DEFAULT_START_DATE
    )
    parser.add_argument(
        "--end-date", type=date.fromisoformat, default=DEFAULT_END_DATE
    )
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--license-tag", default=DEFAULT_LICENSE_TAG)
    parser.add_argument("--api-key-env", default=TIINGO_API_KEY_ENV)
    parser.add_argument("--timeout-seconds", type=int, default=30)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--max-pages", type=int, default=100)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.start_date > args.end_date:
        raise ValueError("start-date must not be after end-date")
    api_key = load_api_key(variable_name=args.api_key_env)
    universe = read_universe(args.universe_file)
    client = TiingoMarketPriceClient(
        api_key,
        timeout_seconds=args.timeout_seconds,
        max_retries=args.max_retries,
        max_pages=args.max_pages,
    )
    downloads = download_panel(
        client,
        universe,
        start_date=args.start_date,
        end_date=args.end_date,
    )
    result = persist_downloads(
        downloads,
        universe,
        start_date=args.start_date,
        end_date=args.end_date,
        database_url=args.database_url,
        raw_dir=args.raw_dir,
        license_tag=args.license_tag,
    )
    print(
        f"ingested Tiingo panel tickers={result.tickers} pages={result.pages} "
        f"received_rows={result.received_rows} inserted_rows={result.inserted_rows} "
        f"skipped_rows={result.skipped_rows} "
        f"created_snapshots={result.created_snapshots} "
        f"reused_snapshots={result.reused_snapshots}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (MarketPriceError, ValueError, OSError) as exc:
        print(f"market-price ingestion failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
