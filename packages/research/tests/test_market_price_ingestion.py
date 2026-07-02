import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from quantfore_research.db import create_schema
from quantfore_research.ingest.market_prices import (
    IncompleteDownloadError,
    MissingCredentialsError,
    RawPage,
    TickerDownload,
    TiingoMarketPriceClient,
    VendorResponseError,
    load_api_key,
    parse_tiingo_page,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "pipelines" / "ingest_market_prices.py"


class FakeResponse:
    def __init__(self, body: bytes, *, status: int = 200, headers=None):
        self.status = status
        self.headers = headers or {}
        self._body = body
        self.closed = False

    def read(self):
        return self._body

    def close(self):
        self.closed = True


class ResponseQueue:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, *, timeout):
        self.requests.append((request, timeout))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def price_payload(day: str, *, close: float = 103.0) -> bytes:
    return json.dumps(
        [
            {
                "date": f"{day}T00:00:00.000Z",
                "open": 100.0,
                "high": 105.0,
                "low": 99.0,
                "close": close,
                "volume": 1000000,
                "adjOpen": 50.0,
                "adjHigh": 52.5,
                "adjLow": 49.5,
                "adjClose": close / 2,
                "adjVolume": 2000000.0,
                "divCash": 0.0,
                "splitFactor": 1.0,
            }
        ]
    ).encode("utf-8")


def fixed_clock():
    return datetime(2026, 7, 1, 12, 30, tzinfo=timezone.utc)


def test_missing_credentials_has_a_clear_environment_error():
    with pytest.raises(MissingCredentialsError, match="set TIINGO_API_KEY"):
        load_api_key({})

    assert load_api_key({"TIINGO_API_KEY": " test-token "}) == "test-token"


def test_pipeline_exits_before_creating_outputs_when_credentials_are_missing(
    tmp_path,
):
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    env = os.environ.copy()
    env.pop("TIINGO_API_KEY", None)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            str(raw_dir),
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "set TIINGO_API_KEY" in result.stderr
    assert not db_path.exists()
    assert not raw_dir.exists()


def test_tiingo_response_maps_raw_and_adjusted_ohlcv_to_canonical_fields():
    row = parse_tiingo_page(price_payload("2025-01-02"))[0]

    assert row.date == date(2025, 1, 2)
    assert str(row.open) == "100.0"
    assert str(row.close) == "103.0"
    assert row.volume == 1000000
    assert str(row.adj_open) == "50.0"
    assert str(row.adj_high) == "52.5"
    assert str(row.adj_low) == "49.5"
    assert str(row.adj_close) == "51.5"
    assert str(row.adj_volume) == "2000000.0"


def test_tiingo_response_rejects_missing_adjusted_fields():
    document = json.loads(price_payload("2025-01-02"))
    del document[0]["adjClose"]

    with pytest.raises(VendorResponseError, match="adjClose must be numeric"):
        parse_tiingo_page(json.dumps(document).encode("utf-8"))


def test_client_follows_pagination_without_putting_token_in_source_urls():
    first_url = (
        "https://api.tiingo.com/tiingo/daily/MSFT/prices?"
        "startDate=2025-01-02&endDate=2025-01-03&resampleFreq=daily&format=json"
    )
    second_url = first_url + "&page=2"
    queue = ResponseQueue(
        [
            FakeResponse(
                price_payload("2025-01-02"),
                headers={"Link": f'<{second_url}>; rel="next"'},
            ),
            FakeResponse(price_payload("2025-01-03", close=104.0)),
        ]
    )
    client = TiingoMarketPriceClient(
        "super-secret-token", opener=queue, sleep=lambda _: None, clock=fixed_clock
    )

    download = client.download(
        "MSFT", start_date=date(2025, 1, 2), end_date=date(2025, 1, 3)
    )

    assert len(download.pages) == 2
    assert [row.date for row in download.prices] == [
        date(2025, 1, 2),
        date(2025, 1, 3),
    ]
    assert all("super-secret-token" not in page.source_url for page in download.pages)
    assert queue.requests[0][0].get_header("Authorization") == (
        "Token super-secret-token"
    )


def test_client_retries_rate_limits_using_retry_after():
    sleeps = []
    queue = ResponseQueue(
        [
            FakeResponse(b"rate limited", status=429, headers={"Retry-After": "2"}),
            FakeResponse(price_payload("2025-01-02")),
        ]
    )
    client = TiingoMarketPriceClient(
        "token",
        opener=queue,
        sleep=sleeps.append,
        clock=fixed_clock,
        max_retries=2,
    )

    download = client.download(
        "MSFT", start_date=date(2025, 1, 2), end_date=date(2025, 1, 2)
    )

    assert len(download.prices) == 1
    assert sleeps == [2.0]
    assert len(queue.requests) == 2


def test_failed_later_page_rejects_the_partial_download():
    second_url = "https://api.tiingo.com/tiingo/daily/MSFT/prices?page=2"
    queue = ResponseQueue(
        [
            FakeResponse(
                price_payload("2025-01-02"),
                headers={"Link": f'<{second_url}>; rel="next"'},
            ),
            FakeResponse(b"unavailable", status=503),
        ]
    )
    client = TiingoMarketPriceClient(
        "token",
        opener=queue,
        sleep=lambda _: None,
        clock=fixed_clock,
        max_retries=0,
    )

    with pytest.raises(IncompleteDownloadError, match="after 1 attempts"):
        client.download(
            "MSFT", start_date=date(2025, 1, 2), end_date=date(2025, 1, 3)
        )


