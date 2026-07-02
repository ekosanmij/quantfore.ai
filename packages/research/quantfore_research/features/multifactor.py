"""Point-in-time raw feature families for the Sprint 8 multi-factor baseline."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.models import (
    Feature,
    FeatureSet,
    Fundamental,
    Price,
    Security,
    SecurityClassification,
    SourceSnapshot,
)
from quantfore_research.validation.fundamental_audit_gate import (
    FundamentalAuditBinding,
)


MULTIFACTOR_FEATURE_VERSION = "multifactor-v1"
MULTIFACTOR_FEATURE_SET_NAME = "pit_multifactor_raw_features"
APPLICABLE = "APPLICABLE"
MISSING = "MISSING"
NOT_APPLICABLE = "NOT_APPLICABLE"
HIGHER = "HIGHER"
LOWER = "LOWER"
ANNUALIZATION_FACTOR = Decimal("252").sqrt()


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    family: str
    formula: str
    direction: str


FEATURE_DEFINITIONS = (
    FeatureDefinition("fcf_yield", "value", "TTM FCF / market_cap", HIGHER),
    FeatureDefinition(
        "earnings_yield", "value", "TTM net_income_common / market_cap", HIGHER
    ),
    FeatureDefinition("ebit_ev", "value", "TTM EBIT / enterprise_value", HIGHER),
    FeatureDefinition(
        "sales_yield", "value", "TTM revenue / enterprise_value", HIGHER
    ),
    FeatureDefinition(
        "roic", "quality", "TTM NOPAT / average(invested_capital)", HIGHER
    ),
    FeatureDefinition(
        "gross_profitability",
        "quality",
        "TTM gross_profit / average(total_assets)",
        HIGHER,
    ),
    FeatureDefinition(
        "fcf_conversion",
        "quality",
        "TTM FCF / TTM net_income_common",
        HIGHER,
    ),
    FeatureDefinition(
        "inverse_accruals",
        "quality",
        "-(TTM net_income_common - TTM cash_from_operations) / average(total_assets)",
        HIGHER,
    ),
    FeatureDefinition(
        "inverse_leverage", "quality", "-total_debt / average(total_assets)", HIGHER
    ),
    FeatureDefinition(
        "revenue_growth",
        "growth",
        "(TTM revenue[t] - TTM revenue[t-4q]) / abs(TTM revenue[t-4q])",
        HIGHER,
    ),
    FeatureDefinition(
        "eps_growth",
        "growth",
        "(TTM diluted_eps[t] - TTM diluted_eps[t-4q]) / abs(TTM diluted_eps[t-4q])",
        HIGHER,
    ),
    FeatureDefinition(
        "fcf_growth",
        "growth",
        "(TTM FCF[t] - TTM FCF[t-4q]) / abs(TTM FCF[t-4q])",
        HIGHER,
    ),
    FeatureDefinition(
        "margin_change",
        "growth",
        "TTM EBIT/revenue[t] - TTM EBIT/revenue[t-4q]",
        HIGHER,
    ),
    FeatureDefinition(
        "momentum_6_1", "momentum", "adj_close[t-21] / adj_close[t-126] - 1", HIGHER
    ),
    FeatureDefinition(
        "momentum_12_1",
        "momentum",
        "adj_close[t-21] / adj_close[t-252] - 1",
        HIGHER,
    ),
    FeatureDefinition(
        "volatility_126d",
        "risk",
        "sample_std(last 126 daily returns) * sqrt(252)",
        LOWER,
    ),
    FeatureDefinition(
        "beta_252d",
        "risk",
        "cov(security, benchmark) / var(benchmark) over up to 252 aligned returns",
        LOWER,
    ),
    FeatureDefinition(
        "downside_volatility_126d",
        "risk",
        "rms(min(daily_return, 0), 126) * sqrt(252)",
        LOWER,
    ),
    FeatureDefinition(
        "maximum_drawdown_252d",
        "risk",
        "min(adj_close / running_peak - 1, 252 sessions)",
        HIGHER,
    ),
)
DEFINITIONS_BY_NAME = {item.name: item for item in FEATURE_DEFINITIONS}

FINANCIALS_MASK = frozenset(
    {
        "fcf_yield",
        "ebit_ev",
        "roic",
        "gross_profitability",
        "fcf_conversion",
        "inverse_accruals",
        "inverse_leverage",
        "fcf_growth",
        "margin_change",
    }
)
REIT_MASK = frozenset(
    {"fcf_yield", "ebit_ev", "roic", "fcf_conversion", "fcf_growth"}
)
SECTOR_SENSITIVE = FINANCIALS_MASK | REIT_MASK


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class FeatureInput:
    input_type: str
    input_name: str
    record_id: str
    value: Decimal
    unit: str
    source_snapshot_id: str
    source_hash: str
    model_available_at: datetime
    period_end: Optional[date] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_type": self.input_type,
            "input_name": self.input_name,
            "record_id": self.record_id,
            "value": str(self.value),
            "unit": self.unit,
            "source_snapshot_id": self.source_snapshot_id,
            "source_hash": self.source_hash,
            "model_available_at": _utc(self.model_available_at)
            .isoformat()
            .replace("+00:00", "Z"),
            "period_end": self.period_end.isoformat() if self.period_end else None,
        }


@dataclass(frozen=True)
class ScalarInput:
    name: str
    value: Decimal
    unit: str
    evidence: tuple[FeatureInput, ...]


@dataclass(frozen=True)
class RawFeature:
    definition: FeatureDefinition
    value: Optional[Decimal]
    status: str
    missing_reason: Optional[str]
    inputs: tuple[FeatureInput, ...]

    def inputs_json(self, prediction_timestamp: datetime) -> dict[str, Any]:
        return {
            "prediction_timestamp": _utc(prediction_timestamp)
            .isoformat()
            .replace("+00:00", "Z"),
            "formula_version": MULTIFACTOR_FEATURE_VERSION,
            "inputs": [row.to_dict() for row in self.inputs],
            "source_snapshot_ids": sorted(
                {row.source_snapshot_id for row in self.inputs}
            ),
            "source_hashes": sorted({row.source_hash for row in self.inputs}),
        }


@dataclass(frozen=True)
class MultiFactorFeatureBatch:
    security_id: str
    benchmark_security_id: str
    prediction_timestamp: datetime
    sector: Optional[str]
    industry: Optional[str]
    features: tuple[RawFeature, ...]
    classification_context: Optional[Mapping[str, Any]] = None

    @property
    def source_snapshot_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    item.source_snapshot_id
                    for feature in self.features
                    for item in feature.inputs
                }
                | (
                    {str(self.classification_context["source_snapshot_id"])}
                    if self.classification_context
                    else set()
                )
            )
        )

    def by_name(self) -> dict[str, RawFeature]:
        return {feature.definition.name: feature for feature in self.features}


class FeatureUnavailable(ValueError):
    def __init__(
        self,
        reason: str,
        inputs: Iterable[FeatureInput] = (),
    ) -> None:
        self.reason = reason
        self.inputs = tuple(inputs)
        super().__init__(reason)


def _unique_evidence(*values: ScalarInput) -> tuple[FeatureInput, ...]:
    result: dict[tuple[str, str], FeatureInput] = {}
    for value in values:
        for item in value.evidence:
            result[(item.input_type, item.record_id)] = item
    return tuple(result[key] for key in sorted(result))


def _derived(name: str, value: Decimal, unit: str, *inputs: ScalarInput) -> ScalarInput:
    return ScalarInput(name, value, unit, _unique_evidence(*inputs))


def _same_unit(*values: ScalarInput) -> str:
    units = {item.unit.upper() for item in values}
    if len(units) != 1:
        raise FeatureUnavailable("UNIT_CONFLICT", _unique_evidence(*values))
    return values[0].unit


def _required(
    values: Mapping[str, Optional[ScalarInput]],
    name: str,
    reasons: Optional[Mapping[str, str]] = None,
) -> ScalarInput:
    value = values.get(name)
    if value is None:
        raise FeatureUnavailable((reasons or {}).get(name, "SOURCE_MISSING"))
    return value


def _positive_denominator(value: ScalarInput) -> Decimal:
    if value.value <= 0:
        raise FeatureUnavailable("INVALID_DENOMINATOR", value.evidence)
    return value.value


def _nonzero_absolute_denominator(value: ScalarInput) -> Decimal:
    if value.value == 0:
        raise FeatureUnavailable("INVALID_DENOMINATOR", value.evidence)
    return abs(value.value)


def select_fundamentals_as_of(
    session: Session,
    *,
    security_id: str,
    prediction_timestamp: datetime,
    source_snapshot_ids: Optional[Sequence[str]] = None,
) -> tuple[Fundamental, ...]:
    """Select the greatest revision actually available by the prediction time."""

    timestamp = _utc(prediction_timestamp)
    query = select(Fundamental).where(Fundamental.security_id == security_id)
    if source_snapshot_ids is not None:
        query = query.where(Fundamental.source_snapshot_id.in_(source_snapshot_ids))
    candidates = [
        row
        for row in session.scalars(query).all()
        if _utc(row.model_available_at) <= timestamp
    ]
    selected: dict[tuple[object, ...], Fundamental] = {}
    for row in candidates:
        identity = (
            row.fiscal_period_end,
            row.period_type,
            row.concept,
            row.unit,
        )
        prior = selected.get(identity)
        if prior is None or (row.revision_version, row.fundamental_id) > (
            prior.revision_version,
            prior.fundamental_id,
        ):
            selected[identity] = row
    return tuple(
        sorted(
            selected.values(),
            key=lambda row: (
                row.standardized_concept,
                row.fiscal_period_end,
                row.period_type,
                row.fundamental_id,
            ),
        )
    )


class FundamentalBook:
    def __init__(self, facts: Sequence[Fundamental]) -> None:
        self.facts = tuple(facts)

    @staticmethod
    def _input(row: Fundamental) -> ScalarInput:
        return ScalarInput(
            row.standardized_concept,
            row.value,
            row.unit,
            (
                FeatureInput(
                    input_type="fundamental",
                    input_name=row.standardized_concept,
                    record_id=row.fundamental_id,
                    value=row.value,
                    unit=row.unit,
                    source_snapshot_id=row.source_snapshot_id,
                    source_hash=row.source_hash,
                    model_available_at=_utc(row.model_available_at),
                    period_end=row.fiscal_period_end,
                ),
            ),
        )

    def _period_rows(self, concept: str, period_type: str) -> list[Fundamental]:
        candidates = [
            row
            for row in self.facts
            if row.standardized_concept == concept and row.period_type == period_type
        ]
        by_period: dict[date, Fundamental] = {}
        for row in candidates:
            prior = by_period.get(row.fiscal_period_end)
            if prior is None or (
                row.revision_version, _utc(row.model_available_at), row.fundamental_id
            ) > (
                prior.revision_version,
                _utc(prior.model_available_at),
                prior.fundamental_id,
            ):
                by_period[row.fiscal_period_end] = row
        return [by_period[key] for key in sorted(by_period)]

    def latest(self, concept: str) -> Optional[ScalarInput]:
        rows = [row for row in self.facts if row.standardized_concept == concept]
        if not rows:
            return None
        row = max(
            rows,
            key=lambda item: (
                item.fiscal_period_end,
                _utc(item.model_available_at),
                item.revision_version,
                item.fundamental_id,
            ),
        )
        return self._input(row)

    def current_and_prior_instant(
        self, concept: str
    ) -> tuple[Optional[ScalarInput], Optional[ScalarInput]]:
        by_period: dict[date, Fundamental] = {}
        for row in self.facts:
            if row.standardized_concept != concept:
                continue
            prior = by_period.get(row.fiscal_period_end)
            if prior is None or (
                row.revision_version, _utc(row.model_available_at), row.fundamental_id
            ) > (
                prior.revision_version,
                _utc(prior.model_available_at),
                prior.fundamental_id,
            ):
                by_period[row.fiscal_period_end] = row
        rows = [by_period[key] for key in sorted(by_period)]
        if not rows:
            return None, None
        current = rows[-1]
        prior_candidates = [
            row
            for row in rows[:-1]
            if 300 <= (current.fiscal_period_end - row.fiscal_period_end).days <= 430
        ]
        prior = prior_candidates[-1] if prior_candidates else None
        return self._input(current), self._input(prior) if prior else None

    def ttm_pair(
        self, concept: str
    ) -> tuple[Optional[ScalarInput], Optional[ScalarInput], Optional[str]]:
        ttm_rows = self._period_rows(concept, "TTM")
        if ttm_rows:
            current = ttm_rows[-1]
            prior_candidates = [
                row
                for row in ttm_rows[:-1]
                if 300 <= (current.fiscal_period_end - row.fiscal_period_end).days <= 430
            ]
            prior = prior_candidates[-1] if prior_candidates else None
            return (
                self._input(current),
                self._input(prior) if prior else None,
                None if prior else "INSUFFICIENT_HISTORY",
            )
        quarters = self._period_rows(concept, "QUARTERLY")
        if len(quarters) < 4:
            return None, None, "INSUFFICIENT_HISTORY" if quarters else "SOURCE_MISSING"

        def consecutive(rows: Sequence[Fundamental]) -> bool:
            indexes = []
            for row in rows:
                if row.fiscal_quarter not in {1, 2, 3, 4}:
                    return False
                indexes.append(row.fiscal_year * 4 + row.fiscal_quarter)
            return all(right - left == 1 for left, right in zip(indexes, indexes[1:]))

        if not consecutive(quarters[-4:]):
            return None, None, "INSUFFICIENT_HISTORY"

        def sum_rows(name: str, rows: Sequence[Fundamental]) -> ScalarInput:
            inputs = tuple(self._input(row) for row in rows)
            unit = _same_unit(*inputs)
            return _derived(
                name,
                sum((item.value for item in inputs), Decimal("0")),
                unit,
                *inputs,
            )

        try:
            current = sum_rows(f"ttm_{concept}", quarters[-4:])
            prior = (
                sum_rows(f"prior_ttm_{concept}", quarters[-8:-4])
                if len(quarters) >= 8 and consecutive(quarters[-8:-4])
                else None
            )
        except FeatureUnavailable as exc:
            return None, None, exc.reason
        return current, prior, None if prior else "INSUFFICIENT_HISTORY"


def _price_input(row: Price, snapshot: SourceSnapshot) -> FeatureInput:
    assert row.adj_close is not None
    return FeatureInput(
        input_type="adjusted_close",
        input_name="adjusted_close",
        record_id=row.price_id,
        value=row.adj_close,
        unit="USD",
        source_snapshot_id=snapshot.snapshot_id,
        source_hash=snapshot.source_hash,
        model_available_at=datetime.combine(
            row.date, datetime.min.time(), tzinfo=timezone.utc
        ),
        period_end=row.date,
    )


def _raw_close_input(row: Price, snapshot: SourceSnapshot) -> FeatureInput:
    if row.close is None:
        raise ValueError("raw close input is missing")
    return FeatureInput(
        input_type="raw_close",
        input_name="raw_close",
        record_id=row.price_id,
        value=row.close,
        unit="USD",
        source_snapshot_id=snapshot.snapshot_id,
        source_hash=snapshot.source_hash,
        model_available_at=datetime.combine(
            row.date, datetime.min.time(), tzinfo=timezone.utc
        ),
        period_end=row.date,
    )


def resolve_security_classification(
    session: Session,
    *,
    security_id: str,
    prediction_timestamp: datetime,
    classification_id: Optional[str] = None,
) -> SecurityClassification:
    """Resolve one source-bound classification known at prediction time."""

    timestamp = _utc(prediction_timestamp)
    query = (
        select(SecurityClassification)
        .where(SecurityClassification.security_id == security_id)
        .where(SecurityClassification.effective_from <= timestamp.date())
        .where(
            (SecurityClassification.effective_to.is_(None))
            | (SecurityClassification.effective_to >= timestamp.date())
        )
        .where(SecurityClassification.model_available_at <= timestamp)
    )
    if classification_id is not None:
        query = query.where(
            SecurityClassification.classification_id == classification_id
        )
    rows = list(session.scalars(query).all())
    if len(rows) != 1:
        raise ValueError(
            "point-in-time classification must resolve to exactly one record; "
            f"security_id={security_id} matches={len(rows)}"
        )
    row = rows[0]
    snapshot = session.get(SourceSnapshot, row.source_snapshot_id)
    if snapshot is None or snapshot.source_hash != row.source_hash:
        raise ValueError("classification source snapshot/hash does not reproduce")
    return row


def _load_prices(
    session: Session,
    *,
    security_id: str,
    prediction_timestamp: datetime,
    source_snapshot_id: Optional[str],
) -> tuple[list[Price], Optional[SourceSnapshot]]:
    query = (
        select(SourceSnapshot)
        .join(Price, Price.source_snapshot_id == SourceSnapshot.snapshot_id)
        .where(Price.security_id == security_id)
        .where(Price.date <= prediction_timestamp.date())
        .where(Price.adj_close.is_not(None))
    )
    if source_snapshot_id is not None:
        query = query.where(SourceSnapshot.snapshot_id == source_snapshot_id)
    snapshot = session.scalar(
        query.order_by(
            SourceSnapshot.retrieved_at.desc(), SourceSnapshot.snapshot_id.desc()
        ).limit(1)
    )
    if snapshot is None:
        return [], None
    prices = list(
        session.scalars(
            select(Price)
            .where(Price.security_id == security_id)
            .where(Price.source_snapshot_id == snapshot.snapshot_id)
            .where(Price.date <= prediction_timestamp.date())
            .where(Price.adj_close.is_not(None))
            .order_by(Price.date, Price.price_id)
        ).all()
    )
    return prices, snapshot


def _returns(prices: Sequence[Price]) -> list[tuple[date, Decimal]]:
    result = []
    for prior, current in zip(prices, prices[1:]):
        assert prior.adj_close is not None and current.adj_close is not None
        if prior.adj_close <= 0 or current.adj_close <= 0:
            raise FeatureUnavailable("INVALID_DENOMINATOR")
        result.append((current.date, current.adj_close / prior.adj_close - Decimal("1")))
    return result


def _sample_std(values: Sequence[Decimal]) -> Decimal:
    if len(values) < 2:
        raise FeatureUnavailable("INSUFFICIENT_HISTORY")
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values) - 1)
    return variance.sqrt()


def _feature(
    definition: FeatureDefinition,
    calculation,
) -> RawFeature:
    try:
        value, inputs = calculation()
        if not value.is_finite():
            raise FeatureUnavailable("NONFINITE_VALUE", inputs)
        return RawFeature(definition, value, APPLICABLE, None, tuple(inputs))
    except FeatureUnavailable as exc:
        return RawFeature(definition, None, MISSING, exc.reason, exc.inputs)


def _not_applicable(definition: FeatureDefinition, reason: str) -> RawFeature:
    status = MISSING if reason == "SECTOR_UNKNOWN" else NOT_APPLICABLE
    return RawFeature(definition, None, status, reason, ())


def _sector_reason(
    feature_name: str, sector: Optional[str], industry: Optional[str]
) -> Optional[str]:
    if feature_name not in SECTOR_SENSITIVE:
        return None
    if not sector:
        return "SECTOR_UNKNOWN"
    normalized_sector = sector.strip().upper()
    normalized_industry = (industry or "").strip().upper()
    if (
        normalized_sector == "40" or normalized_sector.startswith("FINANC")
    ) and feature_name in FINANCIALS_MASK:
        return "NOT_APPLICABLE"
    if (
        normalized_industry == "601010" or "REIT" in normalized_industry
    ) and feature_name in REIT_MASK:
        return "NOT_APPLICABLE"
    return None


def construct_multifactor_features(
    session: Session,
    *,
    security_id: str,
    benchmark_security_id: str,
    prediction_timestamp: datetime,
    classification_id: Optional[str] = None,
    fundamental_source_snapshot_ids: Optional[Sequence[str]] = None,
    security_price_snapshot_id: Optional[str] = None,
    benchmark_price_snapshot_id: Optional[str] = None,
) -> MultiFactorFeatureBatch:
    """Construct all 19 raw v1 components without imputation or future facts."""

    timestamp = _utc(prediction_timestamp)
    if session.get(Security, security_id) is None:
        raise ValueError(f"unknown security: {security_id}")
    if session.get(Security, benchmark_security_id) is None:
        raise ValueError(f"unknown benchmark security: {benchmark_security_id}")
    classification = resolve_security_classification(
        session,
        security_id=security_id,
        prediction_timestamp=timestamp,
        classification_id=classification_id,
    )
    sector = classification.sector
    industry = classification.industry
    facts = select_fundamentals_as_of(
        session,
        security_id=security_id,
        prediction_timestamp=timestamp,
        source_snapshot_ids=fundamental_source_snapshot_ids,
    )
    book = FundamentalBook(facts)
    values: dict[str, Optional[ScalarInput]] = {}
    reasons: dict[str, str] = {}

    def assign(name: str, calculation) -> None:
        try:
            values[name] = calculation()
        except FeatureUnavailable as exc:
            values[name] = None
            reasons[name] = exc.reason
    for concept in (
        "revenue",
        "gross_profit",
        "ebit",
        "net_income_common",
        "diluted_eps",
        "cash_from_operations",
        "capital_expenditure",
        "income_tax_expense",
        "pretax_income",
    ):
        current, prior, reason = book.ttm_pair(concept)
        values[f"ttm_{concept}"] = current
        values[f"prior_ttm_{concept}"] = prior
        if reason:
            reasons[f"prior_ttm_{concept}"] = reason
            if current is None:
                reasons[f"ttm_{concept}"] = reason
    for concept in (
        "total_assets",
        "total_debt",
        "cash_and_equivalents",
        "shareholders_equity",
    ):
        current, prior = book.current_and_prior_instant(concept)
        values[concept] = current
        values[f"prior_{concept}"] = prior
        if current is None:
            reasons[concept] = "SOURCE_MISSING"
        if prior is None:
            reasons[f"prior_{concept}"] = "INSUFFICIENT_HISTORY"

    cfo = values.get("ttm_cash_from_operations")
    capex = values.get("ttm_capital_expenditure")
    if cfo is not None and capex is not None:
        assign(
            "ttm_fcf",
            lambda: _derived(
                "ttm_fcf", cfo.value - capex.value, _same_unit(cfo, capex), cfo, capex
            ),
        )
    else:
        values["ttm_fcf"] = None
        reasons["ttm_fcf"] = (
            reasons.get("ttm_cash_from_operations")
            or reasons.get("ttm_capital_expenditure")
            or "SOURCE_MISSING"
        )
    prior_cfo = values.get("prior_ttm_cash_from_operations")
    prior_capex = values.get("prior_ttm_capital_expenditure")
    if prior_cfo is not None and prior_capex is not None:
        assign(
            "prior_ttm_fcf",
            lambda: _derived(
                "prior_ttm_fcf",
                prior_cfo.value - prior_capex.value,
                _same_unit(prior_cfo, prior_capex),
                prior_cfo,
                prior_capex,
            ),
        )
    else:
        values["prior_ttm_fcf"] = None
        reasons["prior_ttm_fcf"] = "INSUFFICIENT_HISTORY"

    assets = values.get("total_assets")
    prior_assets = values.get("prior_total_assets")
    if assets is not None and prior_assets is not None:
        assign(
            "average_assets",
            lambda: _derived(
                "average_assets",
                (assets.value + prior_assets.value) / Decimal("2"),
                _same_unit(assets, prior_assets),
                assets,
                prior_assets,
            ),
        )
    else:
        values["average_assets"] = None
        reasons["average_assets"] = "INSUFFICIENT_HISTORY"

    debt = values.get("total_debt")
    cash = values.get("cash_and_equivalents")
    equity = values.get("shareholders_equity")
    prior_debt = values.get("prior_total_debt")
    prior_cash = values.get("prior_cash_and_equivalents")
    prior_equity = values.get("prior_shareholders_equity")
    if debt is not None and cash is not None and equity is not None:
        assign(
            "invested_capital",
            lambda: _derived(
                "invested_capital",
                debt.value + equity.value - cash.value,
                _same_unit(debt, cash, equity),
                debt,
                equity,
                cash,
            ),
        )
    else:
        values["invested_capital"] = None
        reasons["invested_capital"] = "SOURCE_MISSING"
    if prior_debt is not None and prior_cash is not None and prior_equity is not None:
        assign(
            "prior_invested_capital",
            lambda: _derived(
                "prior_invested_capital",
                prior_debt.value + prior_equity.value - prior_cash.value,
                _same_unit(prior_debt, prior_cash, prior_equity),
                prior_debt,
                prior_equity,
                prior_cash,
            ),
        )
    else:
        values["prior_invested_capital"] = None
        reasons["prior_invested_capital"] = "INSUFFICIENT_HISTORY"
    invested = values.get("invested_capital")
    prior_invested = values.get("prior_invested_capital")
    if invested is not None and prior_invested is not None:
        assign(
            "average_invested_capital",
            lambda: _derived(
                "average_invested_capital",
                (invested.value + prior_invested.value) / Decimal("2"),
                _same_unit(invested, prior_invested),
                invested,
                prior_invested,
            ),
        )
    else:
        values["average_invested_capital"] = None
        reasons["average_invested_capital"] = "INSUFFICIENT_HISTORY"

    prices, price_snapshot = _load_prices(
        session,
        security_id=security_id,
        prediction_timestamp=timestamp,
        source_snapshot_id=security_price_snapshot_id,
    )
    benchmark_prices, benchmark_snapshot = _load_prices(
        session,
        security_id=benchmark_security_id,
        prediction_timestamp=timestamp,
        source_snapshot_id=benchmark_price_snapshot_id,
    )
    shares = book.latest("common_shares") or book.latest("diluted_shares")
    if prices and price_snapshot is not None and shares is not None:
        latest_price = prices[-1]
        price_scalar = (
            ScalarInput(
                "latest_raw_close",
                latest_price.close,
                "USD",
                (_raw_close_input(latest_price, price_snapshot),),
            )
            if latest_price.close is not None
            else None
        )
        if price_scalar is not None and shares.value > 0 and latest_price.close > 0:
            values["market_cap"] = _derived(
                "market_cap",
                latest_price.close * shares.value,
                "USD",
                price_scalar,
                shares,
            )
        else:
            values["market_cap"] = None
            reasons["market_cap"] = "INVALID_DENOMINATOR"
    else:
        values["market_cap"] = None
        reasons["market_cap"] = "SOURCE_MISSING"
    market_cap = values.get("market_cap")
    if market_cap is not None and debt is not None and cash is not None:
        try:
            unit = _same_unit(market_cap, debt, cash)
            values["enterprise_value"] = _derived(
                "enterprise_value",
                market_cap.value + debt.value - cash.value,
                unit,
                market_cap,
                debt,
                cash,
            )
        except FeatureUnavailable:
            values["enterprise_value"] = None
            reasons["enterprise_value"] = "UNIT_CONFLICT"
    else:
        values["enterprise_value"] = None
        reasons["enterprise_value"] = "SOURCE_MISSING"

    def ratio(numerator_name: str, denominator_name: str, *, positive=True):
        numerator = _required(values, numerator_name, reasons)
        denominator = _required(values, denominator_name, reasons)
        _same_unit(numerator, denominator)
        divisor = (
            _positive_denominator(denominator)
            if positive
            else _nonzero_absolute_denominator(denominator)
        )
        return numerator.value / divisor, _unique_evidence(numerator, denominator)

    calculations = {
        "fcf_yield": lambda: ratio("ttm_fcf", "market_cap"),
        "earnings_yield": lambda: ratio("ttm_net_income_common", "market_cap"),
        "ebit_ev": lambda: ratio("ttm_ebit", "enterprise_value"),
        "sales_yield": lambda: ratio("ttm_revenue", "enterprise_value"),
        "gross_profitability": lambda: ratio("ttm_gross_profit", "average_assets"),
        "fcf_conversion": lambda: ratio("ttm_fcf", "ttm_net_income_common"),
        "inverse_leverage": lambda: (
            -ratio("total_debt", "average_assets")[0],
            ratio("total_debt", "average_assets")[1],
        ),
        "revenue_growth": lambda: _growth(
            values, "ttm_revenue", "prior_ttm_revenue", reasons
        ),
        "eps_growth": lambda: _growth(
            values, "ttm_diluted_eps", "prior_ttm_diluted_eps", reasons
        ),
        "fcf_growth": lambda: _growth(values, "ttm_fcf", "prior_ttm_fcf", reasons),
        "margin_change": lambda: _margin_change(values, reasons),
        "inverse_accruals": lambda: _inverse_accruals(values, reasons),
        "roic": lambda: _roic(values, reasons),
    }
    results: dict[str, RawFeature] = {}
    for name, calculation in calculations.items():
        definition = DEFINITIONS_BY_NAME[name]
        sector_reason = _sector_reason(name, sector, industry)
        results[name] = (
            _not_applicable(definition, sector_reason)
            if sector_reason
            else _feature(definition, calculation)
        )

    results.update(
        _market_features(
            prices,
            price_snapshot,
            benchmark_prices,
            benchmark_snapshot,
        )
    )
    ordered = tuple(results[item.name] for item in FEATURE_DEFINITIONS)
    return MultiFactorFeatureBatch(
        security_id=security_id,
        benchmark_security_id=benchmark_security_id,
        prediction_timestamp=timestamp,
        sector=sector,
        industry=industry,
        features=ordered,
        classification_context={
            "classification_id": classification.classification_id,
            "classification_system": classification.classification_system,
            "sector": classification.sector,
            "industry": classification.industry,
            "effective_from": classification.effective_from.isoformat(),
            "effective_to": (
                classification.effective_to.isoformat()
                if classification.effective_to
                else None
            ),
            "model_available_at": _utc(classification.model_available_at)
            .isoformat()
            .replace("+00:00", "Z"),
            "source_snapshot_id": classification.source_snapshot_id,
            "source_hash": classification.source_hash,
        },
    )


def _growth(
    values: Mapping[str, Optional[ScalarInput]],
    current_name: str,
    prior_name: str,
    reasons: Mapping[str, str],
):
    current = _required(values, current_name, reasons)
    prior = _required(values, prior_name, reasons)
    _same_unit(current, prior)
    denominator = _nonzero_absolute_denominator(prior)
    return (current.value - prior.value) / denominator, _unique_evidence(current, prior)


def _margin_change(
    values: Mapping[str, Optional[ScalarInput]], reasons: Mapping[str, str]
):
    ebit = _required(values, "ttm_ebit", reasons)
    revenue = _required(values, "ttm_revenue", reasons)
    prior_ebit = _required(values, "prior_ttm_ebit", reasons)
    prior_revenue = _required(values, "prior_ttm_revenue", reasons)
    _same_unit(ebit, revenue)
    _same_unit(prior_ebit, prior_revenue)
    current_denominator = _positive_denominator(revenue)
    prior_denominator = _positive_denominator(prior_revenue)
    return (
        ebit.value / current_denominator - prior_ebit.value / prior_denominator,
        _unique_evidence(ebit, revenue, prior_ebit, prior_revenue),
    )


def _inverse_accruals(
    values: Mapping[str, Optional[ScalarInput]], reasons: Mapping[str, str]
):
    income = _required(values, "ttm_net_income_common", reasons)
    cfo = _required(values, "ttm_cash_from_operations", reasons)
    assets = _required(values, "average_assets", reasons)
    _same_unit(income, cfo, assets)
    denominator = _positive_denominator(assets)
    return -(income.value - cfo.value) / denominator, _unique_evidence(income, cfo, assets)


def _roic(
    values: Mapping[str, Optional[ScalarInput]], reasons: Mapping[str, str]
):
    ebit = _required(values, "ttm_ebit", reasons)
    tax = _required(values, "ttm_income_tax_expense", reasons)
    pretax = _required(values, "ttm_pretax_income", reasons)
    invested = _required(values, "average_invested_capital", reasons)
    _same_unit(ebit, tax, pretax, invested)
    pretax_denominator = _positive_denominator(pretax)
    effective_tax = tax.value / pretax_denominator
    if effective_tax < 0 or effective_tax > Decimal("0.50"):
        raise FeatureUnavailable("INVALID_DENOMINATOR", _unique_evidence(tax, pretax))
    invested_denominator = _positive_denominator(invested)
    return (
        ebit.value * (Decimal("1") - effective_tax) / invested_denominator,
        _unique_evidence(ebit, tax, pretax, invested),
    )


def _market_features(
    prices: Sequence[Price],
    snapshot: Optional[SourceSnapshot],
    benchmark_prices: Sequence[Price],
    benchmark_snapshot: Optional[SourceSnapshot],
) -> dict[str, RawFeature]:
    definitions = {name: DEFINITIONS_BY_NAME[name] for name in (
        "momentum_6_1",
        "momentum_12_1",
        "volatility_126d",
        "beta_252d",
        "downside_volatility_126d",
        "maximum_drawdown_252d",
    )}
    if not prices or snapshot is None:
        return {
            name: RawFeature(definition, None, MISSING, "SOURCE_MISSING", ())
            for name, definition in definitions.items()
        }
    evidence = tuple(_price_input(row, snapshot) for row in prices)

    def price_at(offset: int) -> Decimal:
        if len(prices) <= offset:
            raise FeatureUnavailable("INSUFFICIENT_HISTORY", evidence)
        value = prices[-1 - offset].adj_close
        assert value is not None
        if value <= 0:
            raise FeatureUnavailable("INVALID_DENOMINATOR", evidence)
        return value

    result = {
        "momentum_6_1": _feature(
            definitions["momentum_6_1"],
            lambda: (
                price_at(21) / price_at(126) - Decimal("1"),
                tuple(_price_input(prices[index], snapshot) for index in (-22, -127)),
            ),
        ),
        "momentum_12_1": _feature(
            definitions["momentum_12_1"],
            lambda: (
                price_at(21) / price_at(252) - Decimal("1"),
                tuple(_price_input(prices[index], snapshot) for index in (-22, -253)),
            ),
        ),
    }
    try:
        security_returns = _returns(prices)
    except FeatureUnavailable as exc:
        security_returns = []
        return {
            **result,
            **{
                name: RawFeature(definitions[name], None, MISSING, exc.reason, evidence)
                for name in (
                    "volatility_126d",
                    "beta_252d",
                    "downside_volatility_126d",
                    "maximum_drawdown_252d",
                )
            },
        }

    result["volatility_126d"] = _feature(
        definitions["volatility_126d"],
        lambda: (
            _sample_std([value for _, value in security_returns[-126:]])
            * ANNUALIZATION_FACTOR,
            tuple(_price_input(row, snapshot) for row in prices[-127:]),
        ) if len(security_returns) >= 126 else (_raise("INSUFFICIENT_HISTORY", evidence)),
    )
    result["downside_volatility_126d"] = _feature(
        definitions["downside_volatility_126d"],
        lambda: _downside_volatility(security_returns, prices, snapshot, evidence),
    )
    result["maximum_drawdown_252d"] = _feature(
        definitions["maximum_drawdown_252d"],
        lambda: _maximum_drawdown(prices, snapshot, evidence),
    )
    result["beta_252d"] = _feature(
        definitions["beta_252d"],
        lambda: _beta(
            security_returns,
            prices,
            snapshot,
            benchmark_prices,
            benchmark_snapshot,
        ),
    )
    return result


def _raise(reason: str, inputs: Iterable[FeatureInput] = ()):
    raise FeatureUnavailable(reason, inputs)


def _downside_volatility(
    returns: Sequence[tuple[date, Decimal]],
    prices: Sequence[Price],
    snapshot: SourceSnapshot,
    evidence: Sequence[FeatureInput],
):
    if len(returns) < 126:
        return _raise("INSUFFICIENT_HISTORY", evidence)
    values = [min(value, Decimal("0")) for _, value in returns[-126:]]
    rms = (sum((value * value for value in values), Decimal("0")) / Decimal(126)).sqrt()
    return rms * ANNUALIZATION_FACTOR, tuple(
        _price_input(row, snapshot) for row in prices[-127:]
    )


def _maximum_drawdown(
    prices: Sequence[Price], snapshot: SourceSnapshot, evidence: Sequence[FeatureInput]
):
    if len(prices) < 252:
        return _raise("INSUFFICIENT_HISTORY", evidence)
    selected = prices[-252:]
    first = selected[0].adj_close
    assert first is not None
    if first <= 0:
        return _raise("INVALID_DENOMINATOR", evidence)
    peak = first
    drawdown = Decimal("0")
    for row in selected[1:]:
        assert row.adj_close is not None
        if row.adj_close <= 0:
            return _raise("INVALID_DENOMINATOR", evidence)
        peak = max(peak, row.adj_close)
        drawdown = min(drawdown, row.adj_close / peak - Decimal("1"))
    return drawdown, tuple(_price_input(row, snapshot) for row in selected)


def _beta(
    security_returns: Sequence[tuple[date, Decimal]],
    prices: Sequence[Price],
    snapshot: SourceSnapshot,
    benchmark_prices: Sequence[Price],
    benchmark_snapshot: Optional[SourceSnapshot],
):
    if benchmark_snapshot is None:
        return _raise("SOURCE_MISSING")
    benchmark_returns = dict(_returns(benchmark_prices))
    security_by_date = dict(security_returns)
    aligned_dates = sorted(set(security_by_date) & set(benchmark_returns))[-252:]
    if len(aligned_dates) < 240:
        return _raise("INSUFFICIENT_HISTORY")
    security_values = [security_by_date[value] for value in aligned_dates]
    benchmark_values = [benchmark_returns[value] for value in aligned_dates]
    count = Decimal(len(aligned_dates))
    security_mean = sum(security_values, Decimal("0")) / count
    benchmark_mean = sum(benchmark_values, Decimal("0")) / count
    covariance_sum = sum(
        (
            (security_value - security_mean) * (benchmark_value - benchmark_mean)
            for security_value, benchmark_value in zip(
                security_values, benchmark_values
            )
        ),
        Decimal("0"),
    )
    variance_sum = sum(
        ((value - benchmark_mean) ** 2 for value in benchmark_values), Decimal("0")
    )
    if variance_sum <= 0:
        return _raise("INVALID_DENOMINATOR")
    inputs = tuple(
        [_price_input(row, snapshot) for row in prices[-253:]]
        + [
            _price_input(row, benchmark_snapshot)
            for row in benchmark_prices[-253:]
        ]
    )
    return covariance_sum / variance_sum, inputs


def store_multifactor_features(
    session: Session,
    *,
    batch: MultiFactorFeatureBatch,
    feature_set_id: str,
    fundamental_audit: FundamentalAuditBinding,
    code_commit: Optional[str] = None,
) -> FeatureSet:
    """Store valid and missing raw components with complete input lineage."""

    if not batch.source_snapshot_ids:
        raise ValueError("cannot store a feature batch without source evidence")
    if batch.classification_context is None:
        raise ValueError("cannot store features without classification lineage")
    snapshots = {
        row.snapshot_id: row
        for row in session.scalars(
            select(SourceSnapshot).where(
                SourceSnapshot.snapshot_id.in_(batch.source_snapshot_ids)
            )
        ).all()
    }
    if set(snapshots) != set(batch.source_snapshot_ids):
        raise ValueError("feature batch references an unknown source snapshot")
    primary_snapshot_id = batch.source_snapshot_ids[0]
    config = {
        "formula_version": MULTIFACTOR_FEATURE_VERSION,
        "prediction_timestamp": _utc(batch.prediction_timestamp)
        .isoformat()
        .replace("+00:00", "Z"),
        "security_id": batch.security_id,
        "benchmark_security_id": batch.benchmark_security_id,
        "sector": batch.sector,
        "industry": batch.industry,
        "classification": dict(batch.classification_context),
        "fundamental_audit": fundamental_audit.to_dict(),
        "features": [row.definition.name for row in batch.features],
        "source_snapshot_ids": list(batch.source_snapshot_ids),
    }
    existing = session.get(FeatureSet, feature_set_id)
    if existing is not None:
        if (
            existing.name != MULTIFACTOR_FEATURE_SET_NAME
            or existing.version != MULTIFACTOR_FEATURE_VERSION
            or existing.asof_date != batch.prediction_timestamp.date()
            or existing.source_snapshot_id != primary_snapshot_id
            or existing.config_json != config
        ):
            raise ValueError(f"conflicting multi-factor feature set {feature_set_id}")
        stored_rows = list(
            session.scalars(
                select(Feature).where(Feature.feature_set_id == feature_set_id)
            ).all()
        )
        stored = {row.feature_name: row for row in stored_rows}
        if set(stored) != {row.definition.name for row in batch.features}:
            raise ValueError(f"incomplete multi-factor feature set {feature_set_id}")
        quant = Decimal("0.0000000001")
        raw_quant = Decimal("0.000000000001")
        for item in batch.features:
            row = stored[item.definition.name]
            expected_value = (
                item.value.quantize(quant) if item.value is not None else None
            )
            expected_raw_value = (
                item.value.quantize(raw_quant) if item.value is not None else None
            )
            if (
                row.value != expected_value
                or row.raw_value != expected_raw_value
                or row.family != item.definition.family
                or row.formula_version != MULTIFACTOR_FEATURE_VERSION
                or row.formula != item.definition.formula
                or row.direction != item.definition.direction
                or row.applicability_status != item.status
                or row.missing_reason != item.missing_reason
                or row.inputs_json != item.inputs_json(batch.prediction_timestamp)
            ):
                raise ValueError(
                    f"multi-factor feature does not reproduce: {item.definition.name}"
                )
        return existing
    feature_set = FeatureSet(
        feature_set_id=feature_set_id,
        name=MULTIFACTOR_FEATURE_SET_NAME,
        version=MULTIFACTOR_FEATURE_VERSION,
        asof_date=batch.prediction_timestamp.date(),
        config_json=config,
        source_snapshot_id=primary_snapshot_id,
        code_commit=code_commit,
    )
    session.add(feature_set)
    for item in batch.features:
        lineage = item.inputs_json(batch.prediction_timestamp)
        source_ids = lineage["source_snapshot_ids"]
        source_id = source_ids[0] if source_ids else primary_snapshot_id
        snapshot = snapshots[source_id]
        session.add(
            Feature(
                feature_set_id=feature_set_id,
                security_id=batch.security_id,
                asof_date=batch.prediction_timestamp.date(),
                available_at=batch.prediction_timestamp,
                feature_name=item.definition.name,
                value=item.value,
                raw_value=item.value,
                version=MULTIFACTOR_FEATURE_VERSION,
                family=item.definition.family,
                formula_version=MULTIFACTOR_FEATURE_VERSION,
                formula=item.definition.formula,
                direction=item.definition.direction,
                applicability_status=item.status,
                missing_reason=item.missing_reason,
                inputs_json=lineage,
                source_snapshot_id=source_id,
                source_hash=snapshot.source_hash,
            )
        )
    session.flush()
    return feature_set
