from datetime import date

from pipelines.reconcile_free_point_in_time_lineage import (
    SEC_IDENTITY_OVERRIDES,
    SEC_TRANSITION_OVERRIDES,
    _contiguous_alias,
    _covers_episode,
    _overlaps,
)


def test_dated_ticker_candidate_must_overlap_membership_episode():
    episode_start = date(2014, 1, 1)
    episode_end = date(2020, 1, 1)

    assert _overlaps("2010-01-01T00:00:00Z", "2015-01-01T00:00:00Z", episode_start, episode_end)
    assert not _overlaps("2021-01-01T00:00:00Z", None, episode_start, episode_end)


def test_aliases_must_be_dated_non_overlapping_renames():
    assert _contiguous_alias(
        "2012-01-01", "2022-06-08", "2022-06-09", None
    )
    assert not _contiguous_alias(
        "2012-01-01", "2022-06-08", "2012-01-01", None
    )


def test_historical_identity_overrides_cover_remaining_labels():
    assert {
        "BTUUQ",
        "CCE",
        "CVC",
        "DTV",
        "DXC",
        "ESV",
        "FTR",
        "HAR",
        "IGT",
        "TSS",
        "WYND",
        "XL",
    }.issubset(SEC_IDENTITY_OVERRIDES)
    assert set(SEC_TRANSITION_OVERRIDES) == {"FOX", "FOXA", "IR"}


def test_contiguous_alias_price_segments_can_cover_one_episode():
    segments = [
        {"first_price_date": "2013-01-02", "last_price_date": "2017-07-05"},
        {"first_price_date": "2017-07-05", "last_price_date": "2020-01-02"},
    ]
    assert _covers_episode(
        segments, date(2014, 1, 1), date(2019, 10, 17), tolerance_days=7
    )
    assert not _covers_episode(
        segments[:1], date(2014, 1, 1), date(2019, 10, 17), tolerance_days=7
    )
