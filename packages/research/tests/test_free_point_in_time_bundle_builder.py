from pipelines.build_free_point_in_time_equity_bundle import (
    _bundle_price,
    _coalesce_memberships,
    _coalesce_ticker_aliases,
    _vendor_id,
)


def test_vendor_id_prefers_price_series_share_class_figi():
    identifiers = {"NEW": {"share_class_figi": "BBG000TEST"}}
    assert _vendor_id(
        ticker="OLD",
        identity={"cik": "0000000001"},
        identifier_by_ticker=identifiers,
        price_tickers=["NEW"],
    ) == "BBG000TEST"


def test_bundle_price_preserves_raw_and_adjusted_values():
    row = _bundle_price(
        "id",
        {
            "date": "2024-01-02T00:00:00Z",
            "open": 10,
            "high": 11,
            "low": 9,
            "close": 10.5,
            "adjOpen": 5,
            "adjHigh": 5.5,
            "adjLow": 4.5,
            "adjClose": 5.25,
            "volume": 100,
            "adjVolume": 200,
        },
    )
    assert row["vendor_id"] == "id"
    assert row["close"] == 10.5
    assert row["adj_close"] == 5.25


def test_memberships_are_coalesced_by_permanent_identity():
    rows = [
        {"vendor_id": "id", "effective_from": "2017-01-01", "effective_to": "2018-09-18", "announced_at": "2017-01-01T00:00:00Z"},
        {"vendor_id": "id", "effective_from": "2017-01-01", "effective_to": "2020-05-11", "announced_at": "2017-01-01T00:00:00Z"},
    ]
    assert _coalesce_memberships(rows) == [
        {"vendor_id": "id", "effective_from": "2017-01-01", "effective_to": "2020-05-11", "announced_at": "2017-01-01T00:00:00Z"}
    ]


def test_ticker_aliases_are_unique_at_every_point_in_time():
    rows = [
        {"ticker": "OLD", "exchange": None, "effective_from": "2017-01-01", "effective_to": "2019-01-02", "announced_at": "2017-01-01T00:00:00Z"},
        {"ticker": "OLD", "exchange": None, "effective_from": "2018-01-01", "effective_to": "2019-01-02", "announced_at": "2018-01-01T00:00:00Z"},
        {"ticker": "NEW", "exchange": None, "effective_from": "2019-01-02", "effective_to": "2025-06-30", "announced_at": "2019-01-02T00:00:00Z"},
    ]

    assert _coalesce_ticker_aliases(rows, active_to=None) == [
        {"ticker": "OLD", "exchange": None, "effective_from": "2017-01-01", "effective_to": "2019-01-01", "announced_at": "2017-01-01T00:00:00Z"},
        {"ticker": "NEW", "exchange": None, "effective_from": "2019-01-02", "effective_to": "2025-06-30", "announced_at": "2019-01-02T00:00:00Z"},
    ]


def test_retrospective_rename_alias_begins_after_historical_alias_ends():
    rows = [
        {"ticker": "NEW", "exchange": None, "effective_from": "2017-01-01", "effective_to": "2020-05-11", "announced_at": "2017-01-01T00:00:00Z"},
        {"ticker": "OLD", "exchange": None, "effective_from": "2017-01-01", "effective_to": "2018-09-18", "announced_at": "2017-01-01T00:00:00Z"},
    ]

    assert _coalesce_ticker_aliases(rows, active_to=None) == [
        {"ticker": "OLD", "exchange": None, "effective_from": "2017-01-01", "effective_to": "2018-09-18", "announced_at": "2017-01-01T00:00:00Z"},
        {"ticker": "NEW", "exchange": None, "effective_from": "2018-09-19", "effective_to": "2020-05-11", "announced_at": "2018-09-19T00:00:00Z"},
    ]
