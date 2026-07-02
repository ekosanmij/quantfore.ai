import csv
import hashlib
import re
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPO_ROOT / "docs" / "data" / "real-data-trial-v0.md"
UNIVERSE_PATH = (
    REPO_ROOT / "config" / "universes" / "us-equity-trial-v0.csv"
)
EXPECTED_FIELDS = [
    "ticker",
    "company_name",
    "cik",
    "exchange",
    "sector",
    "active_from",
    "active_to",
    "is_benchmark",
    "selection_reason",
]


def read_universe() -> tuple[list[str], list[dict[str, str]]]:
    with UNIVERSE_PATH.open(encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        return list(reader.fieldnames or []), list(reader)


def test_real_data_contract_freezes_non_claim_prototype_metadata():
    contract = CONTRACT_PATH.read_text(encoding="utf-8")

    assert "dataset_kind: prototype_real" in contract
    assert "claims_eligible: false" in contract
    assert (
        "publication_status: "
        "internal_only_pending_derived_report_rights_confirmation"
    ) in contract
    assert "dataset_cutoff_date: 2025-12-31" in contract
    assert (
        "universe_file_sha256: "
        "0a1ec9667fa4f4378f9c1c6bb010d03585690558069d04286a8320e9d02dd584"
        in contract
    )
    assert "vendor: Tiingo" in contract
    assert "licence_tag: tiingo_internal_research_trial_v0" in contract
    assert "retrieval_timestamp: 2026-07-02T06:32:40.222657Z" in contract
    assert "not historical S&P 500 membership" in contract


def test_real_data_universe_has_25_ranked_equities_and_spy_benchmark():
    fields, rows = read_universe()
    benchmarks = [row for row in rows if row["is_benchmark"] == "true"]
    ranked = [row for row in rows if row["is_benchmark"] == "false"]

    assert fields == EXPECTED_FIELDS
    assert len(ranked) == 25
    assert benchmarks == [next(row for row in rows if row["ticker"] == "SPY")]
    assert len(rows) == 26
    assert len({row["ticker"] for row in rows}) == len(rows)
    assert hashlib.sha256(UNIVERSE_PATH.read_bytes()).hexdigest() == (
        "0a1ec9667fa4f4378f9c1c6bb010d03585690558069d04286a8320e9d02dd584"
    )


def test_real_data_universe_has_valid_identifiers_dates_and_sector_coverage():
    _, rows = read_universe()
    ranked = [row for row in rows if row["is_benchmark"] == "false"]

    assert all(re.fullmatch(r"\d{10}", row["cik"]) for row in rows)
    assert {row["exchange"] for row in rows} == {"NASDAQ", "NYSE"}
    assert all(row["active_from"] == "2020-01-01" for row in rows)
    assert all(row["active_to"] == "2025-12-31" for row in rows)
    assert all(
        (
            date.fromisoformat(row["active_to"])
            - date.fromisoformat(row["active_from"])
        ).days
        >= 5 * 365
        for row in rows
    )
    assert len({row["sector"] for row in ranked}) == 11
    assert all(row["selection_reason"].strip() for row in rows)
