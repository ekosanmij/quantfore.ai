"""Ingest a FRED macro series into source_snapshots and macro_series.

Example:
    python pipelines/ingest_fred_macro.py FEDFUNDS
"""

from __future__ import annotations

import argparse
import csv
import io
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Sequence

import _bootstrap  # noqa: F401
from _common import (
    DEFAULT_RAW_DIR,
    fetch_bytes,
    open_research_database,
    parse_date,
    sha256_bytes,
    timestamp_slug,
    utc_now,
    write_raw_payload,
)

from quantfore_research.db import session_scope
from quantfore_research.models import MacroSeries
from quantfore_research.snapshots import record_source_snapshot


FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start_date}"


def parse_fred_csv(
    payload: bytes,
    *,
    series_id: str,
    start_date: Optional[str] = None,
    limit: Optional[int] = None,
) -> list[tuple]:
    text = payload.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return []

    date_column = "observation_date" if "observation_date" in reader.fieldnames else "DATE"
    value_column = series_id if series_id in reader.fieldnames else reader.fieldnames[-1]
    min_date = parse_date(start_date)
    observations = []

    for row in reader:
        observation_date = parse_date(row.get(date_column))
        raw_value = (row.get(value_column) or "").strip()
        if observation_date is None or raw_value in {"", "."}:
            continue
        if min_date and observation_date < min_date:
            continue
        try:
            value = Decimal(raw_value)
        except InvalidOperation:
            continue
        observations.append((observation_date, value))
        if limit and len(observations) >= limit:
            break

    return observations


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest one FRED macro series.")
    parser.add_argument("series_id", help="FRED series ID, for example FEDFUNDS or DGS10.")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument(
        "--start-date",
        default="1900-01-01",
        help="YYYY-MM-DD lower bound. Defaults to 1900-01-01.",
    )
    parser.add_argument("--limit", type=int, help="Optional max observations to insert.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    series_id = args.series_id.upper().strip()
    retrieved_at = utc_now()
    url = FRED_CSV_URL.format(series_id=series_id, start_date=args.start_date)
    payload = fetch_bytes(url, user_agent=None)
    source_hash = sha256_bytes(payload)
    storage_uri = f"raw/fred/{series_id}/{timestamp_slug(retrieved_at)}.csv"
    write_raw_payload(args.raw_dir, storage_uri, payload)
    observations = parse_fred_csv(
        payload,
        series_id=series_id,
        start_date=args.start_date,
        limit=args.limit,
    )

    session_factory = open_research_database(args.database_url)
    with session_scope(session_factory) as session:
        snapshot = record_source_snapshot(
            session,
            vendor="FRED",
            dataset=series_id,
            retrieved_at=retrieved_at,
            license_tag="public_source",
            source_hash=source_hash,
            storage_uri=storage_uri,
        )
        for observation_date, value in observations:
            session.add(
                MacroSeries(
                    series_id=series_id,
                    observation_date=observation_date,
                    value=value,
                    source_snapshot_id=snapshot.snapshot_id,
                )
            )

    print(
        f"ingested {len(observations)} FRED observations "
        f"series_id={series_id} snapshot_id={snapshot.snapshot_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
