import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.models import Price, Security
from quantfore_research.snapshots import record_source_snapshot
from quantfore_research.validation.price_quality import (
    PriceObservation,
    PriceQualityConfig,
    audit_price_panel,
    audit_price_series,
    exchange_sessions,
)


def observation(
    ticker: str,
    day: date,
    *,
    raw_close: str = "100",
    adj_close: str = "100",
    open_price: str = "99",
    high: str = "101",
    low: str = "98",
    volume: int = 1000,
    adj_volume: str = "1000",
    snapshot: str = "snapshot-1",
    retrieved_at: datetime = datetime(2025, 7, 8, tzinfo=timezone.utc),
    cik: str = "0000789019",
) -> PriceObservation:
    raw_close_value = Decimal(raw_close)
    adjusted_close_value = Decimal(adj_close)
    adjustment_factor = (
        adjusted_close_value / raw_close_value
        if raw_close_value != 0
        else Decimal("1")
    )
    return PriceObservation(
        ticker=ticker,
        cik=cik,
        date=day,
        open=Decimal(open_price),
        high=Decimal(high),
        low=Decimal(low),
        close=raw_close_value,
        adj_open=Decimal(open_price) * adjustment_factor,
        adj_high=Decimal(high) * adjustment_factor,
        adj_low=Decimal(low) * adjustment_factor,
        adj_close=adjusted_close_value,
        volume=volume,
        adj_volume=Decimal(adj_volume),
        source_snapshot_id=snapshot,
        retrieved_at=retrieved_at,
    )


def test_xnys_calendar_excludes_independence_day_not_just_weekends():
    sessions = exchange_sessions(date(2025, 7, 1), date(2025, 7, 7))

    assert sessions == (
        date(2025, 7, 1),
        date(2025, 7, 2),
        date(2025, 7, 3),
        date(2025, 7, 7),
    )
    assert date(2025, 7, 4) not in sessions


def test_xnys_calendar_honors_ad_hoc_national_days_of_mourning():
    sessions = exchange_sessions(date(2025, 1, 8), date(2025, 1, 10))

    assert sessions == (date(2025, 1, 8), date(2025, 1, 10))
    assert date(2025, 1, 9) not in sessions


def test_audit_reports_duplicates_missing_sessions_and_coverage():
    sessions = exchange_sessions(date(2025, 7, 1), date(2025, 7, 7))
    rows = [
        observation("MSFT", sessions[0]),
        observation("MSFT", sessions[0], snapshot="snapshot-2"),
        observation("MSFT", sessions[1], raw_close="101", adj_close="101"),
        observation("MSFT", sessions[3], raw_close="102", adj_close="102"),
    ]

    audit = audit_price_series(
        rows,
        expected_ticker="MSFT",
        expected_cik="0000789019",
        expected_sessions=sessions,
        config=PriceQualityConfig(minimum_history_sessions=1),
    )

    assert audit.duplicate_dates == (sessions[0],)
    assert audit.missing_sessions == (sessions[2],)
    assert audit.observed_row_count == 4
    assert audit.unique_date_count == 3
    assert audit.coverage_percentage == 75.0
    assert audit.status == "fail"


def test_audit_detects_invalid_prices_volumes_dates_and_identity():
    sessions = exchange_sessions(date(2025, 7, 1), date(2025, 7, 3))
    bad_ohlc = observation(
        "OLDMSFT",
        sessions[0],
        open_price="100",
        high="99",
        low="98",
        raw_close="101",
        adj_close="-1",
        volume=-1,
        adj_volume="-2",
        cik="wrong-cik",
        retrieved_at=datetime(2025, 6, 30, tzinfo=timezone.utc),
    )
    missing_adjusted = observation("MSFT", sessions[1])
    missing_adjusted = PriceObservation(
        **{
            **missing_adjusted.__dict__,
            "adj_close": None,
        }
    )

    audit = audit_price_series(
        [bad_ohlc, missing_adjusted],
        expected_ticker="MSFT",
        expected_cik="0000789019",
        expected_sessions=sessions,
        config=PriceQualityConfig(minimum_history_sessions=1),
    )

    assert audit.non_positive_price_dates == (sessions[0],)
    assert audit.invalid_ohlc_dates == (sessions[0],)
    assert audit.missing_adjusted_close_dates == (sessions[1],)
    assert audit.negative_volume_dates == (sessions[0],)
    assert audit.dates_beyond_retrieval == (sessions[0],)
    assert audit.unexpected_tickers == ("OLDMSFT",)
    assert audit.unexpected_ciks == ("wrong-cik",)
    assert audit.status == "fail"


