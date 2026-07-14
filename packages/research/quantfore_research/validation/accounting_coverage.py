"""Outcome-blind accounting-history readiness checks for Model V2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Iterable, Mapping, Optional, Sequence


ACCOUNTING_HISTORY_VERSION = "model-v2-accounting-history-v1"
MAX_FLOW_STALENESS_DAYS = 200
MAX_INSTANT_STALENESS_DAYS = 500

EXPECTED_UNITS = {
    "diluted_eps": "USD/shares",
    "diluted_shares": "shares",
}

COMPONENT_REQUIREMENTS = {
    "fcf_yield": (
        ("cash_from_operations", "CURRENT_TTM"),
        ("capital_expenditure", "CURRENT_TTM"),
    ),
    "earnings_yield": (("net_income_common", "CURRENT_TTM"),),
    "ebit_ev": (
        ("ebit", "CURRENT_TTM"),
        ("total_debt", "CURRENT_INSTANT"),
        ("cash_and_equivalents", "CURRENT_INSTANT"),
    ),
    "sales_yield": (("revenue", "CURRENT_TTM"),),
    "roic": (
        ("ebit", "CURRENT_TTM"),
        ("income_tax_expense", "CURRENT_TTM"),
        ("pretax_income", "CURRENT_TTM"),
        ("total_debt", "CURRENT_AND_PRIOR_INSTANT"),
        ("cash_and_equivalents", "CURRENT_AND_PRIOR_INSTANT"),
        ("shareholders_equity", "CURRENT_AND_PRIOR_INSTANT"),
    ),
    "gross_profitability": (
        ("gross_profit", "CURRENT_TTM"),
        ("total_assets", "CURRENT_AND_PRIOR_INSTANT"),
    ),
    "fcf_conversion": (
        ("cash_from_operations", "CURRENT_TTM"),
        ("capital_expenditure", "CURRENT_TTM"),
        ("net_income_common", "CURRENT_TTM"),
    ),
    "inverse_accruals": (
        ("net_income_common", "CURRENT_TTM"),
        ("cash_from_operations", "CURRENT_TTM"),
        ("total_assets", "CURRENT_AND_PRIOR_INSTANT"),
    ),
    "inverse_leverage": (
        ("total_debt", "CURRENT_INSTANT"),
        ("total_assets", "CURRENT_AND_PRIOR_INSTANT"),
    ),
    "revenue_growth": (("revenue", "CURRENT_AND_PRIOR_TTM"),),
    "eps_growth": (("diluted_eps", "CURRENT_AND_PRIOR_TTM"),),
    "fcf_growth": (
        ("cash_from_operations", "CURRENT_AND_PRIOR_TTM"),
        ("capital_expenditure", "CURRENT_AND_PRIOR_TTM"),
    ),
    "margin_change": (
        ("ebit", "CURRENT_AND_PRIOR_TTM"),
        ("revenue", "CURRENT_AND_PRIOR_TTM"),
    ),
}

COMPONENT_FAMILIES = {
    "fcf_yield": "value",
    "earnings_yield": "value",
    "ebit_ev": "value",
    "sales_yield": "value",
    "roic": "quality",
    "gross_profitability": "quality",
    "fcf_conversion": "quality",
    "inverse_accruals": "quality",
    "inverse_leverage": "quality",
    "revenue_growth": "growth",
    "eps_growth": "growth",
    "fcf_growth": "growth",
    "margin_change": "growth",
}


@dataclass(frozen=True)
class AccountingFact:
    fiscal_period_end: date
    period_type: str
    concept: str
    unit: str
    model_available_at: datetime
    revision_version: int
    record_id: str


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _unit_matches(concept: str, unit: str) -> bool:
    expected = EXPECTED_UNITS.get(concept, "USD")
    return unit.lower() == expected.lower()


def select_accounting_facts_as_of(
    facts: Iterable[AccountingFact], prediction_date: date
) -> tuple[AccountingFact, ...]:
    """Select the greatest revision actually available by the prediction date."""

    cutoff = datetime.max.replace(tzinfo=timezone.utc).replace(
        year=prediction_date.year,
        month=prediction_date.month,
        day=prediction_date.day,
    )
    selected = {}
    for fact in facts:
        if _utc(fact.model_available_at) > cutoff:
            continue
        identity = (
            fact.fiscal_period_end,
            fact.period_type,
            fact.concept,
            fact.unit,
        )
        prior = selected.get(identity)
        if prior is None or (
            fact.revision_version,
            _utc(fact.model_available_at),
            fact.record_id,
        ) > (
            prior.revision_version,
            _utc(prior.model_available_at),
            prior.record_id,
        ):
            selected[identity] = fact
    return tuple(selected.values())


def _consecutive_quarters(rows: Sequence[AccountingFact]) -> bool:
    if len(rows) < 2:
        return True
    gaps = [
        (right.fiscal_period_end - left.fiscal_period_end).days
        for left, right in zip(rows, rows[1:])
    ]
    return all(45 <= gap <= 150 for gap in gaps)


def _quarter_status(
    facts: Sequence[AccountingFact],
    *,
    concept: str,
    prediction_date: date,
    require_prior: bool,
) -> Optional[str]:
    concept_rows = [fact for fact in facts if fact.concept == concept]
    if not concept_rows:
        return "SOURCE_MISSING"
    unit_rows = [fact for fact in concept_rows if _unit_matches(concept, fact.unit)]
    if not unit_rows:
        return "UNIT_CONFLICT"
    quarter_rows = [fact for fact in unit_rows if fact.period_type == "QUARTERLY"]
    by_end = {}
    for fact in quarter_rows:
        prior = by_end.get(fact.fiscal_period_end)
        if prior is None or (
            fact.revision_version,
            _utc(fact.model_available_at),
            fact.record_id,
        ) > (
            prior.revision_version,
            _utc(prior.model_available_at),
            prior.record_id,
        ):
            by_end[fact.fiscal_period_end] = fact
    rows = [by_end[key] for key in sorted(by_end)]
    required = 8 if require_prior else 4
    if len(rows) < required:
        return (
            "INSUFFICIENT_PRIOR_TTM_HISTORY"
            if require_prior and len(rows) >= 4
            else "INSUFFICIENT_QUARTERLY_HISTORY"
        )
    suffix = rows[-required:]
    if not _consecutive_quarters(suffix):
        return (
            "INSUFFICIENT_PRIOR_TTM_HISTORY"
            if require_prior
            else "INSUFFICIENT_QUARTERLY_HISTORY"
        )
    if (prediction_date - suffix[-1].fiscal_period_end).days > MAX_FLOW_STALENESS_DAYS:
        return "STALE_FILING"
    return None


def _instant_status(
    facts: Sequence[AccountingFact],
    *,
    concept: str,
    prediction_date: date,
    require_prior: bool,
) -> Optional[str]:
    concept_rows = [
        fact
        for fact in facts
        if fact.concept == concept and _unit_matches(concept, fact.unit)
    ]
    if not any(fact.concept == concept for fact in facts):
        return "SOURCE_MISSING"
    if not concept_rows:
        return "UNIT_CONFLICT"
    by_end = {}
    for fact in concept_rows:
        prior = by_end.get(fact.fiscal_period_end)
        if prior is None or (
            fact.revision_version,
            _utc(fact.model_available_at),
            fact.record_id,
        ) > (
            prior.revision_version,
            _utc(prior.model_available_at),
            prior.record_id,
        ):
            by_end[fact.fiscal_period_end] = fact
    rows = [by_end[key] for key in sorted(by_end)]
    if not rows:
        return "SOURCE_MISSING"
    current = rows[-1]
    if (prediction_date - current.fiscal_period_end).days > MAX_INSTANT_STALENESS_DAYS:
        return "STALE_FILING"
    if require_prior:
        priors = [
            fact
            for fact in rows[:-1]
            if 250
            <= (current.fiscal_period_end - fact.fiscal_period_end).days
            <= 500
        ]
        if not priors:
            return "INSUFFICIENT_BALANCE_SHEET_HISTORY"
    return None


def requirement_status(
    facts: Sequence[AccountingFact],
    *,
    concept: str,
    requirement: str,
    prediction_date: date,
) -> Optional[str]:
    if requirement == "CURRENT_TTM":
        return _quarter_status(
            facts,
            concept=concept,
            prediction_date=prediction_date,
            require_prior=False,
        )
    if requirement == "CURRENT_AND_PRIOR_TTM":
        return _quarter_status(
            facts,
            concept=concept,
            prediction_date=prediction_date,
            require_prior=True,
        )
    if requirement == "CURRENT_INSTANT":
        return _instant_status(
            facts,
            concept=concept,
            prediction_date=prediction_date,
            require_prior=False,
        )
    if requirement == "CURRENT_AND_PRIOR_INSTANT":
        return _instant_status(
            facts,
            concept=concept,
            prediction_date=prediction_date,
            require_prior=True,
        )
    raise ValueError(f"unknown accounting requirement: {requirement}")


def component_statuses(
    facts: Iterable[AccountingFact], prediction_date: date
) -> Mapping[str, Optional[str]]:
    selected = select_accounting_facts_as_of(facts, prediction_date)
    result = {}
    reason_priority = (
        "SOURCE_MISSING",
        "UNIT_CONFLICT",
        "INSUFFICIENT_QUARTERLY_HISTORY",
        "INSUFFICIENT_PRIOR_TTM_HISTORY",
        "INSUFFICIENT_BALANCE_SHEET_HISTORY",
        "STALE_FILING",
    )
    for component, requirements in COMPONENT_REQUIREMENTS.items():
        reasons = [
            requirement_status(
                selected,
                concept=concept,
                requirement=requirement,
                prediction_date=prediction_date,
            )
            for concept, requirement in requirements
        ]
        present = {reason for reason in reasons if reason is not None}
        result[component] = next(
            (reason for reason in reason_priority if reason in present), None
        )
    return result
