import hashlib
import json
from datetime import date
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = (
    REPOSITORY_ROOT / "experiments" / "multifactor-v2-hypothesis-contract.md"
)
LOCK_PATH = (
    REPOSITORY_ROOT / "experiments" / "multifactor-v2-hypothesis-lock-v1.json"
)


def _lock() -> dict:
    return json.loads(LOCK_PATH.read_text(encoding="utf-8"))


def _conditions(gate: dict) -> dict[str, dict]:
    rows = gate.get("conditions", [gate])
    return {row["metric"]: row for row in rows}


def test_design_lock_is_hash_bound_but_deliberately_not_executable():
    lock = _lock()

    assert lock["status"] == "DESIGN_LOCK_PRE_IMPLEMENTATION"
    assert lock["claims_eligible"] is False
    assert lock["executable_for_shadow_predictions"] is False
    assert lock["executable_for_outcome_evaluation"] is False
    assert lock["activation_requirements"]["executable_lock_required"] is True
    assert lock["activation_requirements"]["design_lock_must_remain_immutable"] is True
    assert all(value is None for value in lock["implementation_placeholders"].values())

    expected_hash = hashlib.sha256(CONTRACT_PATH.read_bytes()).hexdigest()
    assert lock["contract"]["path"] == str(CONTRACT_PATH.relative_to(REPOSITORY_ROOT))
    assert lock["contract"]["sha256"] == expected_hash


