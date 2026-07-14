from datetime import date, datetime, timezone

from pipelines.build_model_v2_accounting_bundle import (
    Observation,
    _duration_kind,
    _issuer_rows,
)
from quantfore_research.validation.accounting_coverage import (
    AccountingFact,
    component_statuses,
    select_accounting_facts_as_of,
)


def _observation(
    *,
    start,
    end,
    value,
    accession,
    accepted,
    kind,
    quarter,
    concept="cash_from_operations",
    source_concept="NetCashProvidedByUsedInOperatingActivities",
    form="10-Q",
):
    accepted_at = datetime.fromisoformat(accepted).replace(tzinfo=timezone.utc)
    return Observation(
        standard_concept=concept,
        source_concept=source_concept,
        concept_priority=0,
        start=date.fromisoformat(start) if start else None,
        end=date.fromisoformat(end),
        duration_kind=kind,
        fiscal_year=date.fromisoformat(end).year,
        quarter_hint=quarter,
        form_type=form,
        filing_accession=accession,
        accepted_at=accepted_at,
        model_available_at=accepted_at.replace(hour=accepted_at.hour + 1),
        value=value,
        unit="USD",
        companyfacts_sha256="a" * 64,
        filing_index_sha256="b" * 64,
    )


def test_duration_classification_preserves_ytd_for_derivation():
    assert (
        _duration_kind({"start": "2024-01-01", "end": "2024-03-31"})
        == "DISCRETE"
    )
    assert (
        _duration_kind({"start": "2024-01-01", "end": "2024-06-30"})
        == "YTD"
    )
    assert (
        _duration_kind({"start": "2024-01-01", "end": "2024-09-30"})
        == "YTD"
    )
    assert (
        _duration_kind({"start": "2024-01-01", "end": "2024-12-31"})
        == "ANNUAL"
    )


def test_ytd_deltas_build_q2_q3_and_q4_with_latest_input_availability():
    observations = [
        _observation(
            start="2024-01-01",
            end="2024-03-31",
            value="100",
            accession="q1",
            accepted="2024-04-20T10:00:00",
            kind="DISCRETE",
            quarter=1,
        ),
        _observation(
            start="2024-01-01",
            end="2024-06-30",
            value="250",
            accession="q2",
            accepted="2024-07-20T10:00:00",
            kind="YTD",
            quarter=2,
        ),
        _observation(
            start="2024-01-01",
            end="2024-09-30",
            value="450",
            accession="q3",
            accepted="2024-10-20T10:00:00",
            kind="YTD",
            quarter=3,
        ),
        _observation(
            start="2024-01-01",
            end="2024-12-31",
            value="700",
            accession="fy",
            accepted="2025-02-20T10:00:00",
            kind="ANNUAL",
            quarter=None,
            form="10-K",
        ),
    ]
    rows = _issuer_rows(observations, __import__("collections").Counter())
    quarterly = {
        row["fiscal_quarter"]: row
        for row in rows
        if row["period_type"] == "QUARTERLY"
    }
    assert quarterly[1]["value"] == "100"
    assert quarterly[2]["value"] == "150"
    assert quarterly[3]["value"] == "200"
    assert quarterly[4]["value"] == "250"
    assert quarterly[2]["derivation_type"] == "YTD_DELTA"
    assert quarterly[4]["derivation_type"] == "ANNUAL_MINUS_YTD"
    assert quarterly[4]["model_available_at"] == "2025-02-20T11:00:00Z"
    assert len(quarterly[4]["_lineage"]["inputs"]) == 2


def test_direct_discrete_fact_takes_precedence_over_derived_collision():
    observations = [
        _observation(
            start="2024-01-01",
            end="2024-03-31",
            value="100",
            accession="q1",
            accepted="2024-04-20T10:00:00",
            kind="DISCRETE",
            quarter=1,
        ),
        _observation(
            start="2024-01-01",
            end="2024-06-30",
            value="250",
            accession="q2",
            accepted="2024-07-20T10:00:00",
            kind="YTD",
            quarter=2,
        ),
        _observation(
            start="2024-04-01",
            end="2024-06-30",
            value="151",
            accession="q2",
            accepted="2024-07-20T10:00:00",
            kind="DISCRETE",
            quarter=2,
        ),
    ]
    rows = _issuer_rows(observations, __import__("collections").Counter())
    q2 = next(
        row
        for row in rows
        if row["period_type"] == "QUARTERLY" and row["fiscal_quarter"] == 2
    )
    assert q2["value"] == "151"
    assert q2["derivation_type"] == "REPORTED"


def _fact(period_end, concept, available, revision=1, unit="USD"):
    return AccountingFact(
        fiscal_period_end=date.fromisoformat(period_end),
        period_type="QUARTERLY",
        concept=concept,
        unit=unit,
        model_available_at=datetime.fromisoformat(available).replace(
            tzinfo=timezone.utc
        ),
        revision_version=revision,
        record_id=f"{concept}-{period_end}-{revision}",
    )


def test_accounting_readiness_never_selects_a_future_revision():
    facts = [
        _fact("2024-03-31", "revenue", "2024-04-20T10:00:00", 1),
        _fact("2024-03-31", "revenue", "2025-04-20T10:00:00", 2),
    ]
    selected = select_accounting_facts_as_of(facts, date(2024, 12, 31))
    assert [fact.revision_version for fact in selected] == [1]


def test_growth_requires_eight_consecutive_point_in_time_quarters():
    ends = [
        "2023-03-31",
        "2023-06-30",
        "2023-09-30",
        "2023-12-31",
        "2024-03-31",
        "2024-06-30",
        "2024-09-30",
        "2024-12-31",
    ]
    facts = [
        _fact(end, "revenue", f"{date.fromisoformat(end).year + (date.fromisoformat(end).month == 12)}-01-20T10:00:00")
        for end in ends
    ]
    statuses = component_statuses(facts, date(2025, 1, 31))
    assert statuses["sales_yield"] is None
    assert statuses["revenue_growth"] is None
    insufficient = component_statuses(facts[-7:], date(2025, 1, 31))
    assert insufficient["sales_yield"] is None
    assert insufficient["revenue_growth"] == "INSUFFICIENT_PRIOR_TTM_HISTORY"
