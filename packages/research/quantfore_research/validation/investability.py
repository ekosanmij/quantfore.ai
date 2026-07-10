"""Deterministic Sprint 9.4 investability diagnostics for the Sprint 8 cohort."""

from __future__ import annotations

import hashlib
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from quantfore_research.backtest.baseline import (
    BacktestObservation,
    rank_cross_section,
)
from quantfore_research.validation.factor_diagnostics import (
    FamilyEvaluationRow,
    _load_evaluation_rows,
    _load_scores,
)


PRIMARY_HORIZON = "126d"
TRANSACTION_COSTS_BPS = (10, 25, 50)
LIQUIDITY_THRESHOLDS_USD = (
    1_000_000,
    5_000_000,
    10_000_000,
    25_000_000,
    50_000_000,
    100_000_000,
)
LIQUIDITY_LOOKBACK_SESSIONS = 20
RECONCILIATION_TOLERANCE = Decimal("0.00001")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _mean(values: Sequence[Decimal]) -> Optional[Decimal]:
    return sum(values, Decimal("0")) / Decimal(len(values)) if values else None


def _median(values: Sequence[Decimal]) -> Optional[Decimal]:
    return Decimal(str(statistics.median(values))) if values else None


def _number(value: Optional[Decimal]) -> Optional[float]:
    return float(value) if value is not None else None


@dataclass(frozen=True)
class InvestabilityPeriod:
    """One monthly ranked cohort and its equal-weight top/bottom selections."""

    prediction_date: date
    eligible: tuple[FamilyEvaluationRow, ...]
    selected: tuple[FamilyEvaluationRow, ...]
    bottom: tuple[FamilyEvaluationRow, ...]
    turnover: Decimal

    @staticmethod
    def _average(
        rows: Sequence[FamilyEvaluationRow], attribute: str
    ) -> Optional[Decimal]:
        return _mean([getattr(row, attribute) for row in rows])

    @property
    def selected_security_return(self) -> Optional[Decimal]:
        return self._average(self.selected, "realised_return")

    @property
    def selected_benchmark_return(self) -> Optional[Decimal]:
        return self._average(self.selected, "benchmark_return")

    @property
    def selected_excess_return(self) -> Optional[Decimal]:
        return self._average(self.selected, "excess_return")

    @property
    def eligible_security_return(self) -> Optional[Decimal]:
        return self._average(self.eligible, "realised_return")

    @property
    def eligible_benchmark_return(self) -> Optional[Decimal]:
        return self._average(self.eligible, "benchmark_return")

    @property
    def eligible_excess_return(self) -> Optional[Decimal]:
        return self._average(self.eligible, "excess_return")

    @property
    def bottom_excess_return(self) -> Optional[Decimal]:
        return self._average(self.bottom, "excess_return")