def test_client_rejects_pagination_urls_that_could_leak_credentials():
    leaked_url = (
        "https://api.tiingo.com/tiingo/daily/MSFT/prices?"
        "page=2&token=secret-token"
    )
    queue = ResponseQueue(
        [
            FakeResponse(
                price_payload("2025-01-02"),
                headers={"Link": f'<{leaked_url}>; rel="next"'},
            )
        ]
    )
    client = TiingoMarketPriceClient(
        "secret-token", opener=queue, sleep=lambda _: None, clock=fixed_clock
    )

    with pytest.raises(VendorResponseError, match="containing credentials"):
        client.download(
            "MSFT", start_date=date(2025, 1, 2), end_date=date(2025, 1, 3)
        )


def test_exact_duplicate_dates_are_deduplicated_but_conflicts_are_rejected():
    second_url = "https://api.tiingo.com/tiingo/daily/MSFT/prices?page=2"
    identical_queue = ResponseQueue(
        [
            FakeResponse(
                price_payload("2025-01-02"),
                headers={"Link": f'<{second_url}>; rel="next"'},
            ),
            FakeResponse(price_payload("2025-01-02")),
        ]
    )
    identical_client = TiingoMarketPriceClient(
        "token", opener=identical_queue, sleep=lambda _: None, clock=fixed_clock
    )

    download = identical_client.download(
        "MSFT", start_date=date(2025, 1, 2), end_date=date(2025, 1, 2)
    )
    assert len(download.pages) == 2
    assert len(download.prices) == 1

    conflict_queue = ResponseQueue(
        [
            FakeResponse(
                price_payload("2025-01-02"),
                headers={"Link": f'<{second_url}>; rel="next"'},
            ),
            FakeResponse(price_payload("2025-01-02", close=104.0)),
        ]
    )
    conflict_client = TiingoMarketPriceClient(
        "token", opener=conflict_queue, sleep=lambda _: None, clock=fixed_clock
    )
    with pytest.raises(VendorResponseError, match="conflicting duplicate date"):
        conflict_client.download(
            "MSFT", start_date=date(2025, 1, 2), end_date=date(2025, 1, 2)
        )


def test_persistence_freezes_exact_payload_hash_and_is_idempotent(tmp_path):
    from pipelines.ingest_market_prices import (
        UniverseSecurity,
        persist_downloads,
    )

    body = price_payload("2025-01-02")
    prices = parse_tiingo_page(body)
    page = RawPage(
        source_url=(
            "https://api.tiingo.com/tiingo/daily/MSFT/prices?"
            "startDate=2025-01-02&endDate=2025-01-02"
        ),
        retrieved_at=fixed_clock(),
        body=body,
        headers=(),
        prices=prices,
    )
    download = TickerDownload(
        ticker="MSFT",
        pages=(page,),
        prices=prices,
        price_page_numbers=(1,),
    )
    universe = [
        UniverseSecurity(
            ticker="MSFT",
            company_name="Microsoft Corporation",
            cik="0000789019",
            exchange="NASDAQ",
            sector="Information Technology",
            active_from=date(2025, 1, 2),
            active_to=date(2025, 1, 2),
            is_benchmark=False,
            selection_reason="test",
        )
    ]
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    database_url = f"sqlite+pysqlite:///{db_path}"

    first = persist_downloads(
        [download],
        universe,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 2),
        database_url=database_url,
        raw_dir=raw_dir,
    )
    second = persist_downloads(
        [download],
        universe,
        start_date=date(2025, 1, 2),
        end_date=date(2025, 1, 2),
        database_url=database_url,
        raw_dir=raw_dir,
    )

    assert first.inserted_rows == 1
    assert first.created_snapshots == 1
    assert second.inserted_rows == 0
    assert second.skipped_rows == 1
    assert second.reused_snapshots == 1

    engine = create_engine(database_url)
    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "select (select count(*) from prices), "
                "(select count(*) from source_snapshots)"
            )
        ).one()
        saved = connection.execute(
            text(
                "select p.adj_open, p.adj_high, p.adj_low, p.adj_close, "
                "p.adj_volume, ss.storage_uri, ss.hash, ss.vendor, ss.license_tag "
                "from prices p join source_snapshots ss "
                "on ss.snapshot_id = p.source_snapshot_id"
            )
        ).one()

    assert tuple(counts) == (1, 1)
    assert tuple(saved[:5]) == (50, 52.5, 49.5, 51.5, 2000000)
    assert saved.vendor == "Tiingo"
    assert saved.license_tag == "tiingo_internal_research_trial_v0"
    frozen_path = raw_dir.parent / saved.storage_uri
    metadata_path = frozen_path.with_name(
        frozen_path.name.replace(".json", ".metadata.json")
    )
    assert frozen_path.read_bytes() == body
    assert saved.hash == hashlib.sha256(body).hexdigest()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["source_url"] == page.source_url
    assert metadata["retrieved_at"] == "2026-07-01T12:30:00Z"
    assert metadata["sha256"] == saved.hash


def test_existing_sqlite_price_table_gets_additive_adjusted_ohlcv_upgrade(
    tmp_path,
):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "create table prices ("
                "price_id varchar(36) primary key, security_id varchar(36), "
                "date date, open numeric(18, 6), high numeric(18, 6), "
                "low numeric(18, 6), close numeric(18, 6), "
                "adj_close numeric(18, 6), volume bigint, "
                "source_snapshot_id varchar(36), created_at datetime, "
                "updated_at datetime)"
            )
        )

    create_schema(engine)

    with engine.connect() as connection:
        columns = {
            row.name
            for row in connection.execute(text("pragma table_info(prices)"))
        }
    assert {"adj_open", "adj_high", "adj_low", "adj_volume"}.issubset(columns)
