import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

from quantfore_research.ingest.point_in_time_equities import (
    PointInTimeEquityBundleAdapter,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "pipelines" / "ingest_point_in_time_equities.py"
RETRIEVED_AT = "2026-07-02T10:00:00Z"


def _json_bytes(value) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_bundle(
    bundle_dir: Path,
    *,
    overlapping_membership=False,
    rights=True,
    recycled_ticker=False,
):
    bundle_dir.mkdir(parents=True, exist_ok=True)
    documents = {
        "securities": [
            {
                "vendor_id": "100",
                "ticker": "META",
                "name": "Meta Platforms",
                "exchange": "NASDAQ",
                "sector": "Communication Services",
                "industry": "Internet Content",
                "cik": "0001326801",
                "active_from": "2012-05-18",
                "active_to": None,
                "identifiers": [
                    {
                        "identifier_type": "CIK",
                        "identifier_value": "0001326801",
                        "valid_from": "2012-05-18",
                        "valid_to": None,
                        "is_permanent": True,
                    }
                ],
                "ticker_aliases": [
                    {
                        "ticker": "FB",
                        "exchange": "NASDAQ",
                        "effective_from": "2012-05-18",
                        "effective_to": "2022-06-08",
                        "announced_at": "2021-10-28T20:05:00Z",
                    },
                    {
                        "ticker": "META",
                        "exchange": "NASDAQ",
                        "effective_from": "2022-06-09",
                        "effective_to": None,
                        "announced_at": "2022-05-31T12:00:00Z",
                    },
                ],
            },
            {
                "vendor_id": "200",
                "ticker": "SPY",
                "name": "SPDR S&P 500 ETF Trust",
                "exchange": "NYSE Arca",
                "sector": None,
                "industry": None,
                "cik": "0000884394",
                "active_from": "1993-01-22",
                "active_to": None,
                "identifiers": [],
                "ticker_aliases": [
                    {
                        "ticker": "SPY",
                        "exchange": "NYSE Arca",
                        "effective_from": "1993-01-22",
                        "effective_to": None,
                        "announced_at": "1993-01-01T00:00:00Z",
                    }
                ],
            },
            {
                "vendor_id": "300",
                "ticker": "OLDCO",
                "name": "Old Company",
                "exchange": "NYSE",
                "sector": "Industrials",
                "industry": "Machinery",
                "cik": "0000000300",
                "active_from": "1990-01-01",
                "active_to": "2018-12-31",
                "identifiers": [],
                "ticker_aliases": [
                    {
                        "ticker": "OLDCO",
                        "exchange": "NYSE",
                        "effective_from": "1990-01-01",
                        "effective_to": "2018-12-31",
                        "announced_at": "1990-01-01T00:00:00Z",
                    }
                ],
            },
            {
                "vendor_id": "400",
                "ticker": "NEWCO",
                "name": "New Company",
                "exchange": "NYSE",
                "sector": "Industrials",
                "industry": "Machinery",
                "cik": "0000000400",
                "active_from": "2018-12-31",
                "active_to": None,
                "identifiers": [],
                "ticker_aliases": [
                    {
                        "ticker": "NEWCO",
                        "exchange": "NYSE",
                        "effective_from": "2018-12-31",
                        "effective_to": None,
                        "announced_at": "2018-06-01T12:00:00Z",
                    }
                ],
            },
        ],
        "memberships": [
            {
                "vendor_id": "100",
                "effective_from": "2013-12-23",
                "effective_to": None,
                "announced_at": "2013-12-18T22:00:00Z",
            },
            {
                "vendor_id": "300",
                "effective_from": "2010-01-01",
                "effective_to": "2018-12-30",
                "announced_at": "2009-12-20T22:00:00Z",
            },
            {
                "vendor_id": "400",
                "effective_from": "2018-12-31",
                "effective_to": None,
                "announced_at": "2018-06-01T12:00:00Z",
            },
        ],
        "prices": [
            {
                "vendor_id": vendor_id,
                "date": day,
                "open": 100,
                "high": 105,
                "low": 99,
                "close": 103,
                "adj_open": 50,
                "adj_high": 52.5,
                "adj_low": 49.5,
                "adj_close": 51.5,
                "volume": 1000000,
                "adj_volume": 2000000,
            }
            for vendor_id, day in (
                ("100", "2025-12-31"),
                ("200", "2025-12-31"),
                ("300", "2018-12-31"),
                ("400", "2019-01-02"),
            )
        ],
        "corporate_actions": [
            {
                "vendor_id": "100",
                "action_type": "symbol_change",
                "effective_date": "2022-06-09",
                "announced_at": "2022-05-31T12:00:00Z",
                "cash_amount": None,
                "currency": None,
                "ratio_from": None,
                "ratio_to": None,
                "related_vendor_id": None,
                "details": {"from": "FB", "to": "META"},
            },
            {
                "vendor_id": "300",
                "action_type": "split",
                "effective_date": "2017-01-03",
                "announced_at": "2016-12-01T12:00:00Z",
                "cash_amount": None,
                "currency": None,
                "ratio_from": 1,
                "ratio_to": 2,
                "related_vendor_id": None,
                "details": {},
            },
            {
                "vendor_id": "300",
                "action_type": "cash_dividend",
                "effective_date": "2017-03-01",
                "announced_at": "2017-02-01T12:00:00Z",
                "cash_amount": 0.25,
                "currency": "USD",
                "ratio_from": None,
                "ratio_to": None,
                "related_vendor_id": None,
                "details": {},
            },
        ],
        "delistings": [
            {
                "vendor_id": "300",
                "delisting_date": "2018-12-31",
                "announced_at": "2018-06-01T12:00:00Z",
                "delisting_return": -0.35,
                "return_available_at": "2019-01-02T22:00:00Z",
                "reason": "acquired",
                "successor_vendor_id": "400",
            }
        ],
    }
    if overlapping_membership:
        documents["memberships"].append(
            {
                "vendor_id": "300",
                "effective_from": "2018-01-01",
                "effective_to": "2018-12-31",
                "announced_at": "2017-12-01T12:00:00Z",
            }
        )
    if recycled_ticker:
        documents["securities"].append(
            {
                "vendor_id": "500",
                "ticker": "OLDCO",
                "name": "New Issuer Reusing OLDCO",
                "exchange": "NYSE",
                "sector": "Industrials",
                "industry": "Machinery",
                "cik": "0000000500",
                "active_from": "2020-01-01",
                "active_to": None,
                "identifiers": [],
                "ticker_aliases": [
                    {
                        "ticker": "OLDCO",
                        "exchange": "NYSE",
                        "effective_from": "2020-01-01",
                        "effective_to": None,
                        "announced_at": "2019-12-01T12:00:00Z",
                    }
                ],
            }
        )
        documents["prices"].append(
            {
                "vendor_id": "500",
                "date": "2025-12-31",
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20,
                "adj_open": 20,
                "adj_high": 21,
                "adj_low": 19,
                "adj_close": 20,
                "volume": 1000,
                "adj_volume": 1000,
            }
        )

    files = {}
    for role, document in documents.items():
        body = _json_bytes(document)
        path = bundle_dir / f"{role}.json"
        path.write_bytes(body)
        files[role] = {
            "path": path.name,
            "dataset": f"fixture_{role}_v1",
            "source_uri": f"private://fixture/{role}",
            "retrieved_at": RETRIEVED_AT,
            "sha256": hashlib.sha256(body).hexdigest(),
        }

    manifest = {
        "schema_version": "point-in-time-equity-bundle-v1",
        "created_at": RETRIEVED_AT,
        "vendor": "Licensed Fixture Vendor",
        "license_tag": "fixture_internal_research",
        "license_rights_confirmed": rights,
        "license_evidence_uri": "private://legal/fixture-license",
        "vendor_identifier_type": "FIXTURE_PERMATICKER",
        "audit_contract": {
            "expected_row_counts": {
                role: len(document) for role, document in documents.items()
            },
            "monthly_membership_counts": {
                f"{year:04d}-{month:02d}": 2
                for year in range(2014, 2026)
                for month in range(1, 13)
            },
            "independent_membership_samples": [
                {
                    "as_of_date": "2014-01-31",
                    "vendor_ids": ["100", "300"],
                    "source_uri": "private://independent/sample-2014",
                    "source_sha256": "1" * 64,
                },
                {
                    "as_of_date": "2018-12-31",
                    "vendor_ids": ["100", "400"],
                    "source_uri": "private://independent/sample-2018",
                    "source_sha256": "2" * 64,
                },
                {
                    "as_of_date": "2025-12-31",
                    "vendor_ids": ["100", "400"],
                    "source_uri": "private://independent/sample-2025",
                    "source_sha256": "3" * 64,
                },
            ],
        },
        "universe": {
            "universe_id": "sp500-pit-v1",
            "name": "Historical S&P 500",
            "version": "v1",
            "description": "Historical membership by effective date",
            "window_start": "2014-01-01",
            "window_end": "2025-12-31",
            "benchmark_vendor_id": "200",
            "benchmark_excluded_from_rankings": True,
        },
        "files": files,
    }
    manifest_body = _json_bytes(manifest)
    (bundle_dir / "manifest.json").write_bytes(manifest_body)
    return hashlib.sha256(manifest_body).hexdigest()


def run_pipeline(bundle_dir: Path, db_path: Path, raw_dir: Path, manifest_hash: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        [str(REPO_ROOT), str(REPO_ROOT / "packages" / "research")]
    )
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            str(bundle_dir),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            str(raw_dir),
            "--expected-manifest-hash",
            manifest_hash,
        ],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def database_fingerprint(database_url: str):
    engine = create_engine(database_url)
    statements = {
        "snapshots": "select snapshot_id, vendor, dataset, retrieved_at, hash, storage_uri from source_snapshots order by snapshot_id",
        "securities": "select security_id, ticker, name, active_from, active_to from securities order by security_id",
        "identifiers": "select identifier_id, security_id, identifier_type, identifier_value, valid_from, valid_to, source_hash from security_identifiers order by identifier_id",
        "aliases": "select ticker_alias_id, security_id, ticker, effective_from, effective_to, announced_at, source_hash from ticker_aliases order by ticker_alias_id",
        "memberships": "select membership_id, universe_id, security_id, effective_from, effective_to, announced_at, source_hash from universe_memberships order by membership_id",
        "prices": "select price_id, security_id, date, open, close, adj_close, source_snapshot_id from prices order by price_id",
        "actions": "select corporate_action_id, security_id, action_type, effective_date, source_hash from corporate_actions order by corporate_action_id",
        "delistings": "select delisting_event_id, security_id, delisting_date, delisting_return, source_hash from delisting_events order by delisting_event_id",
    }
    with engine.connect() as connection:
        return {
            name: [tuple(row) for row in connection.execute(text(statement)).all()]
            for name, statement in statements.items()
        }