def build_portfolio_periods(
    rows: Sequence[FamilyEvaluationRow],
) -> tuple[InvestabilityPeriod, ...]:
    """Freeze monthly quintiles and calculate set-overlap selection turnover."""

    if not rows:
        raise ValueError("investability diagnostic requires evaluated rows")
    keys = [(row.prediction_date, row.security_id) for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("investability rows contain duplicate date/security keys")
    by_date: dict[date, list[FamilyEvaluationRow]] = defaultdict(list)
    for row in rows:
        by_date[row.prediction_date].append(row)

    output = []
    prior: set[str] = set()
    for prediction_date in sorted(by_date):
        eligible = tuple(
            sorted(by_date[prediction_date], key=lambda row: row.security_id)
        )
        by_ticker = {row.ticker: row for row in eligible}
        if len(by_ticker) != len(eligible):
            raise ValueError("monthly investability cohort contains duplicate tickers")
        ranked = rank_cross_section(
            BacktestObservation(
                ticker=row.ticker,
                prediction_date=prediction_date,
                score=row.final_score,
                action_label="RANKED",
                excess_return=row.excess_return,
            )
            for row in eligible
        )
        selected = tuple(
            by_ticker[row.observation.ticker]
            for row in ranked
            if row.quintile == 5
        )
        bottom = tuple(
            by_ticker[row.observation.ticker]
            for row in ranked
            if row.quintile == 1
        )
        current = {row.security_id for row in selected}
        if not prior:
            turnover = Decimal("1") if current else Decimal("0")
        else:
            denominator = max(len(prior), len(current))
            turnover = (
                Decimal("1")
                - Decimal(len(prior & current)) / Decimal(denominator)
                if denominator
                else Decimal("0")
            )
        output.append(
            InvestabilityPeriod(
                prediction_date=prediction_date,
                eligible=eligible,
                selected=selected,
                bottom=bottom,
                turnover=turnover,
            )
        )
        prior = current
    return tuple(output)


def cost_sensitivity(
    periods: Sequence[InvestabilityPeriod],
    *,
    costs_bps: Sequence[int] = TRANSACTION_COSTS_BPS,
) -> dict[str, dict[str, Any]]:
    """Apply one-way cost to each period's frozen selection turnover."""

    output = {}
    for bps in costs_bps:
        if isinstance(bps, bool) or int(bps) < 0:
            raise ValueError("transaction-cost bps must be non-negative")
        evaluated = [
            (
                period,
                period.selected_excess_return
                - period.turnover * Decimal(int(bps)) / Decimal("10000"),
            )
            for period in periods
            if period.selected_excess_return is not None
        ]
        net = [value for _, value in evaluated]
        gross = [
            period.selected_excess_return
            for period, _ in evaluated
            if period.selected_excess_return is not None
        ]
        output[f"{int(bps)}_bps"] = {
            "cost_bps": int(bps),
            "evaluated_periods": len(net),
            "mean_gross_excess_return": _number(_mean(gross)),
            "mean_net_excess_return": _number(_mean(net)),
            "mean_cost_drag": _number(
                (_mean(gross) or Decimal("0")) - (_mean(net) or Decimal("0"))
            ),
            "net_benchmark_hit_rate": (
                sum(value > 0 for value in net) / len(net) if net else None
            ),
            "method": "gross_excess_minus_selection_turnover_times_one_way_cost",
        }
    return output


def concentration_summary(
    periods: Sequence[InvestabilityPeriod],
) -> dict[str, Any]:
    """Measure equal-weight selected-name and selected-sector concentration."""

    name_hhi = []
    sector_hhi = []
    maximum_name_weights = []
    maximum_sector_weights = []
    holding_counts = []
    occurrences = Counter()
    sector_occurrences = Counter()
    for period in periods:
        count = len(period.selected)
        holding_counts.append(count)
        if not count:
            continue
        name_weight = Decimal("1") / Decimal(count)
        name_hhi.append(Decimal(count) * name_weight * name_weight)
        maximum_name_weights.append(name_weight)
        occurrences.update(row.ticker for row in period.selected)
        sectors = Counter(row.sector or "Unknown" for row in period.selected)
        sector_weights = {
            sector: Decimal(value) / Decimal(count)
            for sector, value in sectors.items()
        }
        sector_hhi.append(sum((value * value for value in sector_weights.values()), Decimal("0")))
        maximum_sector_weights.append(max(sector_weights.values()))
        sector_occurrences.update(sectors)
    return {
        "single_name": {
            "periods": len(periods),
            "unique_selected_names": len(occurrences),
            "minimum_holdings_per_period": min(holding_counts, default=0),
            "maximum_holdings_per_period": max(holding_counts, default=0),
            "mean_holdings_per_period": (
                statistics.fmean(holding_counts) if holding_counts else None
            ),
            "mean_hhi": _number(_mean(name_hhi)),
            "maximum_hhi": _number(max(name_hhi)) if name_hhi else None,
            "maximum_period_name_weight": _number(
                max(maximum_name_weights)
            )
            if maximum_name_weights
            else None,
            "selection_occurrences": [
                {
                    "ticker": ticker,
                    "periods_selected": count,
                    "period_share": count / len(periods) if periods else None,
                }
                for ticker, count in sorted(occurrences.items())
            ],
        },
        "sector": {
            "unique_selected_sectors": len(sector_occurrences),
            "mean_hhi": _number(_mean(sector_hhi)),
            "maximum_hhi": _number(max(sector_hhi)) if sector_hhi else None,
            "maximum_period_sector_weight": _number(
                max(maximum_sector_weights)
            )
            if maximum_sector_weights
            else None,
            "selection_occurrences": [
                {
                    "sector": sector,
                    "holding_observations": count,
                    "holding_observation_share": (
                        count / sum(sector_occurrences.values())
                        if sector_occurrences
                        else None
                    ),
                }
                for sector, count in sorted(sector_occurrences.items())
            ],
        },
    }


def summarize_liquidity_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    thresholds_usd: Sequence[int] = LIQUIDITY_THRESHOLDS_USD,
) -> dict[str, Any]:
    """Summarize point-in-time trailing dollar-volume checks."""

    valid = [
        Decimal(str(row["median_daily_dollar_volume_20d"]))
        for row in rows
        if row.get("median_daily_dollar_volume_20d") is not None
    ]
    complete = [
        row
        for row in rows
        if int(row.get("valid_lookback_sessions", 0))
        >= LIQUIDITY_LOOKBACK_SESSIONS
    ]
    return {
        "method": (
            "Median of unadjusted close times reported volume over the 20 valid "
            "sessions ending on the prediction date; no future volume is used."
        ),
        "lookback_sessions": LIQUIDITY_LOOKBACK_SESSIONS,
        "selected_holding_observations": len(rows),
        "complete_lookback_observations": len(complete),
        "coverage": len(complete) / len(rows) if rows else None,
        "minimum_median_daily_dollar_volume_20d": _number(min(valid))
        if valid
        else None,
        "median_median_daily_dollar_volume_20d": _number(_median(valid)),
        "maximum_median_daily_dollar_volume_20d": _number(max(valid))
        if valid
        else None,
        "threshold_checks": [
            {
                "threshold_usd": int(threshold),
                "holding_observations_passing": sum(
                    value >= Decimal(int(threshold)) for value in valid
                ),
                "holding_observations_evaluated": len(valid),
                "pass_rate": (
                    sum(value >= Decimal(int(threshold)) for value in valid)
                    / len(valid)
                    if valid
                    else None
                ),
            }
            for threshold in thresholds_usd
        ],
        "thresholds_are_diagnostic_not_promotion_gates": True,
        "market_impact_or_capacity_established": False,
        "limitation": (
            "Dollar volume does not measure bid-ask spreads, order size, market "
            "impact, borrow, or executable capacity."
        ),
        "holding_rows": [dict(row) for row in rows],
    }


