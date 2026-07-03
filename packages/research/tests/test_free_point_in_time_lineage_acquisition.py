from pipelines.acquire_free_point_in_time_lineage import classify_metadata


def episode():
    return {
        "ticker": "AGN",
        "effective_from": "2014-01-01",
        "effective_to": "2020-05-11",
    }


def test_lineage_metadata_accepts_matching_us_history_with_trading_tolerance():
    result = classify_metadata(
        {
            "ticker": "AGN",
            "name": "Allergan Inc",
            "exchangeCode": "NYSE",
            "startDate": "1989-06-22",
            "endDate": "2020-05-08",
        },
        episode=episode(),
        expected_names=["Allergan"],
    )

    assert result["status"] == "direct_ticker_verified"
    assert result["identity_matches"] is True
    assert result["coverage_within_tolerance"] is True


def test_lineage_metadata_rejects_a_recycled_foreign_ticker():
    result = classify_metadata(
        {
            "ticker": "AGN",
            "name": "Unrelated Mining Ltd",
            "exchangeCode": "ASX",
            "startDate": "2024-01-01",
            "endDate": "2026-01-01",
        },
        episode=episode(),
        expected_names=["Allergan"],
    )

    assert result["status"] == "ticker_collision"
    assert result["identity_matches"] is False
