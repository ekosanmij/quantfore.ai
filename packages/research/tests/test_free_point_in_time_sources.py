import csv
import hashlib
import io
import zipfile
from datetime import date, datetime, timezone

import pytest

from pipelines import assess_free_point_in_time_sources as assessment_pipeline
from quantfore_research.ingest.free_point_in_time import (
    FreePointInTimeSourceError,
    classify_episode_coverage,
    derive_membership_episodes,
    normalize_membership_ticker,
    parse_membership_history,
    parse_tiingo_supported_tickers,
    reconcile_samples,
    tiingo_ticker,
)


def membership_csv(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["date", "tickers"])
    writer.writerows(rows)
    return output.getvalue().encode()


def tiingo_csv(rows):
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        [
            "ticker",
            "exchange",
            "assetType",
            "priceCurrency",
            "startDate",
            "endDate",
        ]
    )
    writer.writerows(rows)
    return output.getvalue().encode()


def ticker_zip(body):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("supported_tickers.csv", body)
    return output.getvalue()


def test_membership_history_derives_distinct_reentry_episodes():
    history = parse_membership_history(
        membership_csv(
            [
                ("2013-12-31", "AAA,BRK-B"),
                ("2014-01-03", "AAA,CCC,BRK.B"),
                ("2014-01-05", "CCC,BRK.B"),
                ("2014-01-08", "AAA,CCC,BRK.B"),
            ]
        ),
        label="fixture",
    )
    episodes = derive_membership_episodes(
        history, window_start=date(2014, 1, 1), window_end=date(2014, 1, 10)
    )

    aaa = [row for row in episodes if row.ticker == "AAA"]
    assert [(row.effective_from, row.effective_to) for row in aaa] == [
        (date(2014, 1, 1), date(2014, 1, 4)),
        (date(2014, 1, 8), date(2014, 1, 10)),
    ]
    assert len({row.episode_id for row in aaa}) == 2
    assert normalize_membership_ticker("brk-b") == "BRK.B"
    assert tiingo_ticker("BRK.B") == "BRK-B"


def test_membership_parser_rejects_duplicate_or_unsorted_rows():
    with pytest.raises(FreePointInTimeSourceError, match="duplicate tickers"):
        parse_membership_history(
            membership_csv([("2014-01-01", "AAA,AAA")]), label="fixture"
        )
    with pytest.raises(FreePointInTimeSourceError, match="must be increasing"):
        parse_membership_history(
            membership_csv(
                [("2014-01-02", "AAA"), ("2014-01-01", "AAA")]
            ),
            label="fixture",
        )


def test_membership_parser_collapses_equivalent_same_day_share_class_rows():
    history = parse_membership_history(
        membership_csv(
            [("2014-01-01", "AAA,BRK.B"), ("2014-01-01", "AAA,BRK-B")]
        ),
        label="fixture",
    )
    assert len(history) == 1
    with pytest.raises(FreePointInTimeSourceError, match="conflicting snapshots"):
        parse_membership_history(
            membership_csv(
                [("2014-01-01", "AAA"), ("2014-01-01", "AAA,BBB")]
            ),
            label="fixture",
        )
    revised = parse_membership_history(
        membership_csv(
            [("2014-01-01", "AAA"), ("2014-01-01", "AAA,BBB")]
        ),
        label="fixture",
        allow_same_date_revisions=True,
    )
    assert revised[0].tickers == frozenset({"AAA", "BBB"})


def test_tiingo_inventory_classifies_full_partial_recycled_and_missing():
    history = parse_membership_history(
        membership_csv([("2014-01-01", "FULL,PART,OLD,NONE")]), label="fixture"
    )
    episodes = derive_membership_episodes(
        history, window_start=date(2014, 1, 1), window_end=date(2014, 12, 31)
    )
    listings = parse_tiingo_supported_tickers(
        tiingo_csv(
            [
                ("FULL", "NYSE", "Stock", "USD", "2010-01-01", "2020-01-01"),
                ("PART", "NYSE", "Stock", "USD", "2014-02-01", "2020-01-01"),
                ("OLD", "NYSE", "Stock", "USD", "2025-01-01", "2026-01-01"),
                ("IGNORED", "NYSE", "Stock", "CAD", "2010-01-01", "2020-01-01"),
            ]
        )
    )
    statuses = {
        row.episode.ticker: row.status
        for row in classify_episode_coverage(episodes, listings)
    }
    assert statuses == {
        "FULL": "full",
        "NONE": "missing",
        "OLD": "recycled_or_nonoverlapping",
        "PART": "partial",
    }


