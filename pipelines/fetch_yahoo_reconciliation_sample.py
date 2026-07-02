"""Freeze a Yahoo Chart API export for WP6.4 reconciliation only.

Yahoo's chart quote OHLC fields are split-adjusted, while ``adjclose`` also
reflects distributions. They are therefore exported only in the adjusted
columns. Raw OHLCV columns remain empty so the audit cannot accidentally treat
unlike price bases as comparable.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional, Sequence
from urllib.parse import quote, urlencode

try:
    import _bootstrap  # noqa: F401
    from _common import DEFAULT_RAW_DIR, fetch_bytes, sha256_bytes, write_raw_payload
except ModuleNotFoundError:  # Imported through the pipelines package.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        DEFAULT_RAW_DIR,
        fetch_bytes,
        sha256_bytes,
        write_raw_payload,
    )

from quantfore_research.validation.price_reconciliation import (
    INDEPENDENT_FIELDS,
    deterministic_sample,
)


SOURCE = "Yahoo Finance Chart API"
LICENSE_TAG = "public_web_reconciliation_only_unverified"
DEFAULT_OUTPUT = Path(
    "data/raw/reconciliation/yahoo-finance-chart/us-equity-trial-v0.csv"
)


def _utc_iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _unix_seconds(value: datetime) -> int:
    return int(value.astimezone(timezone.utc).timestamp())


def _decimal(value: object, *, ticker: str, field: str, day: str) -> str:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{ticker} {day}: Yahoo field {field} is missing")
    try:
        return str(Decimal(str(value)))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(
            f"{ticker} {day}: Yahoo field {field} is not numeric"
        ) from exc


def _chart_url(ticker: str, start: datetime, end: datetime) -> str:
    query = urlencode(
        {
            "period1": _unix_seconds(start),
            "period2": _unix_seconds(end),
            "interval": "1d",
            "events": "div,splits",
            "includeAdjustedClose": "true",
        }
    )
    return (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(ticker)}?{query}"
    )


def _parse_chart(
    payload: bytes,
    *,
    ticker: str,
    source_url: str,
    retrieved_at: datetime,
) -> dict[str, dict[str, str]]:
    try:
        document = json.loads(payload.decode("utf-8"))
        chart = document["chart"]
        if chart.get("error") is not None:
            raise ValueError(f"Yahoo returned an error: {chart['error']}")
        result = chart["result"][0]
        timestamps = result["timestamp"]
        quote_rows = result["indicators"]["quote"][0]
        adjusted_closes = result["indicators"]["adjclose"][0]["adjclose"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"{ticker}: invalid Yahoo Chart API response") from exc
    if len(timestamps) != len(adjusted_closes):
        raise ValueError(f"{ticker}: Yahoo timestamps and adjusted closes differ")
    rows = {}
    for index, timestamp in enumerate(timestamps):
        day = datetime.fromtimestamp(timestamp, tz=timezone.utc).date().isoformat()
        rows[day] = {
            "ticker": ticker,
            "date": day,
            "open": "",
            "high": "",
            "low": "",
            "close": "",
            "volume": "",
            "adj_open": _decimal(
                quote_rows["open"][index], ticker=ticker, field="open", day=day
            ),
            "adj_high": _decimal(
                quote_rows["high"][index], ticker=ticker, field="high", day=day
            ),
            "adj_low": _decimal(
                quote_rows["low"][index], ticker=ticker, field="low", day=day
            ),
            "adj_close": _decimal(
                adjusted_closes[index], ticker=ticker, field="adjclose", day=day
            ),
            "adj_volume": _decimal(
                quote_rows["volume"][index],
                ticker=ticker,
                field="volume",
                day=day,
            ),
            "source": SOURCE,
            "source_url": source_url,
            "retrieved_at": _utc_iso(retrieved_at),
            "license_tag": LICENSE_TAG,
        }
    return rows


def fetch_sample(*, raw_dir: Path) -> bytes:
    sample = deterministic_sample()
    dates_by_ticker = {}
    for point in sample:
        dates_by_ticker.setdefault(point.ticker, set()).add(point.date)
    selected_rows = {}
    for ticker, requested_dates in dates_by_ticker.items():
        first = min(requested_dates)
        last = max(requested_dates)
        start = datetime.combine(
            first - timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc
        )
        end = datetime.combine(
            last + timedelta(days=2), datetime.min.time(), tzinfo=timezone.utc
        )
        source_url = _chart_url(ticker, start, end)
        retrieved_at = datetime.now(timezone.utc)
        payload = fetch_bytes(source_url)
        source_hash = sha256_bytes(payload)
        storage_uri = (
            "raw/reconciliation/yahoo-finance-chart/"
            f"{ticker}_{first}_{last}_{source_hash}.json"
        )
        write_raw_payload(raw_dir, storage_uri, payload)
        parsed = _parse_chart(
            payload,
            ticker=ticker,
            source_url=source_url,
            retrieved_at=retrieved_at,
        )
        for day in requested_dates:
            key = (ticker, day.isoformat())
            if day.isoformat() not in parsed:
                raise ValueError(f"{ticker}: Yahoo response omitted {day}")
            selected_rows[key] = parsed[day.isoformat()]

    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=INDEPENDENT_FIELDS, lineterminator="\n")
    writer.writeheader()
    for point in sample:
        writer.writerow(selected_rows[(point.ticker, point.date.isoformat())])
    return output.getvalue().encode("utf-8")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze the deterministic Yahoo reconciliation sample."
    )
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    payload = fetch_sample(raw_dir=args.raw_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_bytes(payload)
    temp.replace(args.output)
    print(
        f"Yahoo reconciliation export rows={len(payload.splitlines()) - 1} "
        f"sha256={sha256_bytes(payload)} output={args.output}"
    )
    print(
        "Raw OHLCV intentionally blank: Yahoo quote OHLCV are exported only "
        "as adjusted fields for reconciliation review."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Yahoo reconciliation fetch failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