def test_adapter_verifies_and_normalizes_full_vendor_bundle(tmp_path):
    bundle_dir = tmp_path / "bundle"
    manifest_hash = write_bundle(bundle_dir)

    bundle = PointInTimeEquityBundleAdapter(
        bundle_dir, expected_manifest_hash=manifest_hash
    ).load()

    assert bundle.universe_id == "sp500-pit-v1"
    assert bundle.benchmark_vendor_id == "200"
    assert len(bundle.securities) == 4
    assert len(bundle.memberships) == 3
    assert len(bundle.prices) == 4
    assert {row.action_type for row in bundle.corporate_actions} == {
        "symbol_change",
        "split",
        "cash_dividend",
    }
    assert str(bundle.delistings[0].delisting_return) == "-0.35"


def test_pipeline_is_duplicate_safe_and_preserves_disappeared_company(tmp_path):
    bundle_dir = tmp_path / "bundle"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    manifest_hash = write_bundle(bundle_dir)

    first = run_pipeline(bundle_dir, db_path, raw_dir, manifest_hash)
    second = run_pipeline(bundle_dir, db_path, raw_dir, manifest_hash)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_result = json.loads(first.stdout)
    second_result = json.loads(second.stdout)
    assert first_result["prices_inserted"] == 4
    assert first_result["corporate_actions_inserted"] == 3
    assert first_result["delistings_inserted"] == 1
    assert second_result["prices_inserted"] == 0
    assert second_result["source_snapshots_reused"] == 6
    assert second_result["duplicate_rows_skipped"] == 22

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as connection:
        counts = connection.execute(
            text(
                "select (select count(*) from securities), "
                "(select count(*) from universe_memberships), "
                "(select count(*) from prices), "
                "(select count(*) from corporate_actions), "
                "(select count(*) from delisting_events)"
            )
        ).one()
        rename_ids = connection.execute(
            text(
                "select distinct security_id from ticker_aliases "
                "where ticker in ('FB', 'META')"
            )
        ).all()
        delisted = connection.execute(
            text(
                "select s.ticker, d.delisting_return from delisting_events d "
                "join securities s on s.security_id=d.security_id"
            )
        ).one()

    assert tuple(counts) == (4, 3, 4, 3, 1)
    assert len(rename_ids) == 1
    assert tuple(delisted) == ("OLDCO", -0.35)
    assert len(list(raw_dir.rglob("*.json"))) == 6


