"""Generate the Sprint 9.4 investability diagnostic report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

try:
    import _bootstrap  # noqa: F401
    from _common import (
        DEFAULT_RAW_DIR,
        get_code_revision,
        repository_relative_path,
    )
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        DEFAULT_RAW_DIR,
        get_code_revision,
        repository_relative_path,
    )

from quantfore_research.validation.investability import (
    diagnose_sprint9_investability,
)


DEFAULT_DATABASE = (
    DEFAULT_RAW_DIR / "free-point-in-time/sprint8-prelock-v9/research.db"
)
DEFAULT_COMPARISON = Path("reports/comparisons/price-vs-multifactor-v1.json")
DEFAULT_BACKTEST = Path("reports/backtests/pit_multifactor_baseline_v1.json")
DEFAULT_COHORT_AUDIT = Path(
    "reports/data-audits/sprint9-cohort-funnel-v1.json"
)
DEFAULT_FACTOR_DIAGNOSTIC = Path(
    "reports/research/sprint9-factor-diagnostics-v1.json"
)
DEFAULT_CONTRACT = Path("docs/research/multifactor-baseline-v1.md")
DEFAULT_JSON_OUTPUT = Path(
    "reports/backtests/sprint9-investability-diagnostic-v1.json"
)
DEFAULT_MARKDOWN_OUTPUT = Path(
    "reports/backtests/sprint9-investability-diagnostic-v1.md"
)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///file:{path.resolve()}?mode=ro&uri=true"


def _open_read_only_session(database_url: str):
    engine = create_engine(
        database_url,
        future=True,
        connect_args={"check_same_thread": False}
        if database_url.startswith("sqlite")
        else {},
    )
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _query_only(dbapi_connection, connection_record):
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA query_only=ON")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA cache_size=-262144")
            cursor.close()

    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


def _write_atomic(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return hashlib.sha256(payload).hexdigest()


def _percent(value: Any, *, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.{decimals}f}%"


def _number(value: Any, *, decimals: int = 4) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{decimals}f}"


def _usd(value: Any) -> str:
    if value is None:
        return "—"
    amount = float(value)
    if abs(amount) >= 1_000_000_000:
        return f"${amount / 1_000_000_000:.2f}bn"
    if abs(amount) >= 1_000_000:
        return f"${amount / 1_000_000:.2f}m"
    return f"${amount:,.0f}"


def render_markdown(document: Mapping[str, Any]) -> str:
    diagnostic = document["diagnostic"]
    scope = diagnostic["scope"]
    top = diagnostic["long_only_top_bucket"]
    costs = diagnostic["transaction_costs"]
    comparison = diagnostic["equal_weight_comparison"]
    drawdown = diagnostic["drawdown_and_downside_capture"]
    concentration = diagnostic["concentration"]
    liquidity = diagnostic["liquidity"]
    causes = diagnostic["root_cause_assessment"]
    lines = [
        "# Sprint 9.4 Investability Diagnostic v1",
        "",
        "`claims_eligible=false`",
        "",
        f"- Decision: `{diagnostic['decision']}`",
        f"- Deployable portfolio evaluable: `{str(diagnostic['deployable_portfolio_evaluable']).lower()}`",
        f"- Evidence generated: `{document['generated_at']}`",
        f"- Code revision: `{document['code_revision']}`",
        f"- Warehouse: `{document['warehouse']['path']}`",
        f"- Machine-readable companion: [`{Path(document['json_output']).name}`]({Path(document['json_output']).name})",
        "",
        "## Decision",
        "",
        "> **The observed Sprint 8 cohort does not support an investable portfolio. "
        "The selected basket underperforms SPY before costs and underperforms the same "
        "eligible cohort held equal weight. A deployable capital-account backtest is not "
        "evaluable from these overlapping, single-name forward windows.**",
        "",
        f"The diagnostic covers `{scope['rebalance_periods']}` monthly rebalance periods "
        f"and `{scope['evaluated_stock_months']}` stock-months. The top bucket contains "
        "exactly one security in every month, every selected holding is labelled "
        f"`{', '.join(scope['sectors_represented'])}`, and "
        f"`{scope['eligible_securities_per_period']['singleton_periods']}` months contain "
        "only one eligible security. No bottom bucket exists.",
        "",
        "The selected stock earns an average 126-session return of "
        f"`{_percent(top['mean_forward_security_return'])}` while SPY earns "
        f"`{_percent(top['mean_forward_benchmark_return'])}` on the aligned windows. "
        f"Gross excess is `{_percent(top['mean_gross_excess_return'])}` and net excess "
        f"after 25 bps is `{_percent(costs['25_bps']['mean_net_excess_return'])}`. "
        "The result is negative before transaction costs, so cost drag is not the root cause.",
        "",
        "## Portfolio outcome summary",
        "",
        "| Metric | Result | Interpretation |",
        "| --- | ---: | --- |",
        f"| Mean selected-stock forward return | {_percent(top['mean_forward_security_return'])} | Arithmetic mean of overlapping 126-session cohorts. |",
        f"| Mean aligned SPY return | {_percent(top['mean_forward_benchmark_return'])} | Same entry/exit sessions as each selected holding. |",
        f"| Mean gross excess return | {_percent(top['mean_gross_excess_return'])} | Negative before costs. |",
        f"| Gross benchmark hit rate | {_percent(top['gross_benchmark_hit_rate'])} | Selected stock beats SPY in 17 of 43 periods. |",
        f"| Positive absolute return rate | {_percent(top['positive_absolute_return_rate'])} | Selected stock has a positive return in 21 of 43 periods. |",
        f"| Mean eligible equal-weight excess return | {_percent(comparison['eligible_equal_weight_mean_excess_return'])} | The narrow cohort itself also underperforms SPY. |",
        f"| Selected minus eligible equal-weight | {_percent(comparison['model_selected_minus_eligible_equal_weight_excess'])} | Ranking reduces return versus holding every eligible name. |",
        "",
        "These figures are not annualized or compounded. Monthly cohorts have overlapping "
        "126-session holding windows, so treating them as a single sequential equity curve "
        "would double-count capital.",
        "",
        "## Top-minus-bottom",
        "",
        f"Top-minus-bottom is **not evaluable**: `{diagnostic['top_minus_bottom']['periods_with_bottom_bucket']}` "
        "of 43 months contain a bottom bucket. The largest eligible cohort contains four "
        "securities; quintile 1 requires at least five. No spread, monotonicity, or "
        "long-short portfolio claim can be made.",
        "",
        "## Turnover and transaction costs",
        "",
        f"Mean selection turnover is `{_percent(diagnostic['turnover']['mean'])}` and the "
        f"median is `{_percent(diagnostic['turnover']['median'])}`. Turnover is non-zero in "
        f"`{diagnostic['turnover']['nonzero_periods']}` periods, including initial entry. "
        "Because the top basket is a single name, turnover is either 0% or 100%.",
        "",
        "| Cost assumption | Mean net excess return | Mean cost drag | Net benchmark hit rate |",
        "| ---: | ---: | ---: | ---: |",
    ]
    for key in ("10_bps", "25_bps", "50_bps"):
        row = costs[key]
        lines.append(
            f"| {row['cost_bps']} bps | {_percent(row['mean_net_excess_return'])} | "
            f"{_percent(row['mean_cost_drag'], decimals=4)} | "
            f"{_percent(row['net_benchmark_hit_rate'])} |"
        )
    lines.extend(
        [
            "",
            "At 25 bps the average drag is only 3.49 basis points, compared with gross "
            "excess of -6.37%. This cost calculation does not include bid-ask spread or "
            "market impact.",
            "",
            "### Monthly turnover ledger",
            "",
            "| Prediction date | Selected holding | Turnover |",
            "| --- | --- | ---: |",
        ]
    )
    for row in diagnostic["turnover"]["periods"]:
        lines.append(
            f"| {row['prediction_date']} | {', '.join(row['holdings'])} | "
            f"{_percent(row['turnover'])} |"
        )
    selected_occurrences = concentration["single_name"]["selection_occurrences"]
    sector_occurrences = concentration["sector"]["selection_occurrences"]
    lines.extend(
        [
            "",
            "## Drawdown and downside capture",
            "",
            "| Diagnostic | Result |",
            "| --- | ---: |",
            f"| Mean selected holding-window max drawdown | {_percent(drawdown['selected_holding_path']['mean_max_drawdown'])} |",
            f"| Median selected holding-window max drawdown | {_percent(drawdown['selected_holding_path']['median_max_drawdown'])} |",
            f"| Worst selected holding-window max drawdown | {_percent(drawdown['selected_holding_path']['worst_max_drawdown'])} |",
            f"| Down-market periods | {drawdown['downside_capture']['down_market_periods']} |",
            f"| Mean selected return in down markets | {_percent(drawdown['downside_capture']['mean_selected_security_return'])} |",
            f"| Mean SPY return in down markets | {_percent(drawdown['downside_capture']['mean_benchmark_return'])} |",
            f"| Downside capture | {_number(drawdown['downside_capture']['percentage'], decimals=2)}% |",
            "",
            "The worst single cohort loses 57.00% peak-to-trough and downside capture is "
            "134.62%, meaning the selected holding loses more than SPY on average when SPY "
            "is down. A stitched capital-account max drawdown is not reported because no "
            "non-overlapping daily allocation ledger exists.",
            "",
            "## Concentration",
            "",
            "| Measure | Result |",
            "| --- | ---: |",
            f"| Holdings per selected basket | {concentration['single_name']['minimum_holdings_per_period']} |",
            f"| Maximum single-name weight | {_percent(concentration['single_name']['maximum_period_name_weight'])} |",
            f"| Mean single-name HHI | {_number(concentration['single_name']['mean_hhi'])} |",
            f"| Unique selected names | {concentration['single_name']['unique_selected_names']} |",
            f"| Maximum sector weight | {_percent(concentration['sector']['maximum_period_sector_weight'])} |",
            f"| Mean sector HHI | {_number(concentration['sector']['mean_hhi'])} |",
            f"| Unique selected sectors | {concentration['sector']['unique_selected_sectors']} |",
            "",
            "| Selected name | Periods selected | Share of periods |",
            "| --- | ---: | ---: |",
        ]
    )
    for row in selected_occurrences:
        lines.append(
            f"| {row['ticker']} | {row['periods_selected']} | "
            f"{_percent(row['period_share'])} |"
        )
    lines.extend(
        [
            "",
            f"The only selected sector is `{sector_occurrences[0]['sector']}` at "
            f"`{_percent(sector_occurrences[0]['holding_observation_share'])}` of holding "
            "observations. This is complete sector and single-name concentration in each "
            "period, not a diversified portfolio.",
            "",
            "## Liquidity screen",
            "",
            "Volume is available, so the report uses the point-in-time median of "
            "unadjusted close × reported volume over the 20 sessions ending on each "
            "prediction date.",
            "",
            "| Liquidity statistic | Result |",
            "| --- | ---: |",
            f"| Complete 20-session windows | {liquidity['complete_lookback_observations']} / {liquidity['selected_holding_observations']} |",
            f"| Minimum median daily dollar volume | {_usd(liquidity['minimum_median_daily_dollar_volume_20d'])} |",
            f"| Median median daily dollar volume | {_usd(liquidity['median_median_daily_dollar_volume_20d'])} |",
            f"| Maximum median daily dollar volume | {_usd(liquidity['maximum_median_daily_dollar_volume_20d'])} |",
            "",
            "| Diagnostic threshold | Holding observations passing | Pass rate |",
            "| ---: | ---: | ---: |",
        ]
    )
    for row in liquidity["threshold_checks"]:
        lines.append(
            f"| {_usd(row['threshold_usd'])} | "
            f"{row['holding_observations_passing']} / {row['holding_observations_evaluated']} | "
            f"{_percent(row['pass_rate'])} |"
        )
    lines.extend(
        [
            "",
            "All selected holding observations pass the $25 million screen; liquidity is "
            "therefore not the observed reason for the negative result. These are diagnostic "
            "thresholds, not promotion gates, and dollar volume alone does not establish "
            "capacity or executable slippage.",
            "",
            "## Model selection versus equal weight",
            "",
            f"The eligible equal-weight basket has mean excess return "
            f"`{_percent(comparison['eligible_equal_weight_mean_excess_return'])}` versus "
            f"SPY. Model selection reduces that by a further "
            f"`{_percent(comparison['model_selected_minus_eligible_equal_weight_excess'])}` "
            "across all periods. In the nine months with an actual choice among multiple "
            "names, selection lift averages "
            f"`{_percent(comparison['multi_name_mean_selection_lift'])}` and is positive in "
            f"`{_percent(comparison['multi_name_positive_selection_lift_rate'])}` of those months.",
            "",
            "The comparison separates two effects: the narrow Financials-labelled cohort "
            "itself trails SPY, and the model's top selection trails that narrow cohort. "
            "Because 34 months are singletons and no sector-neutral benchmark was frozen, "
            "the relative contribution of weak signal and benchmark mismatch cannot be "
            "identified cleanly.",
            "",
            "## Why net excess is negative",
            "",
            "| Candidate cause | Finding | Evidence |",
            "| --- | --- | --- |",
            f"| Transaction costs | **Not primary** | 25 bps costs add only {_percent(causes['cost_drag']['cost_drag_25_bps'], decimals=4)} drag; gross excess is already {_percent(causes['cost_drag']['gross_excess_return'])}. |",
            f"| Model selection | **Negative incremental value** | Selected minus eligible equal-weight is {_percent(causes['model_selection']['selected_minus_eligible_equal_weight_excess'])}; multi-name-month lift is {_percent(causes['model_selection']['multi_name_month_selection_lift'])}. |",
            "| Cohort construction | **Dominant structural limitation** | 34 singleton months, one selected name, one selected sector, and no bottom bucket. |",
            f"| Benchmark mismatch | **Unresolved** | The eligible Financials-labelled cohort is {_percent(causes['benchmark_match']['eligible_equal_weight_excess_vs_spy'])} versus broad-market SPY; no sector-neutral benchmark exists. |",
            f"| Liquidity | **Not an observed bottleneck** | Minimum trailing median daily dollar volume is {_usd(causes['liquidity']['minimum_median_daily_dollar_volume_20d'])}. |",
            "| Weak signal | **Not separately identifiable** | Only nine tiny cross-sections have Rank IC, while portfolio selection is negative. |",
            "",
            causes["conclusion"],
            "",
            "## Implementability boundary",
            "",
            "The current evidence is a cohort-level forward-outcome diagnostic, not a "
            "deployable portfolio backtest. It lacks a single daily capital-allocation "
            "curve, non-overlapping return protocol, bid-ask spreads, market impact, and a "
            "sector-neutral comparator. Accordingly:",
            "",
            f"- Investability established: `{str(diagnostic['implementability_assessment']['investability_established']).lower()}`",
            f"- Ranking usefulness for portfolio construction established: `{str(diagnostic['implementability_assessment']['ranking_useful_for_portfolio_construction_established']).lower()}`",
            f"- Annualized return reported: `{str(diagnostic['implementability_assessment']['annualized_return_reported']).lower()}`",
            f"- Stitched capital-account curve available: `{str(diagnostic['implementability_assessment']['stitched_capital_account_curve_available']).lower()}`",
            f"- Volume screen available: `{str(diagnostic['implementability_assessment']['volume_screen_available']).lower()}`",
            "",
            "## Evidence integrity",
            "",
            "All reconstructed gross return, equal-weight return, turnover, cost, drawdown, "
            "and downside-capture metrics reconcile to the published Sprint 8 comparison "
            f"within `{diagnostic['integrity']['published_metric_reconciliation']['tolerance']}`.",
            "",
            "| Artifact | SHA-256 |",
            "| --- | --- |",
        ]
    )
    for source in document["source_artifacts"]:
        lines.append(f"| `{source['path']}` | `{source['sha256']}` |")
    lines.extend(
        [
            "",
            "The large derived warehouse is bound through the reconstructed outcome, "
            "portfolio-period, and point-in-time liquidity hashes in the JSON companion.",
            "",
            "## Claims boundary",
            "",
            "This report does not establish predictive value, outperformance, "
            "investability, suitability, executable capacity, or investment advice. "
            "`claims_eligible=false` remains mandatory.",
            "",
        ]
    )
    return "\n".join(lines)


def build_document(
    diagnostic: Mapping[str, Any],
    *,
    generated_at: datetime,
    code_revision: Optional[str],
    source_artifacts: Sequence[Mapping[str, str]],
    warehouse_path: str,
    json_output: Path,
) -> dict[str, Any]:
    return {
        "report_id": "sprint9-investability-diagnostic-v1",
        "schema_version": "sprint9_investability_diagnostic_v1",
        "claims_eligible": False,
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "code_revision": code_revision,
        "decision": diagnostic["decision"],
        "json_output": repository_relative_path(json_output),
        "source_artifacts": list(source_artifacts),
        "warehouse": {
            "path": warehouse_path,
            "file_sha256_omitted": True,
            "reason": (
                "Large derived warehouse is bound by deterministic outcome, portfolio, "
                "and liquidity fingerprints."
            ),
        },
        "diagnostic": dict(diagnostic),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose Sprint 8 cohort-level portfolio investability."
    )
    parser.add_argument("--database-url")
    parser.add_argument("--database-path", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--comparison-json", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--backtest-json", type=Path, default=DEFAULT_BACKTEST)
    parser.add_argument("--cohort-audit-json", type=Path, default=DEFAULT_COHORT_AUDIT)
    parser.add_argument(
        "--factor-diagnostic-json",
        type=Path,
        default=DEFAULT_FACTOR_DIAGNOSTIC,
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument("--generated-at", type=_parse_timestamp)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        sources = [
            args.comparison_json,
            args.backtest_json,
            args.cohort_audit_json,
            args.factor_diagnostic_json,
            args.contract,
        ]
        source_artifacts = [
            {
                "path": repository_relative_path(path),
                "sha256": _sha256_file(path),
            }
            for path in sources
        ]
        factory = _open_read_only_session(
            args.database_url or _read_only_database_url(args.database_path)
        )
        with factory() as session:
            diagnostic = diagnose_sprint9_investability(
                session,
                comparison=_load_json(args.comparison_json),
                backtest=_load_json(args.backtest_json),
                cohort_audit=_load_json(args.cohort_audit_json),
                factor_diagnostic=_load_json(args.factor_diagnostic_json),
            )
        document = build_document(
            diagnostic,
            generated_at=args.generated_at or datetime.now(timezone.utc),
            code_revision=get_code_revision(),
            source_artifacts=source_artifacts,
            warehouse_path=(
                repository_relative_path(args.database_path)
                if args.database_url is None
                else "external-read-only-database"
            ),
            json_output=args.json_output,
        )
        json_payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        markdown_payload = render_markdown(document).encode("utf-8")
        json_sha = _write_atomic(args.json_output, json_payload)
        markdown_sha = _write_atomic(args.markdown_output, markdown_payload)
    except (KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Sprint 9 investability diagnostic failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"decision={diagnostic['decision']} "
        f"periods={diagnostic['scope']['rebalance_periods']} "
        f"json_sha256={json_sha} markdown_sha256={markdown_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