def test_audit_detects_stale_extreme_and_split_like_movements():
    sessions = exchange_sessions(date(2025, 7, 1), date(2025, 7, 9))
    stale_rows = [
        observation("MSFT", day, raw_close="100", adj_close="100")
        for day in sessions[:5]
    ]
    stale_audit = audit_price_series(
        stale_rows,
        expected_ticker="MSFT",
        expected_cik="0000789019",
        expected_sessions=sessions[:5],
        config=PriceQualityConfig(
            minimum_history_sessions=1,
            stale_run_sessions=5,
        ),
    )

    assert len(stale_audit.stale_runs) == 1
    assert stale_audit.stale_runs[0].sessions == 5
    assert stale_audit.status == "review"

    movement_rows = [
        observation("MSFT", sessions[0], raw_close="100", adj_close="50"),
        observation(
            "MSFT",
            sessions[1],
            raw_close="50",
            adj_close="50",
            open_price="49",
            high="51",
            low="48",
        ),
        observation(
            "MSFT",
            sessions[2],
            raw_close="55",
            adj_close="80",
            open_price="54",
            high="56",
            low="53",
        ),
    ]
    movement_audit = audit_price_series(
        movement_rows,
        expected_ticker="MSFT",
        expected_cik="0000789019",
        expected_sessions=sessions[:3],
        config=PriceQualityConfig(minimum_history_sessions=3),
    )

    assert [item.date for item in movement_audit.split_like_discontinuities] == [
        sessions[1]
    ]
    assert [item.date for item in movement_audit.extreme_returns] == [sessions[2]]
    assert movement_audit.status == "review"


def test_panel_audit_flags_benchmark_misalignment_and_insufficient_history():
    sessions = exchange_sessions(date(2025, 7, 1), date(2025, 7, 7))
    panel = {
        "SPY": [
            observation("SPY", day, cik="0000884394") for day in sessions
        ],
        "MSFT": [
            observation("MSFT", day)
            for day in sessions
            if day != sessions[2]
        ],
    }

    audit = audit_price_panel(
        panel,
        expected_tickers=["MSFT", "SPY"],
        expected_ciks={"MSFT": "0000789019", "SPY": "0000884394"},
        start_date=date(2025, 7, 1),
        end_date=date(2025, 7, 7),
        config=PriceQualityConfig(minimum_history_sessions=4),
    )
    by_ticker = {item.ticker: item for item in audit.securities}

    assert by_ticker["MSFT"].missing_vs_benchmark == (sessions[2],)
    assert by_ticker["MSFT"].insufficient_history is True
    assert by_ticker["SPY"].missing_vs_benchmark == ()
    assert audit.status == "fail"
    assert audit.audit_passed is False


def test_complete_aligned_panel_passes():
    sessions = exchange_sessions(date(2025, 7, 1), date(2025, 7, 7))

    def changing_rows(ticker: str, cik: str):
        rows = []
        for index, day in enumerate(sessions):
            close = 100 + index
            rows.append(
                observation(
                    ticker,
                    day,
                    raw_close=str(close),
                    adj_close=str(close),
                    open_price=str(close - 1),
                    high=str(close + 1),
                    low=str(close - 2),
                    cik=cik,
                )
            )
        return rows

    audit = audit_price_panel(
        {
            "MSFT": changing_rows("MSFT", "0000789019"),
            "SPY": changing_rows("SPY", "0000884394"),
        },
        expected_tickers=["MSFT", "SPY"],
        expected_ciks={"MSFT": "0000789019", "SPY": "0000884394"},
        start_date=date(2025, 7, 1),
        end_date=date(2025, 7, 7),
        config=PriceQualityConfig(minimum_history_sessions=4),
    )

    assert audit.status == "pass"
    assert audit.audit_passed is True
    assert all(item.coverage_percentage == 100.0 for item in audit.securities)