def test_identical_raw_bundle_produces_identical_normalized_records(tmp_path):
    bundle_dir = tmp_path / "bundle"
    manifest_hash = write_bundle(bundle_dir)
    first_db = tmp_path / "first.db"
    second_db = tmp_path / "second.db"

    first = run_pipeline(bundle_dir, first_db, tmp_path / "raw-one" / "raw", manifest_hash)
    second = run_pipeline(bundle_dir, second_db, tmp_path / "raw-two" / "raw", manifest_hash)

    assert first.returncode == second.returncode == 0
    assert database_fingerprint(f"sqlite+pysqlite:///{first_db}") == database_fingerprint(
        f"sqlite+pysqlite:///{second_db}"
    )


def test_failed_transaction_can_restart_from_already_frozen_raw_files(tmp_path):
    bundle_dir = tmp_path / "bundle"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    bad_hash = write_bundle(bundle_dir, overlapping_membership=True)

    failed = run_pipeline(bundle_dir, db_path, raw_dir, bad_hash)

    assert failed.returncode == 2
    assert "overlapping universe memberships" in failed.stderr
    assert len(list(raw_dir.rglob("*.json"))) == 6
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from securities")).scalar_one() == 0

    fixed_hash = write_bundle(bundle_dir, overlapping_membership=False)
    restarted = run_pipeline(bundle_dir, db_path, raw_dir, fixed_hash)

    assert restarted.returncode == 0, restarted.stderr
    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from securities")).scalar_one() == 4
    assert len(list(raw_dir.rglob("*.json"))) > 6


