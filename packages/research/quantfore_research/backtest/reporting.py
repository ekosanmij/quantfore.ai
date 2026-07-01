"""Deterministic JSON and Markdown reports for baseline backtest runs."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Mapping

from sqlalchemy import select

from quantfore_research.backtest.baseline import (
    BacktestObservation,
    summarize_backtest,
)
from quantfore_research.backtest.execution import BacktestRunResult
from quantfore_research.evaluation import canonical_datetime_text
from quantfore_research.models import (
    ExperimentRegistry,
    ModelOutcome,
    ModelPrediction,
    Security,
    SourceSnapshot,
)


REPORT_SCHEMA_VERSION = "synthetic_baseline_backtest_v0"
SYNTHETIC_WARNING = (
    "SYNTHETIC ENGINEERING DATA - NOT VALIDATION EVIDENCE"
)
KNOWN_LIMITATIONS = (
    "All prices and volumes are synthetic; results do not measure real-market performance.",
    "Sessions are aligned weekdays and do not model exchange holidays or trading halts.",
    "The universe is fixed and fictional, so survivorship, delisting and corporate-action effects are absent.",
    "The baseline is a deterministic heuristic rather than a trained or calibrated forecasting model.",
    "Cost sensitivity is a fixed top-quintile round-trip deduction, not a full turnover, liquidity, tax or market-impact model.",
    "The sample is too small and artificial for investment-performance or predictive-validity claims.",
)


def _distribution_dict(distribution) -> Mapping[str, object]:
    return asdict(distribution)


def build_backtest_report(
    session,
    *,
    result: BacktestRunResult,
) -> dict[str, object]:
    """Build a complete report from immutable stored predictions and outcomes."""

    experiment = session.get(ExperimentRegistry, result.experiment_id)
    if experiment is None:
        raise ValueError(f"unregistered experiment: {result.experiment_id}")
    snapshots = [
        session.get(SourceSnapshot, snapshot_id)
        for snapshot_id in result.source_snapshot_ids
    ]
    if any(snapshot is None for snapshot in snapshots):
        raise ValueError("backtest report references an unknown source snapshot")

    rows = []
    if result.prediction_ids:
        rows = list(
            session.execute(
                select(ModelPrediction, Security, ModelOutcome)
                .join(Security, Security.security_id == ModelPrediction.security_id)
                .outerjoin(
                    ModelOutcome,
                    ModelOutcome.prediction_id == ModelPrediction.prediction_id,
                )
                .where(ModelPrediction.prediction_id.in_(result.prediction_ids))
                .order_by(
                    ModelPrediction.asof_date,
                    Security.ticker,
                    ModelPrediction.prediction_id,
                )
            )
        )
    observations = [
        BacktestObservation(
            ticker=security.ticker,
            prediction_date=prediction.asof_date,
            score=prediction.score,
            action_label=prediction.action_label,
            excess_return=(outcome.excess_return if outcome is not None else None),
        )
        for prediction, security, outcome in rows
    ]
    summary = summarize_backtest(observations)
    period_by_date = {period.prediction_date: period for period in summary.periods}
    rank_ic_by_month = []
    for prediction_date in result.prediction_dates:
        period = period_by_date.get(prediction_date)
        rank_ic_by_month.append(
            {
                "prediction_date": prediction_date.isoformat(),
                "eligible_observations": (
                    period.eligible_observations if period is not None else 0
                ),
                "evaluated_observations": (
                    period.evaluated_observations if period is not None else 0
                ),
                "coverage": period.coverage if period is not None else 0.0,
                "rank_ic": period.rank_ic if period is not None else None,
            }
        )

    snapshot_lineage = [
        {
            "snapshot_id": snapshot.snapshot_id,
            "source_hash": snapshot.source_hash,
            "vendor": snapshot.vendor,
            "dataset": snapshot.dataset,
            "retrieved_at": canonical_datetime_text(snapshot.retrieved_at),
            "license_tag": snapshot.license_tag,
            "storage_uri": snapshot.storage_uri,
        }
        for snapshot in sorted(snapshots, key=lambda value: value.snapshot_id)
    ]
    config = dict(experiment.config_json)
    config.update(
        {
            "experiment_id": experiment.experiment_id,
            "hypothesis_id": experiment.hypothesis_id,
            "data_snapshot_hash": experiment.data_snapshot_hash,
            "code_commit": experiment.code_commit,
            "result_uri": experiment.result_uri,
        }
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "synthetic_warning": SYNTHETIC_WARNING,
        "configuration": config,
        "dataset_and_snapshot_lineage": {
            "data_snapshot_hash": experiment.data_snapshot_hash,
            "source_snapshots": snapshot_lineage,
        },
        "observation_counts": {
            "eligible": summary.eligible_observations,
            "evaluated": summary.evaluated_observations,
        },
        "coverage": summary.coverage,
        "rank_ic_by_month": rank_ic_by_month,
        "rank_ic_summary": {
            "mean": summary.mean_rank_ic,
            "median": summary.median_rank_ic,
            "t_statistic": summary.rank_ic_t_statistic,
            "positive_period_percentage": (
                summary.positive_rank_ic_period_percentage
            ),
        },
        "quintile_returns": {
            str(quintile): value
            for quintile, value in summary.average_excess_return_by_quintile.items()
        },
        "quintile_observation_counts": {
            str(quintile): value
            for quintile, value in summary.observation_count_by_quintile.items()
        },
        "top_minus_bottom_spread": summary.top_minus_bottom_spread,
        "top_quintile_benchmark_hit_rate": (
            summary.top_quintile_benchmark_hit_rate
        ),
        "top_quintile_cost_sensitivity": {
            f"{cost_bps}_bps": asdict(cost_result)
            for cost_bps, cost_result in (
                summary.top_quintile_cost_sensitivity.items()
            )
        },
        "quintile_returns_monotonic": summary.monotonic,
        "score_distribution": _distribution_dict(summary.score_distribution),
        "label_distribution": dict(summary.label_distribution),
        "failed_or_skipped_observations": [
            {
                "ticker": skipped.ticker,
                "prediction_date": skipped.prediction_date.isoformat(),
                "stage": skipped.stage,
                "reason": skipped.reason,
            }
            for skipped in result.skipped_observations
        ],
        "lineage": {
            "prediction_ids": list(result.prediction_ids),
            "outcome_hashes": list(result.outcome_hashes),
            "source_snapshot_ids": list(result.source_snapshot_ids),
        },
        "known_limitations": list(KNOWN_LIMITATIONS),
    }


def _metric(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def render_backtest_markdown(report: Mapping[str, object]) -> str:
    """Render the canonical report mapping as deterministic Markdown."""

    config = report["configuration"]
    lineage = report["dataset_and_snapshot_lineage"]
    counts = report["observation_counts"]
    ic_summary = report["rank_ic_summary"]
    lines = [
        "# Synthetic Baseline Backtest v0.1",
        "",
        f"> **{report['synthetic_warning']}**",
        "",
        "## Configuration",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Experiment | `{config['experiment_id']}` |",
        f"| Hypothesis ID | `{config['hypothesis_id']}` |",
        f"| Hypothesis | {config['hypothesis']} |",
        f"| Model version | `{config['model_version']}` |",
        f"| Feature version | `{config['feature_version']}` |",
        f"| Universe | {', '.join(config['universe'])} |",
        f"| Benchmark | `{config['benchmark']}` |",
        f"| Horizon | `{config['horizon']}` |",
        f"| Date range | {config['date_range']['start']} to {config['date_range']['end']} |",
        f"| Frequency | `{config['frequency']}` |",
        f"| Securities | {config['number_of_securities']} |",
        f"| Periods | {config['number_of_periods']} |",
        f"| Dataset kind | `{config['dataset_kind']}` |",
        f"| Claims eligible | `{str(config['claims_eligible']).lower()}` |",
        f"| Code commit | `{config['code_commit'] or 'unavailable'}` |",
        "",
        "## Dataset and Snapshot Lineage",
        "",
        f"Data snapshot hash: `{lineage['data_snapshot_hash']}`",
        "",
        "| Snapshot ID | Dataset | Vendor | Retrieved at | License | Storage URI |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for snapshot in lineage["source_snapshots"]:
        lines.append(
            f"| `{snapshot['snapshot_id']}` | {snapshot['dataset']} | "
            f"{snapshot['vendor']} | {snapshot['retrieved_at']} | "
            f"{snapshot['license_tag']} | `{snapshot['storage_uri']}` |"
        )
    lines.extend(
        [
            "",
            "## Observation Counts and Coverage",
            "",
            f"- Eligible observations: {counts['eligible']}",
            f"- Evaluated observations: {counts['evaluated']}",
            f"- Coverage: {_metric(report['coverage'])}",
            "",
            "## Rank IC by Month",
            "",
            "| Prediction date | Eligible | Evaluated | Coverage | Rank IC |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for period in report["rank_ic_by_month"]:
        lines.append(
            f"| {period['prediction_date']} | {period['eligible_observations']} | "
            f"{period['evaluated_observations']} | {_metric(period['coverage'])} | "
            f"{_metric(period['rank_ic'])} |"
        )
    lines.extend(
        [
            "",
            "## Rank IC Summary",
            "",
            f"- Mean Rank IC: {_metric(ic_summary['mean'])}",
            f"- Median Rank IC: {_metric(ic_summary['median'])}",
            f"- Rank IC t-statistic: {_metric(ic_summary['t_statistic'])}",
            f"- Positive IC-period percentage: {_metric(ic_summary['positive_period_percentage'])}",
            "",
            "## Quintile Returns",
            "",
            "| Quintile | Average excess return | Evaluated observations |",
            "| ---: | ---: | ---: |",
        ]
    )
    for quintile in (1, 2, 3, 4, 5):
        key = str(quintile)
        lines.append(
            f"| {quintile} | {_metric(report['quintile_returns'][key])} | "
            f"{report['quintile_observation_counts'][key]} |"
        )
    lines.extend(
        [
            "",
            "## Top-minus-Bottom Quintile Spread",
            "",
            _metric(report["top_minus_bottom_spread"]),
            "",
            "## Top-Quintile Benchmark Hit Rate",
            "",
            _metric(report["top_quintile_benchmark_hit_rate"]),
            "",
            "## Monotonicity Check",
            "",
            f"Monotonic quintile returns: {_metric(report['quintile_returns_monotonic'])}",
            "",
            "## Top-Quintile Cost Sensitivity",
            "",
            "A fixed round-trip cost is deducted once from each evaluated top-quintile excess return.",
            "",
            "| Round-trip cost | Evaluated observations | Average net excess return | Benchmark hit rate |",
            "| ---: | ---: | ---: | ---: |",
        ]
    )
    for cost_key in ("0_bps", "10_bps", "25_bps"):
        sensitivity = report["top_quintile_cost_sensitivity"][cost_key]
        lines.append(
            f"| {sensitivity['round_trip_cost_bps']} bps | "
            f"{sensitivity['evaluated_observations']} | "
            f"{_metric(sensitivity['average_net_excess_return'])} | "
            f"{_metric(sensitivity['benchmark_hit_rate'])} |"
        )
    lines.extend(
        [
            "",
            "## Score Distribution",
            "",
            "| Count | Minimum | Maximum | Mean | Median |",
            "| ---: | ---: | ---: | ---: | ---: |",
            "| {count} | {minimum} | {maximum} | {mean} | {median} |".format(
                **{
                    key: _metric(value)
                    for key, value in report["score_distribution"].items()
                }
            ),
            "",
            "## Label Distribution",
            "",
            "| Label | Count |",
            "| --- | ---: |",
        ]
    )
    for label, count in report["label_distribution"].items():
        lines.append(f"| `{label}` | {count} |")
    lines.extend(["", "## Failed or Skipped Observations", ""])
    skipped = report["failed_or_skipped_observations"]
    if skipped:
        lines.extend(
            [
                "| Date | Ticker | Stage | Reason |",
                "| --- | --- | --- | --- |",
            ]
        )
        for item in skipped:
            lines.append(
                f"| {item['prediction_date']} | `{item['ticker']}` | "
                f"{item['stage']} | {item['reason']} |"
            )
    else:
        lines.append("None.")

    lines.extend(["", "## Experiment Lineage", "", "### Prediction IDs", ""])
    lines.extend(f"- `{value}`" for value in report["lineage"]["prediction_ids"])
    lines.extend(["", "### Outcome Hashes", ""])
    lines.extend(f"- `{value}`" for value in report["lineage"]["outcome_hashes"])
    lines.extend(["", "### Source Snapshot IDs", ""])
    lines.extend(f"- `{value}`" for value in report["lineage"]["source_snapshot_ids"])
    lines.extend(["", "## Known Limitations", ""])
    lines.extend(f"- {value}" for value in report["known_limitations"])
    lines.extend(
        [
            "",
            "## Synthetic-Data Warning",
            "",
            f"**{report['synthetic_warning']}**",
            "",
        ]
    )
    return "\n".join(lines)


def write_backtest_reports(
    report: Mapping[str, object],
    *,
    json_path: Path,
    markdown_path: Path,
) -> None:
    """Write byte-stable JSON and Markdown report artifacts."""

    json_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    markdown_path.write_text(
        render_backtest_markdown(report),
        encoding="utf-8",
    )
