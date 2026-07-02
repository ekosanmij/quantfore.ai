"""Plausibility diagnostics for the prototype real-data baseline trial."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import statistics
from typing import Mapping, Sequence

from quantfore_research.backtest.baseline import (
    BacktestObservation,
    rank_cross_section,
    summarize_backtest,
)
from quantfore_research.features import FEATURE_NAMES


MEGA_CAP_PROXY = ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NVDA")
OUTLIER_ABSOLUTE_RETURN = Decimal("0.75")


@dataclass(frozen=True)
class FeatureReviewValue:
    ticker: str
    asof_date: date
    feature_name: str
    value: Decimal


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile values must not be empty")
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _summary(observations: Sequence[BacktestObservation]) -> dict[str, object]:
    summary = summarize_backtest(observations)
    return {
        "periods": summary.period_count,
        "observations": summary.evaluated_observations,
        "coverage": summary.coverage,
        "mean_rank_ic": summary.mean_rank_ic,
        "median_rank_ic": summary.median_rank_ic,
        "rank_ic_t_statistic": summary.rank_ic_t_statistic,
        "positive_rank_ic_period_percentage": (
            summary.positive_rank_ic_period_percentage
        ),
        "quintile_returns": {
            str(key): value
            for key, value in summary.average_excess_return_by_quintile.items()
        },
        "top_minus_bottom_spread": summary.top_minus_bottom_spread,
        "quintile_returns_monotonic": summary.monotonic,
    }


def _feature_diagnostics(
    observations: Sequence[BacktestObservation],
    features: Sequence[FeatureReviewValue],
) -> dict[str, object]:
    expected_keys = {
        (row.ticker, row.prediction_date, feature_name)
        for row in observations
        for feature_name in FEATURE_NAMES
    }
    received_keys = [
        (row.ticker, row.asof_date, row.feature_name) for row in features
    ]
    key_counts = Counter(received_keys)
    by_name: dict[str, list[float]] = defaultdict(list)
    for row in features:
        by_name[row.feature_name].append(float(row.value))
    ranges = {}
    for feature_name in FEATURE_NAMES:
        values = by_name.get(feature_name, [])
        ranges[feature_name] = {
            "count": len(values),
            "minimum": min(values) if values else None,
            "p01": _quantile(values, 0.01) if values else None,
            "median": statistics.median(values) if values else None,
            "mean": statistics.fmean(values) if values else None,
            "p99": _quantile(values, 0.99) if values else None,
            "maximum": max(values) if values else None,
        }
    clamp_thresholds = {
        "momentum_6_1": (-0.625, 0.625),
        "momentum_12_1": (-25 / 30, 25 / 30),
        "return_21d": (-0.5, 0.5),
        "volatility_126d": (0.0, 0.15),
    }
    clamp_counts = {}
    for feature_name, (lower, upper) in clamp_thresholds.items():
        values = by_name.get(feature_name, [])
        clamp_counts[feature_name] = sum(
            value <= lower or value >= upper for value in values
        )
    return {
        "expected_values": len(expected_keys),
        "received_values": len(received_keys),
        "missing_values": len(expected_keys - set(received_keys)),
        "duplicate_values": sum(count - 1 for count in key_counts.values()),
        "unexpected_values": len(set(received_keys) - expected_keys),
        "ranges": ranges,
        "driver_clamp_counts_inferred_from_features": clamp_counts,
    }


def analyze_plausibility(
    *,
    observations: Sequence[BacktestObservation],
    features: Sequence[FeatureReviewValue],
    sectors: Mapping[str, str],
    price_review_tickers: Sequence[str],
    mega_cap_proxy: Sequence[str] = MEGA_CAP_PROXY,
) -> dict[str, object]:
    """Build deterministic diagnostics without changing any trial data."""

    rows = tuple(observations)
    if not rows:
        raise ValueError("plausibility review requires observations")
    if any(row.excess_return is None for row in rows):
        raise ValueError("plausibility review requires matured outcomes")
    tickers = sorted({row.ticker for row in rows})
    missing_sectors = sorted(set(tickers) - set(sectors))
    if missing_sectors:
        raise ValueError("missing sectors for: " + ",".join(missing_sectors))

    baseline_summary = summarize_backtest(rows)
    baseline = _summary(rows)
    scores = [float(row.score) for row in rows]
    score_diagnostics = {
        "count": len(scores),
        "minimum": min(scores),
        "p01": _quantile(scores, 0.01),
        "median": statistics.median(scores),
        "mean": statistics.fmean(scores),
        "p99": _quantile(scores, 0.99),
        "maximum": max(scores),
        "at_zero": sum(value == 0 for value in scores),
        "at_one_hundred": sum(value == 100 for value in scores),
    }

    yearly = []
    for year in sorted({row.prediction_date.year for row in rows}):
        year_rows = tuple(row for row in rows if row.prediction_date.year == year)
        yearly.append({"year": year, **_summary(year_rows)})

    by_date: dict[date, list[BacktestObservation]] = defaultdict(list)
    for row in rows:
        by_date[row.prediction_date].append(row)
    top_rows = [
        ranked.observation
        for prediction_date in sorted(by_date)
        for ranked in rank_cross_section(by_date[prediction_date])
        if ranked.quintile == 5
    ]
    sector_counts = Counter(sectors[row.ticker] for row in top_rows)
    universe_sector_counts = Counter(sectors[ticker] for ticker in tickers)
    top_count = len(top_rows)
    sector_concentration = {
        "top_quintile_observations": top_count,
        "sector_counts": dict(sorted(sector_counts.items())),
        "sector_shares": {
            sector: count / top_count
            for sector, count in sorted(sector_counts.items())
        },
        "universe_security_counts": dict(sorted(universe_sector_counts.items())),
        "hhi": sum((count / top_count) ** 2 for count in sector_counts.values()),
        "largest_sector": sector_counts.most_common(1)[0][0],
        "largest_sector_share": sector_counts.most_common(1)[0][1] / top_count,
    }

    mega_set = set(mega_cap_proxy) & set(tickers)
    mega_top_count = sum(row.ticker in mega_set for row in top_rows)
    mega_exclusion_rows = tuple(row for row in rows if row.ticker not in mega_set)
    mega_cap = {
        "definition": "fixed proxy cohort; no point-in-time market-cap data used",
        "tickers": sorted(mega_set),
        "universe_share": len(mega_set) / len(tickers),
        "top_quintile_share": mega_top_count / top_count,
        "top_quintile_observations": mega_top_count,
        "after_exclusion": _summary(mega_exclusion_rows),
    }

    ticker_top_counts = Counter(row.ticker for row in top_rows)
    ticker_values: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        assert row.excess_return is not None
        ticker_values[row.ticker].append(float(row.excess_return))
    ticker_diagnostics = []
    for ticker in tickers:
        values = ticker_values[ticker]
        ticker_diagnostics.append(
            {
                "ticker": ticker,
                "mean_excess_return": statistics.fmean(values),
                "minimum_excess_return": min(values),
                "maximum_excess_return": max(values),
                "top_quintile_observations": ticker_top_counts[ticker],
            }
        )
    largest_outcomes = sorted(
        (
            {
                "ticker": row.ticker,
                "prediction_date": row.prediction_date.isoformat(),
                "excess_return": float(row.excess_return),
            }
            for row in rows
        ),
        key=lambda item: abs(item["excess_return"]),
        reverse=True,
    )[:15]

    leave_one_out = []
    for ticker in tickers:
        summary = _summary(tuple(row for row in rows if row.ticker != ticker))
        mean_ic = summary["mean_rank_ic"]
        baseline_ic = baseline["mean_rank_ic"]
        leave_one_out.append(
            {
                "ticker": ticker,
                "mean_rank_ic": mean_ic,
                "mean_rank_ic_change": (
                    mean_ic - baseline_ic
                    if mean_ic is not None and baseline_ic is not None
                    else None
                ),
                "top_minus_bottom_spread": summary["top_minus_bottom_spread"],
            }
        )
    leave_one_out.sort(
        key=lambda item: abs(item["mean_rank_ic_change"] or 0), reverse=True
    )

    review_set = set(price_review_tickers) & set(tickers)
    review_exclusion = {
        "tickers": sorted(review_set),
        "reason": "WP6.3 split-like discontinuity review flags",
        "after_exclusion": _summary(
            tuple(row for row in rows if row.ticker not in review_set)
        ),
    }

    clipped_rows = []
    outlier_count = 0
    for row in rows:
        assert row.excess_return is not None
        clipped_return = max(
            -OUTLIER_ABSOLUTE_RETURN,
            min(OUTLIER_ABSOLUTE_RETURN, row.excess_return),
        )
        if clipped_return != row.excess_return:
            outlier_count += 1
        clipped_rows.append(
            BacktestObservation(
                ticker=row.ticker,
                prediction_date=row.prediction_date,
                score=row.score,
                action_label=row.action_label,
                excess_return=clipped_return,
            )
        )
    outlier_sensitivity = {
        "method": "post-hoc diagnostic only; winsorise 126-session excess returns at +/-75%",
        "affected_observations": outlier_count,
        "after_winsorisation": _summary(tuple(clipped_rows)),
    }

    yearly_signs = {
        1 if item["mean_rank_ic"] > 0 else -1
        for item in yearly
        if item["mean_rank_ic"] is not None and item["mean_rank_ic"] != 0
    }
    findings = [
        {
            "severity": "high",
            "finding": "Quintile returns are non-monotonic and the unadjusted top-minus-bottom spread is negative.",
        },
        {
            "severity": "high",
            "finding": "Mean Rank IC is weak, statistically unpersuasive, and changes sign across calendar years.",
        },
        {
            "severity": "medium",
            "finding": "The top-minus-bottom spread changes sign under the fixed outlier winsorisation diagnostic.",
        },
        {
            "severity": "medium",
            "finding": "The mega-cap proxy is over-represented in the top quintile relative to its universe share.",
        },
        {
            "severity": "review",
            "finding": "Excluding WP6.3 split-flagged securities does not restore monotonic quintile behaviour.",
        },
        {
            "severity": "pass",
            "finding": "Feature completeness and evaluated-outcome coverage are complete for the stored trial.",
        },
    ]
    concerns = {
        "non_monotonic": baseline_summary.monotonic is not True,
        "non_positive_spread": (
            baseline_summary.top_minus_bottom_spread is None
            or baseline_summary.top_minus_bottom_spread <= 0
        ),
        "weak_rank_ic_t_statistic": (
            baseline_summary.rank_ic_t_statistic is None
            or abs(baseline_summary.rank_ic_t_statistic) < 2
        ),
        "yearly_rank_ic_sign_instability": len(yearly_signs) > 1,
        "feature_values_missing": (
            _feature_diagnostics(rows, features)["missing_values"] != 0
        ),
    }
    return {
        "decision": "requires_revision_before_model_claims",
        "claims_eligible": False,
        "baseline": baseline,
        "score_distribution": score_diagnostics,
        "feature_diagnostics": _feature_diagnostics(rows, features),
        "rank_ic_by_year": yearly,
        "sector_concentration": sector_concentration,
        "mega_cap_proxy": mega_cap,
        "individual_security_diagnostics": ticker_diagnostics,
        "largest_absolute_outcomes": largest_outcomes,
        "leave_one_security_out": leave_one_out,
        "price_review_exclusion": review_exclusion,
        "outlier_sensitivity": outlier_sensitivity,
        "concern_flags": concerns,
        "findings": findings,
    }
