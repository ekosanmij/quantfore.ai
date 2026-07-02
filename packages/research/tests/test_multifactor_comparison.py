from datetime import date
from decimal import Decimal

import pytest

from quantfore_research.evaluation.multifactor_comparison import (
    AttributionComponent,
    MultiModelObservation,
    build_multifactor_comparison,
)


FAMILIES = ("value", "quality", "growth", "momentum", "risk")


def _component(index: int, contribution: Decimal) -> AttributionComponent:
    return AttributionComponent(
        name=f"component_{index}",
        family=FAMILIES[index % len(FAMILIES)],
        contribution=contribution,
        raw_value=Decimal(index),
        directed_value=contribution * Decimal("10"),
        normalization_scope="SECTOR",
        group_label="Technology",
        group_count=12,
        group_mean=Decimal("1.2"),
        group_std=Decimal("0.4"),
        evidence_refs=(f"fundamental-fact:{index}", f"snapshot:{index}"),
    )


def observations():
    rows = []
    for month in range(1, 7):
        for index in range(10):
            signal = Decimal(index)
            family_z = {
                "value": signal,
                "quality": signal * Decimal("0.8"),
                "growth": signal * Decimal("0.6"),
                "momentum": signal * Decimal("0.4"),
                "risk": -signal * Decimal("0.1"),
            }
            excess = Decimal(index - 4) / Decimal("100")
            rows.append(
                MultiModelObservation(
                    security_id=f"security-{index}",
                    ticker=f"T{index}",
                    prediction_date=date(2021, month, 28),
                    sector="Technology" if index < 5 else "Energy",
                    price_score=signal * Decimal("5"),
                    multifactor_score=signal * Decimal("10"),
                    family_z=family_z,
                    family_scores={
                        family: Decimal("50") + value for family, value in family_z.items()
                    },
                    missing_data_flags={"has_missing": index == 0},
                    components=(
                        _component(0, signal / Decimal("100")),
                        _component(1, -signal / Decimal("200")),
                    ),
                    excess_return=excess,
                    realised_return=Decimal("0.02") + excess,
                    benchmark_return=Decimal("0.02"),
                    max_drawdown=-Decimal(index + 1) / Decimal("100"),
                    delisted_outcome=index == 0 and month == 6,
                )
            )
    return tuple(rows)


def test_comparison_uses_one_exact_intersection_and_all_ablations():
    source = observations()
    unaligned = MultiModelObservation(
        **{
            **source[0].__dict__,
            "security_id": "not-in-price-model",
            "ticker": "MISS",
            "price_score": None,
        }
    )
    report = build_multifactor_comparison(source + (unaligned,))

    assert report["claims_eligible"] is False
    assert report["alignment"]["input_observations"] == 61
    assert report["alignment"]["aligned_observations"] == 60
    assert report["alignment"]["excluded_missing_price_score"] == 1
    assert report["models"]["equal_weight_benchmark"]["eligible_observations"] == 60
    assert report["models"]["sprint7_price_only"]["eligible_observations"] == 60
    assert report["models"]["sprint8_multifactor"]["eligible_observations"] == 60
    assert set(report["family_ablations"]) == {
        f"without_{family}" for family in FAMILIES
    }
    for ablation in report["family_ablations"].values():
        assert ablation["design"]["retuned"] is False
        assert ablation["evaluation"]["eligible_observations"] == 60


def test_prediction_attribution_is_complete_and_source_bound():
    report = build_multifactor_comparison(observations())
    prediction = report["prediction_attribution"][1]

    assert set(prediction["family_scores"]) == set(FAMILIES)
    assert prediction["final_score"] is not None
    assert prediction["strongest_positive_component"]["name"] == "component_0"
    assert prediction["strongest_negative_component"]["name"] == "component_1"
    assert prediction["missing_data_flags"] == {"has_missing": False}
    assert len(prediction["sector_normalization_context"]) == 2
    assert prediction["sector_normalization_context"][0]["normalization"] == {
        "scope": "SECTOR",
        "group_label": "Technology",
        "group_count": 12,
        "group_mean": "1.2",
        "group_std": "0.4",
    }
    assert prediction["source_evidence_refs"] == [
        "fundamental-fact:0",
        "fundamental-fact:1",
        "snapshot:0",
        "snapshot:1",
    ]


def test_duplicate_date_security_rows_are_rejected():
    source = observations()
    with pytest.raises(ValueError, match="duplicate"):
        build_multifactor_comparison(source + (source[0],))
