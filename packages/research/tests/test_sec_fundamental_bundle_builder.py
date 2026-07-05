from pipelines.build_sec_point_in_time_fundamental_bundle import (
    _canonical_fiscal_year,
    _period_type,
    _quarter,
    _sic_sector,
    _warehouse_decimal,
)


def test_comparative_revisions_keep_one_plausible_fiscal_year():
    assert _canonical_fiscal_year(
        [
            {"fiscal_period_end": "2012-12-31", "fiscal_year": 2015},
            {"fiscal_period_end": "2012-12-31", "fiscal_year": 2016},
        ]
    ) == 2012
    assert _canonical_fiscal_year(
        [{"fiscal_period_end": "2025-01-31", "fiscal_year": 2024}]
    ) == 2024


def test_period_classification_rejects_ytd_contexts():
    quarterly = {
        "start": "2024-01-01",
        "end": "2024-03-31",
        "form": "10-Q",
        "fp": "Q1",
    }
    ytd = {
        "start": "2024-01-01",
        "end": "2024-09-30",
        "form": "10-Q",
        "fp": "Q3",
    }
    annual = {
        "start": "2024-01-01",
        "end": "2024-12-31",
        "form": "10-K",
        "fp": "FY",
    }

    assert _period_type(quarterly) == "QUARTERLY"
    assert _quarter(quarterly, "QUARTERLY") == 1
    assert _period_type(ytd) is None
    assert _period_type(annual) == "ANNUAL"
    assert _quarter(annual, "ANNUAL") is None


def test_warehouse_decimal_enforces_fundamental_precision():
    assert _warehouse_decimal("123.456") == "123.456"
    assert _warehouse_decimal("1.1234567") is None
    assert _warehouse_decimal("1234567890123456789") is None
    assert _warehouse_decimal("NaN") is None


def test_sic_mapping_uses_frozen_eleven_sector_labels():
    assert _sic_sector("1311") == "Energy"
    assert _sic_sector("2834") == "Health Care"
    assert _sic_sector("3571") == "Information Technology"
    assert _sic_sector("4813") == "Communication Services"
    assert _sic_sector("4911") == "Utilities"
    assert _sic_sector("6500") == "Real Estate"
    assert _sic_sector("6021") == "Financials"