def _load_liquidity_rows(
    session: Session,
    periods: Sequence[InvestabilityPeriod],
) -> list[dict[str, Any]]:
    security_ids = sorted(
        {row.security_id for period in periods for row in period.selected}
    )
    maximum_date = max(period.prediction_date for period in periods)
    statement = text(
        """
        SELECT security_id, date, close, volume
        FROM prices
        WHERE security_id IN :security_ids
          AND date <= :maximum_date
          AND close IS NOT NULL
          AND close > 0
          AND volume IS NOT NULL
          AND volume > 0
        ORDER BY security_id, date
        """
    ).bindparams(bindparam("security_ids", expanding=True))
    history: dict[str, list[tuple[date, Decimal]]] = defaultdict(list)
    for row in session.execute(
        statement,
        {"security_ids": security_ids, "maximum_date": maximum_date},
    ).mappings():
        row_date = (
            row["date"]
            if isinstance(row["date"], date)
            else date.fromisoformat(str(row["date"]))
        )
        dollar_volume = Decimal(str(row["close"])) * Decimal(str(row["volume"]))
        history[str(row["security_id"])].append((row_date, dollar_volume))

    output = []
    for period in periods:
        for holding in period.selected:
            available = [
                value
                for value_date, value in history[holding.security_id]
                if value_date <= period.prediction_date
            ][-LIQUIDITY_LOOKBACK_SESSIONS:]
            median = _median(available)
            output.append(
                {
                    "prediction_date": period.prediction_date.isoformat(),
                    "security_id": holding.security_id,
                    "ticker": holding.ticker,
                    "valid_lookback_sessions": len(available),
                    "median_daily_dollar_volume_20d": _number(median),
                }
            )
    return output


