import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "pipelines" / "ingest_prices_csv.py"


def run_ingest_prices_csv(csv_path: Path, db_path: Path, raw_dir: Path):
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT_PATH),
            str(csv_path),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--raw-dir",
            str(raw_dir),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_ingest_prices_csv_inserts_multiple_rows_and_freezes_source(tmp_path):
    csv_path = tmp_path / "msft_prices.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    csv_path.write_text(
        "\n".join(
            [
                "ticker,date,open,high,low,close,adj_close,volume",
                "MSFT,2026-06-22,486.00,491.00,484.00,490.00,490.00,19000000",
                "MSFT,2026-06-23,490.00,493.00,489.00,492.00,492.00,20000000",
                "MSFT,2026-06-24,492.00,496.00,491.00,495.00,495.00,21000000",
            ]
        ),
        encoding="utf-8",
    )

    result = run_ingest_prices_csv(csv_path, db_path, raw_dir)

    assert result.returncode == 0, result.stderr
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as conn:
        price_rows = conn.execute(
            text(
                """
                select s.ticker, p.date, p.adj_close, p.volume, ss.storage_uri, ss.hash
                from prices p
                join securities s on s.security_id = p.security_id
                join source_snapshots ss on ss.snapshot_id = p.source_snapshot_id
                order by p.date
                """
            )
        ).all()
        snapshot_count = conn.execute(text("select count(*) from source_snapshots")).scalar_one()

    assert snapshot_count == 1
    assert len(price_rows) == 3
    assert {row._mapping["ticker"] for row in price_rows} == {"MSFT"}
    assert price_rows[-1]._mapping["adj_close"] == 495
    storage_uri = price_rows[0]._mapping["storage_uri"]
    assert storage_uri.startswith("raw/prices/csv/msft_prices/")
    assert price_rows[0]._mapping["hash"]
    frozen_csv = raw_dir.parent / storage_uri
    assert frozen_csv.exists()
    assert frozen_csv.read_text(encoding="utf-8") == csv_path.read_text(encoding="utf-8")


def test_ingest_prices_csv_rejects_missing_required_fields(tmp_path):
    csv_path = tmp_path / "bad_prices.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    csv_path.write_text(
        "\n".join(
            [
                "ticker,date,open,high,low,close,adj_close,volume",
                "MSFT,2026-06-22,486.00,491.00,484.00,490.00,490.00,19000000",
                "MSFT,2026-06-23,490.00,493.00,489.00,492.00,,20000000",
            ]
        ),
        encoding="utf-8",
    )

    result = run_ingest_prices_csv(csv_path, db_path, raw_dir)

    assert result.returncode != 0
    assert "adj_close is required" in result.stderr
    assert not db_path.exists()
    assert not raw_dir.exists()