def test_secondary_reconciliation_normalizes_share_class_notation():
    primary = parse_membership_history(
        membership_csv([("2014-01-01", "AAA,BRK.B")]), label="primary"
    )
    secondary = parse_membership_history(
        membership_csv([("2014-01-01", "AAA,BRK-B")]), label="secondary"
    )
    result = reconcile_samples(primary, secondary, [date(2014, 1, 31)])
    assert result[0]["exact_match"] is True
    assert result[0]["primary_only"] == []


def test_assessment_blocks_quota_gaps_and_source_disagreement(monkeypatch):
    primary_body = membership_csv(
        [
            ("2013-12-31", "AAA,BBB"),
            ("2014-01-03", "AAA,CCC"),
        ]
    )
    secondary_body = membership_csv([("2013-12-31", "AAA")])
    inventory = ticker_zip(
        tiingo_csv(
            [
                ("AAA", "NYSE", "Stock", "USD", "2010-01-01", "2020-01-01"),
                ("BBB", "NYSE", "Stock", "USD", "2010-01-01", "2014-01-02"),
                ("CCC", "NYSE", "Stock", "USD", "2015-01-01", "2020-01-01"),
                ("SPY", "NYSE ARCA", "ETF", "USD", "1993-01-29", "2020-01-01"),
            ]
        )
    )
    primary_license = b"primary MIT fixture"
    secondary_license = b"secondary MIT fixture"
    monkeypatch.setattr(
        assessment_pipeline,
        "EXPECTED_PRIMARY_SHA256",
        hashlib.sha256(primary_body).hexdigest(),
    )
    monkeypatch.setattr(
        assessment_pipeline,
        "EXPECTED_SECONDARY_SHA256",
        hashlib.sha256(secondary_body).hexdigest(),
    )
    monkeypatch.setattr(
        assessment_pipeline,
        "EXPECTED_PRIMARY_LICENSE_SHA256",
        hashlib.sha256(primary_license).hexdigest(),
    )
    monkeypatch.setattr(
        assessment_pipeline,
        "EXPECTED_SECONDARY_LICENSE_SHA256",
        hashlib.sha256(secondary_license).hexdigest(),
    )

    report, private_plan = assessment_pipeline.build_assessment(
        primary_body=primary_body,
        secondary_body=secondary_body,
        primary_license_body=primary_license,
        secondary_license_body=secondary_license,
        tiingo_zip_body=inventory,
        window_start=date(2014, 1, 1),
        window_end=date(2014, 1, 31),
        sample_dates=[date(2014, 1, 2)],
        free_symbol_limit=2,
        generated_at=datetime(2014, 2, 1, tzinfo=timezone.utc),
    )

    assert report["decision"] == "blocked"
    assert report["claims_eligible"] is False
    assert {row["code"] for row in report["blockers"]} == {
        "implausible_membership_count",
        "incomplete_tiingo_episode_resolution",
        "secondary_membership_disagreement",
        "tiingo_free_monthly_symbol_limit",
    }
    assert report["tiingo_preflight"]["required_unique_symbol_count"] == 4
    assert report["tiingo_preflight"]["safe_unique_symbol_count"] == 3
    assert private_plan["safe_acquisition_batches"] == [
        {"batch_number": 1, "symbol_count": 2, "symbols": ["AAA", "BBB"]},
        {"batch_number": 2, "symbol_count": 1, "symbols": ["SPY"]},
    ]
    assert "safe_acquisition_batches" not in report["tiingo_preflight"]
    assert report["tiingo_preflight"]["safe_acquisition_batch_counts"] == [2, 1]
    assert report["membership"]["secondary_samples"][0]["primary_only_count"] == 1
    assert "primary_only" not in report["membership"]["secondary_samples"][0]


def test_tiingo_zip_rejects_extra_archive_members():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("supported_tickers.csv", b"fixture")
        archive.writestr("unexpected.txt", b"fixture")
    with pytest.raises(FreePointInTimeSourceError, match="must contain only"):
        assessment_pipeline._extract_tiingo_csv(output.getvalue())