def _portfolio_period_document(period: InvestabilityPeriod) -> dict[str, Any]:
    weight = Decimal("1") / Decimal(len(period.selected)) if period.selected else None
    bottom_excess = period.bottom_excess_return
    selected_excess = period.selected_excess_return
    return {
        "prediction_date": period.prediction_date.isoformat(),
        "eligible_security_count": len(period.eligible),
        "selected_holdings": [
            {
                "security_id": row.security_id,
                "ticker": row.ticker,
                "sector": row.sector,
                "weight": _number(weight),
            }
            for row in period.selected
        ],
        "bottom_holdings": [
            {"security_id": row.security_id, "ticker": row.ticker}
            for row in period.bottom
        ],
        "turnover": _number(period.turnover),
        "selected_security_return": _number(period.selected_security_return),
        "benchmark_return": _number(period.selected_benchmark_return),
        "selected_gross_excess_return": _number(selected_excess),
        "eligible_equal_weight_security_return": _number(
            period.eligible_security_return
        ),
        "eligible_equal_weight_excess_return": _number(
            period.eligible_excess_return
        ),
        "selection_lift_over_eligible_equal_weight": _number(
            selected_excess - period.eligible_excess_return
            if selected_excess is not None
            and period.eligible_excess_return is not None
            else None
        ),
        "top_minus_bottom_excess_return": _number(
            selected_excess - bottom_excess
            if selected_excess is not None and bottom_excess is not None
            else None
        ),
    }


def _reconcile_published(
    *,
    periods: Sequence[InvestabilityPeriod],
    comparison: Mapping[str, Any],
    costs: Mapping[str, Mapping[str, Any]],
    drawdown: Mapping[str, Any],
) -> dict[str, Any]:
    root = comparison.get("comparison", comparison)
    published = root["models"]["sprint8_multifactor"]
    reconstructed_gross = _mean(
        [
            period.selected_excess_return
            for period in periods
            if period.selected_excess_return is not None
        ]
    )
    reconstructed_equal_weight = _mean(
        [
            period.eligible_excess_return
            for period in periods
            if period.eligible_excess_return is not None
        ]
    )
    published_equal_weight = root["models"]["equal_weight_benchmark"][
        "mean_excess_return"
    ]
    differences = {
        "top_bucket_gross_excess_return": abs(
            reconstructed_gross
            - Decimal(str(published["quintile_returns"]["5"]))
        ),
        "eligible_equal_weight_excess_return": abs(
            reconstructed_equal_weight - Decimal(str(published_equal_weight))
        ),
        "mean_turnover": abs(
            (_mean([period.turnover for period in periods]) or Decimal("0"))
            - Decimal(str(published["turnover"]["mean"]))
        ),
        "mean_selected_max_drawdown": abs(
            Decimal(str(drawdown["selected_holding_path"]["mean_max_drawdown"]))
            - Decimal(
                str(
                    published["drawdown_and_downside_capture"]["top_quintile"]
                    ["mean_max_drawdown"]
                )
            )
        ),
        "downside_capture_percentage": abs(
            Decimal(str(drawdown["downside_capture"]["percentage"]))
            - Decimal(
                str(
                    published["drawdown_and_downside_capture"]["top_quintile"]
                    ["downside_capture_percentage"]
                )
            )
        ),
    }
    for bps in TRANSACTION_COSTS_BPS:
        differences[f"net_excess_{bps}_bps"] = abs(
            Decimal(str(costs[f"{bps}_bps"]["mean_net_excess_return"]))
            - Decimal(
                str(
                    published["transaction_costs"][f"{bps}_bps"]
                    ["average_net_excess_return"]
                )
            )
        )
    failed = {
        key: value
        for key, value in differences.items()
        if value > RECONCILIATION_TOLERANCE
    }
    if failed:
        raise ValueError(f"investability reconstruction does not reconcile: {failed}")
    return {
        "tolerance": float(RECONCILIATION_TOLERANCE),
        "all_checks_passed": True,
        "absolute_differences": {
            key: float(value) for key, value in sorted(differences.items())
        },
    }


