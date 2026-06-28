import subprocess
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import create_engine, text


REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPO_ROOT / "pipelines"
INGEST_SCRIPT = REPO_ROOT / "pipelines" / "ingest_prices_csv.py"
FEATURE_SCRIPT = REPO_ROOT / "pipelines" / "build_baseline_features.py"
SCORE_SCRIPT = REPO_ROOT / "pipelines" / "build_baseline_score.py"
ASOF_DATE = date(2026, 6, 24)

if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

from build_baseline_score import immutable_prediction_hash  # noqa: E402
from quantfore_research.scoring import BaselineScore, ScoreDriver  # noqa: E402


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


def sample_score(
    *,
    score: Decimal = Decimal("57.000000"),
    confidence: Decimal = Decimal("0.556000"),
    action_label: str = "neutral",
    drivers=None,
) -> BaselineScore:
    if drivers is None:
        drivers = (
            ScoreDriver("momentum_6_1", Decimal("4.000000"), "feature:momentum_6_1"),
            ScoreDriver("momentum_12_1", Decimal("6.000000"), "feature:momentum_12_1"),
            ScoreDriver("return_21d", Decimal("1.000000"), "feature:return_21d"),
            ScoreDriver("volatility_126d", Decimal("-4.000000"), "feature:volatility_126d"),
        )
    return BaselineScore(
        score=score,
        confidence=confidence,
        action_label=action_label,
        drivers=tuple(drivers),
    )


def sample_hash_kwargs() -> dict:
    return {
        "model_version": "baseline_v0.1",
        "ticker": "MSFT",
        "security_id": "security-msft",
        "asof_date": ASOF_DATE,
        "horizon": "126d",
        "feature_set_id": "baseline_features_v0.1_MSFT_2026-06-24",
        "score": sample_score(),
    }


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


def test_immutable_prediction_hash_is_deterministic_and_canonicalizes_driver_order():
    hash_kwargs = sample_hash_kwargs()
    expected_hash = immutable_prediction_hash(**hash_kwargs)
    reversed_driver_score = sample_score(drivers=reversed(sample_score().drivers))

    assert len(expected_hash) == 64
    assert immutable_prediction_hash(**hash_kwargs) == expected_hash
    assert immutable_prediction_hash(
        **{**hash_kwargs, "score": reversed_driver_score}
    ) == expected_hash


def test_immutable_prediction_hash_changes_when_prediction_inputs_change():
    base_kwargs = sample_hash_kwargs()
    base_hash = immutable_prediction_hash(**base_kwargs)
    changed_inputs = (
        {"model_version": "baseline_v0.2"},
        {"ticker": "AAPL"},
        {"security_id": "security-aapl"},
        {"asof_date": date(2026, 6, 25)},
        {"horizon": "3m"},
        {"feature_set_id": "other_feature_set"},
        {"score": sample_score(score=Decimal("58.000000"))},
        {"score": sample_score(confidence=Decimal("0.700000"))},
        {"score": sample_score(action_label="favourable_setup")},
        {
            "score": sample_score(
                drivers=(
                    ScoreDriver("momentum_6_1", Decimal("5.000000"), "feature:momentum_6_1"),
                    ScoreDriver("momentum_12_1", Decimal("6.000000"), "feature:momentum_12_1"),
                    ScoreDriver("return_21d", Decimal("1.000000"), "feature:return_21d"),
                    ScoreDriver("volatility_126d", Decimal("-4.000000"), "feature:volatility_126d"),
                )
            )
        },
    )

    for changed_input in changed_inputs:
        assert immutable_prediction_hash(**{**base_kwargs, **changed_input}) != base_hash


def ingest_prices(db_path: Path, raw_dir: Path, csv_path: Path) -> None:
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