def write_test_universe(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                (
                    "ticker,company_name,cik,exchange,sector,active_from,"
                    "active_to,is_benchmark,selection_reason"
                ),
                (
                    "MSFT,Microsoft Corporation,0000789019,NASDAQ,"
                    "Information Technology,2025-07-01,2025-07-07,false,test"
                ),
                (
                    "SPY,SPDR S&P 500 ETF Trust,0000884394,NYSE,"
                    "Benchmark ETF,2025-07-01,2025-07-07,true,test"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def seed_price_panel(database_url: str) -> None:
    engine = build_engine(database_url=database_url)
    create_schema(engine)
    session_factory = make_session_factory(engine)
    sessions = exchange_sessions(date(2025, 7, 1), date(2025, 7, 7))
    with session_scope(session_factory) as session:
        for ticker, cik, included_dates in (
            ("SPY", "0000884394", sessions),
            ("MSFT", "0000789019", sessions[:3]),
        ):
            security = Security(
                ticker=ticker,
                name=ticker,
                cik=cik,
                exchange="NYSE",
            )
            session.add(security)
            snapshot = record_source_snapshot(
                session,
                vendor="Tiingo",
                dataset=(
                    f"tiingo_eod_prices_{ticker}_"
                    "2025-07-01_2025-07-07_page_001"
                ),
                retrieved_at=datetime(2025, 7, 8, tzinfo=timezone.utc),
                license_tag="tiingo_internal_research_trial_v0",
                source_hash=f"hash-{ticker}",
                storage_uri=f"raw/prices/tiingo/{ticker}/test.json",
            )
            session.flush()
            for index, day in enumerate(included_dates):
                close = Decimal("100") + index
                session.add(
                    Price(
                        security_id=security.security_id,
                        date=day,
                        open=close,
                        high=close + 1,
                        low=close - 1,
                        close=close,
                        adj_open=close,
                        adj_high=close + 1,
                        adj_low=close - 1,
                        adj_close=close,
                        volume=1000,
                        adj_volume=Decimal("1000"),
                        source_snapshot_id=snapshot.snapshot_id,
                    )
                )


def test_audit_pipeline_writes_lineage_and_failed_quality_result(tmp_path):
    from pipelines.audit_price_panel import main

    universe_path = tmp_path / "universe.csv"
    output_path = tmp_path / "audit.json"
    database_url = f"sqlite+pysqlite:///{tmp_path / 'research.db'}"
    write_test_universe(universe_path)
    seed_price_panel(database_url)

    result = main(
        [
            "--universe-file",
            str(universe_path),
            "--database-url",
            database_url,
            "--start-date",
            "2025-07-01",
            "--end-date",
            "2025-07-07",
            "--minimum-history-sessions",
            "1",
            "--output",
            str(output_path),
        ]
    )

    assert result == 0
    document = json.loads(output_path.read_text(encoding="utf-8"))
    assert document["dataset_kind"] == "prototype_real"
    assert document["claims_eligible"] is False
    assert document["source_snapshot_count"] == 2
    assert document["audit"]["calendar"] == "XNYS"
    assert document["audit"]["status"] == "fail"
    by_ticker = {
        item["ticker"]: item for item in document["audit"]["securities"]
    }
    assert by_ticker["MSFT"]["coverage_percentage"] == 75.0
    assert by_ticker["MSFT"]["issue_counts"]["missing_vs_benchmark"] == 1
