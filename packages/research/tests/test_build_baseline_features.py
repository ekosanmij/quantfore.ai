import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
INGEST_SCRIPT = REPO_ROOT / "pipelines" / "ingest_prices_csv.py"
FEATURE_SCRIPT = REPO_ROOT / "pipelines" / "build_baseline_features.py"
ASOF_DATE = date(2026, 6, 24)


def business_days_ending(end_date: date, count: int) -> list[date]:
    dates = []
    current = end_date
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
    return list(reversed(dates))


def write_price_history(csv_path: Path, *, rows: int = 253, start_price: int = 100) -> None:
    lines = ["ticker,date,open,high,low,close,adj_close,volume"]
    for index, observation_date in enumerate(business_days_ending(ASOF_DATE, rows)):
        price = start_price + index
        lines.append(
            f"MSFT,{observation_date.isoformat()},{price},{price},{price},{price},{price},1000000"
        )
    csv_path.write_text("\n".join(lines), encoding="utf-8")


def run_command(args, *, cwd=REPO_ROOT):
    return subprocess.run(
        [sys.executable, *[str(arg) for arg in args]],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )


def snapshot_id_from_stdout(stdout: str) -> str:
    for part in stdout.split():
        if part.startswith("snapshot_id="):
            return part.partition("=")[2]
    raise AssertionError(f"snapshot_id not found in stdout: {stdout}")


