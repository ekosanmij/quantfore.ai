import json
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quantfore_research.validation.price_reconciliation import (
    INDEPENDENT_FIELDS,
    ReconciliationConfig,
    ReconciliationPrice,
    SamplePoint,
    compare_price_row,
    deterministic_sample,
    parse_independent_csv,
    reconcile_sample,
)


def price(
    *,
    source: str,
    ticker: str = "AAPL",
    day: date = date(2020, 8, 31),
    raw: str = "100",
    adjusted: str = "25",
    volume: str = "1000",
) -> ReconciliationPrice:
    raw_value = Decimal(raw)
    adjusted_value = Decimal(adjusted)
    return ReconciliationPrice(
        ticker=ticker,
        date=day,
        open=raw_value,
        high=raw_value,
        low=raw_value,
        close=raw_value,
        volume=Decimal(volume),
        adj_open=adjusted_value,
        adj_high=adjusted_value,
        adj_low=adjusted_value,
        adj_close=adjusted_value,
        adj_volume=Decimal(volume) * Decimal("4"),
        source=source,
        source_url=f"https://example.test/{source}",
        retrieved_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        license_tag="test-license",
        source_snapshot_id="snapshot-1" if source == "Tiingo" else None,
    )


def one_point_sample() -> tuple[SamplePoint, ...]:
    return (
        SamplePoint(
            ticker="AAPL",
            date=date(2020, 8, 31),
            anchor_date=date(2020, 8, 31),
            event_type="split",
            selection_reason="test split",
        ),
    )


def independent_csv(*, source: str = "IndependentCo", duplicate: bool = False):
    row = [
        "AAPL",
        "2020-08-31",
        "100",
        "101",
        "99",
        "100",
        "1000",
        "25",
        "25.25",
        "24.75",
        "25",
        "4000",
        source,
        "https://independent.example/AAPL",
        "2026-07-01T12:00:00Z",
        "independent-research",
    ]
    lines = [",".join(INDEPENDENT_FIELDS), ",".join(row)]
    if duplicate:
        lines.append(",".join(row))
    return ("\n".join(lines) + "\n").encode("utf-8")


def adjusted_only_independent_csv():
    row = [
        "AAPL",
        "2020-08-31",
        "",
        "",
        "",
        "",
        "",
        "25",
        "25",
        "25",
        "25",
        "4000",
        "Yahoo Finance Chart API",
        "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
        "2026-07-01T12:00:00Z",
        "public-web-reconciliation-only-unverified",
    ]
    return (
        ",".join(INDEPENDENT_FIELDS) + "\n" + ",".join(row) + "\n"
    ).encode("utf-8")


def test_deterministic_sample_has_five_securities_and_twenty_dates_each():
    sample = deterministic_sample()
    counts = Counter(point.ticker for point in sample)

    assert len(sample) == 100
    assert counts == {"AAPL": 20, "NVDA": 20, "META": 20, "XOM": 20, "JPM": 20}
    assert all(
        any(point.date == point.anchor_date for point in sample if point.ticker == ticker)
        for ticker in counts
    )
    assert {point.event_type for point in sample} == {"split", "volatile_period"}


def test_independent_csv_parser_requires_distinct_source_and_no_duplicates():
    rows = parse_independent_csv(independent_csv())

    assert len(rows) == 1
    assert rows[0].source == "IndependentCo"
    assert rows[0].adj_close == Decimal("25")
    with pytest.raises(ValueError, match="must not be Tiingo"):
        parse_independent_csv(independent_csv(source="Tiingo"))
    with pytest.raises(ValueError, match="contains duplicate"):
        parse_independent_csv(independent_csv(duplicate=True))


def test_adjusted_only_export_is_preserved_and_requires_review():
    independent = parse_independent_csv(adjusted_only_independent_csv())[0]
    comparison = compare_price_row(
        price(source="Tiingo"),
        independent,
        config=ReconciliationConfig(),
    )

    assert independent.open is None
    assert independent.volume is None
    assert comparison.status == "review"
    assert comparison.raw_price_differences_bps["close"] is None
    assert "raw close is unavailable from one source" in comparison.notes


def test_matching_sources_pass_reconciliation():
    result = reconcile_sample(
        sample=one_point_sample(),
        primary=[price(source="Tiingo")],
        independent=[price(source="IndependentCo")],
        missing_session_counts={"AAPL": 0},
        price_quality_status="pass",
    )

    assert result.decision == "pass"
    assert result.rows_received == {"primary": 1, "independent": 1}
    assert result.rows_accepted == 1
    assert result.failed_securities == ()
    assert result.securities[0].coverage_percentage == 100.0