def test_hash_mismatch_and_unconfirmed_rights_fail_before_database_creation(tmp_path):
    bad_hash_bundle = tmp_path / "bad-hash"
    manifest_hash = write_bundle(bad_hash_bundle)
    (bad_hash_bundle / "prices.json").write_text("[]\n", encoding="utf-8")
    hash_db = tmp_path / "hash.db"
    hash_result = run_pipeline(
        bad_hash_bundle, hash_db, tmp_path / "hash-raw" / "raw", manifest_hash
    )

    assert hash_result.returncode == 2
    assert "prices SHA-256 does not match manifest" in hash_result.stderr
    assert not hash_db.exists()

    rights_bundle = tmp_path / "rights"
    rights_hash = write_bundle(rights_bundle, rights=False)
    rights_db = tmp_path / "rights.db"
    rights_result = run_pipeline(
        rights_bundle, rights_db, tmp_path / "rights-raw" / "raw", rights_hash
    )

    assert rights_result.returncode == 2
    assert "licensing rights are not confirmed" in rights_result.stderr
    assert not rights_db.exists()


def test_recycled_display_ticker_persists_as_distinct_permanent_securities(tmp_path):
    bundle_dir = tmp_path / "bundle"
    database = tmp_path / "recycled.db"
    manifest_hash = write_bundle(bundle_dir, recycled_ticker=True)

    result = run_pipeline(
        bundle_dir,
        database,
        tmp_path / "data" / "raw",
        manifest_hash,
    )

    assert result.returncode == 0, result.stderr
    engine = create_engine(f"sqlite+pysqlite:///{database}")
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "select security_id, ticker, name from securities "
                "where ticker = 'OLDCO' order by name"
            )
        ).all()
    assert len(rows) == 2
    assert len({row.security_id for row in rows}) == 2