def _drawdown_and_downside(
    periods: Sequence[InvestabilityPeriod],
) -> dict[str, Any]:
    selected_rows = [row for period in periods for row in period.selected]
    drawdowns = [row.max_drawdown for row in selected_rows]
    down_periods = [
        period
        for period in periods
        if period.selected_benchmark_return is not None
        and period.selected_benchmark_return < 0
    ]
    down_security = [
        period.selected_security_return
        for period in down_periods
        if period.selected_security_return is not None
    ]
    down_benchmark = [
        period.selected_benchmark_return
        for period in down_periods
        if period.selected_benchmark_return is not None
    ]
    mean_security = _mean(down_security)
    mean_benchmark = _mean(down_benchmark)
    return {
        "selected_holding_path": {
            "observations": len(drawdowns),
            "mean_max_drawdown": _number(_mean(drawdowns)),
            "median_max_drawdown": _number(_median(drawdowns)),
            "worst_max_drawdown": _number(min(drawdowns))
            if drawdowns
            else None,
            "equals_cohort_portfolio_path_because_every_period_has_one_holding": all(
                len(period.selected) == 1 for period in periods
            ),
        },
        "downside_capture": {
            "down_market_periods": len(down_periods),
            "mean_selected_security_return": _number(mean_security),
            "mean_benchmark_return": _number(mean_benchmark),
            "percentage": (
                float(mean_security / mean_benchmark * Decimal("100"))
                if mean_security is not None
                and mean_benchmark not in (None, Decimal("0"))
                else None
            ),
        },
        "stitched_capital_account_max_drawdown": {
            "evaluable": False,
            "value": None,
            "reason": (
                "Monthly 126-session forward windows overlap and no single daily "
                "capital-allocation ledger was frozen. Compounding them would double-count capital."
            ),
        },
    }