def test_adjustment_difference_is_a_conditional_pass():
    comparison = compare_price_row(
        price(source="Tiingo", adjusted="25"),
        price(source="IndependentCo", adjusted="26"),
        config=ReconciliationConfig(),
    )
    result = reconcile_sample(
        sample=one_point_sample(),
        primary=[price(source="Tiingo", adjusted="25")],
        independent=[price(source="IndependentCo", adjusted="26")],
        missing_session_counts={"AAPL": 0},
        price_quality_status="pass",
    )

    assert comparison.status == "review"
    assert result.decision == "conditional_pass"
    assert result.adjustment_difference_count == 1


def test_raw_price_difference_or_missing_row_fails_reconciliation():
    raw_difference = reconcile_sample(
        sample=one_point_sample(),
        primary=[price(source="Tiingo", raw="100")],
        independent=[price(source="IndependentCo", raw="101")],
        missing_session_counts={"AAPL": 0},
        price_quality_status="pass",
    )
    missing = reconcile_sample(
        sample=one_point_sample(),
        primary=[],
        independent=[price(source="IndependentCo")],
        missing_session_counts={"AAPL": 0},
        price_quality_status="pass",
    )

    assert raw_difference.decision == "fail"
    assert raw_difference.failed_securities == ("AAPL",)
    assert missing.decision == "fail"
    assert missing.securities[0].missing_primary_dates == (date(2020, 8, 31),)


def write_quality_audit(path, *, universe_hash="universe-hash", hashes=None):
    snapshot_hashes = hashes or ["snapshot-a", "snapshot-b"]
    path.write_text(
        json.dumps(
            {
                "dataset_kind": "prototype_real",
                "claims_eligible": False,
                "universe_file_sha256": universe_hash,
                "source_snapshot_count": len(snapshot_hashes),
                "source_snapshots": [
                    {"sha256": value} for value in snapshot_hashes
                ],
                "audit": {
                    "status": "pass",
                    "securities": [
                        {
                            "ticker": "AAPL",
                            "issue_counts": {
                                "missing_expected_sessions": 0
                            },
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )


def test_quality_audit_loader_rejects_stale_universe_hash(tmp_path):
    from pipelines.reconcile_price_sample import load_price_quality_audit

    audit_path = tmp_path / "quality.json"
    write_quality_audit(audit_path)

    with pytest.raises(ValueError, match="universe hash does not match"):
        load_price_quality_audit(
            audit_path,
            expected_universe_sha256="different-universe",
            expected_snapshot_hashes=["snapshot-a", "snapshot-b"],
        )


def test_quality_audit_loader_rejects_stale_snapshot_hashes(tmp_path):
    from pipelines.reconcile_price_sample import load_price_quality_audit

    audit_path = tmp_path / "quality.json"
    write_quality_audit(audit_path)

    with pytest.raises(ValueError, match="snapshot hashes do not match"):
        load_price_quality_audit(
            audit_path,
            expected_universe_sha256="universe-hash",
            expected_snapshot_hashes=["snapshot-a", "snapshot-current"],
        )


def test_quality_audit_loader_records_validated_lineage(tmp_path):
    from pipelines.reconcile_price_sample import load_price_quality_audit

    audit_path = tmp_path / "quality.json"
    write_quality_audit(audit_path)

    status, missing_counts, metadata = load_price_quality_audit(
        audit_path,
        expected_universe_sha256="universe-hash",
        expected_snapshot_hashes=["snapshot-b", "snapshot-a"],
    )

    assert status == "pass"
    assert missing_counts == {"AAPL": 0}
    assert metadata is not None
    assert metadata["source_snapshot_hashes"] == ["snapshot-a", "snapshot-b"]


def test_pipeline_generates_honest_fail_closed_reports_without_sources(tmp_path):
    from pipelines.reconcile_price_sample import main

    json_path = tmp_path / "audit.json"
    markdown_path = tmp_path / "audit.md"
    database_url = f"sqlite+pysqlite:///{tmp_path / 'empty.db'}"

    exit_code = main(
        [
            "--universe-file",
            "config/universes/us-equity-trial-v0.csv",
            "--database-url",
            database_url,
            "--price-audit",
            str(tmp_path / "missing-price-audit.json"),
            "--json-output",
            str(json_path),
            "--markdown-output",
            str(markdown_path),
        ]
    )

    assert exit_code == 0
    document = json.loads(json_path.read_text(encoding="utf-8"))
    reconciliation = document["reconciliation"]
    assert document["dataset_kind"] == "prototype_real"
    assert document["claims_eligible"] is False
    assert reconciliation["decision"] == "fail"
    assert reconciliation["rows_accepted"] == 0
    assert reconciliation["sample_size"] == 100
    assert "no Tiingo source snapshots were found" in reconciliation["blocking_reasons"]
    assert "independent comparison export was not supplied" in reconciliation[
        "blocking_reasons"
    ]
    markdown = markdown_path.read_text(encoding="utf-8")
    assert "**Decision:** `FAIL`" in markdown
    assert "Rows received and accepted" in markdown
    assert "Price and adjustment differences" in markdown
    assert "Manual-review notes" in markdown
