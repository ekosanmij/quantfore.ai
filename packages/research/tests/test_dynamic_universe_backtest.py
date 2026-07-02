import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from pipelines.run_point_in_time_backtest import main as backtest_main
from quantfore_research.backtest.point_in_time import (
    build_dynamic_universe_report,
    run_dynamic_universe_backtest,
)
from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.models import (
    DelistingEvent,
    Feature,
    ModelOutcome,
    ModelPrediction,
    Price,
    Security,
    SourceSnapshot,
    TickerAlias,
    UniverseDefinition,
    UniverseMembership,
)
from quantfore_research.validation.reproducibility import universe_membership_hash


HASH = "d" * 64
RETRIEVED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def weekday_dates(start: date, count: int) -> list[date]:
    result = []
    current = start
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current += timedelta(days=1)
    return result


def seed_dynamic_panel(
    database_url: str,
    *,
    short_b_history: bool = False,
    missing_delisting_return: bool = False,
):
    engine = build_engine(database_url=database_url)
    create_schema(engine)
    factory = make_session_factory(engine)
    dates = weekday_dates(date(2019, 1, 2), 700)
    with session_scope(factory) as session:
        snapshot = SourceSnapshot(
            snapshot_id="snapshot-panel",
            vendor="Licensed Test Vendor",
            dataset="pit-dynamic-panel",
            retrieved_at=RETRIEVED_AT,
            license_tag="test",
            source_hash=HASH,
            storage_uri="raw/test/pit-dynamic-panel.json",
        )
        securities = {
            ticker: Security(
                security_id=f"security-{ticker.lower()}",
                ticker=ticker,
                name=ticker,
                active_from=dates[0],
                active_to=(date(2020, 5, 15) if ticker == "DEL" else None),
            )
            for ticker in ("AAA", "BBB", "DEL", "SPY")
        }
        session.add_all([snapshot, *securities.values()])
        session.flush()
        universe = UniverseDefinition(
            universe_id="sp500-pit-v1",
            name="Historical S&P 500",
            version="v1",
            description="Dynamic universe test panel",
            window_start=dates[0],
            window_end=dates[-1],
            benchmark_security_id=securities["SPY"].security_id,
            benchmark_excluded_from_rankings=True,
            source_snapshot_id=snapshot.snapshot_id,
            source_hash=HASH,
        )
        session.add(universe)
        for ticker, security in securities.items():
            session.add(
                TickerAlias(
                    ticker_alias_id=f"alias-{ticker.lower()}",
                    security_id=security.security_id,
                    ticker=ticker,
                    effective_from=dates[0],
                    effective_to=(date(2020, 5, 15) if ticker == "DEL" else None),
                    announced_at=datetime(2018, 12, 1, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                )
            )
        session.add_all(
            [
                UniverseMembership(
                    membership_id="membership-aaa",
                    universe_id=universe.universe_id,
                    security_id=securities["AAA"].security_id,
                    effective_from=dates[0],
                    effective_to=None,
                    announced_at=datetime(2018, 12, 1, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
                UniverseMembership(
                    membership_id="membership-bbb",
                    universe_id=universe.universe_id,
                    security_id=securities["BBB"].security_id,
                    effective_from=date(2020, 4, 1),
                    effective_to=None,
                    announced_at=datetime(2020, 3, 20, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
                UniverseMembership(
                    membership_id="membership-del",
                    universe_id=universe.universe_id,
                    security_id=securities["DEL"].security_id,
                    effective_from=dates[0],
                    effective_to=date(2020, 5, 15),
                    announced_at=datetime(2018, 12, 1, tzinfo=timezone.utc),
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=HASH,
                ),
            ]
        )
        session.add(
            DelistingEvent(
                delisting_event_id="delisting-del",
                security_id=securities["DEL"].security_id,
                delisting_date=date(2020, 5, 15),
                announced_at=datetime(2020, 5, 1, tzinfo=timezone.utc),
                delisting_return=(
                    None if missing_delisting_return else Decimal("-0.50")
                ),
                return_available_at=(
                    None
                    if missing_delisting_return
                    else datetime(2020, 5, 18, tzinfo=timezone.utc)
                ),
                reason="bankruptcy",
                source_snapshot_id=snapshot.snapshot_id,
                source_hash=HASH,
            )
        )
        for ticker, security in securities.items():
            ticker_dates = dates
            if ticker == "DEL":
                ticker_dates = [day for day in dates if day <= date(2020, 5, 15)]
            elif ticker == "BBB" and short_b_history:
                ticker_dates = [day for day in dates if day >= date(2020, 1, 1)]
            start_price = {
                "AAA": Decimal("50"),
                "BBB": Decimal("80"),
                "DEL": Decimal("30"),
                "SPY": Decimal("200"),
            }[ticker]
            step = {
                "AAA": Decimal("0.05"),
                "BBB": Decimal("0.04"),
                "DEL": Decimal("0.02"),
                "SPY": Decimal("0.03"),
            }[ticker]
            for index, price_date in enumerate(ticker_dates):
                close = start_price + Decimal(index) * step
                session.add(
                    Price(
                        price_id=f"price-{ticker.lower()}-{price_date.isoformat()}",
                        security_id=security.security_id,
                        date=price_date,
                        open=close,
                        high=close + Decimal("0.10"),
                        low=close - Decimal("0.10"),
                        close=close,
                        adj_open=close,
                        adj_high=close + Decimal("0.10"),
                        adj_low=close - Decimal("0.10"),
                        adj_close=close,
                        volume=1_000_000,
                        adj_volume=Decimal("1000000"),
                        source_snapshot_id=snapshot.snapshot_id,
                    )
                )
    return factory


def run_dynamic(factory, *, experiment_id="pit-test", minimum=Decimal("0.95")):
    with session_scope(factory) as session:
        return run_dynamic_universe_backtest(
            session,
            experiment_id=experiment_id,
            universe_id="sp500-pit-v1",
            start_date=date(2020, 3, 1),
            end_date=date(2020, 5, 31),
            price_source_snapshot_id="snapshot-panel",
            minimum_coverage=minimum,
            code_commit="test-commit",
            audit_sha256="audit-hash",
            evaluated_at=RETRIEVED_AT,
            result_uri="reports/backtests/pit-test.json",
        )


def test_dynamic_membership_changes_monthly_and_delisted_outcomes_are_retained(tmp_path):
    factory = seed_dynamic_panel(f"sqlite+pysqlite:///{tmp_path / 'panel.db'}")

    result = run_dynamic(factory)

    assert result.prediction_dates == (
        date(2020, 3, 31),
        date(2020, 4, 30),
        date(2020, 5, 29),
    )
    assert [cohort.expected_security_ids for cohort in result.cohorts] == [
        ("security-aaa", "security-del"),
        ("security-aaa", "security-bbb", "security-del"),
        ("security-aaa", "security-bbb"),
    ]
    assert all(cohort.coverage == Decimal("1") for cohort in result.cohorts)
    assert result.coverage_gate_passed is True
    delisted = [
        row
        for cohort in result.cohorts
        for row in cohort.evaluations
        if row.outcome_kind == "delisting"
    ]
    assert len(delisted) == 2
    assert {row.delisting_return for row in delisted} == {Decimal("-0.50")}
    assert all(row.exit_date == date(2020, 5, 15) for row in delisted)
    assert all(not cohort.exclusions for cohort in result.cohorts)
    assert len(result.prediction_ids) == 7
    assert len(result.outcome_hashes) == 7

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ModelPrediction)) == 7
        assert session.scalar(select(func.count()).select_from(ModelOutcome)) == 7
        assert session.scalar(select(func.count()).select_from(Feature)) == 28
        report = build_dynamic_universe_report(session, result=result)
    assert report["coverage_gate_passed"] is True
    assert report["configuration"]["model_version"] == "baseline_v0.1"
    assert report["configuration"]["features"] == [
        "momentum_6_1",
        "momentum_12_1",
        "return_21d",
        "volatility_126d",
    ]
    assert report["observation_counts"]["delisted_outcomes"] == 2


def test_every_exclusion_has_machine_readable_reason_and_coverage_gate_is_per_cohort(
    tmp_path,
):
    factory = seed_dynamic_panel(
        f"sqlite+pysqlite:///{tmp_path / 'short.db'}", short_b_history=True
    )

    result = run_dynamic(factory, experiment_id="pit-short")

    assert result.coverage_gate_passed is False
    assert result.cohorts[0].coverage == Decimal("1")
    assert result.cohorts[1].coverage < Decimal("0.95")
    assert result.cohorts[2].coverage < Decimal("0.95")
    exclusions = [row for cohort in result.cohorts for row in cohort.exclusions]
    assert len(exclusions) == 2
    assert {row.reason_code for row in exclusions} == {"INSUFFICIENT_HISTORY"}
    assert all(row.stage == "features" for row in exclusions)
    assert all(row.membership_id == "membership-bbb" for row in exclusions)
    assert all(row.membership_source_hash == HASH for row in exclusions)


def test_missing_terminal_return_is_an_explicit_outcome_exclusion(tmp_path):
    factory = seed_dynamic_panel(
        f"sqlite+pysqlite:///{tmp_path / 'missing-return.db'}",
        missing_delisting_return=True,
    )

    result = run_dynamic(factory, experiment_id="pit-missing-return")

    exclusions = [row for cohort in result.cohorts for row in cohort.exclusions]
    delisting_exclusions = [
        row for row in exclusions if row.reason_code == "DELISTING_RETURN_UNAVAILABLE"
    ]
    assert len(delisting_exclusions) == 2
    assert all(row.stage == "outcome" for row in delisting_exclusions)
    assert all(row.prediction_id for row in delisting_exclusions)
    assert result.coverage_gate_passed is False


def test_dynamic_runner_is_idempotent_and_manifest_excludes_run_state_counts(tmp_path):
    factory = seed_dynamic_panel(f"sqlite+pysqlite:///{tmp_path / 'repeat.db'}")

    first = run_dynamic(factory, experiment_id="pit-repeat")
    second = run_dynamic(factory, experiment_id="pit-repeat")

    assert first.to_manifest() == second.to_manifest()
    assert first.created_predictions == 7
    assert first.created_outcomes == 7
    assert second.created_predictions == 0
    assert second.existing_predictions == 7
    assert second.created_outcomes == 0
    assert second.existing_outcomes == 7


def test_dynamic_runner_reproduces_across_clean_databases(tmp_path):
    first_factory = seed_dynamic_panel(
        f"sqlite+pysqlite:///{tmp_path / 'clean-one.db'}"
    )
    second_factory = seed_dynamic_panel(
        f"sqlite+pysqlite:///{tmp_path / 'clean-two.db'}"
    )

    first = run_dynamic(first_factory, experiment_id="pit-clean")
    second = run_dynamic(second_factory, experiment_id="pit-clean")

    assert first.to_manifest() == second.to_manifest()
    with first_factory() as session:
        first_report = build_dynamic_universe_report(session, result=first)
    with second_factory() as session:
        second_report = build_dynamic_universe_report(session, result=second)
    assert first_report == second_report


def test_pipeline_requires_a_clean_audit_and_writes_deterministic_reports(tmp_path):
    db_path = tmp_path / "pipeline.db"
    factory = seed_dynamic_panel(f"sqlite+pysqlite:///{db_path}")
    with factory() as session:
        membership_hash = universe_membership_hash(
            session, universe_id="sp500-pit-v1"
        )
    audit_path = tmp_path / "audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "decision": "pass",
                "claims_eligible": False,
                    "audit": {
                        "universe_id": "sp500-pit-v1",
                        "hard_failure_count": 0,
                        "dataset_binding": {
                            "membership_content_hash": membership_hash,
                            "universe_snapshot": {
                                "snapshot_id": "snapshot-panel",
                                "source_hash": HASH,
                            },
                            "membership_snapshots": {
                                "snapshot-panel": HASH,
                            },
                            "price_snapshots_by_security": {
                                f"security-{ticker.lower()}": {
                                    "snapshot_id": "snapshot-panel",
                                    "source_hash": HASH,
                                }
                                for ticker in ("AAA", "BBB", "DEL", "SPY")
                            },
                        },
                    },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    with session_scope(factory) as session:
        newer = SourceSnapshot(
            snapshot_id="snapshot-newer-unreviewed",
            vendor="Unreviewed Test Vendor",
            dataset="newer-prices",
            retrieved_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
            license_tag="test",
            source_hash="e" * 64,
            storage_uri="raw/test/newer-prices.json",
        )
        session.add(newer)
        original_prices = session.scalars(select(Price)).all()
        session.flush()
        for row in original_prices:
            session.add(
                Price(
                    price_id=f"newer-{row.price_id}",
                    security_id=row.security_id,
                    date=row.date,
                    open=row.open,
                    high=row.high,
                    low=row.low,
                    close=row.close,
                    adj_open=row.adj_open,
                    adj_high=row.adj_high,
                    adj_low=row.adj_low,
                    adj_close=row.adj_close,
                    volume=row.volume,
                    adj_volume=row.adj_volume,
                    source_snapshot_id=newer.snapshot_id,
                )
            )
    json_output = tmp_path / "report.json"
    markdown_output = tmp_path / "report.md"
    lineage_output = tmp_path / "report.lineage.json"
    args = [
        "--database-url",
        f"sqlite+pysqlite:///{db_path}",
        "--start-date",
        "2020-03-01",
        "--end-date",
        "2020-05-31",
        "--experiment-id",
        "pit-pipeline",
        "--audit-json",
        str(audit_path),
        "--json-output",
        str(json_output),
        "--markdown-output",
        str(markdown_output),
        "--lineage-output",
        str(lineage_output),
        "--evaluated-at",
        "2026-01-01T00:00:00Z",
    ]

    first_exit = backtest_main(args)
    first_report = json_output.read_bytes()
    first_markdown = markdown_output.read_bytes()
    first_lineage = lineage_output.read_bytes()
    second_exit = backtest_main(args)

    assert first_exit == second_exit == 0
    assert json_output.read_bytes() == first_report
    assert markdown_output.read_bytes() == first_markdown
    assert lineage_output.read_bytes() == first_lineage
    assert "snapshot-newer-unreviewed" not in json.loads(first_report)[
        "source_snapshot_ids"
    ]
    report = json.loads(first_report)
    assert report["coverage_gate_passed"] is True
    assert report["observation_counts"] == {
        "expected": 7,
        "features_built": 7,
        "evaluated": 7,
        "delisted_outcomes": 2,
    }
    assert b"## Monthly cohorts" in first_markdown


def test_pipeline_refuses_failed_audit_before_database_access(tmp_path):
    audit_path = tmp_path / "failed-audit.json"
    audit_path.write_text(
        json.dumps(
            {
                "decision": "fail",
                "claims_eligible": False,
                "audit": {
                    "universe_id": "sp500-pit-v1",
                    "hard_failure_count": 1,
                },
            }
        ),
        encoding="utf-8",
    )
    missing_database = tmp_path / "missing.db"

    exit_code = backtest_main(
        [
            "--database-url",
            f"sqlite+pysqlite:///{missing_database}",
            "--start-date",
            "2020-03-01",
            "--end-date",
            "2020-05-31",
            "--experiment-id",
            "pit-failed-audit",
            "--audit-json",
            str(audit_path),
        ]
    )

    assert exit_code == 2
    assert not missing_database.exists()
