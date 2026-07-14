from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from quantfore_research.features.model_v2 import ScalarValue
from quantfore_research.features.model_v2_inputs import (
    AccountingFactValue,
    build_formula_inputs_as_of,
    select_fact_values_as_of,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _fact(
    period_end,
    concept,
    value,
    available,
    *,
    revision=1,
    unit="USD",
    record=None,
):
    return AccountingFactValue(
        fiscal_period_end=date.fromisoformat(period_end),
        period_type="QUARTERLY",
        concept=concept,
        unit=unit,
        model_available_at=datetime.fromisoformat(available).replace(tzinfo=timezone.utc),
        revision_version=revision,
        record_id=record or f"{concept}-{period_end}-{revision}",
        value=Decimal(str(value)),
    )


def test_scalar_selection_uses_only_revisions_available_by_prediction_date():
    facts = [
        _fact("2024-03-31", "total_assets", 100, "2024-04-20T10:00:00", revision=1),
        _fact("2024-03-31", "total_assets", 999, "2025-04-20T10:00:00", revision=2),
    ]
    selected = select_fact_values_as_of(facts, date(2024, 12, 31))
    assert len(selected) == 1
    assert selected[0].value == Decimal("100")
    assert selected[0].revision_version == 1


def test_formula_inputs_build_current_and_prior_ttm_with_exact_lineage():
    ends = (
        "2023-03-31",
        "2023-06-30",
        "2023-09-30",
        "2023-12-31",
        "2024-03-31",
        "2024-06-30",
        "2024-09-30",
        "2024-12-31",
    )
    facts = [
        _fact(
            end,
            "revenue",
            index + 1,
            f"{date.fromisoformat(end).year + (end[5:7] == '12')}-01-20T10:00:00",
            record=f"revenue-{index}",
        )
        for index, end in enumerate(ends)
    ]
    inputs = build_formula_inputs_as_of(
        facts,
        date(2025, 1, 31),
        latest_raw_close=ScalarValue(Decimal("10"), "USD", ("price",)),
    )
    assert inputs["current_ttm_revenue"].value == Decimal("26")
    assert inputs["prior_ttm_revenue"].value == Decimal("10")
    assert inputs["current_ttm_revenue"].lineage_ids == (
        "revenue-4",
        "revenue-5",
        "revenue-6",
        "revenue-7",
    )
    assert inputs["latest_raw_close"].lineage_ids == ("price",)


def test_stale_flow_history_is_not_promoted_to_a_formula_input():
    facts = [
        _fact("2022-03-31", "net_income_common", 1, "2022-04-20T10:00:00"),
        _fact("2022-06-30", "net_income_common", 1, "2022-07-20T10:00:00"),
        _fact("2022-09-30", "net_income_common", 1, "2022-10-20T10:00:00"),
        _fact("2022-12-31", "net_income_common", 1, "2023-02-20T10:00:00"),
    ]
    inputs = build_formula_inputs_as_of(facts, date(2025, 1, 31))
    assert "current_ttm_net_income_common" not in inputs


def test_input_preparation_pipeline_never_queries_outcome_tables():
    source = (REPOSITORY_ROOT / "pipelines/build_model_v2_score_inputs.py").read_text(
        encoding="utf-8"
    )
    assert "model_outcomes" not in source
    assert "model_predictions" not in source
    assert '"tables_read": [' in source
