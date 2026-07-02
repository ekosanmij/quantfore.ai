import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import select

from quantfore_research.backtest import (
    PROTOTYPE_REAL_MODEL_VERSION,
    build_backtest_report,
    load_universe_definition,
    render_backtest_markdown,
    resolve_backtest_dataset,
    run_historical_backtest,
    validate_experiment_namespace,
)
from quantfore_research.backtest.datasets import sha256_file
from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.models import (
    ExperimentRegistry,
    FeatureSet,
    ModelOutcome,
    ModelPrediction,
    Price,
    Security,
    SourceSnapshot,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_UNIVERSE = REPO_ROOT / "config" / "universes" / "us-equity-trial-v0.csv"
REAL_AUDIT = REPO_ROOT / "reports" / "data-audits" / "us-equity-trial-v0.json"
RUNNER = REPO_ROOT / "pipelines" / "run_baseline_backtest.py"


def weekday_dates(start: date, count: int) -> list[date]:
    values = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return values


def write_small_universe(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                (
                    "ticker,company_name,cik,exchange,sector,active_from,"
                    "active_to,is_benchmark,selection_reason"
                ),
                (
                    "MSFT,Microsoft Corporation,0000789019,NASDAQ,"
                    "Information Technology,2020-01-01,2025-12-31,false,test"
                ),
                (
                    "AAPL,Apple Inc.,0000320193,NASDAQ,Information Technology,"
                    "2020-01-01,2025-12-31,false,test"
                ),
                (
                    "SPY,SPDR S&P 500 ETF Trust,0000884394,NYSE,Benchmark ETF,"
                    "2020-01-01,2025-12-31,true,benchmark"
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_pass_audit(
    path: Path,
    universe_path: Path,
    source_hashes=("hash-aapl", "hash-msft", "hash-spy"),
) -> None:
    path.write_text(
        json.dumps(
            {
                "dataset_kind": "prototype_real",
                "claims_eligible": False,
                "universe_file_sha256": sha256_file(universe_path),
                "primary_source_snapshots": [
                    {"sha256": value} for value in source_hashes
                ],
                "reconciliation": {"decision": "pass"},
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def write_failed_audit(path: Path, universe_path: Path) -> None:
    write_pass_audit(path, universe_path)
    document = json.loads(path.read_text(encoding="utf-8"))
    document["reconciliation"]["decision"] = "fail"
    path.write_text(json.dumps(document), encoding="utf-8")


def seed_real_panel(database_url: str):
    engine = build_engine(database_url=database_url)
    create_schema(engine)
    session_factory = make_session_factory(engine)
    dates = weekday_dates(date(2021, 1, 4), 700)
    with session_scope(session_factory) as session:
        for ticker, start, step in (
            ("AAPL", Decimal("50"), Decimal("0.08")),
            ("MSFT", Decimal("80"), Decimal("0.04")),
            ("SPY", Decimal("200"), Decimal("0.05")),
        ):
            security = Security(
                security_id=f"security-{ticker.lower()}",
                ticker=ticker,
                name=ticker,
            )
            snapshot = SourceSnapshot(
                snapshot_id=f"snapshot-{ticker.lower()}",
                vendor="Tiingo",
                dataset=f"tiingo_eod_prices_{ticker}",
                retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                license_tag="tiingo_internal_research_trial_v0",
                source_hash=f"hash-{ticker.lower()}",
                storage_uri=f"raw/prices/tiingo/{ticker}/panel.json",
            )
            session.add_all([security, snapshot])
            session.flush()
            for index, price_date in enumerate(dates):
                close = start + Decimal(index) * step
                session.add(
                    Price(
                        security_id=security.security_id,
                        date=price_date,
                        open=close,
                        high=close + Decimal("0.10"),
                        low=close - Decimal("0.10"),
                        close=close,
                        adj_close=close,
                        volume=1_000_000 + index,
                        source_snapshot_id=snapshot.snapshot_id,
                    )
                )
    return session_factory


def test_real_universe_hash_and_benchmark_exclusion_are_frozen():
    universe = load_universe_definition(
        REAL_UNIVERSE,
        expected_benchmark="SPY",
    )

    assert universe.sha256 == (
        "0a1ec9667fa4f4378f9c1c6bb010d03585690558069d04286a8320e9d02dd584"
    )
    assert universe.benchmark == "SPY"
    assert len(universe.ranked_tickers) == 25
    assert "SPY" not in universe.ranked_tickers
    assert len(universe.rows) == 26
    assert universe.rows[-1]["is_benchmark"] == "true"


def test_real_dataset_refuses_a_failed_audit(tmp_path):
    audit_path = tmp_path / "failed-audit.json"
    write_failed_audit(audit_path, REAL_UNIVERSE)

    with pytest.raises(ValueError, match="data audit failed"):
        resolve_backtest_dataset(
            dataset_kind="prototype_real",
            benchmark="SPY",
            universe_file=REAL_UNIVERSE,
            audit_file=audit_path,
        )


def test_runner_cli_blocks_failed_real_audit_before_database_access(tmp_path):
    database_path = tmp_path / "should-not-exist.db"
    audit_path = tmp_path / "failed-audit.json"
    write_failed_audit(audit_path, REAL_UNIVERSE)
    result = subprocess.run(
        [
            sys.executable,
            str(RUNNER),
            "--benchmark",
            "SPY",
            "--universe-file",
            str(REAL_UNIVERSE),
            "--dataset-kind",
            "prototype_real",
            "--data-audit-file",
            str(audit_path),
            "--start-date",
            "2020-01-01",
            "--end-date",
            "2025-12-31",
            "--horizon",
            "126d",
            "--frequency",
            "monthly",
            "--experiment-id",
            "real_price_blocked_v0_1",
            "--database-url",
            f"sqlite+pysqlite:///{database_path}",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "data audit failed" in result.stderr
    assert not database_path.exists()


def test_dataset_and_experiment_namespaces_cannot_be_confused(tmp_path):
    universe_path = tmp_path / "universe.csv"
    audit_path = tmp_path / "audit.json"
    write_small_universe(universe_path)
    write_pass_audit(audit_path, universe_path)

    with pytest.raises(ValueError, match="must not provide --universe-file"):
        resolve_backtest_dataset(
            dataset_kind="synthetic",
            benchmark="SPY",
            universe_file=universe_path,
            audit_file=None,
        )
    with pytest.raises(ValueError, match="must start with real_price_"):
        validate_experiment_namespace("baseline_trial", "prototype_real")
    with pytest.raises(ValueError, match="must not start with real_price_"):
        validate_experiment_namespace("real_price_wrong", "synthetic")


def test_real_audit_must_match_the_exact_universe_hash(tmp_path):
    universe_path = tmp_path / "universe.csv"
    audit_path = tmp_path / "audit.json"
    write_small_universe(universe_path)
    write_pass_audit(audit_path, universe_path)
    document = json.loads(audit_path.read_text(encoding="utf-8"))
    document["universe_file_sha256"] = "wrong-hash"
    audit_path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ValueError, match="universe hash does not match"):
        resolve_backtest_dataset(
            dataset_kind="prototype_real",
            benchmark="SPY",
            universe_file=universe_path,
            audit_file=audit_path,
        )


def test_real_runner_uses_separate_model_experiment_and_snapshot_lineage(tmp_path):
    universe_path = tmp_path / "universe.csv"
    audit_path = tmp_path / "audit.json"
    write_small_universe(universe_path)
    write_pass_audit(audit_path, universe_path)
    dataset = resolve_backtest_dataset(
        dataset_kind="prototype_real",
        benchmark="SPY",
        universe_file=universe_path,
        audit_file=audit_path,
    )
    database_url = f"sqlite+pysqlite:///{tmp_path / 'real.db'}"
    session_factory = seed_real_panel(database_url)

    with session_scope(session_factory) as session:
        result = run_historical_backtest(
            session,
            experiment_id="real_price_test_v0_1",
            benchmark_ticker="SPY",
            start_date=date(2022, 1, 1),
            end_date=date(2024, 12, 31),
            horizon="126d",
            frequency="monthly",
            code_commit="test-commit",
            dataset=dataset,
        )
        report = build_backtest_report(session, result=result)

    assert result.dataset_kind == "prototype_real"
    assert result.model_version == PROTOTYPE_REAL_MODEL_VERSION
    assert result.security_tickers == ("AAPL", "MSFT")
    assert "SPY" not in result.security_tickers
    assert result.source_snapshot_ids == (
        "snapshot-aapl",
        "snapshot-msft",
        "snapshot-spy",
    )
    assert result.universe_file_sha256 == sha256_file(universe_path)
    assert result.audit_sha256 == sha256_file(audit_path)
    assert report["configuration"]["dataset_kind"] == "prototype_real"
    assert report["configuration"]["claims_eligible"] is False
    assert report["configuration"]["universe_file_sha256"] == sha256_file(
        universe_path
    )
    assert report["configuration"]["universe_definition"][0]["ticker"] == (
        "MSFT"
    )
    assert report["trial_warnings"] == [
        "PROTOTYPE REAL-DATA TRIAL",
        "NOT POINT-IN-TIME UNIVERSE VALIDATION",
        "NOT ELIGIBLE FOR PERFORMANCE CLAIMS",
    ]
    assert "synthetic_warning" not in report
    markdown = render_backtest_markdown(report)
    assert "# Prototype Real-Data Baseline Trial v0" in markdown
    assert "NOT POINT-IN-TIME UNIVERSE VALIDATION" in markdown

    with session_factory() as session:
        predictions = list(session.scalars(select(ModelPrediction)))
        feature_sets = list(session.scalars(select(FeatureSet)))
        outcomes = list(session.scalars(select(ModelOutcome)))
        experiment = session.get(ExperimentRegistry, "real_price_test_v0_1")

    assert {item.model_version for item in predictions} == {
        PROTOTYPE_REAL_MODEL_VERSION
    }
    assert all(
        item.name == "baseline_features_prototype_real" for item in feature_sets
    )
    assert {
        item.security_price_snapshot_id for item in outcomes
    } == {"snapshot-aapl", "snapshot-msft"}
    assert {
        item.benchmark_price_snapshot_id for item in outcomes
    } == {"snapshot-spy"}
    assert experiment.hypothesis_id.startswith("H6_prototype_real")
    assert experiment.data_snapshot_hash not in {
        "hash-aapl",
        "hash-msft",
        "hash-spy",
    }
    assert experiment.config_json["data_audit"]["decision"] == "pass"
    assert experiment.config_json["universe_definition"] == [
        dict(row) for row in dataset.universe.rows
    ]