def build_features_with_id(db_path: Path, feature_set_id: str) -> None:
    feature_result = run_command(
        [
            FEATURE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--feature-set-id",
            feature_set_id,
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
        security_id = conn.execute(
            text("select security_id from securities where ticker = 'MSFT'")
        ).scalar_one()
        prediction = conn.execute(
            text(
                """
                select
                  prediction_id,
                  model_version,
                  feature_set_id,
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
    stored_drivers = tuple(
        ScoreDriver(
            driver._mapping["driver_name"],
            Decimal(str(driver._mapping["contribution"])),
            driver._mapping["evidence_uri"],
        )
        for driver in drivers
    )
    recomputed_hash = immutable_prediction_hash(
        model_version=prediction_values["model_version"],
        ticker="MSFT",
        security_id=security_id,
        asof_date=ASOF_DATE,
        horizon=prediction_values["horizon"],
        feature_set_id=prediction_values["feature_set_id"],
        score=BaselineScore(
            score=Decimal(str(prediction_values["score"])),
            confidence=Decimal(str(prediction_values["confidence"])),
            action_label=prediction_values["action_label"],
            drivers=stored_drivers,
        ),
    )

    assert prediction_values["model_version"] == "baseline_v0.1"
    assert prediction_values["feature_set_id"] == "baseline_features_v0.1_MSFT_2026-06-24"
    assert prediction_values["asof_date"] == ASOF_DATE.isoformat()
    assert prediction_values["horizon"] == "126d"
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
    assert prediction_values["immutable_hash"] == recomputed_hash
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


def test_build_baseline_score_uses_explicit_feature_set_id(tmp_path):
    first_csv_path = tmp_path / "msft_prices_first.csv"
    second_csv_path = tmp_path / "msft_prices_second.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(first_csv_path, start_price=100)
    write_price_history(second_csv_path, start_price=200)
    ingest_prices(db_path, raw_dir, first_csv_path)
    build_features_with_id(db_path, "first_feature_set")
    ingest_prices(db_path, raw_dir, second_csv_path)
    build_features_with_id(db_path, "latest_feature_set")

    score_result = run_command(
        [
            SCORE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--feature-set-id",
            "first_feature_set",
        ]
    )

    assert score_result.returncode == 0, score_result.stderr
    assert "feature_set_id=first_feature_set" in score_result.stdout

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as conn:
        prediction = conn.execute(
            text("select feature_set_id, immutable_hash from model_predictions")
        ).one()
        driver_count = conn.execute(text("select count(*) from score_drivers")).scalar_one()

    assert prediction._mapping["feature_set_id"] == "first_feature_set"
    assert prediction._mapping["immutable_hash"]
    assert driver_count == 4


def test_build_baseline_score_explicit_feature_set_rerun_skips_when_hash_matches(tmp_path):
    csv_path = tmp_path / "msft_prices.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    feature_set_id = "baseline_features_v0.1_MSFT_2026-06-24"
    write_price_history(csv_path)
    build_baseline_features(db_path, raw_dir, csv_path)

    score_args = [
        SCORE_SCRIPT,
        "MSFT",
        "--asof-date",
        ASOF_DATE.isoformat(),
        "--database-url",
        f"sqlite+pysqlite:///{db_path}",
        "--feature-set-id",
        feature_set_id,
    ]
    first_score_result = run_command(score_args)
    second_score_result = run_command(score_args)

    assert first_score_result.returncode == 0, first_score_result.stderr
    assert second_score_result.returncode == 0, second_score_result.stderr
    assert "prediction already exists; skipping" in second_score_result.stdout
    assert f"feature_set_id={feature_set_id}" in second_score_result.stdout

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.connect() as conn:
        prediction_count = conn.execute(
            text("select count(*) from model_predictions")
        ).scalar_one()
        driver_count = conn.execute(text("select count(*) from score_drivers")).scalar_one()

    assert prediction_count == 1
    assert driver_count == 4


def test_build_baseline_score_rejects_explicit_feature_set_mismatch(tmp_path):
    first_csv_path = tmp_path / "msft_prices_first.csv"
    second_csv_path = tmp_path / "msft_prices_second.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(first_csv_path, start_price=100)
    write_price_history(second_csv_path, start_price=200)
    ingest_prices(db_path, raw_dir, first_csv_path)
    build_features_with_id(db_path, "first_feature_set")
    ingest_prices(db_path, raw_dir, second_csv_path)
    build_features_with_id(db_path, "latest_feature_set")

    first_score_result = run_command(
        [
            SCORE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--feature-set-id",
            "first_feature_set",
        ]
    )
    mismatch_result = run_command(
        [
            SCORE_SCRIPT,
            "MSFT",
            "--asof-date",
            ASOF_DATE.isoformat(),
            "--database-url",
            f"sqlite+pysqlite:///{db_path}",
            "--feature-set-id",
            "latest_feature_set",
        ]
    )

    assert first_score_result.returncode == 0, first_score_result.stderr
    assert mismatch_result.returncode != 0
    assert "prediction already exists but does not match requested" in mismatch_result.stderr
    assert "feature_set_id=latest_feature_set" in mismatch_result.stderr
    assert "refusing to skip" in mismatch_result.stderr

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


def test_build_baseline_score_fails_clearly_when_feature_set_is_incomplete(tmp_path):
    csv_path = tmp_path / "msft_prices.csv"
    db_path = tmp_path / "research.db"
    raw_dir = tmp_path / "data" / "raw"
    write_price_history(csv_path)
    build_baseline_features(db_path, raw_dir, csv_path)

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text("delete from features where feature_name = 'volatility_126d'")
        )

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
    assert (
        "feature set baseline_features_v0.1_MSFT_2026-06-24 "
        "missing score features: volatility_126d"
    ) in score_result.stderr


def test_build_baseline_score_rejects_unknown_feature_set_id(tmp_path):
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
            "--feature-set-id",
            "missing_feature_set",
        ]
    )

    assert score_result.returncode != 0
    assert "unknown feature set: missing_feature_set" in score_result.stderr
