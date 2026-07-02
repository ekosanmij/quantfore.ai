import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from pipelines.compare_static_vs_point_in_time import main as comparison_main
from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.evaluation.comparative import (
    ComparativeObservation,
    UniverseCohort,
    build_comparative_evidence,
)
from quantfore_research.models import (
    FeatureSet,
    ModelOutcome,
    ModelPrediction,
    Security,
    SourceSnapshot,
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _database_url(path) -> str:
    return f"sqlite+pysqlite:///{path}"


def _seed_run(path, *, prefix: str, tickers: tuple[str, ...], adverse: bool):
    engine = build_engine(database_url=_database_url(path))
    create_schema(engine)
    factory = make_session_factory(engine)
    prediction_ids = []
    outcome_hashes = []
    by_date = {}
    snapshot_id = f"snap-{prefix}"
    benchmark_id = f"sec-{prefix}-spy"
    with session_scope(factory) as session:
        session.add(
            SourceSnapshot(
                snapshot_id=snapshot_id,
                vendor="Test Vendor",
                dataset=f"{prefix}-comparison",
                retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                license_tag="test",
                source_hash=_hash(f"snapshot-{prefix}"),
                storage_uri=f"raw/test/{prefix}-comparison.json",
            )
        )
        session.add(
            Security(
                security_id=benchmark_id,
                ticker="SPY",
                name="Benchmark",
                sector="Benchmark ETF",
            )
        )
        securities = {}
        for index, ticker in enumerate(tickers):
            security = Security(
                security_id=f"sec-{prefix}-{index}",
                ticker=ticker,
                name=ticker,
                sector="Technology" if index < 5 else "Industrials",
            )
            securities[ticker] = security
            session.add(security)
        session.flush()
        for month in range(1, 8):
            prediction_date = date(2024, month, 28)
            feature_set_id = f"fs-{prefix}-{month}"
            session.add(
                FeatureSet(
                    feature_set_id=feature_set_id,
                    name="baseline",
                    version="v0.1",
                    asof_date=prediction_date,
                    config_json={},
                    source_snapshot_id=snapshot_id,
                    code_commit="test",
                )
            )
            evaluations = []
            for index, ticker in enumerate(tickers):
                prediction_id = f"pred-{prefix}-{month}-{index}"
                outcome_id = f"out-{prefix}-{month}-{index}"
                prediction_hash = _hash(prediction_id)
                outcome_hash = _hash(outcome_id)
                score = Decimal(index)
                excess_return = Decimal(index - 4) / Decimal("100")
                if adverse:
                    excess_return = -excess_return
                benchmark_return = Decimal("-0.02" if month % 2 == 0 else "0.01")
                prediction = ModelPrediction(
                    prediction_id=prediction_id,
                    model_version=f"baseline_{prefix}_v0.1",
                    security_id=securities[ticker].security_id,
                    feature_set_id=feature_set_id,
                    asof_date=prediction_date,
                    horizon="126d",
                    score=score,
                    confidence=Decimal("0.5"),
                    action_label="Hold",
                    immutable_hash=prediction_hash,
                )
                session.add(prediction)
                session.flush()
                session.add(
                    ModelOutcome(
                        outcome_id=outcome_id,
                        prediction_id=prediction_id,
                        benchmark_security_id=benchmark_id,
                        security_price_snapshot_id=snapshot_id,
                        benchmark_price_snapshot_id=snapshot_id,
                        entry_date=date(2024, month, 20),
                        exit_date=date(2024, month, 21),
                        security_entry_price=Decimal("100"),
                        security_exit_price=Decimal("101"),
                        benchmark_entry_price=Decimal("100"),
                        benchmark_exit_price=Decimal("101"),
                        realised_return=benchmark_return + excess_return,
                        benchmark_return=benchmark_return,
                        excess_return=excess_return,
                        max_drawdown=Decimal("-0.01") * Decimal(index + 1),
                        evaluated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                        immutable_hash=outcome_hash,
                    )
                )
                prediction_ids.append(prediction_id)
                outcome_hashes.append(outcome_hash)
                evaluations.append(
                    {
                        "prediction_date": prediction_date.isoformat(),
                        "security_id": securities[ticker].security_id,
                        "ticker": ticker,
                        "prediction_id": prediction_id,
                        "outcome_kind": (
                            "delisting"
                            if adverse and month == 7 and index == 0
                            else "standard"
                        ),
                    }
                )
            by_date[prediction_date.isoformat()] = evaluations
    engine.dispose()
    return prediction_ids, outcome_hashes, by_date


def _write_inputs(tmp_path):
    static_tickers = tuple(f"T{index}" for index in range(10))
    pit_tickers = tuple(f"T{index}" for index in range(1, 11))
    static_db = tmp_path / "static.db"
    pit_db = tmp_path / "pit.db"
    static_ids, static_hashes, _ = _seed_run(
        static_db, prefix="static", tickers=static_tickers, adverse=False
    )
    pit_ids, pit_hashes, pit_by_date = _seed_run(
        pit_db, prefix="pit", tickers=pit_tickers, adverse=True
    )
    static_report = {
        "schema_version": "real_price_baseline_trial_v1",
        "configuration": {
            "claims_eligible": False,
            "dataset_kind": "prototype_real",
            "experiment_id": "sprint6-static",
            "model_version": "baseline_static_v0.1",
            "feature_version": "v0.1",
            "horizon": "126d",
            "frequency": "monthly",
            "benchmark": "SPY",
            "universe": list(static_tickers),
        },
    }
    static_lineage = {
        "dataset_kind": "prototype_real",
        "experiment_id": "sprint6-static",
        "model_version": "baseline_static_v0.1",
        "benchmark": "SPY",
        "prediction_ids": static_ids,
        "outcome_hashes": static_hashes,
    }
    cohorts = []
    for prediction_date, evaluations in sorted(pit_by_date.items()):
        security_ids = [row["security_id"] for row in evaluations]
        cohorts.append(
            {
                "prediction_date": prediction_date,
                "expected_security_ids": security_ids,
                "feature_security_ids": security_ids,
                "evaluated_security_ids": security_ids,
                "evaluations": evaluations,
                "exclusions": [],
            }
        )
    pit_lineage = {
        "dataset_kind": "point_in_time",
        "experiment_id": "sprint7-pit",
        "model_version": "baseline_pit_v0.1",
        "benchmark_ticker": "SPY",
        "prediction_ids": pit_ids,
        "prediction_count": len(pit_ids),
        "outcome_hashes": pit_hashes,
        "outcome_count": len(pit_hashes),
        "coverage_gate_passed": True,
        "cohorts": cohorts,
    }
    pit_report = {
        "schema_version": "pit_dynamic_universe_baseline_v1",
        "claims_eligible": False,
        "configuration": {
            "dataset_kind": "point_in_time",
            "experiment_id": "sprint7-pit",
            "model_version": "baseline_pit_v0.1",
            "feature_version": "v0.1",
            "horizon": "126d",
            "frequency": "monthly",
        },
        "coverage_gate_passed": True,
        "cohorts": cohorts,
        "manifest": pit_lineage,
    }
    paths = {}
    for name, document in (
        ("static_report", static_report),
        ("static_lineage", static_lineage),
        ("pit_report", pit_report),
        ("pit_lineage", pit_lineage),
    ):
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
        paths[name] = path
    return static_db, pit_db, paths


def test_comparative_report_is_database_backed_complete_and_reproducible(tmp_path):
    static_db, pit_db, paths = _write_inputs(tmp_path)
    output = tmp_path / "comparison.json"
    markdown = tmp_path / "comparison.md"
    args = [
        "--static-database-url",
        _database_url(static_db),
        "--pit-database-url",
        _database_url(pit_db),
        "--static-report",
        str(paths["static_report"]),
        "--static-lineage",
        str(paths["static_lineage"]),
        "--pit-report",
        str(paths["pit_report"]),
        "--pit-lineage",
        str(paths["pit_lineage"]),
        "--json-output",
        str(output),
        "--markdown-output",
        str(markdown),
    ]
    assert comparison_main(args) == 0
    first_json = output.read_bytes()
    first_markdown = markdown.read_bytes()
    report = json.loads(first_json)

    assert report["schema_version"] == "sprint6_vs_pit_comparison_v1"
    assert report["claims_eligible"] is False
    assert report["comparison_complete"] is True
    assert report["comparison_window"]["shared_period_count"] == 7
    assert report["point_in_time"]["mean_rank_ic"] < 0
    assert report["point_in_time"]["delisted_security_contribution"][
        "observation_count"
    ] == 1
    assert set(report["point_in_time"]["transaction_costs"]) == {
        "10_bps",
        "25_bps",
        "50_bps",
    }
    universe = report["static_vs_pit_universe_difference"]
    assert universe["periods"][0]["static_only"] == ["T0"]
    assert universe["periods"][0]["pit_only"] == ["T10"]
    body = first_markdown.decode("utf-8")
    for heading in (
        "## Headline diagnostics",
        "## Quintile returns",
        "## Year stability",
        "## Sector stability",
        "## Turnover and transaction costs",
        "## Drawdown and downside capture",
        "## Delisted-security contribution",
        "## Static versus PIT universe difference",
    ):
        assert heading in body

    assert comparison_main(args) == 0
    assert output.read_bytes() == first_json
    assert markdown.read_bytes() == first_markdown


def test_comparative_report_refuses_a_database_outcome_hash_mismatch(tmp_path, capsys):
    static_db, pit_db, paths = _write_inputs(tmp_path)
    lineage = json.loads(paths["pit_lineage"].read_text(encoding="utf-8"))
    lineage["outcome_hashes"][0] = "0" * 64
    paths["pit_lineage"].write_text(json.dumps(lineage), encoding="utf-8")
    report = json.loads(paths["pit_report"].read_text(encoding="utf-8"))
    report["manifest"] = lineage
    paths["pit_report"].write_text(json.dumps(report), encoding="utf-8")

    assert comparison_main(
        [
            "--static-database-url",
            _database_url(static_db),
            "--pit-database-url",
            _database_url(pit_db),
            "--static-report",
            str(paths["static_report"]),
            "--static-lineage",
            str(paths["static_lineage"]),
            "--pit-report",
            str(paths["pit_report"]),
            "--pit-lineage",
            str(paths["pit_lineage"]),
            "--json-output",
            str(tmp_path / "must-not-exist.json"),
        ]
    ) == 2
    assert "database outcomes do not match lineage hashes" in capsys.readouterr().err
    assert not (tmp_path / "must-not-exist.json").exists()


def test_comparison_requires_shared_prediction_dates():
    def observation(day):
        return ComparativeObservation(
            security_id="security",
            ticker="AAA",
            prediction_date=day,
            sector="Technology",
            score=Decimal("1"),
            action_label="Hold",
            excess_return=Decimal("0.01"),
            realised_return=Decimal("0.02"),
            benchmark_return=Decimal("0.01"),
            max_drawdown=Decimal("-0.01"),
        )

    with pytest.raises(ValueError, match="no shared dates"):
        build_comparative_evidence(
            static_observations=[observation(date(2024, 1, 31))],
            pit_observations=[observation(date(2024, 2, 29))],
            static_tickers=["AAA"],
            pit_cohorts=[UniverseCohort(date(2024, 2, 29), ("AAA",))],
            static_lineage={},
            pit_lineage={},
        )
