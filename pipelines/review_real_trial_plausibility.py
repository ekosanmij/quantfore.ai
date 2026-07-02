"""Generate the WP6.7 plausibility review from stored trial evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import (
        get_code_revision,
        open_research_database,
        repository_relative_path,
    )
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        get_code_revision,
        open_research_database,
        repository_relative_path,
    )

from sqlalchemy import select

from quantfore_research.backtest.baseline import BacktestObservation
from quantfore_research.db import session_scope
from quantfore_research.models import Feature, ModelOutcome, ModelPrediction, Security
from quantfore_research.validation.plausibility import (
    FeatureReviewValue,
    analyze_plausibility,
)


DEFAULT_BACKTEST = Path("reports/backtests/real_price_baseline_trial_v0_1.json")
DEFAULT_PRICE_AUDIT = Path(
    "reports/data-audits/us-equity-trial-v0-price-quality.json"
)
DEFAULT_JSON = Path(
    "reports/backtests/real_price_baseline_trial_v0_1-plausibility-review.json"
)
DEFAULT_MARKDOWN = DEFAULT_JSON.with_suffix(".md")
WARNINGS = (
    "PROTOTYPE REAL-DATA TRIAL",
    "NOT POINT-IN-TIME UNIVERSE VALIDATION",
    "NOT ELIGIBLE FOR PERFORMANCE CLAIMS",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metric(value: object) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def render_markdown(document: dict[str, object]) -> str:
    review = document["review"]
    baseline = review["baseline"]
    feature = review["feature_diagnostics"]
    mega = review["mega_cap_proxy"]
    split = review["price_review_exclusion"]
    outliers = review["outlier_sensitivity"]
    lines = ["# Real-Data Trial v0 — WP6.7 Plausibility Review", ""]
    lines.extend(f"> **{warning}**" for warning in WARNINGS)
    lines.extend(
        [
            "",
            f"**Decision:** `{review['decision']}`",
            "",
            "The data and execution path are mechanically plausible, but the stored trial does not support model-performance claims.",
            "",
            "## Findings",
            "",
        ]
    )
    lines.extend(
        f"- **{item['severity'].upper()}** — {item['finding']}"
        for item in review["findings"]
    )
    lines.extend(
        [
            "",
            "## Baseline diagnostics",
            "",
            f"- Periods: {baseline['periods']}",
            f"- Observations: {baseline['observations']}",
            f"- Coverage: {_metric(baseline['coverage'])}",
            f"- Mean Rank IC: {_metric(baseline['mean_rank_ic'])}",
            f"- Rank IC t-statistic: {_metric(baseline['rank_ic_t_statistic'])}",
            f"- Top-minus-bottom spread: {_metric(baseline['top_minus_bottom_spread'])}",
            f"- Monotonic quintiles: {baseline['quintile_returns_monotonic']}",
            "",
            "### Rank IC stability",
            "",
            "| Year | Periods | Mean Rank IC | Positive periods | Top-bottom spread |",
            "| ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in review["rank_ic_by_year"]:
        lines.append(
            f"| {item['year']} | {item['periods']} | {_metric(item['mean_rank_ic'])} | "
            f"{_metric(item['positive_rank_ic_period_percentage'])} | "
            f"{_metric(item['top_minus_bottom_spread'])} |"
        )
    lines.extend(
        [
            "",
            "## Score and feature checks",
            "",
            f"- Feature values expected/received: {feature['expected_values']}/{feature['received_values']}",
            f"- Missing/duplicate/unexpected: {feature['missing_values']}/{feature['duplicate_values']}/{feature['unexpected_values']}",
            f"- Scores at 0/100: {review['score_distribution']['at_zero']}/{review['score_distribution']['at_one_hundred']}",
            "",
            "| Feature | Min | Median | Mean | Max | Inferred clamp count |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for name, values in feature["ranges"].items():
        lines.append(
            f"| {name} | {_metric(values['minimum'])} | {_metric(values['median'])} | "
            f"{_metric(values['mean'])} | {_metric(values['maximum'])} | "
            f"{feature['driver_clamp_counts_inferred_from_features'][name]} |"
        )
    sector = review["sector_concentration"]
    lines.extend(
        [
            "",
            "## Concentration and dependence",
            "",
            f"- Largest top-quintile sector: {sector['largest_sector']} ({_metric(sector['largest_sector_share'])})",
            f"- Top-quintile sector HHI: {_metric(sector['hhi'])}",
            f"- Mega-cap proxy universe share: {_metric(mega['universe_share'])}",
            f"- Mega-cap proxy top-quintile share: {_metric(mega['top_quintile_share'])}",
            "",
            "## Before/after diagnostics",
            "",
            "| Scenario | Observations | Mean Rank IC | Top-bottom spread | Monotonic |",
            "| --- | ---: | ---: | ---: | --- |",
            f"| Baseline | {baseline['observations']} | {_metric(baseline['mean_rank_ic'])} | {_metric(baseline['top_minus_bottom_spread'])} | {baseline['quintile_returns_monotonic']} |",
        ]
    )
    for label, scenario in (
        ("Exclude WP6.3 review securities", split["after_exclusion"]),
        ("Exclude mega-cap proxy", mega["after_exclusion"]),
        ("Winsorise outcomes at +/-75%", outliers["after_winsorisation"]),
    ):
        lines.append(
            f"| {label} | {scenario['observations']} | {_metric(scenario['mean_rank_ic'])} | "
            f"{_metric(scenario['top_minus_bottom_spread'])} | {scenario['quintile_returns_monotonic']} |"
        )
    lines.extend(
        [
            "",
            f"WP6.3 review exclusions: {', '.join(split['tickers'])}.",
            f"Mega-cap proxy exclusions: {', '.join(mega['tickers'])}.",
            f"Outlier winsorisation affected {outliers['affected_observations']} observations and is a post-hoc sensitivity diagnostic only.",
            "",
            "## Largest outcome outliers",
            "",
            "| Ticker | Prediction date | Excess return |",
            "| --- | --- | ---: |",
        ]
    )
    for item in review["largest_absolute_outcomes"][:10]:
        lines.append(
            f"| {item['ticker']} | {item['prediction_date']} | {_metric(item['excess_return'])} |"
        )
    lines.extend(
        [
            "",
            "## Required follow-up",
            "",
            "1. Validate corporate actions and adjusted-price conventions for every WP6.3 review security.",
            "2. Replace the retrospective universe with point-in-time membership before any performance interpretation.",
            "3. Investigate the 2023 regime concentration and NVDA/META outcome outliers.",
            "4. Revisit the heuristic or feature normalisation; require stable, monotonic out-of-sample behaviour before promotion.",
            "5. Add realistic turnover, liquidity and market-impact modelling.",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the WP6.7 review.")
    parser.add_argument("--database-url")
    parser.add_argument("--backtest-report", type=Path, default=DEFAULT_BACKTEST)
    parser.add_argument("--price-audit", type=Path, default=DEFAULT_PRICE_AUDIT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    backtest = json.loads(args.backtest_report.read_text(encoding="utf-8"))
    audit = json.loads(args.price_audit.read_text(encoding="utf-8"))
    config = backtest["configuration"]
    if config.get("dataset_kind") != "prototype_real":
        raise ValueError("plausibility review requires prototype_real input")
    prediction_dates = {
        item["prediction_date"] for item in backtest["rank_ic_by_month"]
    }
    universe = set(config["universe"])
    sectors = {
        item["ticker"]: item["sector"] for item in config["universe_definition"]
    }
    session_factory = open_research_database(args.database_url)
    with session_scope(session_factory) as session:
        rows = list(
            session.execute(
                select(ModelPrediction, ModelOutcome, Security)
                .join(ModelOutcome, ModelOutcome.prediction_id == ModelPrediction.prediction_id)
                .join(Security, Security.security_id == ModelPrediction.security_id)
                .where(ModelPrediction.model_version == config["model_version"])
            )
        )
        rows = [
            row for row in rows
            if row[2].ticker in universe and row[0].asof_date.isoformat() in prediction_dates
        ]
        feature_set_ids = {row[0].feature_set_id for row in rows}
        feature_rows = list(
            session.execute(
                select(Feature, Security)
                .join(Security, Security.security_id == Feature.security_id)
                .where(Feature.feature_set_id.in_(feature_set_ids))
            )
        )
    observations = tuple(
        BacktestObservation(
            ticker=security.ticker,
            prediction_date=prediction.asof_date,
            score=prediction.score,
            action_label=prediction.action_label,
            excess_return=outcome.excess_return,
        )
        for prediction, outcome, security in rows
    )
    features = tuple(
        FeatureReviewValue(
            ticker=security.ticker,
            asof_date=feature.asof_date,
            feature_name=feature.feature_name,
            value=feature.value,
        )
        for feature, security in feature_rows
    )
    price_review_tickers = [
        item["ticker"]
        for item in audit["audit"]["securities"]
        if item["status"] != "pass"
    ]
    review = analyze_plausibility(
        observations=observations,
        features=features,
        sectors=sectors,
        price_review_tickers=price_review_tickers,
    )
    document = {
        "review_id": "real_price_baseline_trial_v0_1_wp6_7",
        "dataset_kind": "prototype_real",
        "claims_eligible": False,
        "code_revision": get_code_revision(),
        "source_backtest_report": repository_relative_path(args.backtest_report),
        "source_backtest_sha256": _sha256(args.backtest_report),
        "source_price_audit": repository_relative_path(args.price_audit),
        "source_price_audit_sha256": _sha256(args.price_audit),
        "trial_warnings": list(WARNINGS),
        "review": review,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.write_text(render_markdown(document), encoding="utf-8")
    print(
        f"plausibility decision={review['decision']} "
        f"json={args.json_output} markdown={args.markdown_output}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"plausibility review failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