def test_build_baseline_features_stores_audited_feature_rows(tmp_path):
    csv_path = tmp_path / "msft_prices.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(csv_path)

    ingest_result = run_command(
        [
            INGEST_SCRIPT,
            csv_path,
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            raw_dir,
        ]
    )
    assert ingest_result.returncode == 0, ingest_result.stderr

    build_result = run_command(
        [
            FEATURE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
        ]
    )
    assert build_result.returncode == 0, build_result.stderr

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                select
                  fs.feature_set_id,
                  fs.name,
                  fs.version,
                  fs.asof_date,
                  fs.source_snapshot_id as feature_set_snapshot_id,
                  f.feature_name,
                  f.available_at,
                  f.source_snapshot_id,
                  f.source_hash,
                  ss.hash as snapshot_hash
                from features f
                join feature_sets fs on fs.feature_set_id = f.feature_set_id
                join source_snapshots ss on ss.snapshot_id = f.source_snapshot_id
                order by f.feature_name
                """
            )
        ).all()

    assert len(rows) == 4
    feature_names = {row._mapping["feature_name"] for row in rows}
    assert feature_names == {
        "momentum_6_1",
        "momentum_12_1",
        "return_21d",
        "volatility_126d",
    }
    for row in rows:
        values = row._mapping
        assert values["feature_set_id"].startswith("baseline_features_v0.1_MSFT_")
        assert values["name"] == "baseline_features"
        assert values["version"] == "v0.1"
        assert values["asof_date"] == ASOF_DATE.isoformat()
        assert values["available_at"].startswith(ASOF_DATE.isoformat())
        assert values["source_snapshot_id"] == values["feature_set_snapshot_id"]
        assert values["source_hash"] == values["snapshot_hash"]


def test_build_baseline_features_stores_correct_momentum_values(tmp_path):
    csv_path = tmp_path / "msft_prices.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(csv_path)

    ingest_result = run_command(
        [
            INGEST_SCRIPT,
            csv_path,
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            raw_dir,
        ]
    )
    assert ingest_result.returncode == 0, ingest_result.stderr

    build_result = run_command(
        [
            FEATURE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
        ]
    )
    assert build_result.returncode == 0, build_result.stderr

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as conn:
        feature_values = {
            row._mapping["feature_name"]: Decimal(str(row._mapping["value"]))
            for row in conn.execute(
                text("select feature_name, value from features")
            ).all()
        }

    stored_precision = Decimal("0.0000000001")
    expected_momentum_6_1 = (
        (Decimal("331") / Decimal("226")) - Decimal("1")
    ).quantize(stored_precision)
    expected_momentum_12_1 = (
        (Decimal("331") / Decimal("100")) - Decimal("1")
    ).quantize(stored_precision)

    assert feature_values["momentum_6_1"].quantize(stored_precision) == expected_momentum_6_1
    assert feature_values["momentum_12_1"].quantize(stored_precision) == expected_momentum_12_1


def test_build_baseline_features_skips_existing_feature_set_on_rerun(tmp_path):
    csv_path = tmp_path / "msft_prices.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(csv_path)

    ingest_result = run_command(
        [
            INGEST_SCRIPT,
            csv_path,
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            raw_dir,
        ]
    )
    assert ingest_result.returncode == 0, ingest_result.stderr

    build_args = [
        FEATURE_SCRIPT,
        "MSFT",
        "--asof-date",
        ASOF_DATE.isoformat(),
        "--database-url",
        f"sqlite+pysqlite:///{db_path}",
    ]
    first_build_result = run_command(build_args)
    assert first_build_result.returncode == 0, first_build_result.stderr

    second_build_result = run_command(build_args)

    assert second_build_result.returncode == 0, second_build_result.stderr
    assert "feature set already exists; skipping" in second_build_result.stdout
    assert "UNIQUE constraint failed" not in second_build_result.stderr

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as conn:
        feature_set_count = conn.execute(text("select count(*) from feature_sets")).scalar_one()
        feature_count = conn.execute(text("select count(*) from features")).scalar_one()

    assert feature_set_count == 1
    assert feature_count == 4


def test_build_baseline_features_selects_latest_snapshot_and_allows_explicit_snapshot(
    tmp_path,
):
    first_csv_path = tmp_path / "msft_prices_first.csv"
    second_csv_path = tmp_path / "msft_prices_second.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(first_csv_path, start_price=100)
    write_price_history(second_csv_path, start_price=200)

    first_ingest_result = run_command(
        [
            INGEST_SCRIPT,
            first_csv_path,
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            raw_dir,
        ]
    )
    assert first_ingest_result.returncode == 0, first_ingest_result.stderr
    first_snapshot_id = snapshot_id_from_stdout(first_ingest_result.stdout)

    second_ingest_result = run_command(
        [
            INGEST_SCRIPT,
            second_csv_path,
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            raw_dir,
        ]
    )
    assert second_ingest_result.returncode == 0, second_ingest_result.stderr
    second_snapshot_id = snapshot_id_from_stdout(second_ingest_result.stdout)

    latest_build_result = run_command(
        [
            FEATURE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--feature-set-id",
            "latest_feature_set",
        ]
    )
    assert latest_build_result.returncode == 0, latest_build_result.stderr
    assert f"source_snapshot_id={second_snapshot_id}" in latest_build_result.stdout

    pinned_build_result = run_command(
        [
            FEATURE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--source-snapshot-id",
            first_snapshot_id,
            "--feature-set-id",
            "first_feature_set",
        ]
    )
    assert pinned_build_result.returncode == 0, pinned_build_result.stderr
    assert f"source_snapshot_id={first_snapshot_id}" in pinned_build_result.stdout

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as conn:
        feature_sets = {
            row._mapping["feature_set_id"]: row._mapping["source_snapshot_id"]
            for row in conn.execute(
                text(
                    """
                    select feature_set_id, source_snapshot_id
                    from feature_sets
                    where feature_set_id in ('latest_feature_set', 'first_feature_set')
                    """
                )
            ).all()
        }
        feature_counts = {
            row._mapping["source_snapshot_id"]: row._mapping["feature_count"]
            for row in conn.execute(
                text(
                    """
                    select source_snapshot_id, count(*) as feature_count
                    from features
                    group by source_snapshot_id
                    """
                )
            ).all()
        }

    assert feature_sets == {
        "latest_feature_set": second_snapshot_id,
        "first_feature_set": first_snapshot_id,
    }
    assert feature_counts[first_snapshot_id] == 4
    assert feature_counts[second_snapshot_id] == 4


def test_build_baseline_features_reports_existing_id_for_different_snapshot(tmp_path):
    first_csv_path = tmp_path / "msft_prices_first.csv"
    second_csv_path = tmp_path / "msft_prices_second.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(first_csv_path, start_price=100)
    write_price_history(second_csv_path, start_price=200)

    first_ingest_result = run_command(
        [
            INGEST_SCRIPT,
            first_csv_path,
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            raw_dir,
        ]
    )
    assert first_ingest_result.returncode == 0, first_ingest_result.stderr
    first_snapshot_id = snapshot_id_from_stdout(first_ingest_result.stdout)

    first_build_result = run_command(
        [
            FEATURE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--source-snapshot-id",
            first_snapshot_id,
        ]
    )
    assert first_build_result.returncode == 0, first_build_result.stderr

    second_ingest_result = run_command(
        [
            INGEST_SCRIPT,
            second_csv_path,
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            raw_dir,
        ]
    )
    assert second_ingest_result.returncode == 0, second_ingest_result.stderr
    second_snapshot_id = snapshot_id_from_stdout(second_ingest_result.stdout)

    second_build_result = run_command(
        [
            FEATURE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
        ]
    )

    assert second_build_result.returncode != 0
    assert "already exists for source snapshot" in second_build_result.stderr
    assert first_snapshot_id in second_build_result.stderr
    assert second_snapshot_id in second_build_result.stderr
    assert "UNIQUE constraint failed" not in second_build_result.stderr
