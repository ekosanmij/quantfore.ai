import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from quantfore_research.features.model_v2 import (
    APPLICABLE,
    BRANCH_ACCOUNTING_DEFINITIONS,
    BRANCH_FEATURE_DEFINITIONS,
    MODEL_V2_FEATURE_VERSION,
    MODEL_V2_FORMULA_VERSION,
    ScalarValue,
    branch_schema_document,
    evaluate_branch_accounting_features,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _scalar(value, unit="USD", name="source"):
    return ScalarValue(Decimal(str(value)), unit, (name,))


def _by_name(branch, inputs):
    return {
        row.definition.name: row
        for row in evaluate_branch_accounting_features(
            sector_branch=branch, accounting_inputs=inputs
        )
    }


def test_specialist_schemas_are_strict_subsets_of_locked_candidate_envelope():
    contract = json.loads(
        (
            REPOSITORY_ROOT
            / "docs/research/sector-specific-factor-treatment-v1.json"
        ).read_text(encoding="utf-8")
    )
    candidates = contract["proposed_branch_features"]
    for branch, definitions in BRANCH_ACCOUNTING_DEFINITIONS.items():
        families = {family: set(values) for family, values in candidates.get(branch, {}).items()}
        if branch == "INDUSTRIAL_GENERAL":
            assert len(definitions) == 13
            continue
        for definition in definitions:
            assert definition.name in families[definition.family], (branch, definition.name)

    for branch, definitions in BRANCH_FEATURE_DEFINITIONS.items():
        assert {row.family for row in definitions} == {
            "value",
            "quality",
            "growth",
            "momentum",
            "risk",
        }, branch


def test_bank_formulas_use_bank_balance_sheet_growth_not_industrial_fcf():
    inputs = {
        "market_cap": _scalar(1_000, name="market"),
        "current_ttm_net_income_common": _scalar(50, name="income"),
        "current_total_assets": _scalar(1_100, name="assets-current"),
        "prior_total_assets": _scalar(900, name="assets-prior"),
        "current_shareholders_equity": _scalar(220, name="equity-current"),
        "prior_shareholders_equity": _scalar(180, name="equity-prior"),
        "current_loans_and_leases_net": _scalar(600, name="loans-current"),
        "prior_loans_and_leases_net": _scalar(500, name="loans-prior"),
        "current_customer_deposits": _scalar(800, name="deposits-current"),
        "prior_customer_deposits": _scalar(700, name="deposits-prior"),
        "current_ttm_diluted_eps": _scalar(5, "USD/shares", "eps-current"),
        "prior_ttm_diluted_eps": _scalar(4, "USD/shares", "eps-prior"),
        # Industrial-only inputs must not enter a bank feature.
        "current_ttm_cash_from_operations": _scalar(999, name="cfo"),
        "current_ttm_capital_expenditure": _scalar(1, name="capex"),
    }
    features = _by_name("BANK", inputs)

    assert set(features) == {
        "earnings_yield",
        "return_on_assets",
        "return_on_equity",
        "loan_growth",
        "deposit_growth",
        "eps_growth",
    }
    assert features["earnings_yield"].value == Decimal("0.05")
    assert features["return_on_assets"].value == Decimal("0.05")
    assert features["return_on_equity"].value == Decimal("0.25")
    assert features["loan_growth"].value == Decimal("0.2")
    assert "cfo" not in features["loan_growth"].lineage_ids


def test_insurer_and_reit_formulas_follow_their_distinct_accounting_models():
    insurer = _by_name(
        "INSURER_P_AND_C",
        {
            "market_cap": _scalar(1_000),
            "current_ttm_net_income_common": _scalar(50),
            "current_shareholders_equity": _scalar(500),
            "prior_shareholders_equity": _scalar(450),
            "current_ttm_policyholder_benefits_claims_net": _scalar(75),
            "current_ttm_premiums_earned_net": _scalar(100),
            "latest_diluted_shares": _scalar(10, "shares"),
            "prior_diluted_shares": _scalar(10, "shares"),
            "current_ttm_diluted_eps": _scalar(5, "USD/shares"),
            "prior_ttm_diluted_eps": _scalar(4, "USD/shares"),
        },
    )
    assert insurer["loss_ratio_inverse"].value == Decimal("-0.75")

    reit = _by_name(
        "EQUITY_REIT",
        {
            "market_cap": _scalar(2_000),
            "current_ttm_net_income_common": _scalar(100),
            "current_ttm_depreciation_and_amortization": _scalar(80),
            "current_ttm_investment_real_estate_sale_gain_loss": _scalar(20),
            "prior_ttm_net_income_common": _scalar(90),
            "prior_ttm_depreciation_and_amortization": _scalar(70),
            "prior_ttm_investment_real_estate_sale_gain_loss": _scalar(10),
            "current_ttm_ebit": _scalar(150),
            "current_ttm_interest_expense": _scalar(50),
            "latest_diluted_shares": _scalar(10, "shares"),
            "prior_diluted_shares": _scalar(10, "shares"),
        },
    )
    assert reit["ffo_yield"].value == Decimal("0.08")
    assert reit["interest_coverage"].value == Decimal("4.2")
    assert reit["ffo_per_share_growth"].value == Decimal("0.06666666666666666666666666667")


def test_missing_branch_input_stays_missing_with_a_stable_reason_and_no_imputation():
    features = _by_name(
        "BANK",
        {
            "market_cap": _scalar(1_000),
            "current_ttm_net_income_common": _scalar(50),
            "current_total_assets": _scalar(1_100),
            "prior_total_assets": _scalar(900),
            "current_shareholders_equity": _scalar(220),
            "prior_shareholders_equity": _scalar(180),
            "current_loans_and_leases_net": _scalar(600),
        },
    )
    missing = features["loan_growth"]
    assert missing.value is None
    assert missing.status != APPLICABLE
    assert missing.reason_code == "SOURCE_MISSING"
    assert "prior_loans_and_leases_net" in missing.reason_detail


def test_schema_document_is_complete_and_versioned():
    document = branch_schema_document()
    assert document["feature_version"] == MODEL_V2_FEATURE_VERSION
    assert document["formula_version"] == MODEL_V2_FORMULA_VERSION
    assert set(document["branches"]) == set(BRANCH_FEATURE_DEFINITIONS)
    assert all(
        component["required"] is True
        for components in document["branches"].values()
        for component in components
    )
