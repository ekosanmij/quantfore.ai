import hashlib
import json
from pathlib import Path

from quantfore_research.features.multifactor import (
    FEATURE_DEFINITIONS,
    FINANCIALS_MASK,
    REIT_MASK,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "research"
    / "sector-specific-factor-treatment-v1.json"
)


def _contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def test_contract_locks_separate_subtype_branches_without_rewriting_sprint8():
    contract = _contract()
    decision = contract["decision"]

    assert contract["claims_eligible"] is False
    assert decision["financials_need_own_model_branch"] is True
    assert decision["allow_financials_in_generic_industrial_model"] is False
    assert decision["allow_reits_in_generic_financials_model"] is False
    assert decision["retroactively_change_sprint8"] is False
    assert (
        decision["model_structure"]
        == "SEPARATE_SUBTYPE_BRANCHES_NOT_ONE_MONOLITHIC_FINANCIALS_BRANCH"
    )
    assert contract["implementation_boundary"]["implementation_authorized_by_this_contract"] is False


def test_each_branch_partitions_every_current_feature_exactly_once():
    contract = _contract()
    expected = {definition.name for definition in FEATURE_DEFINITIONS}
    assert set(contract["current_feature_set"]) == expected

    for branch, statuses in contract["existing_feature_treatment"].items():
        values = [feature for features in statuses.values() for feature in features]
        assert len(values) == len(set(values)), branch
        assert set(values) == expected, branch


def test_reit_routes_before_financials_and_unresolved_subtypes_are_excluded():
    contract = _contract()
    classification = contract["classification_contract"]
    first_rule = classification["exact_sic_routing"][0]

    assert first_rule["sic_codes"] == ["6798"]
    assert first_rule["branch"] == "REIT_SUBTYPE_UNRESOLVED"
    assert first_rule["eligible"] is False
    assert classification["broad_financial_range_routing_allowed"] is False
    assert classification["append_only_corrections"] is True
    assert (
        classification["unknown_subtype_behavior"]
        .startswith("Price-derived diagnostics may be stored")
    )


def test_contract_matches_frozen_masks_and_sprint9_evidence_counts():
    contract = _contract()
    evidence = contract["evidence"]

    assert evidence["current_financial_mask_component_count"] == len(FINANCIALS_MASK)
    assert evidence["current_reit_mask_component_count"] == len(REIT_MASK)
    assert evidence["eligible_observations"] == 60
    assert evidence["eligible_observations_reit_sic_6798"] == 51
    assert evidence["eligible_observations_insurer_sic_6331"] == 9
    assert evidence["quality_available_evaluated_observations"] == 0
    assert evidence["reit_sic_6798_unique_securities"] == 32


def test_branch_candidates_cover_accounting_families_and_market_only_is_prohibited():
    contract = _contract()
    for branch, families in contract["proposed_branch_features"].items():
        assert set(families) == {"value", "quality", "growth"}, branch
        assert all(families[family] for family in families), branch

    scoring = contract["scoring_contract"]
    assert scoring["market_only_final_score_allowed"] is False
    assert scoring["cross_branch_accounting_normalization_allowed"] is False
    assert scoring["normalization_fallback_to_industrial_universe_allowed"] is False
    assert scoring["minimum_cross_section_per_branch"] >= 20
    assert scoring["minimum_required_accounting_families"] >= 2


def test_evidence_inputs_are_hash_bound_and_references_are_primary_https_sources():
    contract = _contract()
    for source in contract["evidence_inputs"]:
        path = REPOSITORY_ROOT / source["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]

    references = contract["source_references"]
    assert len(references) >= 6
    assert all(row["url"].startswith("https://") for row in references)
    authorities = {row["authority"] for row in references}
    assert "U.S. Securities and Exchange Commission" in authorities
    assert "Federal Deposit Insurance Corporation" in authorities
    assert "National Association of Insurance Commissioners" in authorities
