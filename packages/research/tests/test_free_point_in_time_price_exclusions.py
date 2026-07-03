from pipelines.freeze_free_point_in_time_price_exclusions import _month_ends, _reason


def test_month_ends_include_leap_day_and_end_month():
    from datetime import date

    assert _month_ends(date(2024, 1, 1), date(2024, 3, 1)) == [
        date(2024, 1, 31),
        date(2024, 2, 29),
        date(2024, 3, 31),
    ]


def test_exclusion_reasons_distinguish_transition_collision_and_missing():
    assert _reason({}, {"selected_identity": {"segments": [{}]}}).startswith(
        "IDENTITY_TRANSITION"
    )
    assert _reason({"status": "ticker_collision"}, {}) == "TICKER_REUSED_IDENTITY_COLLISION"
    assert _reason({"identity_matches": True, "start_date": "2016-01-01"}, {}) == "SOURCE_HISTORY_INCOMPLETE"
    assert _reason({}, {}) == "SOURCE_PRICE_UNAVAILABLE"