def diagnose_sprint9_investability(
    session: Session,
    *,
    comparison: Mapping[str, Any],
    backtest: Mapping[str, Any],
    cohort_audit: Mapping[str, Any],
    factor_diagnostic: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the full Sprint 9.4 diagnostic and its non-claim verdict."""

    cohort_root = cohort_audit.get("audit", cohort_audit)
    scores = _load_scores(session)
    rows, outcome_reproduction = _load_evaluation_rows(
        session,
        scores=scores,
        comparison=comparison,
        universe_id=str(cohort_root["universe_id"]),
    )
    periods = build_portfolio_periods(rows)
    if len(rows) != cohort_root["funnel_totals"]["eligible_final_scores"]:
        raise ValueError("Sprint 9.2 eligible row count does not reconcile")
    factor_root = factor_diagnostic.get("diagnostic", factor_diagnostic)
    if factor_root["scope"]["eligible_evaluated_security_months"] != len(rows):
        raise ValueError("Sprint 9.3 evaluated row count does not reconcile")
    if backtest.get("evaluation", backtest)["primary_horizon"] != PRIMARY_HORIZON:
        raise ValueError("Sprint 8 primary horizon is not 126d")

    period_documents = [_portfolio_period_document(period) for period in periods]
    selected_returns = [
        period.selected_security_return
        for period in periods
        if period.selected_security_return is not None
    ]
    benchmark_returns = [
        period.selected_benchmark_return
        for period in periods
        if period.selected_benchmark_return is not None
    ]
    selected_excess = [
        period.selected_excess_return
        for period in periods
        if period.selected_excess_return is not None
    ]
    eligible_returns = [
        period.eligible_security_return
        for period in periods
        if period.eligible_security_return is not None
    ]
    eligible_excess = [
        period.eligible_excess_return
        for period in periods
        if period.eligible_excess_return is not None
    ]
    selection_lifts = [
        period.selected_excess_return - period.eligible_excess_return
        for period in periods
        if period.selected_excess_return is not None
        and period.eligible_excess_return is not None
    ]
    multi_name_periods = [period for period in periods if len(period.eligible) > 1]
    multi_name_lifts = [
        period.selected_excess_return - period.eligible_excess_return
        for period in multi_name_periods
        if period.selected_excess_return is not None
        and period.eligible_excess_return is not None
    ]
    costs = cost_sensitivity(periods)
    concentration = concentration_summary(periods)
    drawdown = _drawdown_and_downside(periods)
    liquidity = summarize_liquidity_rows(_load_liquidity_rows(session, periods))
    reconciliation = _reconcile_published(
        periods=periods,
        comparison=comparison,
        costs=costs,
        drawdown=drawdown,
    )
    mean_gross_excess = _mean(selected_excess)
    mean_eligible_excess = _mean(eligible_excess)
    mean_25bps_net = Decimal(
        str(costs["25_bps"]["mean_net_excess_return"])
    )
    cost_drag_25bps = mean_gross_excess - mean_25bps_net
    minimum_liquidity = Decimal(
        str(liquidity["minimum_median_daily_dollar_volume_20d"])
    )
    return {
        "schema_version": "sprint9-investability-diagnostic-v1",
        "claims_eligible": False,
        "decision": "NOT_INVESTABLE_ON_OBSERVED_EVIDENCE",
        "deployable_portfolio_evaluable": False,
        "scope": {
            "primary_horizon": PRIMARY_HORIZON,
            "evaluated_stock_months": len(rows),
            "rebalance_periods": len(periods),
            "unique_eligible_securities": len({row.security_id for row in rows}),
            "eligible_securities_per_period": {
                "minimum": min(len(period.eligible) for period in periods),
                "maximum": max(len(period.eligible) for period in periods),
                "mean": statistics.fmean(len(period.eligible) for period in periods),
                "singleton_periods": sum(
                    len(period.eligible) == 1 for period in periods
                ),
                "multi_name_periods": len(multi_name_periods),
            },
            "selected_holdings_per_period": {
                "minimum": min(len(period.selected) for period in periods),
                "maximum": max(len(period.selected) for period in periods),
                "mean": statistics.fmean(len(period.selected) for period in periods),
            },
            "sectors_represented": sorted({row.sector for row in rows}),
        },
        "methodology": {
            "selection": (
                "Equal-weight quintile 5 after the frozen tie-aware monthly ranking."
            ),
            "benchmark": "SPY forward return on the same aligned 126-session dates.",
            "eligible_equal_weight_comparator": (
                "Equal weight across every eligible security in the same month."
            ),
            "turnover": "one_minus_overlap_divided_by_max_portfolio_size",
            "transaction_costs": (
                "One-way cost in bps times monthly selection turnover. No spread or "
                "market-impact estimate is added."
            ),
            "return_interpretation": (
                "Arithmetic mean of overlapping 126-session cohort outcomes; not an "
                "annualized or compounded capital-account return."
            ),
        },
        "long_only_top_bucket": {
            "evaluable_as_cohort_diagnostic": True,
            "evaluable_as_deployable_portfolio": False,
            "periods": len(periods),
            "holding_observations": sum(len(period.selected) for period in periods),
            "mean_forward_security_return": _number(_mean(selected_returns)),
            "median_forward_security_return": _number(_median(selected_returns)),
            "mean_forward_benchmark_return": _number(_mean(benchmark_returns)),
            "mean_gross_excess_return": _number(mean_gross_excess),
            "median_gross_excess_return": _number(_median(selected_excess)),
            "positive_absolute_return_rate": sum(
                value > 0 for value in selected_returns
            )
            / len(selected_returns),
            "gross_benchmark_hit_rate": sum(value > 0 for value in selected_excess)
            / len(selected_excess),
        },
        "top_minus_bottom": {
            "evaluable": any(period.bottom for period in periods),
            "periods_with_bottom_bucket": sum(bool(period.bottom) for period in periods),
            "periods_with_both_top_and_bottom": sum(
                bool(period.selected and period.bottom) for period in periods
            ),
            "mean_spread": _number(
                _mean(
                    [
                        period.selected_excess_return - period.bottom_excess_return
                        for period in periods
                        if period.selected_excess_return is not None
                        and period.bottom_excess_return is not None
                    ]
                )
            ),
            "reason": (
                "No monthly cohort contains five eligible securities, so quintile 1 "
                "is never populated."
            ),
        },
        "turnover": {
            "method": "one_minus_overlap_divided_by_max_portfolio_size",
            "includes_initial_entry": True,
            "mean": _number(_mean([period.turnover for period in periods])),
            "median": _number(_median([period.turnover for period in periods])),
            "nonzero_periods": sum(period.turnover > 0 for period in periods),
            "periods": [
                {
                    "prediction_date": period.prediction_date.isoformat(),
                    "holdings": sorted(row.ticker for row in period.selected),
                    "turnover": _number(period.turnover),
                }
                for period in periods
            ],
        },
        "transaction_costs": costs,
        "drawdown_and_downside_capture": drawdown,
        "concentration": concentration,
        "liquidity": liquidity,
        "equal_weight_comparison": {
            "periods": len(periods),
            "eligible_equal_weight_mean_security_return": _number(
                _mean(eligible_returns)
            ),
            "eligible_equal_weight_mean_excess_return": _number(
                mean_eligible_excess
            ),
            "model_selected_mean_security_return": _number(
                _mean(selected_returns)
            ),
            "model_selected_mean_excess_return": _number(mean_gross_excess),
            "model_selected_minus_eligible_equal_weight_excess": _number(
                _mean(selection_lifts)
            ),
            "singleton_periods_with_no_selection_choice": sum(
                len(period.eligible) == 1 for period in periods
            ),
            "multi_name_periods": len(multi_name_periods),
            "multi_name_mean_selection_lift": _number(_mean(multi_name_lifts)),
            "multi_name_positive_selection_lift_rate": sum(
                value > 0 for value in multi_name_lifts
            )
            / len(multi_name_lifts),
        },
        "root_cause_assessment": {
            "primary_measured_driver": "NEGATIVE_GROSS_EXCESS_BEFORE_COSTS",
            "cost_drag": {
                "assessment": "MINOR_NOT_PRIMARY",
                "gross_excess_return": _number(mean_gross_excess),
                "net_excess_return_25_bps": _number(mean_25bps_net),
                "cost_drag_25_bps": _number(cost_drag_25bps),
            },
            "model_selection": {
                "assessment": "NEGATIVE_INCREMENTAL_VALUE",
                "selected_minus_eligible_equal_weight_excess": _number(
                    mean_gross_excess - mean_eligible_excess
                ),
                "multi_name_month_selection_lift": _number(
                    _mean(multi_name_lifts)
                ),
            },
            "cohort_construction": {
                "assessment": "DOMINANT_STRUCTURAL_LIMITATION",
                "singleton_periods": sum(
                    len(period.eligible) == 1 for period in periods
                ),
                "single_holding_selected_every_period": all(
                    len(period.selected) == 1 for period in periods
                ),
                "single_sector_selected_every_period": (
                    concentration["sector"]["maximum_period_sector_weight"] == 1.0
                    and concentration["sector"]["unique_selected_sectors"] == 1
                ),
            },
            "benchmark_match": {
                "assessment": "UNRESOLVED_SECTOR_MISMATCH",
                "eligible_equal_weight_excess_vs_spy": _number(
                    mean_eligible_excess
                ),
                "reason": (
                    "The eligible cohort is entirely Financials-labelled while SPY is "
                    "broad-market. A sector-neutral benchmark was not frozen."
                ),
            },
            "liquidity": {
                "assessment": "NOT_AN_OBSERVED_BOTTLENECK_AT_SCREENED_LEVELS",
                "minimum_median_daily_dollar_volume_20d": float(minimum_liquidity),
                "capacity_established": False,
            },
            "weak_signal": {
                "assessment": "NOT_IDENTIFIABLE_SEPARATELY_FROM_COHORT_ARTIFACT",
                "reason": (
                    "Only nine tiny months have calculable Rank IC, while the selected "
                    "basket is negative and less effective than eligible equal weight."
                ),
            },
            "conclusion": (
                "Negative net excess is already present before costs. Costs add only a "
                "small drag; extreme cohort and benchmark concentration prevent a clean "
                "weak-signal versus benchmark-mismatch attribution."
            ),
        },
        "implementability_assessment": {
            "investability_established": False,
            "ranking_useful_for_portfolio_construction_established": False,
            "annualized_return_reported": False,
            "stitched_capital_account_curve_available": False,
            "bid_ask_spread_model_available": False,
            "market_impact_model_available": False,
            "volume_screen_available": True,
            "decision_reason": (
                "The selected cohort is one stock and one sector in every period, has no "
                "bottom bucket, underperforms SPY before costs, and underperforms the same "
                "cohort's equal-weight basket."
            ),
        },
        "integrity": {
            "outcome_reproduction": outcome_reproduction,
            "published_metric_reconciliation": reconciliation,
            "portfolio_period_ledger_sha256": _canonical_sha256(period_documents),
            "liquidity_ledger_sha256": _canonical_sha256(
                liquidity["holding_rows"]
            ),
        },
        "portfolio_periods": period_documents,
    }
