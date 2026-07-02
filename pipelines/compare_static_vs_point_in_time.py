"""Generate the canonical Sprint 6 versus point-in-time evidence report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
except ModuleNotFoundError:  # Imported through pipelines in tests.
    from pipelines import _bootstrap  # type: ignore  # noqa: F401

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.db import build_engine
from quantfore_research.evaluation.comparative import (
    ComparativeObservation,
    UniverseCohort,
    build_comparative_evidence,
)
from quantfore_research.models import ModelOutcome, ModelPrediction, Security


DEFAULT_STATIC_REPORT = Path(
    "reports/backtests/real_price_baseline_trial_v0_1.json"
)
DEFAULT_STATIC_LINEAGE = Path(
    "reports/backtests/real_price_baseline_trial_v0_1.lineage.json"
)
DEFAULT_PIT_REPORT = Path("reports/backtests/pit_baseline_v0_1.json")
DEFAULT_PIT_LINEAGE = Path("reports/backtests/pit_baseline_v0_1.lineage.json")
DEFAULT_JSON_OUTPUT = Path("reports/backtests/sprint6-vs-pit-v1.json")
DEFAULT_MARKDOWN_OUTPUT = Path("reports/backtests/sprint6-vs-pit-v1.md")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_document(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {label}: {path}") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{label} must be a JSON object")
    return document


def _string_list(value: Any, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{label} must be a non-empty list")
    values = tuple(value)
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{label} must contain non-empty strings")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicates")
    return values


def validate_inputs(
    *,
    static_report: Mapping[str, Any],
    static_lineage: Mapping[str, Any],
    pit_report: Mapping[str, Any],
    pit_lineage: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Validate evidence identities and the unchanged-baseline contract."""

    static_config = static_report.get("configuration")
    pit_config = pit_report.get("configuration")
    if not isinstance(static_config, dict) or not isinstance(pit_config, dict):
        raise ValueError("both reports must contain configuration objects")
    if static_config.get("claims_eligible") is not False:
        raise ValueError("Sprint 6 report must retain claims_eligible=false")
    if pit_report.get("claims_eligible") is not False:
        raise ValueError("point-in-time report must retain claims_eligible=false")
    if static_config.get("dataset_kind") != "prototype_real":
        raise ValueError("Sprint 6 report is not the prototype_real dataset")
    if pit_config.get("dataset_kind") != "point_in_time":
        raise ValueError("point-in-time report has the wrong dataset kind")
    if static_lineage.get("dataset_kind") != "prototype_real":
        raise ValueError("Sprint 6 lineage has the wrong dataset kind")
    if pit_lineage.get("dataset_kind") != "point_in_time":
        raise ValueError("point-in-time lineage has the wrong dataset kind")
    if pit_report.get("coverage_gate_passed") is not True:
        raise ValueError("point-in-time comparison requires a passing coverage gate")
    if pit_report.get("manifest") != pit_lineage:
        raise ValueError("point-in-time report manifest does not match its lineage file")

    for label, config, lineage in (
        ("Sprint 6", static_config, static_lineage),
        ("point-in-time", pit_config, pit_lineage),
    ):
        for field in ("experiment_id", "model_version", "dataset_kind"):
            if config.get(field) != lineage.get(field):
                raise ValueError(f"{label} {field} differs between report and lineage")

    for field in ("feature_version", "horizon", "frequency"):
        if static_config.get(field) != pit_config.get(field):
            raise ValueError(f"unchanged baseline contract violated: {field} differs")
    if static_config.get("benchmark") != pit_lineage.get("benchmark_ticker"):
        raise ValueError("unchanged baseline contract violated: benchmark differs")

    static_ids = _string_list(
        static_lineage.get("prediction_ids"), label="Sprint 6 prediction_ids"
    )
    pit_ids = _string_list(
        pit_lineage.get("prediction_ids"), label="point-in-time prediction_ids"
    )
    static_tickers = _string_list(
        static_config.get("universe"), label="Sprint 6 universe"
    )
    expected_count = pit_lineage.get("prediction_count")
    if expected_count != len(pit_ids):
        raise ValueError("point-in-time prediction_count does not match prediction_ids")
    return static_ids, pit_ids, static_tickers


