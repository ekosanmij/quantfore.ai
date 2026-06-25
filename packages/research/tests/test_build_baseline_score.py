import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
INGEST_SCRIPT = REPO_ROOT / "pipelines" / "ingest_prices_csv.py"
FEATURE_SCRIPT = REPO_ROOT / "pipelines" / "build_baseline_features.py"
SCORE_SCRIPT = REPO_ROOT / "pipelines" / "build_baseline_score.py"
ASOF_DATE = date(2026, 6, 24)


def business_days_ending(end_date: date, count: int) -> list[date]:
    dates = []
    current = end_date
    while len(dates) < count:
        if current.weekday() < 5:
            dates.append(current)
        current -= timedelta(days=1)
    return list(reversed(dates))


def write_price_history(csv_path: Path, *, rows: int = 253) -> None:
    lines = ["ticker,date,open,high,low,close,adj_close,volume"]
    for index, observation_date in enumerate(business_days_ending(ASOF_DATE, rows)):
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


def build_baseline_features(db_path: Path, raw_dir: Path, csv_path: Path) -> None:
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

    feature_result = run_command(
        [
            FEATURE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
        ]
    )
    assert feature_result.returncode == 0, feature_result.stderr


def test_build_baseline_score_creates_prediction_and_score_drivers(tmp_path):
    csv_path = tmp_path / "msft_prices.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(csv_path)
    build_baseline_features(db_path, raw_dir, csv_path)

    score_result = run_command(
        [
            SCORE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
        ]
    )

    assert score_result.returncode == 0, score_result.stderr
    assert "stored baseline prediction" in score_result.stdout
    assert "feature_set_id=baseline_features_v0.1_MSFT_2026-06-24" in score_result.stdout

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as conn:
        prediction = conn.execute(
            text(
                """
                select
                  prediction_id,
                  model_version,
                  asof_date,
                  horizon,
                  score,
                  confidence,
                  action_label,
                  immutable_hash
                from model_predictions
                """
            )
        ).one()
        drivers = conn.execute(
            text(
                """
                select driver_name, contribution, evidence_uri
                from score_drivers
                where prediction_id = :prediction_id
                order by driver_name
                """
            ),
            {"prediction_id": prediction._mapping["prediction_id"]},
        ).all()

    prediction_values = prediction._mapping
    assert prediction_values["model_version"] == "baseline_v0.1"
    assert prediction_values["asof_date"] == ASOF_DATE.isoformat()
    assert prediction_values["horizon"] == "unspecified"
    assert 0 <= prediction_values["score"] <= 100
    assert prediction_values["confidence"] is not None
    assert prediction_values["action_label"] in {
        "watch_positive",
        "favourable_setup",
        "neutral",
        "watch_negative",
        "thesis_risk_review",
    }
    assert prediction_values["immutable_hash"]
    assert {driver._mapping["driver_name"] for driver in drivers} == {
        "momentum_6_1",
        "momentum_12_1",
        "return_21d",
        "volatility_126d",
    }
    assert {
        driver._mapping["evidence_uri"]
        for driver in drivers
    } == {
        "feature:momentum_6_1",
        "feature:momentum_12_1",
        "feature:return_21d",
        "feature:volatility_126d",
    }


def test_build_baseline_score_rerun_skips_existing_prediction(tmp_path):
    csv_path = tmp_path / "msft_prices.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(csv_path)
    build_baseline_features(db_path, raw_dir, csv_path)

    score_args = [
        SCORE_SCRIPT,
        "MSFT",
        "--asof-date",
        ASOF_DATE.isoformat(),
        "--database-url",
        f"sqlite+pysqlite:///{db_path}",
    ]
    first_score_result = run_command(score_args)
    assert first_score_result.returncode == 0, first_score_result.stderr

    second_score_result = run_command(score_args)

    assert second_score_result.returncode == 0, second_score_result.stderr
    assert "prediction already exists; skipping" in second_score_result.stdout
    assert "UNIQUE constraint failed" not in second_score_result.stderr

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as conn:
        prediction_count = conn.execute(
            text("select count(*) from model_predictions")
        ).scalar_one()
        driver_count = conn.execute(text("select count(*) from score_drivers")).scalar_one()

    assert prediction_count == 1
    assert driver_count == 4


def test_build_baseline_score_requires_existing_features(tmp_path):
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

    score_result = run_command(
        [
            SCORE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
        ]
    )

    assert score_result.returncode != 0
    assert "no baseline feature set found for MSFT on 2026-06-24" in score_result.stderr
