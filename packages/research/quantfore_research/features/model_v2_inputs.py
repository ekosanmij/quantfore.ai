"""Point-in-time accounting scalar selection for Model V2 formulas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable, Mapping, Optional, Sequence

from quantfore_research.features.model_v2 import ScalarValue


MAX_FLOW_STALENESS_DAYS = 200
MAX_INSTANT_STALENESS_DAYS = 500

FLOW_CONCEPTS = (
    "revenue",
    "gross_profit",
    "ebit",
    "net_income_common",
    "diluted_eps",
    "cash_from_operations",
    "capital_expenditure",
    "income_tax_expense",
    "pretax_income",
    "net_interest_income",
    "credit_loss_provision",
    "premiums_earned_net",
    "policyholder_benefits_claims_net",
    "net_investment_income",
    "depreciation_and_amortization",
    "investment_real_estate_sale_gain_loss",
    "interest_expense",
)
INSTANT_CONCEPTS = (
    "total_assets",
    "total_debt",
    "cash_and_equivalents",
    "shareholders_equity",
    "loans_and_leases_net",
    "customer_deposits",
    "real_estate_investment_property_net",
)
EXPECTED_UNITS = {
    "diluted_eps": "USD/shares",
    "diluted_shares": "shares",
}


@dataclass(frozen=True)
class AccountingFactValue:
    fiscal_period_end: date
    period_type: str
    concept: str
    unit: str
    model_available_at: datetime
    revision_version: int
    record_id: str
    value: Decimal


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _cutoff(prediction_date: date) -> datetime:
    return datetime(
        prediction_date.year,
        prediction_date.month,
        prediction_date.day,
        23,
        59,
        59,
        999999,
        tzinfo=timezone.utc,
    )


def _unit_matches(fact: AccountingFactValue) -> bool:
    return fact.unit.lower() == EXPECTED_UNITS.get(fact.concept, "USD").lower()


def select_fact_values_as_of(
    facts: Iterable[AccountingFactValue], prediction_date: date
) -> tuple[AccountingFactValue, ...]:
    """Select the latest revision actually available for every filed identity."""

    selected = {}
    cutoff = _cutoff(prediction_date)
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


def _consecutive(rows: Sequence[AccountingFactValue]) -> bool:
    return all(
        45 <= (right.fiscal_period_end - left.fiscal_period_end).days <= 150
        for left, right in zip(rows, rows[1:])
    )


def _scalar(rows: Sequence[AccountingFactValue], value: Decimal) -> ScalarValue:
    return ScalarValue(
        value=value,
        unit=rows[0].unit,
        lineage_ids=tuple(sorted({row.record_id for row in rows})),
    )


def _ttm_pair(
    rows: Sequence[AccountingFactValue], prediction_date: date
) -> tuple[Optional[ScalarValue], Optional[ScalarValue]]:
    by_end = {}
    for fact in rows:
        if fact.period_type != "QUARTERLY" or not _unit_matches(fact):
            continue
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
    ordered = [by_end[key] for key in sorted(by_end)]
    if len(ordered) < 4:
        return None, None
    current_rows = ordered[-4:]
    if not _consecutive(current_rows):
        return None, None
    if (
        prediction_date - current_rows[-1].fiscal_period_end
    ).days > MAX_FLOW_STALENESS_DAYS:
        return None, None
    current = _scalar(current_rows, sum((row.value for row in current_rows), Decimal("0")))
    if len(ordered) < 8:
        return current, None
    prior_rows = ordered[-8:-4]
    if not _consecutive(prior_rows + current_rows[:1]):
        return current, None
    prior = _scalar(prior_rows, sum((row.value for row in prior_rows), Decimal("0")))
    return current, prior


def _current_and_prior(
    rows: Sequence[AccountingFactValue], prediction_date: date
) -> tuple[Optional[ScalarValue], Optional[ScalarValue]]:
    by_end = {}
    for fact in rows:
        if not _unit_matches(fact):
            continue
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
    ordered = [by_end[key] for key in sorted(by_end)]
    if not ordered:
        return None, None
    current = ordered[-1]
    if (
        prediction_date - current.fiscal_period_end
    ).days > MAX_INSTANT_STALENESS_DAYS:
        return None, None
    current_scalar = _scalar((current,), current.value)
    candidates = [
        row
        for row in ordered[:-1]
        if 250 <= (current.fiscal_period_end - row.fiscal_period_end).days <= 500
    ]
    prior_scalar = _scalar((candidates[-1],), candidates[-1].value) if candidates else None
    return current_scalar, prior_scalar


def build_formula_inputs_as_of(
    facts: Iterable[AccountingFactValue],
    prediction_date: date,
    *,
    latest_raw_close: Optional[ScalarValue] = None,
) -> Mapping[str, ScalarValue]:
    """Create the named scalar context consumed by locked branch formulas."""

    selected = select_fact_values_as_of(facts, prediction_date)
    by_concept = {}
    for fact in selected:
        by_concept.setdefault(fact.concept, []).append(fact)
    result = {}
    for concept in FLOW_CONCEPTS:
        current, prior = _ttm_pair(by_concept.get(concept, ()), prediction_date)
        if current is not None:
            result[f"current_ttm_{concept}"] = current
        if prior is not None:
            result[f"prior_ttm_{concept}"] = prior
    for concept in INSTANT_CONCEPTS:
        current, prior = _current_and_prior(
            by_concept.get(concept, ()), prediction_date
        )
        if current is not None:
            result[f"current_{concept}"] = current
        if prior is not None:
            result[f"prior_{concept}"] = prior
    shares, prior_shares = _current_and_prior(
        by_concept.get("diluted_shares", ()), prediction_date
    )
    if shares is not None:
        result["latest_diluted_shares"] = shares
    if prior_shares is not None:
        result["prior_diluted_shares"] = prior_shares
    if latest_raw_close is not None:
        result["latest_raw_close"] = latest_raw_close
    return result
