import gzip
import json
from pathlib import Path

from pipelines.build_model_v2_scores import build_scores, input_row_to_batch


def _input_row(index):
    def scalar(value, unit="USD"):
        return {"value": str(value), "unit": unit, "lineage_ids": [f"fact-{index}"]}

    return {
        "security_id": f"security-{index:02d}",
        "prediction_date": "2025-06-30",
        "sector_branch": "BANK",
        "classification_eligible": True,
        "classification_reason_codes": [],
        "classification_id": f"classification-{index}",
        "accounting_inputs": {
            "market_cap": scalar(1_000 + index * 10),
            "current_ttm_net_income_common": scalar(50 + index),
            "current_total_assets": scalar(1_100 + index * 10),
            "prior_total_assets": scalar(900 + index * 10),
            "current_shareholders_equity": scalar(220 + index * 2),
            "prior_shareholders_equity": scalar(180 + index * 2),
            "current_loans_and_leases_net": scalar(600 + index * 5),
            "prior_loans_and_leases_net": scalar(500 + index * 4),
            "current_customer_deposits": scalar(800 + index * 5),
            "prior_customer_deposits": scalar(700 + index * 4),
            "current_ttm_diluted_eps": scalar(5 + index / 10, "USD/shares"),
            "prior_ttm_diluted_eps": scalar(4 + index / 10, "USD/shares"),
        },
        "universal_features": {
            "momentum_6_1": scalar(index / 100, "ratio"),
            "momentum_12_1": scalar(index / 80, "ratio"),
            "volatility_126d": scalar(0.3 + index / 1000, "ratio"),
            "beta_252d": scalar(0.8 + index / 100, "ratio"),
            "downside_volatility_126d": scalar(0.2 + index / 1000, "ratio"),
            "maximum_drawdown_252d": scalar(-0.4 + index / 1000, "ratio"),
        },
    }


def test_pipeline_writes_deterministic_scored_ledger_and_invariant_manifest(tmp_path):
    input_path = tmp_path / "inputs.jsonl"
    output_path = tmp_path / "scores.jsonl.gz"
    manifest_path = tmp_path / "manifest.json"
    input_path.write_text(
        "".join(json.dumps(_input_row(index), sort_keys=True) + "\n" for index in range(20)),
        encoding="utf-8",
    )

    manifest = build_scores(
        input_path=input_path,
        output_path=output_path,
        manifest_path=manifest_path,
        minimum_branch_cross_section=20,
    )
    with gzip.open(output_path, "rt", encoding="utf-8") as handle:
        rows = [json.loads(line) for line in handle]

    assert len(rows) == 20
    assert all(row["eligible"] for row in rows)
    assert all(all(row["family_available"].values()) for row in rows)
    assert all(set(row["family_weights"].values()) == {"0.20"} for row in rows)
    assert manifest["outcomes_accessed"] is False
    assert manifest["family_weight_renormalization"] is False
    assert manifest["counts"]["eligible_rows_missing_any_family"] == 0
    assert manifest["counts"]["cross_branch_fallback_count"] == 0
    assert manifest_path.is_file()


def test_scoring_input_rejects_outcome_fields_before_building_features():
    row = _input_row(0)
    row["forward_return"] = "0.25"
    try:
        input_row_to_batch(row)
    except ValueError as exc:
        assert "outcome field is prohibited" in str(exc)
    else:
        raise AssertionError("outcome-bearing input was accepted")