def test_every_evidence_input_exists_and_matches_its_frozen_hash():
    lock = _lock()

    assert len(lock["evidence_inputs"]) == 8
    for source in lock["evidence_inputs"]:
        path = REPOSITORY_ROOT / source["path"]
        assert path.is_file(), source["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]


def test_v2_keeps_all_five_families_at_fixed_equal_weight_without_fallback():
    model = _lock()["model"]

    assert set(model["factor_families"]) == {
        "value",
        "quality",
        "growth",
        "momentum",
        "risk",
    }
    assert set(model["family_weights"]) == set(model["factor_families"])
    assert set(model["family_weights"].values()) == {0.2}
    assert sum(model["family_weights"].values()) == 1.0
    assert model["all_five_families_required"] is True
    assert model["missing_family_weight_renormalization"] is False
    assert model["cross_branch_fallback"] is False
    assert model["minimum_component_coverage"] == 0.8
    assert model["minimum_required_components_per_family"] == 0.6
    assert model["minimum_branch_cross_section"] == 20


def test_exposed_history_cannot_promote_and_forward_window_has_24_cohorts():
    protocol = _lock()["evaluation_protocol"]
    historical = protocol["historical_exposed_diagnostic_window"]
    forward = protocol["forward_shadow_window"]

    assert historical == {
        "start": "2017-01-01",
        "end": "2025-06-30",
        "claims_eligible": False,
        "allowed_use": "engineering_and_sensitivity_diagnostics_only",
    }
    assert forward["start"] == "2026-07-31"
    assert forward["end"] == "2028-06-30"
    assert forward["scheduled_monthly_cohorts"] == 24
    start = date.fromisoformat(forward["start"])
    end = date.fromisoformat(forward["end"])
    inclusive_months = (end.year - start.year) * 12 + end.month - start.month + 1
    assert inclusive_months == forward["scheduled_monthly_cohorts"]
    assert forward["claims_eligible_before_full_maturity"] is False
    assert forward["primary_evaluation_condition"] == "ALL_24_126D_OUTCOMES_MATURE"
    assert protocol["aggregate_shadow_results_blinded_until_primary_evaluation"] is True
    assert protocol["prediction_artifact_must_precede_outcome_availability"] is True
    assert protocol["primary_horizon"] == "126d"
    assert protocol["hac_lag_months"] == 5


def test_engineering_gates_lock_coverage_breadth_lineage_and_capacity():
    gates = _lock()["engineering_gates"]

    assert list(gates) == [f"E{number}" for number in range(1, 11)]
    assert gates["E1"]["threshold"] == 0
    assert gates["E3"]["threshold"] == 1.0
    assert gates["E4"]["threshold"] == 0.98
    assert gates["E5"]["threshold"] == 0.9
    assert gates["E6"]["threshold"] == 0.8
    e7 = _conditions(gates["E7"])
    assert e7["eligible_names_per_active_branch_every_month"]["threshold"] == 20
    assert e7["represented_active_branches_every_month"]["threshold"] == 5
    assert e7["represented_gics_sectors_every_month"]["threshold"] == 5
    assert gates["E8"]["threshold"] == 0
    assert gates["E9"]["threshold"] is True
    e10 = _conditions(gates["E10"])
    assert (
        e10["selected_holdings_with_complete_trailing_20_session_liquidity_record_fraction"][
            "threshold"
        ]
        == 1.0
    )
    assert (
        e10["modeled_order_fraction_of_trailing_20_session_median_dollar_volume"][
            "threshold"
        ]
        == 0.01
    )


def test_promotion_gates_lock_signal_portfolio_and_concentration_thresholds():
    gates = _lock()["promotion_gates"]

    assert list(gates) == [f"M{number}" for number in range(1, 12)]
    m1 = _conditions(gates["M1"])
    assert m1["scheduled_forward_cohorts"]["threshold"] == 24
    assert m1["calculable_monthly_branch_neutral_rank_ic_values"]["threshold"] == 24
    assert m1["evaluated_stock_months"]["threshold"] == 10_000
    assert gates["M2"]["threshold"] == 0.03
    assert gates["M3"]["threshold"] == 2.0
    assert gates["M4"]["threshold"] == 0.01

    m5 = _conditions(gates["M5"])
    assert m5["equal_weight_within_branch_top_minus_bottom_after_25bps"]["operator"] == "gt"
    assert m5["equal_weight_within_branch_top_minus_bottom_after_50bps"]["operator"] == "gte"

    m6 = _conditions(gates["M6"])
    assert m6["long_only_six_sleeve_excess_return_vs_spy_after_25bps"]["operator"] == "gt"
    assert (
        m6[
            "long_only_six_sleeve_excess_return_vs_eligible_equal_weight_after_25bps"
        ]["operator"]
        == "gt"
    )
    assert m6["benchmark_hit_rate"]["threshold"] == 0.5

    assert gates["M8"]["threshold"] == 0.5
    m9 = _conditions(gates["M9"])
    assert m9["selected_basket_name_count_every_rebalance"]["threshold"] == 20
    assert m9["maximum_single_name_weight_every_rebalance"]["threshold"] == 0.05
    assert m9["maximum_sector_weight_every_rebalance"]["threshold"] == 0.35
    m10 = _conditions(gates["M10"])
    assert m10["six_sleeve_downside_capture"]["operator"] == "lt"
    assert m10["max_drawdown_shortfall_vs_eligible_equal_weight_percentage_points"][
        "threshold"
    ] == 5.0
    m11 = _conditions(gates["M11"])
    assert m11["final_scored_rows_with_all_five_families_available_fraction"][
        "threshold"
    ] == 0.9
    assert m11["family_ablation_used_as_promotion_substitute"]["threshold"] is False


def test_contract_prohibits_return_tuning_and_early_forward_unblinding():
    lock = _lock()

    prohibited = set(lock["prohibited_changes"])
    assert "outcome_driven_feature_selection" in prohibited
    assert "outcome_driven_weight_selection" in prohibited
    assert "retroactive_sprint8_baseline_mutation" in prohibited
    assert "unblinding_aggregate_forward_results_before_primary_evaluation" in prohibited
    controls = set(lock["anti_overfitting_controls"])
    assert "no_outcome_based_feature_or_weight_selection" in controls
    assert "aggregate_forward_results_blinded_until_full_primary_maturity" in controls
    assert "prediction_artifacts_hash_locked_before_outcomes_are_available" in controls


def test_human_contract_contains_every_required_decision_section():
    contract = CONTRACT_PATH.read_text(encoding="utf-8")

    required_headings = (
        "## What Sprint 8 taught us",
        "## Primary hypothesis",
        "## What may change",
        "## What may not change",
        "## Data and evaluation windows",
        "### Walk-forward protocol",
        "## Engineering gates",
        "## Model and portfolio promotion gates",
        "## Anti-overfitting rules",
        "## Claims boundary",
    )
    assert all(heading in contract for heading in required_headings)
    assert "Do not tune it to improve the Sprint 8 return result" in contract
    assert "claims_eligible=false" in contract
