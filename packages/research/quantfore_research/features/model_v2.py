"""Outcome-blind branch feature formulas for the Model V2 implementation.

This module is deliberately separate from the frozen Sprint 8 feature builder.  It
defines the exact, pre-outcome accounting formulas selected from the candidate
envelope in ``sector-specific-factor-treatment-v1`` and combines them with the six
unchanged universal price components.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Mapping, Optional, Sequence


MODEL_V2_FEATURE_VERSION = "multifactor-v2-branch-aware-v1"
MODEL_V2_FORMULA_VERSION = "multifactor-v2-branch-formulas-v1"
APPLICABLE = "APPLICABLE"
MISSING = "MISSING"
HIGHER = "HIGHER"
LOWER = "LOWER"

ACTIVE_BRANCHES = (
    "INDUSTRIAL_GENERAL",
    "BANK",
    "INSURER_P_AND_C",
    "INSURER_LIFE_HEALTH",
    "BROKER_DEALER",
    "ASSET_MANAGER",
    "EQUITY_REIT",
    "MORTGAGE_REIT",
)


@dataclass(frozen=True)
class ModelV2FeatureDefinition:
    name: str
    family: str
    formula: str
    direction: str


@dataclass(frozen=True)
class ScalarValue:
    """One point-in-time formula input and its immutable source lineage."""

    value: Decimal
    unit: str
    lineage_ids: tuple[str, ...]


@dataclass(frozen=True)
class RawModelV2Feature:
    definition: ModelV2FeatureDefinition
    value: Optional[Decimal]
    status: str
    reason_code: Optional[str]
    reason_detail: Optional[str]
    lineage_ids: tuple[str, ...]


@dataclass(frozen=True)
class ModelV2FeatureBatch:
    security_id: str
    prediction_date: date
    sector_branch: str
    classification_eligible: bool
    classification_reason_codes: tuple[str, ...]
    classification_id: Optional[str]
    components: tuple[RawModelV2Feature, ...]

    def by_name(self) -> dict[str, RawModelV2Feature]:
        return {row.definition.name: row for row in self.components}


UNIVERSAL_DEFINITIONS = (
    ModelV2FeatureDefinition(
        "momentum_6_1",
        "momentum",
        "adj_close[t-21] / adj_close[t-126] - 1",
        HIGHER,
    ),
    ModelV2FeatureDefinition(
        "momentum_12_1",
        "momentum",
        "adj_close[t-21] / adj_close[t-252] - 1",
        HIGHER,
    ),
    ModelV2FeatureDefinition(
        "volatility_126d",
        "risk",
        "sample_std(last 126 daily returns) * sqrt(252)",
        LOWER,
    ),
    ModelV2FeatureDefinition(
        "beta_252d",
        "risk",
        "cov(security, benchmark) / var(benchmark) over 252 aligned returns",
        LOWER,
    ),
    ModelV2FeatureDefinition(
        "downside_volatility_126d",
        "risk",
        "rms(min(daily_return, 0), 126) * sqrt(252)",
        LOWER,
    ),
    ModelV2FeatureDefinition(
        "maximum_drawdown_252d",
        "risk",
        "min(adj_close / running_peak - 1, 252 sessions)",
        HIGHER,
    ),
)


def _definition(name: str, family: str, formula: str) -> ModelV2FeatureDefinition:
    # Every accounting component is defined in an already economically directed
    # form (for example, leverage and loss ratios are negated), so higher is
    # consistently preferable after formula evaluation.
    return ModelV2FeatureDefinition(name, family, formula, HIGHER)


BRANCH_ACCOUNTING_DEFINITIONS = {
    "INDUSTRIAL_GENERAL": (
        _definition("fcf_yield", "value", "(TTM CFO - TTM capex) / market_cap"),
        _definition("earnings_yield", "value", "TTM net_income_common / market_cap"),
        _definition("ebit_ev", "value", "TTM EBIT / (market_cap + debt - cash)"),
        _definition("sales_yield", "value", "TTM revenue / (market_cap + debt - cash)"),
        _definition("roic", "quality", "TTM EBIT * (1 - effective_tax_rate) / average invested capital"),
        _definition("gross_profitability", "quality", "TTM gross_profit / average total_assets"),
        _definition("fcf_conversion", "quality", "TTM FCF / abs(TTM net_income_common)"),
        _definition("inverse_accruals", "quality", "-(TTM net_income_common - TTM CFO) / average total_assets"),
        _definition("inverse_leverage", "quality", "-total_debt / average total_assets"),
        _definition("revenue_growth", "growth", "(current TTM revenue - prior TTM revenue) / abs(prior TTM revenue)"),
        _definition("eps_growth", "growth", "(current TTM diluted EPS - prior TTM diluted EPS) / abs(prior TTM diluted EPS)"),
        _definition("fcf_growth", "growth", "(current TTM FCF - prior TTM FCF) / abs(prior TTM FCF)"),
        _definition("margin_change", "growth", "current TTM EBIT margin - prior TTM EBIT margin"),
    ),
    "BANK": (
        _definition("earnings_yield", "value", "TTM net_income_common / market_cap"),
        _definition("return_on_assets", "quality", "TTM net_income_common / average total_assets"),
        _definition("return_on_equity", "quality", "TTM net_income_common / average shareholders_equity"),
        _definition("loan_growth", "growth", "(loans[t] - loans[t-1y]) / abs(loans[t-1y])"),
        _definition("deposit_growth", "growth", "(deposits[t] - deposits[t-1y]) / abs(deposits[t-1y])"),
        _definition("eps_growth", "growth", "(current TTM diluted EPS - prior TTM diluted EPS) / abs(prior TTM diluted EPS)"),
    ),
    "BROKER_DEALER": (
        _definition("earnings_yield", "value", "TTM net_income_common / market_cap"),
        _definition("return_on_equity", "quality", "TTM net_income_common / average shareholders_equity"),
        _definition("net_revenue_growth", "growth", "(current TTM revenue - prior TTM revenue) / abs(prior TTM revenue)"),
        _definition("eps_growth", "growth", "(current TTM diluted EPS - prior TTM diluted EPS) / abs(prior TTM diluted EPS)"),
    ),
    "ASSET_MANAGER": (
        _definition("earnings_yield", "value", "TTM net_income_common / market_cap"),
        _definition("price_to_book_inverse", "value", "shareholders_equity / market_cap"),
        _definition("operating_margin", "quality", "TTM EBIT / TTM revenue"),
        _definition("return_on_equity", "quality", "TTM net_income_common / average shareholders_equity"),
        _definition("eps_growth", "growth", "(current TTM diluted EPS - prior TTM diluted EPS) / abs(prior TTM diluted EPS)"),
    ),
    "INSURER_P_AND_C": (
        _definition("earnings_yield", "value", "TTM net_income_common / market_cap"),
        _definition("price_to_book_inverse", "value", "shareholders_equity / market_cap"),
        _definition("loss_ratio_inverse", "quality", "-TTM policyholder benefits and claims / TTM net premiums earned"),
        _definition("return_on_equity", "quality", "TTM net_income_common / average shareholders_equity"),
        _definition("book_value_per_share_growth", "growth", "growth in shareholders_equity / diluted_shares"),
        _definition("eps_growth", "growth", "(current TTM diluted EPS - prior TTM diluted EPS) / abs(prior TTM diluted EPS)"),
    ),
    "INSURER_LIFE_HEALTH": (
        _definition("earnings_yield", "value", "TTM net_income_common / market_cap"),
        _definition("price_to_book_inverse", "value", "shareholders_equity / market_cap"),
        _definition("return_on_equity", "quality", "TTM net_income_common / average shareholders_equity"),
        _definition("investment_yield", "quality", "TTM net investment income / average total_assets"),
        _definition("premium_growth", "growth", "growth in TTM net premiums earned"),
        _definition("book_value_per_share_growth", "growth", "growth in shareholders_equity / diluted_shares"),
        _definition("eps_growth", "growth", "(current TTM diluted EPS - prior TTM diluted EPS) / abs(prior TTM diluted EPS)"),
    ),
    "EQUITY_REIT": (
        _definition("ffo_yield", "value", "(TTM net income + TTM D&A - TTM real-estate sale gains) / market_cap"),
        _definition("interest_coverage", "quality", "(TTM EBIT + TTM D&A - TTM real-estate sale gains) / abs(TTM interest expense)"),
        _definition("ffo_per_share_growth", "growth", "growth in FFO / diluted_shares"),
    ),
    "MORTGAGE_REIT": (
        _definition("price_to_book_inverse", "value", "shareholders_equity / market_cap"),
        _definition("earnings_yield", "value", "TTM net_income_common / market_cap"),
        _definition("economic_leverage_inverse", "quality", "-total_debt / shareholders_equity"),
        _definition("liquidity_ratio", "quality", "cash_and_equivalents / total_assets"),
        _definition("book_value_per_share_growth", "growth", "growth in shareholders_equity / diluted_shares"),
        _definition("net_interest_income_growth", "growth", "growth in TTM net interest income"),
    ),
}

BRANCH_FEATURE_DEFINITIONS = {
    branch: accounting + UNIVERSAL_DEFINITIONS
    for branch, accounting in BRANCH_ACCOUNTING_DEFINITIONS.items()
}


class FormulaUnavailable(Exception):
    def __init__(
        self,
        reason_code: str,
        detail: str,
        lineage_ids: Sequence[str] = (),
    ) -> None:
        super().__init__(detail)
        self.reason_code = reason_code
        self.detail = detail
        self.lineage_ids = tuple(lineage_ids)


def _unique_lineage(*values: ScalarValue) -> tuple[str, ...]:
    return tuple(sorted({item for value in values for item in value.lineage_ids}))


def _required(inputs: Mapping[str, ScalarValue], name: str) -> ScalarValue:
    value = inputs.get(name)
    if value is None:
        raise FormulaUnavailable("SOURCE_MISSING", f"required input is missing: {name}")
    if not value.value.is_finite():
        raise FormulaUnavailable(
            "NONFINITE_VALUE", f"required input is non-finite: {name}", value.lineage_ids
        )
    return value


def _same_unit(*values: ScalarValue) -> None:
    units = {value.unit.lower() for value in values}
    if len(units) != 1:
        raise FormulaUnavailable(
            "UNIT_CONFLICT",
            "formula inputs use incompatible units",
            _unique_lineage(*values),
        )


def _positive(value: ScalarValue) -> Decimal:
    if value.value <= 0:
        raise FormulaUnavailable(
            "INVALID_DENOMINATOR", "formula denominator must be positive", value.lineage_ids
        )
    return value.value


def _nonzero_abs(value: ScalarValue) -> Decimal:
    if value.value == 0:
        raise FormulaUnavailable(
            "INVALID_DENOMINATOR", "formula denominator must be non-zero", value.lineage_ids
        )
    return abs(value.value)


def _ratio(
    inputs: Mapping[str, ScalarValue],
    numerator: str,
    denominator: str,
    *,
    positive_denominator: bool = True,
) -> tuple[Decimal, tuple[str, ...]]:
    left = _required(inputs, numerator)
    right = _required(inputs, denominator)
    _same_unit(left, right)
    divisor = _positive(right) if positive_denominator else _nonzero_abs(right)
    return left.value / divisor, _unique_lineage(left, right)


def _average(
    inputs: Mapping[str, ScalarValue], current: str, prior: str
) -> ScalarValue:
    left = _required(inputs, current)
    right = _required(inputs, prior)
    _same_unit(left, right)
    return ScalarValue(
        (left.value + right.value) / Decimal("2"),
        left.unit,
        _unique_lineage(left, right),
    )


def _growth(
    inputs: Mapping[str, ScalarValue], current: str, prior: str
) -> tuple[Decimal, tuple[str, ...]]:
    left = _required(inputs, current)
    right = _required(inputs, prior)
    _same_unit(left, right)
    return (
        (left.value - right.value) / _nonzero_abs(right),
        _unique_lineage(left, right),
    )


def _fcf(inputs: Mapping[str, ScalarValue], prefix: str) -> ScalarValue:
    cfo = _required(inputs, f"{prefix}_ttm_cash_from_operations")
    capex = _required(inputs, f"{prefix}_ttm_capital_expenditure")
    _same_unit(cfo, capex)
    return ScalarValue(cfo.value - capex.value, cfo.unit, _unique_lineage(cfo, capex))


def _ffo(inputs: Mapping[str, ScalarValue], prefix: str) -> ScalarValue:
    income = _required(inputs, f"{prefix}_ttm_net_income_common")
    depreciation = _required(inputs, f"{prefix}_ttm_depreciation_and_amortization")
    sale_gain = _required(inputs, f"{prefix}_ttm_investment_real_estate_sale_gain_loss")
    _same_unit(income, depreciation, sale_gain)
    return ScalarValue(
        income.value + depreciation.value - sale_gain.value,
        income.unit,
        _unique_lineage(income, depreciation, sale_gain),
    )


def _market_cap(inputs: Mapping[str, ScalarValue]) -> ScalarValue:
    close = _required(inputs, "latest_raw_close")
    shares = _required(inputs, "latest_diluted_shares")
    if close.unit.lower() != "usd" or shares.unit.lower() != "shares":
        raise FormulaUnavailable(
            "UNIT_CONFLICT",
            "market cap requires USD close and share-count inputs",
            _unique_lineage(close, shares),
        )
    if close.value <= 0 or shares.value <= 0:
        raise FormulaUnavailable(
            "INVALID_DENOMINATOR",
            "market cap inputs must be positive",
            _unique_lineage(close, shares),
        )
    return ScalarValue(
        close.value * shares.value,
        "USD",
        _unique_lineage(close, shares),
    )


def _augmented_inputs(inputs: Mapping[str, ScalarValue]) -> dict[str, ScalarValue]:
    values = dict(inputs)
    optional_derived = (
        ("market_cap", lambda: _market_cap(values)),
        (
            "average_total_assets",
            lambda: _average(values, "current_total_assets", "prior_total_assets"),
        ),
        (
            "average_shareholders_equity",
            lambda: _average(
                values, "current_shareholders_equity", "prior_shareholders_equity"
            ),
        ),
    )
    for name, calculation in optional_derived:
        if name in values:
            continue
        try:
            values[name] = calculation()
        except FormulaUnavailable:
            # A derived scalar can be unnecessary for the active branch.  The
            # individual component formula records the missing reason if it is
            # actually required.
            pass
    return values


def _calculate(
    name: str, inputs: Mapping[str, ScalarValue]
) -> tuple[Decimal, tuple[str, ...]]:
    values = dict(inputs)

    if name in {"earnings_yield", "return_on_assets", "return_on_equity"}:
        denominator = {
            "earnings_yield": "market_cap",
            "return_on_assets": "average_total_assets",
            "return_on_equity": "average_shareholders_equity",
        }[name]
        return _ratio(values, "current_ttm_net_income_common", denominator)
    if name == "price_to_book_inverse":
        return _ratio(values, "current_shareholders_equity", "market_cap")
    if name == "fcf_yield":
        fcf = _fcf(values, "current")
        values["current_ttm_fcf"] = fcf
        return _ratio(values, "current_ttm_fcf", "market_cap")
    if name in {"ebit_ev", "sales_yield"}:
        market_cap = _required(values, "market_cap")
        debt = _required(values, "current_total_debt")
        cash = _required(values, "current_cash_and_equivalents")
        _same_unit(market_cap, debt, cash)
        enterprise_value = ScalarValue(
            market_cap.value + debt.value - cash.value,
            market_cap.unit,
            _unique_lineage(market_cap, debt, cash),
        )
        values["enterprise_value"] = enterprise_value
        numerator = "current_ttm_ebit" if name == "ebit_ev" else "current_ttm_revenue"
        return _ratio(values, numerator, "enterprise_value")
    if name == "gross_profitability":
        return _ratio(values, "current_ttm_gross_profit", "average_total_assets")
    if name == "fcf_conversion":
        fcf = _fcf(values, "current")
        income = _required(values, "current_ttm_net_income_common")
        _same_unit(fcf, income)
        return fcf.value / _positive(income), _unique_lineage(fcf, income)
    if name == "inverse_accruals":
        income = _required(values, "current_ttm_net_income_common")
        cfo = _required(values, "current_ttm_cash_from_operations")
        assets = _required(values, "average_total_assets")
        _same_unit(income, cfo, assets)
        return -(income.value - cfo.value) / _positive(assets), _unique_lineage(
            income, cfo, assets
        )
    if name == "inverse_leverage":
        value, lineage = _ratio(values, "current_total_debt", "average_total_assets")
        return -value, lineage
    if name in {"revenue_growth", "net_revenue_growth"}:
        return _growth(values, "current_ttm_revenue", "prior_ttm_revenue")
    if name == "eps_growth":
        return _growth(values, "current_ttm_diluted_eps", "prior_ttm_diluted_eps")
    if name == "fcf_growth":
        current = _fcf(values, "current")
        prior = _fcf(values, "prior")
        values.update(current_ttm_fcf=current, prior_ttm_fcf=prior)
        return _growth(values, "current_ttm_fcf", "prior_ttm_fcf")
    if name in {"loan_growth", "deposit_growth"}:
        concept = "loans_and_leases_net" if name == "loan_growth" else "customer_deposits"
        return _growth(values, f"current_{concept}", f"prior_{concept}")
    if name == "margin_change":
        current_ebit = _required(values, "current_ttm_ebit")
        current_revenue = _required(values, "current_ttm_revenue")
        prior_ebit = _required(values, "prior_ttm_ebit")
        prior_revenue = _required(values, "prior_ttm_revenue")
        _same_unit(current_ebit, current_revenue)
        _same_unit(prior_ebit, prior_revenue)
        return (
            current_ebit.value / _positive(current_revenue)
            - prior_ebit.value / _positive(prior_revenue),
            _unique_lineage(current_ebit, current_revenue, prior_ebit, prior_revenue),
        )
    if name == "operating_margin":
        return _ratio(values, "current_ttm_ebit", "current_ttm_revenue")
    if name == "loss_ratio_inverse":
        ratio, lineage = _ratio(
            values,
            "current_ttm_policyholder_benefits_claims_net",
            "current_ttm_premiums_earned_net",
        )
        return -ratio, lineage
    if name == "investment_yield":
        return _ratio(
            values, "current_ttm_net_investment_income", "average_total_assets"
        )
    if name == "premium_growth":
        return _growth(
            values,
            "current_ttm_premiums_earned_net",
            "prior_ttm_premiums_earned_net",
        )
    if name == "book_value_per_share_growth":
        equity = _required(values, "current_shareholders_equity")
        prior_equity = _required(values, "prior_shareholders_equity")
        shares = _required(values, "latest_diluted_shares")
        prior_shares = _required(values, "prior_diluted_shares")
        _same_unit(equity, prior_equity)
        _same_unit(shares, prior_shares)
        if shares.unit.lower() != "shares":
            raise FormulaUnavailable(
                "UNIT_CONFLICT",
                "book value per share requires share-count inputs",
                _unique_lineage(equity, prior_equity, shares, prior_shares),
            )
        current = ScalarValue(
            equity.value / _positive(shares),
            "USD/shares",
            _unique_lineage(equity, shares),
        )
        prior = ScalarValue(
            prior_equity.value / _positive(prior_shares),
            "USD/shares",
            _unique_lineage(prior_equity, prior_shares),
        )
        values.update(current_bvps=current, prior_bvps=prior)
        return _growth(values, "current_bvps", "prior_bvps")
    if name == "ffo_yield":
        ffo = _ffo(values, "current")
        values["current_ttm_ffo"] = ffo
        return _ratio(values, "current_ttm_ffo", "market_cap")
    if name == "interest_coverage":
        ebit = _required(values, "current_ttm_ebit")
        depreciation = _required(values, "current_ttm_depreciation_and_amortization")
        sale_gain = _required(values, "current_ttm_investment_real_estate_sale_gain_loss")
        interest = _required(values, "current_ttm_interest_expense")
        _same_unit(ebit, depreciation, sale_gain, interest)
        numerator = ebit.value + depreciation.value - sale_gain.value
        return numerator / _nonzero_abs(interest), _unique_lineage(
            ebit, depreciation, sale_gain, interest
        )
    if name == "ffo_per_share_growth":
        current_ffo = _ffo(values, "current")
        prior_ffo = _ffo(values, "prior")
        shares = _required(values, "latest_diluted_shares")
        prior_shares = _required(values, "prior_diluted_shares")
        if shares.unit.lower() != "shares" or prior_shares.unit.lower() != "shares":
            raise FormulaUnavailable(
                "UNIT_CONFLICT",
                "FFO per share requires share-count inputs",
                _unique_lineage(current_ffo, prior_ffo, shares, prior_shares),
            )
        values["current_ffo_per_share"] = ScalarValue(
            current_ffo.value / _positive(shares),
            "USD/shares",
            _unique_lineage(current_ffo, shares),
        )
        values["prior_ffo_per_share"] = ScalarValue(
            prior_ffo.value / _positive(prior_shares),
            "USD/shares",
            _unique_lineage(prior_ffo, prior_shares),
        )
        return _growth(values, "current_ffo_per_share", "prior_ffo_per_share")
    if name == "economic_leverage_inverse":
        ratio, lineage = _ratio(
            values, "current_total_debt", "current_shareholders_equity"
        )
        return -ratio, lineage
    if name == "liquidity_ratio":
        return _ratio(values, "current_cash_and_equivalents", "current_total_assets")
    if name == "net_interest_income_growth":
        return _growth(
            values,
            "current_ttm_net_interest_income",
            "prior_ttm_net_interest_income",
        )
    if name == "roic":
        ebit = _required(values, "current_ttm_ebit")
        tax = _required(values, "current_ttm_income_tax_expense")
        pretax = _required(values, "current_ttm_pretax_income")
        debt = _required(values, "current_total_debt")
        prior_debt = _required(values, "prior_total_debt")
        equity = _required(values, "current_shareholders_equity")
        prior_equity = _required(values, "prior_shareholders_equity")
        cash = _required(values, "current_cash_and_equivalents")
        prior_cash = _required(values, "prior_cash_and_equivalents")
        _same_unit(ebit, tax, pretax, debt, prior_debt, equity, prior_equity, cash, prior_cash)
        tax_rate = tax.value / _positive(pretax)
        if tax_rate < 0 or tax_rate > Decimal("0.50"):
            raise FormulaUnavailable(
                "INVALID_DENOMINATOR",
                "effective tax rate is outside the locked 0%-50% range",
                _unique_lineage(tax, pretax),
            )
        current_capital = debt.value + equity.value - cash.value
        prior_capital = prior_debt.value + prior_equity.value - prior_cash.value
        average_capital = (current_capital + prior_capital) / Decimal("2")
        if average_capital <= 0:
            raise FormulaUnavailable(
                "INVALID_DENOMINATOR",
                "average invested capital must be positive",
                _unique_lineage(debt, prior_debt, equity, prior_equity, cash, prior_cash),
            )
        return (
            ebit.value * (Decimal("1") - tax_rate) / average_capital,
            _unique_lineage(
                ebit, tax, pretax, debt, prior_debt, equity, prior_equity, cash, prior_cash
            ),
        )
    raise ValueError(f"no locked Model V2 formula exists for component: {name}")


def _raw_feature(
    definition: ModelV2FeatureDefinition,
    calculation: Callable[[], tuple[Decimal, tuple[str, ...]]],
) -> RawModelV2Feature:
    try:
        value, lineage = calculation()
        if not value.is_finite() or not math.isfinite(float(value)):
            raise FormulaUnavailable("NONFINITE_VALUE", "formula result is non-finite", lineage)
        return RawModelV2Feature(
            definition=definition,
            value=value,
            status=APPLICABLE,
            reason_code=None,
            reason_detail=None,
            lineage_ids=tuple(sorted(set(lineage))),
        )
    except FormulaUnavailable as exc:
        return RawModelV2Feature(
            definition=definition,
            value=None,
            status=MISSING,
            reason_code=exc.reason_code,
            reason_detail=exc.detail,
            lineage_ids=tuple(sorted(set(exc.lineage_ids))),
        )


def evaluate_branch_accounting_features(
    *,
    sector_branch: str,
    accounting_inputs: Mapping[str, ScalarValue],
) -> tuple[RawModelV2Feature, ...]:
    """Calculate only the accounting schema locked for one branch."""

    if sector_branch not in BRANCH_ACCOUNTING_DEFINITIONS:
        raise ValueError(f"unsupported Model V2 branch: {sector_branch}")
    augmented = _augmented_inputs(accounting_inputs)
    return tuple(
        _raw_feature(definition, lambda definition=definition: _calculate(definition.name, augmented))
        for definition in BRANCH_ACCOUNTING_DEFINITIONS[sector_branch]
    )


def build_model_v2_feature_batch(
    *,
    security_id: str,
    prediction_date: date,
    sector_branch: str,
    classification_eligible: bool,
    classification_reason_codes: Sequence[str],
    classification_id: Optional[str],
    accounting_inputs: Mapping[str, ScalarValue],
    universal_features: Mapping[str, RawModelV2Feature],
) -> ModelV2FeatureBatch:
    """Build one branch-complete row without imputation or cross-branch reuse."""

    if not classification_eligible:
        return ModelV2FeatureBatch(
            security_id=security_id,
            prediction_date=prediction_date,
            sector_branch=sector_branch,
            classification_eligible=False,
            classification_reason_codes=tuple(classification_reason_codes)
            or ("CLASSIFICATION_SOURCE_UNAVAILABLE",),
            classification_id=classification_id,
            components=(),
        )
    if sector_branch not in BRANCH_FEATURE_DEFINITIONS:
        raise ValueError(f"classification routed to an inactive branch: {sector_branch}")
    expected_universal = {definition.name for definition in UNIVERSAL_DEFINITIONS}
    if set(universal_features) != expected_universal:
        missing = sorted(expected_universal - set(universal_features))
        extra = sorted(set(universal_features) - expected_universal)
        raise ValueError(f"universal feature set mismatch; missing={missing} extra={extra}")
    for definition in UNIVERSAL_DEFINITIONS:
        raw = universal_features[definition.name]
        if raw.definition != definition:
            raise ValueError(f"universal definition mismatch: {definition.name}")
    accounting = evaluate_branch_accounting_features(
        sector_branch=sector_branch,
        accounting_inputs=accounting_inputs,
    )
    return ModelV2FeatureBatch(
        security_id=security_id,
        prediction_date=prediction_date,
        sector_branch=sector_branch,
        classification_eligible=True,
        classification_reason_codes=(),
        classification_id=classification_id,
        components=accounting
        + tuple(universal_features[definition.name] for definition in UNIVERSAL_DEFINITIONS),
    )


def branch_schema_document() -> dict[str, Any]:
    """Return the deterministic machine-readable formula/schema lock."""

    return {
        "feature_version": MODEL_V2_FEATURE_VERSION,
        "formula_version": MODEL_V2_FORMULA_VERSION,
        "branches": {
            branch: [
                {
                    "name": definition.name,
                    "family": definition.family,
                    "formula": definition.formula,
                    "direction": definition.direction,
                    "required": True,
                }
                for definition in definitions
            ]
            for branch, definitions in BRANCH_FEATURE_DEFINITIONS.items()
        },
    }
