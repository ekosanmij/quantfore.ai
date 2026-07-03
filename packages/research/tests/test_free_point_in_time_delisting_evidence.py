from pipelines.freeze_free_point_in_time_delisting_evidence import _terminal_prices


def test_terminal_price_keeps_delisting_return_unavailable():
    row = _terminal_prices(
        [
            {"date": "2020-01-02T00:00:00Z", "close": 10, "adjClose": 10},
            {"date": "2020-01-03T00:00:00Z", "close": 8, "adjClose": 8},
        ]
    )
    assert row["ordinary_last_session_adjusted_return"] == "-0.2"
    assert row["delisting_return"] is None
    assert row["delisting_return_available"] is False
