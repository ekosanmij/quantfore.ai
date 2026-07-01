"""Run the Sprint 5 baseline model across historical monthly dates.

Example:
    python pipelines/run_baseline_backtest.py \
      --benchmark SPY \
      --start-date 2023-01-01 \
      --end-date 2024-12-31 \
      --horizon 126d \
      --frequency monthly \
      --experiment-id synthetic_baseline_v0_1
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Optional, Sequence

import _bootstrap  # noqa: F401
from _common import open_research_database, parse_date

from quantfore_research.backtest import (
    build_backtest_report,
    run_historical_backtest,
    write_backtest_reports,
)
from quantfore_research.db import session_scope


def get_code_commit() -> Optional[str]:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
            or None
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def terminal_metric(value: object) -> str:
    return "n/a" if value is None else f"{float(value):.6f}"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run deterministic historical baseline predictions and outcomes."
    )
    parser.add_argument("--benchmark", required=True)
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--horizon", default="126d")
    parser.add_argument("--frequency", default="monthly")
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument(
        "--source-snapshot-id",
        help="Pin the price snapshot instead of discovering the broadest panel.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help=(
            "Write the JSON report here. Defaults to "
            "reports/backtests/EXPERIMENT_ID.json."
        ),
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        help=(
            "Write the Markdown report here. Defaults to the JSON report path "
            "with an .md suffix."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    start_date = parse_date(args.start_date)
    end_date = parse_date(args.end_date)
    if start_date is None or end_date is None:
        raise ValueError("--start-date and --end-date are required")

    json_output = args.json_output or (
        Path("reports")
        / "backtests"
        / f"{args.experiment_id}.json"
    )
    markdown_output = args.markdown_output or json_output.with_suffix(".md")
    session_factory = open_research_database(args.database_url)
    with session_scope(session_factory) as session:
        result = run_historical_backtest(
            session,
            experiment_id=args.experiment_id,
            benchmark_ticker=args.benchmark,
            start_date=start_date,
            end_date=end_date,
            horizon=args.horizon,
            frequency=args.frequency,
            source_snapshot_id=args.source_snapshot_id,
            code_commit=get_code_commit(),
            result_uri=json_output.as_posix(),
        )
        report = build_backtest_report(session, result=result)
        write_backtest_reports(
            report,
            json_path=json_output,
            markdown_path=markdown_output,
        )

    for skipped in result.skipped_observations:
        print(
            "skipped observation "
            f"ticker={skipped.ticker} date={skipped.prediction_date} "
            f"stage={skipped.stage} reason={skipped.reason}"
        )
    print(f"backtest complete experiment={result.experiment_id}")
    print(
        f"periods={len(result.prediction_dates)} "
        f"securities={len(result.security_tickers)} "
        f"predictions={len(result.prediction_ids)} "
        f"outcomes={len(result.outcome_hashes)} "
        f"skipped={len(result.skipped_observations)}"
    )
    print(
        f"coverage={terminal_metric(report['coverage'])} "
        f"mean_rank_ic={terminal_metric(report['rank_ic_summary']['mean'])}"
    )
    print(
        f"top_bottom_spread={terminal_metric(report['top_minus_bottom_spread'])} "
        f"hit_rate={terminal_metric(report['top_quintile_benchmark_hit_rate'])}"
    )
    print(
        f"created_predictions={result.created_predictions} "
        f"existing_predictions={result.existing_predictions} "
        f"created_outcomes={result.created_outcomes} "
        f"existing_outcomes={result.existing_outcomes}"
    )
    print(
        "source_snapshot_ids=" + ",".join(result.source_snapshot_ids)
    )
    print(f"json_report={json_output}")
    print(f"markdown_report={markdown_output}")
    print("SYNTHETIC ENGINEERING DATA - NOT VALIDATION EVIDENCE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
