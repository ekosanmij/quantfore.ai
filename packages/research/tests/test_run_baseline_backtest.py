import json
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select

from quantfore_research.backtest import (
    BACKTEST_CONTRACT,
    BASELINE_HYPOTHESIS,
    BASELINE_HYPOTHESIS_ID,
    discover_eligible_prediction_dates,
    register_backtest_experiment,
    run_historical_backtest,
)
from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.models import (
    Feature,
    FeatureSet,
    ExperimentRegistry,
    ModelOutcome,
    ModelPrediction,
    Price,
    ScoreDriver,
    Security,
    SourceSnapshot,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER_PATH = REPO_ROOT / "pipelines" / "run_baseline_backtest.py"


def weekday_dates(start: date, count: int) -> list[date]:
    values = []
    current = start
    while len(values) < count:
        if current.weekday() < 5:
            values.append(current)
        current += timedelta(days=1)
    return values


def create_panel_database(tmp_path):
    database_url = f"sqlite+pysqlite:///{tmp_path / 'backtest.db'}"
    engine = build_engine(database_url=database_url)
    create_schema(engine)
    session_factory = make_session_factory(engine)
    dates = weekday_dates(date(2021, 1, 4), 700)

    with session_scope(session_factory) as session:
        snapshot = SourceSnapshot(
            snapshot_id="snapshot-panel",
            vendor="synthetic",
            dataset="test_backtest_panel",
            retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            license_tag="internal_sample",
            source_hash="panel-source-hash",
            storage_uri="raw/test/backtest-panel.csv",
        )
        session.add(snapshot)
        securities = {
            ticker: Security(
                security_id=f"security-{ticker.lower()}",
                ticker=ticker,
                name=ticker,
            )
            for ticker in ("QF01", "QF02", "QF03", "SPY")
        }
        session.add_all(securities.values())
        session.flush()

        for ticker, security in securities.items():
            ticker_dates = dates if ticker != "QF03" else dates[-200:]
            start_price = {
                "QF01": Decimal("50"),
                "QF02": Decimal("80"),
                "QF03": Decimal("30"),
                "SPY": Decimal("200"),
            }[ticker]
            daily_step = {
                "QF01": Decimal("0.08"),
                "QF02": Decimal("0.04"),
                "QF03": Decimal("0.03"),
                "SPY": Decimal("0.05"),
            }[ticker]
            for index, price_date in enumerate(ticker_dates):
                close = start_price + (Decimal(index) * daily_step)
                session.add(
                    Price(
                        price_id=f"price-{ticker.lower()}-{index:04d}",
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

    return session_factory, dates


def run_backtest(session_factory):
    with session_scope(session_factory) as session:
        return run_historical_backtest(
            session,
            experiment_id="test_baseline_v0_1",
            benchmark_ticker="SPY",
            start_date=date(2022, 1, 1),
            end_date=date(2024, 12, 31),
            horizon="126d",
            frequency="monthly",
            source_snapshot_id="snapshot-panel",
            code_commit="test-commit",
        )


def test_discovers_only_month_ends_with_complete_history_and_future_prices(tmp_path):
    session_factory, dates = create_panel_database(tmp_path)
    with session_factory() as session:
        spy = session.scalar(select(Security).where(Security.ticker == "SPY"))
        prices = tuple(
            session.scalars(
                select(Price)
                .where(Price.security_id == spy.security_id)
                .order_by(Price.date)
            )
        )

    prediction_dates = discover_eligible_prediction_dates(
        prices,
        start_date=date(2022, 1, 1),
        end_date=date(2024, 12, 31),
    )

    assert len(prediction_dates) >= BACKTEST_CONTRACT.minimum_test_periods
    assert all(
        value == max(item for item in dates if (item.year, item.month) == (value.year, value.month))
        for value in prediction_dates
    )
    for value in prediction_dates:
        position = dates.index(value)
        assert position + 1 >= 253
        assert len(dates) - position - 1 >= 127


def test_historical_runner_stores_audited_predictions_outcomes_and_lineage(tmp_path):
    session_factory, _ = create_panel_database(tmp_path)

    result = run_backtest(session_factory)

    expected_complete_observations = len(result.prediction_dates) * 2
    assert result.security_tickers == ("QF01", "QF02", "QF03")
    assert "SPY" not in result.security_tickers
    assert len(result.prediction_ids) == expected_complete_observations
    assert len(result.outcome_hashes) == expected_complete_observations
    assert result.created_predictions == expected_complete_observations
    assert result.existing_predictions == 0
    assert result.created_outcomes == expected_complete_observations
    assert result.existing_outcomes == 0
    assert result.source_snapshot_ids == ("snapshot-panel",)
    assert len(result.skipped_observations) == len(result.prediction_dates)
    assert all(value.ticker == "QF03" for value in result.skipped_observations)
    assert all(value.stage == "features" for value in result.skipped_observations)

    manifest = result.to_manifest()
    assert manifest["prediction_ids"] == sorted(result.prediction_ids)
    assert manifest["outcome_hashes"] == sorted(result.outcome_hashes)
    assert manifest["source_snapshot_ids"] == ["snapshot-panel"]

    with session_factory() as session:
        stored_prediction_ids = tuple(
            sorted(session.scalars(select(ModelPrediction.prediction_id)).all())
        )
        stored_outcome_hashes = tuple(
            sorted(session.scalars(select(ModelOutcome.immutable_hash)).all())
        )
        feature_set_count = session.scalar(select(func.count()).select_from(FeatureSet))
        feature_count = session.scalar(select(func.count()).select_from(Feature))
        driver_count = session.scalar(select(func.count()).select_from(ScoreDriver))
        latest_feature_date = session.scalar(select(func.max(Feature.asof_date)))
        experiment = session.get(ExperimentRegistry, "test_baseline_v0_1")

    assert result.prediction_ids == stored_prediction_ids
    assert result.outcome_hashes == stored_outcome_hashes
    assert feature_set_count == expected_complete_observations
    assert feature_count == expected_complete_observations * 4
    assert driver_count == expected_complete_observations * 4
    assert latest_feature_date == max(result.prediction_dates)
    assert experiment.hypothesis_id == BASELINE_HYPOTHESIS_ID
    assert experiment.data_snapshot_hash == "panel-source-hash"
    assert experiment.code_commit == "test-commit"
    assert experiment.result_uri == (
        "reports/backtests/test_baseline_v0_1.json"
    )
    assert experiment.config_json == {
        "hypothesis": BASELINE_HYPOTHESIS,
        "model_version": "baseline_v0.1",
        "feature_version": "v0.1",
        "universe": ["QF01", "QF02", "QF03"],
        "benchmark": "SPY",
        "horizon": "126d",
        "date_range": {"start": "2022-01-01", "end": "2024-12-31"},
        "frequency": "monthly",
        "number_of_securities": 3,
        "number_of_periods": len(result.prediction_dates),
        "claims_eligible": False,
        "dataset_kind": "synthetic",
    }
    assert "not validation evidence" in experiment.notes


def test_historical_runner_is_idempotent_on_rerun(tmp_path):
    session_factory, _ = create_panel_database(tmp_path)
    first = run_backtest(session_factory)

    second = run_backtest(session_factory)

    assert second.prediction_ids == first.prediction_ids
    assert second.outcome_hashes == first.outcome_hashes
    assert second.source_snapshot_ids == first.source_snapshot_ids
    assert second.skipped_observations == first.skipped_observations
    assert second.to_manifest() == first.to_manifest()
    assert second.created_predictions == 0
    assert second.existing_predictions == len(first.prediction_ids)
    assert second.created_outcomes == 0
    assert second.existing_outcomes == len(first.outcome_hashes)

    with session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelPrediction)) == len(
            first.prediction_ids
        )
        assert session.scalar(select(func.count()).select_from(ModelOutcome)) == len(
            first.outcome_hashes
        )
        assert session.scalar(
            select(func.count()).select_from(ExperimentRegistry)
        ) == 1


def test_experiment_registration_refuses_conflicting_rerun(tmp_path):
    session_factory, _ = create_panel_database(tmp_path)
    result = run_backtest(session_factory)

    with session_scope(session_factory) as session:
        snapshot = session.get(SourceSnapshot, "snapshot-panel")
        with pytest.raises(ValueError, match="conflicting experiment registration"):
            register_backtest_experiment(
                session,
                result=result,
                source_snapshot=snapshot,
                start_date=date(2022, 1, 1),
                end_date=date(2024, 12, 31),
                horizon="126d",
                frequency="monthly",
                model_version="baseline_v0.1",
                code_commit="different-commit",
                result_uri="reports/backtests/test_baseline_v0_1.json",
            )


def test_historical_runner_rejects_non_contract_configuration(tmp_path):
    session_factory, _ = create_panel_database(tmp_path)
    with session_factory() as session:
        try:
            run_historical_backtest(
                session,
                experiment_id="bad-config",
                benchmark_ticker="SPY",
                start_date=date(2022, 1, 1),
                end_date=date(2024, 12, 31),
                horizon="21d",
                frequency="monthly",
                source_snapshot_id="snapshot-panel",
            )
        except ValueError as exc:
            assert str(exc) == "Sprint 5 horizon must be 126d; found 21d"
        else:
            raise AssertionError("non-contract horizon did not fail")


def test_runner_cli_writes_complete_deterministic_reports(tmp_path):
    create_panel_database(tmp_path)
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    command = [
        sys.executable,
        str(RUNNER_PATH),
        "--database-url",
        f"sqlite+pysqlite:///{tmp_path / 'backtest.db'}",
        "--benchmark",
        "SPY",
        "--start-date",
        "2022-01-01",
        "--end-date",
        "2024-12-31",
        "--horizon",
        "126d",
        "--frequency",
        "monthly",
        "--experiment-id",
        "test_baseline_v0_1",
        "--source-snapshot-id",
        "snapshot-panel",
        "--json-output",
        str(json_path),
        "--markdown-output",
        str(markdown_path),
    ]

    result = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "backtest complete experiment=test_baseline_v0_1" in result.stdout
    assert f"json_report={json_path}" in result.stdout
    assert f"markdown_report={markdown_path}" in result.stdout
    assert "SYNTHETIC ENGINEERING DATA - NOT VALIDATION EVIDENCE" in result.stdout
    report = json.loads(json_path.read_text(encoding="utf-8"))
    lineage = report["lineage"]
    assert lineage["prediction_ids"] == sorted(lineage["prediction_ids"])
    assert lineage["outcome_hashes"] == sorted(lineage["outcome_hashes"])
    assert len(lineage["prediction_ids"]) == len(lineage["outcome_hashes"])
    assert lineage["source_snapshot_ids"] == ["snapshot-panel"]
    assert report["configuration"]["universe"] == ["QF01", "QF02", "QF03"]
    assert report["synthetic_warning"] == (
        "SYNTHETIC ENGINEERING DATA - NOT VALIDATION EVIDENCE"
    )
    assert report["observation_counts"]["eligible"] == len(
        lineage["prediction_ids"]
    )
    assert len(report["rank_ic_by_month"]) >= 12
    assert tuple(report["top_quintile_cost_sensitivity"]) == (
        "0_bps",
        "10_bps",
        "25_bps",
    )
    assert report["top_quintile_cost_sensitivity"]["0_bps"][
        "average_net_excess_return"
    ] == report["quintile_returns"]["5"]
    markdown = markdown_path.read_text(encoding="utf-8")
    for heading in (
        "## Configuration",
        "## Dataset and Snapshot Lineage",
        "## Observation Counts and Coverage",
        "## Rank IC by Month",
        "## Rank IC Summary",
        "## Quintile Returns",
        "## Top-minus-Bottom Quintile Spread",
        "## Top-Quintile Benchmark Hit Rate",
        "## Top-Quintile Cost Sensitivity",
        "## Label Distribution",
        "## Failed or Skipped Observations",
        "## Known Limitations",
        "## Synthetic-Data Warning",
    ):
        assert heading in markdown
    first_json = json_path.read_bytes()
    first_markdown = markdown_path.read_bytes()
    rerun = subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert rerun.returncode == 0, rerun.stderr
    assert json_path.read_bytes() == first_json
    assert markdown_path.read_bytes() == first_markdown
    engine = build_engine(
        database_url=f"sqlite+pysqlite:///{tmp_path / 'backtest.db'}"
    )
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        experiment = session.get(ExperimentRegistry, "test_baseline_v0_1")
    assert experiment.result_uri == json_path.as_posix()
    assert experiment.data_snapshot_hash == "panel-source-hash"
    assert experiment.config_json["claims_eligible"] is False
    assert experiment.config_json["dataset_kind"] == "synthetic"
