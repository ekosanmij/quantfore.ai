from datetime import date

import pytest

from pipelines.build_point_in_time_subtype_ledger import (
    DatedEvidence,
    _explicit_classification,
)
from quantfore_research.classification.point_in_time_subtypes import (
    parse_wikipedia_constituent_classifications,
    route_point_in_time_subtype,
)


@pytest.mark.parametrize(
    ("sic", "expected"),
    [
        ("6021", "BANK"),
        ("6099", "BANK"),
        ("6200", "BROKER_DEALER"),
        ("6211", "BROKER_DEALER"),
        ("6282", "ASSET_MANAGER"),
        ("6311", "INSURER_LIFE_HEALTH"),
        ("6324", "INSURER_LIFE_HEALTH"),
        ("6331", "INSURER_P_AND_C"),
        ("6361", "INSURER_P_AND_C"),
    ],
)
def test_exact_sic_floor_routes_specialist_financial_branches(sic, expected):
    route = route_point_in_time_subtype(sector="Financials", sic=sic)
    assert route.sector_branch == expected
    assert route.known_subtype is True
    assert route.classification_eligible is True


def test_sic_6798_requires_explicit_dated_reit_subtype():
    unresolved = route_point_in_time_subtype(sector="Financials", sic="6798")
    assert unresolved.sector_branch == "UNKNOWN"
    assert unresolved.reason_codes == ("REIT_SUBTYPE_UNKNOWN",)
    assert unresolved.classification_eligible is False

    equity = route_point_in_time_subtype(
        sector="Financials",
        sic="6798",
        explicit_sector="Real Estate",
        explicit_subindustry="Industrial REITs",
    )
    mortgage = route_point_in_time_subtype(
        sector="Financials",
        sic="6798",
        explicit_sector="Real Estate",
        explicit_subindustry="Mortgage REITs",
    )
    assert equity.sector_branch == "EQUITY_REIT"
    assert mortgage.sector_branch == "MORTGAGE_REIT"


def test_explicit_asset_manager_is_not_misread_as_a_custody_bank():
    route = route_point_in_time_subtype(
        sector="Financials",
        sic=None,
        explicit_sector="Financials",
        explicit_subindustry="Asset Management & Custody Banks",
    )
    assert route.sector_branch == "ASSET_MANAGER"


@pytest.mark.parametrize(
    ("subindustry", "expected"),
    [
        ("Life & Health Insurance", "INSURER_LIFE_HEALTH"),
        ("Property & Casualty Insurance", "INSURER_P_AND_C"),
        ("Reinsurance", "INSURER_P_AND_C"),
        ("Investment Banking & Brokerage", "BROKER_DEALER"),
        ("Insurance Brokers", "OTHER_FINANCIAL"),
    ],
)
def test_explicit_financial_subindustries_are_not_generic(subindustry, expected):
    route = route_point_in_time_subtype(
        sector="Unknown",
        sic=None,
        explicit_sector="Financials",
        explicit_subindustry=subindustry,
    )
    assert route.sector_branch == expected
    assert route.known_subtype is True


def test_other_financial_is_known_but_not_an_active_scoring_branch():
    route = route_point_in_time_subtype(sector="Financials", sic="6411")
    assert route.sector_branch == "OTHER_FINANCIAL"
    assert route.known_subtype is True
    assert route.classification_eligible is False
    assert route.reason_codes == ("SECTOR_BRANCH_EXCLUDED",)


def test_unknown_and_conflicting_subtypes_are_explicitly_excluded():
    missing = route_point_in_time_subtype(sector="Unknown", sic=None)
    conflict = route_point_in_time_subtype(
        sector="Financials", sic="6021", conflict=True
    )
    assert missing.known_subtype is False
    assert missing.classification_eligible is False
    assert missing.reason_codes == ("CLASSIFICATION_SOURCE_UNAVAILABLE",)
    assert conflict.known_subtype is False
    assert conflict.classification_eligible is False
    assert conflict.reason_codes == ("CLASSIFICATION_CONFLICT",)


def _evidence(as_of: date, sector: str, subindustry: str) -> DatedEvidence:
    return DatedEvidence(
        as_of_date=as_of,
        revision_id=int(as_of.strftime("%Y%m%d")),
        registry_path="registry.json",
        registry_sha256="a" * 64,
        response_path="revision.json",
        response_sha256="b" * 64,
        ticker="TEST",
        cik="0000000001",
        sector=sector,
        subindustry=subindustry,
        matched_by="CIK",
    )


def test_later_explicit_evidence_cannot_classify_an_earlier_prediction_date():
    values = [
        _evidence(date(2018, 1, 1), "Financials", "Regional Banks"),
        _evidence(date(2020, 1, 1), "Real Estate", "Industrial REITs"),
    ]
    before, conflict = _explicit_classification(values, date(2017, 12, 31))
    during, _ = _explicit_classification(values, date(2019, 12, 31))
    after, _ = _explicit_classification(values, date(2020, 1, 31))
    assert before is None and conflict is False
    assert during is not None and during.subindustry == "Regional Banks"
    assert after is not None and after.subindustry == "Industrial REITs"


def test_same_date_explicit_disagreement_is_a_conflict():
    values = [
        _evidence(date(2018, 1, 1), "Financials", "Regional Banks"),
        _evidence(date(2018, 1, 1), "Financials", "Life Insurance"),
    ]
    _, conflict = _explicit_classification(values, date(2018, 1, 31))
    assert conflict is True


def test_parser_supports_old_and_new_constituent_table_layouts():
    old_rows = "\n|-\n".join(
        f"| {{{{NyseSymbol|T{i}}}}} || Company || reports || Financials || "
        f"Regional Banks || Place || 2010-01-01 || {i + 100000:010d}"
        for i in range(450)
    )
    old = (
        "== Components ==\n{| class=\"wikitable\"\n! Symbol !! Security !! "
        "SEC !! GICS Sector !! GICS Sub-Industry !! HQ !! Added !! CIK\n|-\n"
        + old_rows
        + "\n|}"
    )
    new_rows = "\n|-\n".join(
        f"|{{{{NasdaqSymbol|N{i}}}}}\n|Company || Industrials || Building Products "
        f"|| Place || 2010-01-01 || {i + 200000:010d}"
        for i in range(450)
    )
    new = (
        "== Components ==\n{| class=\"wikitable\"\n! Symbol !! Security !! "
        "GICS Sector !! GICS Sub-Industry !! HQ !! Added !! CIK\n|-\n"
        + new_rows
        + "\n|}"
    )
    old_evidence = parse_wikipedia_constituent_classifications(old)
    new_evidence = parse_wikipedia_constituent_classifications(new)
    assert old_evidence[0].sector == "Financials"
    assert old_evidence[0].subindustry == "Regional Banks"
    assert new_evidence[0].sector == "Industrials"
    assert new_evidence[0].subindustry == "Building Products"
