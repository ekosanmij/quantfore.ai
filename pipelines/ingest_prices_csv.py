"""Ingest historical daily prices from a CSV file.

Expected columns:
    ticker,date,open,high,low,close,adj_close,volume

Example:
    python pipelines/ingest_prices_csv.py data/sample/msft_prices.csv
"""

from __future__ import annotations

import argparse
import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Sequence

import _bootstrap  # noqa: F401
from _common import (
    DEFAULT_RAW_DIR,
    get_or_create_security,
    open_research_database,
    parse_date,
    sha256_bytes,
    timestamp_slug,
    utc_now,
    write_raw_payload,
)

from quantfore_research.db import session_scope
from quantfore_research.models import Price
from quantfore_research.snapshots import record_source_snapshot


REQUIRED_COLUMNS = {"ticker", "date", "adj_close"}
EXPECTED_COLUMNS = [
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
]


@dataclass(frozen=True)
class PriceCsvRow:
    ticker: str
    date: date
    open: Optional[Decimal]
    high: Optional[Decimal]
    low: Optional[Decimal]
    close: Optional[Decimal]
    adj_close: Decimal
    volume: Optional[int]


def _required_text(row: dict[str, str], column: str, row_number: int) -> str:
    value = (row.get(column) or "").strip()
    if not value:
        raise ValueError(f"row {row_number}: {column} is required")
    return value


def _optional_decimal(value: Optional[str], column: str, row_number: int) -> Optional[Decimal]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"row {row_number}: {column} must be numeric") from exc


def _required_decimal(value: str, column: str, row_number: int) -> Decimal:
    cleaned = value.strip()
    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise ValueError(f"row {row_number}: {column} must be numeric") from exc


def _optional_int(value: Optional[str], column: str, row_number: int) -> Optional[int]:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError as exc:
        raise ValueError(f"row {row_number}: {column} must be an integer") from exc


def parse_price_csv(payload: bytes) -> list[PriceCsvRow]:
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))

    if not reader.fieldnames:
        raise ValueError("CSV must include a header row")

    missing_columns = REQUIRED_COLUMNS.difference(reader.fieldnames)
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"CSV missing required columns: {missing}")

    rows = []
    for row_number, row in enumerate(reader, start=2):
        ticker = _required_text(row, "ticker", row_number).upper()
        raw_date = _required_text(row, "date", row_number)
        raw_adj_close = _required_text(row, "adj_close", row_number)
        parsed_date = parse_date(raw_date)
        if parsed_date is None:
            raise ValueError(f"row {row_number}: date is required")

        rows.append(
            PriceCsvRow(
                ticker=ticker,
                date=parsed_date,
                open=_optional_decimal(row.get("open"), "open", row_number),
                high=_optional_decimal(row.get("high"), "high", row_number),
                low=_optional_decimal(row.get("low"), "low", row_number),
                close=_optional_decimal(row.get("close"), "close", row_number),
                adj_close=_required_decimal(raw_adj_close, "adj_close", row_number),
                volume=_optional_int(row.get("volume"), "volume", row_number),
            )
        )

    if not rows:
        raise ValueError("CSV must include at least one price row")

    return rows


def dataset_name(csv_path: Path, rows: list[PriceCsvRow]) -> str:
    tickers = sorted({row.ticker for row in rows})
    if len(tickers) == 1:
        return f"prices_csv_{tickers[0]}"
    return f"prices_csv_{csv_path.stem}"


def storage_uri_for(csv_path: Path, retrieved_at) -> str:
    safe_name = csv_path.name.replace("/", "-")
    return f"raw/prices/csv/{csv_path.stem}/{timestamp_slug(retrieved_at)}_{safe_name}"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest historical prices from CSV.")
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--vendor", default="csv")
    parser.add_argument("--license-tag", default="internal_sample")
    parser.add_argument("--exchange", help="Optional exchange to use for newly created securities.")
    parser.add_argument("--sector", help="Optional sector to use for newly created securities.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    csv_path = args.csv_path
    payload = csv_path.read_bytes()
    rows = parse_price_csv(payload)

    retrieved_at = utc_now()
    source_hash = sha256_bytes(payload)
    storage_uri = storage_uri_for(csv_path, retrieved_at)
    write_raw_payload(args.raw_dir, storage_uri, payload)

    session_factory = open_research_database(args.database_url)
    with session_scope(session_factory) as session:
        snapshot = record_source_snapshot(
            session,
            vendor=args.vendor,
            dataset=dataset_name(csv_path, rows),
            retrieved_at=retrieved_at,
            license_tag=args.license_tag,
            source_hash=source_hash,
            storage_uri=storage_uri,
        )

        security_ids: dict[str, str] = {}
        for row in rows:
            if row.ticker not in security_ids:
                security = get_or_create_security(
                    session,
                    ticker=row.ticker,
                    name=row.ticker,
                    exchange=args.exchange,
                    sector=args.sector,
                )
                security_ids[row.ticker] = security.security_id

            session.add(
                Price(
                    security_id=security_ids[row.ticker],
                    date=row.date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    adj_close=row.adj_close,
                    volume=row.volume,
                    source_snapshot_id=snapshot.snapshot_id,
                )
            )

    tickers = ",".join(sorted(security_ids))
    print(
        f"ingested {len(rows)} price rows "
        f"tickers={tickers} snapshot_id={snapshot.snapshot_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