def load_observations(
    session: Session,
    *,
    prediction_ids: Sequence[str],
    delisted_prediction_ids: Sequence[str] = (),
) -> tuple[tuple[ComparativeObservation, ...], tuple[str, ...]]:
    """Reload immutable predictions and outcomes named by one lineage file."""

    rows = []
    for offset in range(0, len(prediction_ids), 500):
        chunk = prediction_ids[offset : offset + 500]
        rows.extend(
            session.execute(
                select(ModelPrediction, Security, ModelOutcome)
                .join(Security, Security.security_id == ModelPrediction.security_id)
                .outerjoin(
                    ModelOutcome,
                    ModelOutcome.prediction_id == ModelPrediction.prediction_id,
                )
                .where(ModelPrediction.prediction_id.in_(chunk))
            ).all()
        )
    by_id = {
        prediction.prediction_id: (prediction, security, outcome)
        for prediction, security, outcome in rows
    }
    missing = sorted(set(prediction_ids) - set(by_id))
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"database is missing lineage predictions: {preview}")
    delisted = set(delisted_prediction_ids)
    unknown_delisted = delisted - set(prediction_ids)
    if unknown_delisted:
        raise ValueError("delisted prediction IDs are absent from point-in-time lineage")

    observations = []
    outcome_hashes = []
    for prediction_id in prediction_ids:
        prediction, security, outcome = by_id[prediction_id]
        if outcome is not None:
            outcome_hashes.append(outcome.immutable_hash)
        observations.append(
            ComparativeObservation(
                security_id=security.security_id,
                ticker=security.ticker,
                prediction_date=prediction.asof_date,
                sector=security.sector or "Unknown",
                score=Decimal(prediction.score),
                action_label=prediction.action_label,
                excess_return=(Decimal(outcome.excess_return) if outcome else None),
                realised_return=(Decimal(outcome.realised_return) if outcome else None),
                benchmark_return=(Decimal(outcome.benchmark_return) if outcome else None),
                max_drawdown=(Decimal(outcome.max_drawdown) if outcome else None),
                delisted_outcome=prediction_id in delisted,
            )
        )
    return tuple(observations), tuple(sorted(outcome_hashes))


