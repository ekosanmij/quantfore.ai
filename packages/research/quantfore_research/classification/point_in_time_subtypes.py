"""Outcome-blind, point-in-time routing for Model V2 sector branches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


CLASSIFICATION_VERSION = "sec-sic-financial-subtype-v2"
CLASSIFICATION_SYSTEM = "SEC_SIC_AND_REVISION_PINNED_GICS_V1"

ACTIVE_BRANCHES = frozenset(
    {
        "INDUSTRIAL_GENERAL",
        "BANK",
        "INSURER_P_AND_C",
        "INSURER_LIFE_HEALTH",
        "BROKER_DEALER",
        "ASSET_MANAGER",
        "EQUITY_REIT",
        "MORTGAGE_REIT",
    }
)
GICS_SECTORS = frozenset(
    {
        "Communication Services",
        "Consumer Discretionary",
        "Consumer Staples",
        "Energy",
        "Financials",
        "Health Care",
        "Industrials",
        "Information Technology",
        "Materials",
        "Real Estate",
        "Utilities",
    }
)

_TICKER_TEMPLATE = re.compile(
    r"\{\{(?:NyseSymbol|NasdaqSymbol|NYSE|NASDAQ|BZX link)\|([^}|]+)", re.I
)
_CIK_CELL = re.compile(r"^0*([0-9]{6,10})$")
_TAG = re.compile(r"<[^>]+>")
_WIKILINK = re.compile(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]")


@dataclass(frozen=True)
class WikipediaClassificationEvidence:
    """Explicit GICS label preserved in a revision-pinned Wikipedia snapshot."""

    ticker: str
    cik: Optional[str]
    sector: str
    subindustry: str


@dataclass(frozen=True)
class SubtypeRoute:
    """One deterministic branch decision, including explicit exclusion state."""

    sector_branch: str
    subtype: str
    known_subtype: bool
    classification_eligible: bool
    routing_rule: str
    reason_codes: tuple[str, ...] = ()


def _plain_cell(value: str) -> str:
    value = _TAG.sub("", value)
    value = _WIKILINK.sub(r"\1", value)
    value = value.replace("&amp;", "&").replace("&nbsp;", " ")
    return " ".join(value.replace("\t", " ").split())


def _ticker(value: str) -> str:
    return value.strip().upper().replace(".", "-")


def _constituent_table(wikitext: str) -> str:
    matches = list(_TICKER_TEMPLATE.finditer(wikitext))
    if not matches:
        raise ValueError("Wikipedia revision lacks a supported constituent ticker")
    start = wikitext.rfind("{|", 0, matches[0].start())
    if start < 0:
        raise ValueError("Wikipedia revision lacks a constituent table")
    end = wikitext.find("\n|}", matches[0].start())
    if end < 0:
        raise ValueError("Wikipedia constituent table is unterminated")
    return wikitext[start:end]


def _row_cells(row: str) -> list[str]:
    # Older revisions keep the whole row on one line; newer ones put the ticker
    # and company cells on separate lines. Both use ``||`` for later columns.
    cells: list[str] = []
    for line in row.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|-") or stripped.startswith("!"):
            continue
        if stripped.startswith("|"):
            stripped = stripped[1:]
        cells.extend(part.strip() for part in stripped.split("||"))
    return cells


def parse_wikipedia_constituent_classifications(
    wikitext: str,
) -> tuple[WikipediaClassificationEvidence, ...]:
    """Extract dated ticker/CIK/GICS evidence without consulting present-day data."""

    table = _constituent_table(wikitext)
    evidence: list[WikipediaClassificationEvidence] = []
    for row in re.split(r"\n\|-\s*\n", table)[1:]:
        ticker_match = _TICKER_TEMPLATE.search(row)
        if ticker_match is None:
            continue
        cells = _row_cells(row)
        plain = [_plain_cell(cell) for cell in cells]
        sector_index = next(
            (index for index, cell in enumerate(plain) if cell in GICS_SECTORS),
            None,
        )
        if sector_index is None or sector_index + 1 >= len(plain):
            continue
        cik = None
        for cell in plain:
            match = _CIK_CELL.fullmatch(cell)
            if match:
                cik = match.group(1).zfill(10)
                break
        evidence.append(
            WikipediaClassificationEvidence(
                ticker=_ticker(ticker_match.group(1)),
                cik=cik,
                sector=plain[sector_index],
                subindustry=plain[sector_index + 1],
            )
        )
    if not 450 <= len(evidence) <= 550:
        raise ValueError("Wikipedia constituent classification count is implausible")
    return tuple(evidence)


def _route(
    branch: str,
    *,
    rule: str,
    subtype: Optional[str] = None,
    known: bool = True,
    eligible: Optional[bool] = None,
    reasons: tuple[str, ...] = (),
) -> SubtypeRoute:
    return SubtypeRoute(
        sector_branch=branch,
        subtype=subtype or branch,
        known_subtype=known,
        classification_eligible=branch in ACTIVE_BRANCHES if eligible is None else eligible,
        routing_rule=rule,
        reason_codes=reasons,
    )


def _explicit_route(sector: str, subindustry: str) -> SubtypeRoute:
    normalized = subindustry.upper().replace("&", "AND")
    if sector == "Real Estate":
        if "MORTGAGE" in normalized and "REIT" in normalized:
            return _route("MORTGAGE_REIT", rule="POINT_IN_TIME_EXPLICIT_GICS")
        if "REIT" in normalized:
            return _route("EQUITY_REIT", rule="POINT_IN_TIME_EXPLICIT_GICS")
        return _route("INDUSTRIAL_GENERAL", rule="POINT_IN_TIME_EXPLICIT_GICS")
    if sector != "Financials":
        return _route("INDUSTRIAL_GENERAL", rule="POINT_IN_TIME_EXPLICIT_GICS")

    if "ASSET MANAGEMENT" in normalized or "ASSET MANAGER" in normalized:
        return _route("ASSET_MANAGER", rule="POINT_IN_TIME_EXPLICIT_GICS")
    if "INVESTMENT BANKING" in normalized or "BROKERAGE" in normalized:
        return _route("BROKER_DEALER", rule="POINT_IN_TIME_EXPLICIT_GICS")
    if "BANK" in normalized:
        return _route("BANK", rule="POINT_IN_TIME_EXPLICIT_GICS")
    if "LIFE" in normalized or "HEALTH INSURANCE" in normalized:
        return _route("INSURER_LIFE_HEALTH", rule="POINT_IN_TIME_EXPLICIT_GICS")
    if any(
        label in normalized
        for label in (
            "PROPERTY AND CASUALTY",
            "PROPERTY & CASUALTY",
            "REINSURANCE",
            "MULTI-LINE INSURANCE",
        )
    ):
        return _route("INSURER_P_AND_C", rule="POINT_IN_TIME_EXPLICIT_GICS")
    return _route(
        "OTHER_FINANCIAL",
        rule="POINT_IN_TIME_EXPLICIT_GICS",
        eligible=False,
        reasons=("SECTOR_BRANCH_EXCLUDED",),
    )


def route_point_in_time_subtype(
    *,
    sector: Optional[str],
    sic: Optional[str],
    explicit_sector: Optional[str] = None,
    explicit_subindustry: Optional[str] = None,
    conflict: bool = False,
) -> SubtypeRoute:
    """Route one as-of classification using explicit evidence before exact SIC.

    A caller must supply only explicit evidence timestamped on or before the model
    date. The function intentionally has no outcome, return, or present-day inputs.
    """

    if conflict:
        return _route(
            "UNKNOWN",
            subtype="CLASSIFICATION_CONFLICT",
            rule="POINT_IN_TIME_CONFLICT",
            known=False,
            eligible=False,
            reasons=("CLASSIFICATION_CONFLICT",),
        )
    if explicit_sector and explicit_subindustry:
        return _explicit_route(explicit_sector.strip(), explicit_subindustry.strip())

    sic_value = sic.strip() if sic else ""
    sic_number = int(sic_value) if sic_value.isdigit() else None
    if sic_value == "6798":
        return _route(
            "UNKNOWN",
            subtype="REIT_SUBTYPE_UNRESOLVED",
            rule="POINT_IN_TIME_EXACT_SIC_6798",
            known=False,
            eligible=False,
            reasons=("REIT_SUBTYPE_UNKNOWN",),
        )
    if sic_number is not None and 6020 <= sic_number <= 6099:
        return _route("BANK", rule="POINT_IN_TIME_EXACT_SIC")
    exact_routes = {
        "6200": "BROKER_DEALER",
        "6211": "BROKER_DEALER",
        "6282": "ASSET_MANAGER",
        "6311": "INSURER_LIFE_HEALTH",
        "6321": "INSURER_LIFE_HEALTH",
        "6324": "INSURER_LIFE_HEALTH",
        "6331": "INSURER_P_AND_C",
        "6351": "INSURER_P_AND_C",
        "6361": "INSURER_P_AND_C",
    }
    if sic_value in exact_routes:
        return _route(exact_routes[sic_value], rule="POINT_IN_TIME_EXACT_SIC")
    if sic_value in {"6199", "6399", "6411", "6792", "6799"}:
        return _route(
            "OTHER_FINANCIAL",
            rule="POINT_IN_TIME_EXACT_SIC_OTHER_FINANCIAL",
            eligible=False,
            reasons=("SECTOR_BRANCH_EXCLUDED",),
        )

    normalized_sector = (sector or "").strip()
    if normalized_sector and normalized_sector not in {"Financials", "Unknown"}:
        return _route("INDUSTRIAL_GENERAL", rule="POINT_IN_TIME_BROAD_SECTOR_LABEL")
    if normalized_sector == "Financials":
        return _route(
            "UNKNOWN",
            subtype="FINANCIAL_SUBTYPE_UNRESOLVED",
            rule="POINT_IN_TIME_BROAD_SECTOR_LABEL",
            known=False,
            eligible=False,
            reasons=("FINANCIAL_SUBTYPE_UNKNOWN",),
        )
    return _route(
        "UNKNOWN",
        subtype="CLASSIFICATION_SOURCE_UNAVAILABLE",
        rule="NO_POINT_IN_TIME_CLASSIFICATION",
        known=False,
        eligible=False,
        reasons=("CLASSIFICATION_SOURCE_UNAVAILABLE",),
    )
