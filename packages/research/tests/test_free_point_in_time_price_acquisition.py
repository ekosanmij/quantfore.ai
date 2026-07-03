import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pipelines.acquire_free_point_in_time_prices import acquire_batch
from quantfore_research.ingest.market_prices import (
    CanonicalPrice,
    RawPage,
    TickerDownload,
)


class Client:
    def __init__(self):
        self.calls = []

    def download(self, ticker, *, start_date, end_date):
        self.calls.append(ticker)
        body = json.dumps(
            [{"ticker": ticker, "date": start_date.isoformat()}]
        ).encode()
        price = CanonicalPrice(
            date=start_date,
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=Decimal("1"),
            volume=1,
            adj_open=Decimal("1"),
            adj_high=Decimal("1"),
            adj_low=Decimal("1"),
            adj_close=Decimal("1"),
            adj_volume=Decimal("1"),
        )
        page = RawPage(
            source_url=f"https://example.test/{ticker}",
            retrieved_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
            body=body,
            headers=(),
            prices=(price,),
        )
        return TickerDownload(ticker, (page,), (price,), (1,))


class FailingClient(Client):
    def download(self, ticker, *, start_date, end_date):
        if ticker == "BBB":
            raise RuntimeError("rate limited")
        return super().download(ticker, start_date=start_date, end_date=end_date)


def plan():
    return {
        "safe_acquisition_batches": [
            {"batch_number": 1, "symbol_count": 2, "symbols": ["AAA", "BBB"]}
        ]
    }


def test_batch_download_is_content_addressed_and_resumable(tmp_path):
    client = Client()
    first = acquire_batch(
        client=client,
        plan=plan(),
        plan_sha256="a" * 64,
        batch_number=1,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        output_root=tmp_path,
    )
    second = acquire_batch(
        client=client,
        plan=plan(),
        plan_sha256="a" * 64,
        batch_number=1,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        output_root=tmp_path,
    )

    assert client.calls == ["AAA", "BBB"]
    assert first["downloaded_symbol_count"] == 2
    assert first["status"] == "complete"
    assert second["reused_symbol_count"] == 2
    record = json.loads((tmp_path / "batch-001/AAA/complete.json").read_text())
    page = tmp_path / "batch-001/AAA" / record["pages"][0]["path"]
    assert hashlib.sha256(page.read_bytes()).hexdigest() == record["pages"][0]["sha256"]


def test_batch_rejects_tampered_frozen_page(tmp_path):
    client = Client()
    acquire_batch(
        client=client,
        plan=plan(),
        plan_sha256="a" * 64,
        batch_number=1,
        start_date=date(2020, 1, 1),
        end_date=date(2020, 12, 31),
        output_root=tmp_path,
        max_symbols=1,
    )
    record_path = tmp_path / "batch-001/AAA/complete.json"
    record = json.loads(record_path.read_text())
    page = record_path.parent / record["pages"][0]["path"]
    page.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="does not reproduce"):
        acquire_batch(
            client=client,
            plan=plan(),
            plan_sha256="a" * 64,
            batch_number=1,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 12, 31),
            output_root=tmp_path,
            max_symbols=1,
        )


def test_batch_checkpoints_before_vendor_failure(tmp_path):
    with pytest.raises(RuntimeError, match="rate limited"):
        acquire_batch(
            client=FailingClient(),
            plan=plan(),
            plan_sha256="a" * 64,
            batch_number=1,
            start_date=date(2020, 1, 1),
            end_date=date(2020, 12, 31),
            output_root=tmp_path,
        )

    registry = json.loads(
        (tmp_path / "batch-001/batch-registry.json").read_text()
    )
    assert registry["status"] == "in_progress"
    assert registry["requested_symbol_count"] == 2
    assert registry["complete_symbol_count"] == 1
    assert registry["tickers"] == ["AAA"]