def _pit_universe_cohorts(
    pit_lineage: Mapping[str, Any],
) -> tuple[tuple[UniverseCohort, ...], tuple[str, ...]]:
    source = pit_lineage.get("cohorts")
    if not isinstance(source, list) or not source:
        raise ValueError("point-in-time lineage must contain cohorts")
    cohorts = []
    delisted_prediction_ids = []
    for raw in source:
        if not isinstance(raw, dict):
            raise ValueError("point-in-time cohort must be an object")
        expected_ids = _string_list(
            raw.get("expected_security_ids"), label="cohort expected_security_ids"
        )
        tickers_by_id: dict[str, str] = {}
        for field in ("evaluations", "exclusions"):
            records = raw.get(field)
            if not isinstance(records, list):
                raise ValueError(f"point-in-time cohort {field} must be a list")
            for record in records:
                if not isinstance(record, dict):
                    raise ValueError(f"point-in-time cohort {field} row must be an object")
                security_id = record.get("security_id")
                ticker = record.get("ticker")
                if not isinstance(security_id, str) or not isinstance(ticker, str):
                    raise ValueError("point-in-time cohort row lacks security_id or ticker")
                existing = tickers_by_id.setdefault(security_id, ticker)
                if existing != ticker:
                    raise ValueError("one cohort maps a security to conflicting tickers")
                if field == "evaluations" and record.get("outcome_kind") == "delisting":
                    prediction_id = record.get("prediction_id")
                    if not isinstance(prediction_id, str):
                        raise ValueError("delisting evaluation lacks prediction_id")
                    delisted_prediction_ids.append(prediction_id)
        missing = set(expected_ids) - set(tickers_by_id)
        if missing:
            raise ValueError("cohort lacks ticker evidence for expected securities")
        try:
            prediction_date = date.fromisoformat(raw["prediction_date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("point-in-time cohort has an invalid prediction_date") from exc
        cohorts.append(
            UniverseCohort(
                prediction_date=prediction_date,
                tickers=tuple(tickers_by_id[security_id] for security_id in expected_ids),
            )
        )
    return tuple(cohorts), tuple(sorted(set(delisted_prediction_ids)))


def _verify_outcome_hashes(
    actual: Sequence[str], lineage: Mapping[str, Any], *, label: str
) -> None:
    expected = lineage.get("outcome_hashes")
    if not isinstance(expected, list) or any(not isinstance(value, str) for value in expected):
        raise ValueError(f"{label} lineage outcome_hashes must be a list of strings")
    if tuple(sorted(expected)) != tuple(sorted(actual)):
        raise ValueError(f"{label} database outcomes do not match lineage hashes")


def _metric(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def render_markdown(report: Mapping[str, Any]) -> str:
    static = report["static"]
    pit = report["point_in_time"]
    deltas = report["headline_deltas_pit_minus_static"]
    window = report["comparison_window"]
    lines = [
        "# Sprint 6 versus Point-in-Time Evidence Report",
        "",
        "`claims_eligible=false`",
        "",
        f"Shared comparison window: `{window['start']}` to `{window['end']}` "
        f"({window['shared_period_count']} prediction dates).",
        "",
        "## Headline diagnostics",
        "",
        "| Diagnostic | Sprint 6 static | Point-in-time | PIT minus static |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, key in (
        ("Coverage", "coverage"),
        ("Mean Rank IC", "mean_rank_ic"),
        ("Median Rank IC", "median_rank_ic"),
        ("Non-overlapping IC t-statistic", "non_overlapping_rank_ic_t_statistic"),
        ("Top-minus-bottom spread", "top_minus_bottom_spread"),
    ):
        lines.append(
            f"| {label} | {_metric(static[key])} | {_metric(pit[key])} | "
            f"{_metric(deltas[key])} |"
        )
    lines.extend(
        [
            "",
            "## Quintile returns",
            "",
            "| Quintile | Sprint 6 static | Point-in-time |",
            "| ---: | ---: | ---: |",
        ]
    )
    for quintile in ("1", "2", "3", "4", "5"):
        lines.append(
            f"| {quintile} | {_metric(static['quintile_returns'][quintile])} | "
            f"{_metric(pit['quintile_returns'][quintile])} |"
        )
    for title, key in (
        ("Year stability", "year_stability"),
        ("Sector stability", "sector_stability"),
    ):
        lines.extend(
            [
                "",
                f"## {title}",
                "",
                "| Group | Static observations | Static Rank IC | PIT observations | PIT Rank IC |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for group in sorted(set(static[key]) | set(pit[key])):
            left = static[key].get(group, {})
            right = pit[key].get(group, {})
            lines.append(
                f"| {group} | {_metric(left.get('observations'))} | "
                f"{_metric(left.get('mean_rank_ic'))} | {_metric(right.get('observations'))} | "
                f"{_metric(right.get('mean_rank_ic'))} |"
            )
    lines.extend(
        [
            "",
            "## Turnover and transaction costs",
            "",
            "| Diagnostic | Sprint 6 static | Point-in-time |",
            "| --- | ---: | ---: |",
            "| Mean top-quintile turnover | "
            f"{_metric(static['turnover']['mean'])} | "
            f"{_metric(pit['turnover']['mean'])} |",
            "| Median top-quintile turnover | "
            f"{_metric(static['turnover']['median'])} | "
            f"{_metric(pit['turnover']['median'])} |",
        ]
    )
    for bps in (10, 25, 50):
        key = f"{bps}_bps"
        lines.append(
            f"| Average net excess return at {bps} bps | "
            f"{_metric(static['transaction_costs'][key]['average_net_excess_return'])} | "
            f"{_metric(pit['transaction_costs'][key]['average_net_excess_return'])} |"
        )
    lines.extend(
        [
            "",
            "## Drawdown and downside capture",
            "",
            "| Diagnostic | Sprint 6 static | Point-in-time |",
            "| --- | ---: | ---: |",
        ]
    )
    static_down = static["drawdown_and_downside_capture"]
    pit_down = pit["drawdown_and_downside_capture"]
    for label, section, key in (
        ("Worst max drawdown (all)", "all_observations", "worst_max_drawdown"),
        ("Mean max drawdown (top quintile)", "top_quintile", "mean_max_drawdown"),
        ("Down-market periods", "top_quintile", "down_market_periods"),
        ("Downside capture (%)", "top_quintile", "downside_capture_percentage"),
    ):
        lines.append(
            f"| {label} | {_metric(static_down[section][key])} | "
            f"{_metric(pit_down[section][key])} |"
        )
    lines.extend(
        [
            "",
            "## Delisted-security contribution",
            "",
            "| Diagnostic | Sprint 6 static | Point-in-time |",
            "| --- | ---: | ---: |",
        ]
    )
    static_delisted = static["delisted_security_contribution"]
    pit_delisted = pit["delisted_security_contribution"]
    for label, key in (
        ("Observations", "observation_count"),
        ("Securities", "security_count"),
        ("Mean excess return", "mean_excess_return"),
        ("Contribution to overall mean", "contribution_to_all_observation_mean"),
    ):
        lines.append(
            f"| {label} | {_metric(static_delisted[key])} | {_metric(pit_delisted[key])} |"
        )
    universe = report["static_vs_pit_universe_difference"]
    lines.extend(
        [
            "",
            "## Static versus PIT universe difference",
            "",
            f"- Static universe size: `{universe['static_universe_size']}`",
            f"- PIT periods compared: `{universe['pit_period_count']}`",
            "- Mean symmetric difference: "
            f"`{_metric(universe['mean_symmetric_difference_count'])}`",
            f"- Mean Jaccard similarity: `{_metric(universe['mean_jaccard_similarity'])}`",
            "",
            "| Date | Static | PIT | Static only | PIT only | Jaccard |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in universe["periods"]:
        lines.append(
            f"| {row['prediction_date']} | {row['static_count']} | {row['pit_count']} | "
            f"{len(row['static_only'])} | {len(row['pit_only'])} | "
            f"{_metric(row['jaccard_similarity'])} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation boundary",
            "",
            report["interpretation"],
            "Negative or insignificant results are valid completion evidence; "
            "no metric is treated as a pass condition.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)


def write_reports(
    report: Mapping[str, Any], *, json_output: Path, markdown_output: Path
) -> None:
    _write_atomic(
        json_output,
        (json.dumps(report, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    _write_atomic(markdown_output, render_markdown(report).encode("utf-8"))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare Sprint 6 static evidence with a point-in-time run."
    )
    parser.add_argument("--static-database-url")
    parser.add_argument("--pit-database-url")
    parser.add_argument("--static-report", type=Path, default=DEFAULT_STATIC_REPORT)
    parser.add_argument("--static-lineage", type=Path, default=DEFAULT_STATIC_LINEAGE)
    parser.add_argument("--pit-report", type=Path, default=DEFAULT_PIT_REPORT)
    parser.add_argument("--pit-lineage", type=Path, default=DEFAULT_PIT_LINEAGE)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    engines = []
    try:
        static_report = _load_document(args.static_report, label="Sprint 6 report")
        static_lineage = _load_document(args.static_lineage, label="Sprint 6 lineage")
        pit_report = _load_document(args.pit_report, label="point-in-time report")
        pit_lineage = _load_document(args.pit_lineage, label="point-in-time lineage")
        static_ids, pit_ids, static_tickers = validate_inputs(
            static_report=static_report,
            static_lineage=static_lineage,
            pit_report=pit_report,
            pit_lineage=pit_lineage,
        )
        pit_cohorts, delisted_ids = _pit_universe_cohorts(pit_lineage)

        static_engine = build_engine(database_url=args.static_database_url)
        pit_engine = build_engine(database_url=args.pit_database_url)
        engines.extend((static_engine, pit_engine))
        with Session(static_engine) as static_session, Session(pit_engine) as pit_session:
            static_observations, static_outcome_hashes = load_observations(
                static_session, prediction_ids=static_ids
            )
            pit_observations, pit_outcome_hashes = load_observations(
                pit_session,
                prediction_ids=pit_ids,
                delisted_prediction_ids=delisted_ids,
            )
        _verify_outcome_hashes(
            static_outcome_hashes, static_lineage, label="Sprint 6"
        )
        _verify_outcome_hashes(
            pit_outcome_hashes, pit_lineage, label="point-in-time"
        )
        report = build_comparative_evidence(
            static_observations=static_observations,
            pit_observations=pit_observations,
            static_tickers=static_tickers,
            pit_cohorts=pit_cohorts,
            static_lineage={
                "report_path": str(args.static_report),
                "report_sha256": _sha256(args.static_report),
                "lineage_path": str(args.static_lineage),
                "lineage_sha256": _sha256(args.static_lineage),
                "experiment_id": static_lineage["experiment_id"],
                "prediction_count": len(static_ids),
                "outcome_count": len(static_outcome_hashes),
            },
            pit_lineage={
                "report_path": str(args.pit_report),
                "report_sha256": _sha256(args.pit_report),
                "lineage_path": str(args.pit_lineage),
                "lineage_sha256": _sha256(args.pit_lineage),
                "experiment_id": pit_lineage["experiment_id"],
                "prediction_count": len(pit_ids),
                "outcome_count": len(pit_outcome_hashes),
            },
        )
        write_reports(
            report,
            json_output=args.json_output,
            markdown_output=args.markdown_output,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"comparative evidence report failed: {exc}", file=sys.stderr)
        return 2
    finally:
        for engine in engines:
            engine.dispose()
    print(
        "comparative evidence report complete "
        f"shared_periods={report['comparison_window']['shared_period_count']} "
        f"json_report={args.json_output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
