"""Load a deterministic sample daily price row into source_snapshots and prices.

This is intentionally a sample loader, not proof-grade market data ingestion.

Example:
    python pipelines/ingest_prices_sample.py MSFT
"""

from __future__ import annotations

import argparse
import csv
import io
from decimal import Decimal
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


def build_sample_csv(args: argparse.Namespace) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["ticker", "date", "open", "high", "low", "close", "adj_close", "volume"],
    )
    writer.writeheader()
    writer.writerow(
        {
            "ticker": args.ticker.upper(),
            "date": args.date,
            "open": args.open,
            "high": args.high,
            "low": args.low,
            "close": args.close,
            "adj_close": args.adj_close,
            "volume": args.volume,
        }
    )
    return buffer.getvalue().encode("utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest one sample daily price row.")
    parser.add_argument("ticker", nargs="?", default="MSFT")
    parser.add_argument("--name", default="Microsoft")
    parser.add_argument("--exchange", default="NASDAQ")
    parser.add_argument("--sector", default="Technology")
    parser.add_argument("--cik", default="0000789019")
    parser.add_argument("--date", default="2026-06-24")
    parser.add_argument("--open", default="490.00")
    parser.add_argument("--high", default="496.00")
    parser.add_argument("--low", default="488.50")
    parser.add_argument("--close", default="495.00")
    parser.add_argument("--adj-close", default="495.00")
    parser.add_argument("--volume", default="21000000")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    ticker = args.ticker.upper().strip()
    retrieved_at = utc_now()
    payload = build_sample_csv(args)
    source_hash = sha256_bytes(payload)
    storage_uri = f"raw/sample/prices/{ticker}/{timestamp_slug(retrieved_at)}.csv"
    write_raw_payload(args.raw_dir, storage_uri, payload)

    session_factory = open_research_database(args.database_url)
    with session_scope(session_factory) as session:
        security = get_or_create_security(
            session,
            ticker=ticker,
            name=args.name,
            cik=args.cik,
            exchange=args.exchange,
            sector=args.sector,
        )
        snapshot = record_source_snapshot(
            session,
            vendor="sample",
            dataset=f"prices_{ticker}",
            retrieved_at=retrieved_at,
            license_tag="internal_sample",
            source_hash=source_hash,
            storage_uri=storage_uri,
        )
        session.add(
            Price(
                security_id=security.security_id,
                date=parse_date(args.date),
                open=Decimal(args.open),
                high=Decimal(args.high),
                low=Decimal(args.low),
                close=Decimal(args.close),
                adj_close=Decimal(args.adj_close),
                volume=int(args.volume),
                source_snapshot_id=snapshot.snapshot_id,
            )
        )

    print(
        f"ingested sample price ticker={ticker} "
        f"date={args.date} snapshot_id={snapshot.snapshot_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
