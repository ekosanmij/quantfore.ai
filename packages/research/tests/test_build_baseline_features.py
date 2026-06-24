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


def write_price_history(csv_path: Path, *, rows: int = 253) -> None:
    start_date = ASOF_DATE - timedelta(days=rows - 1)
    lines = ["ticker,date,open,high,low,close,adj_close,volume"]
    for index in range(rows):
        observation_date = start_date + timedelta(days=index)
        price = 100 + index
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
