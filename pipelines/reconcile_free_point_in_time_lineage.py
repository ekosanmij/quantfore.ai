"""Reconcile historical identities and usable price aliases from frozen evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LINEAGE = REPO_ROOT / "data/raw/free-point-in-time/lineage-evidence-v1/registry.json"
DEFAULT_WIKIDATA = REPO_ROOT / "data/raw/free-point-in-time/wikidata-lineage-v1/registry.json"
DEFAULT_IDENTIFIERS = REPO_ROOT / "data/raw/free-point-in-time/resolved-identifiers-v1.json"
DEFAULT_PRICE_ROOTS = (
    REPO_ROOT / "data/raw/free-point-in-time/tiingo-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v1",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v2",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v3",
    REPO_ROOT / "data/raw/free-point-in-time/lineage-alias-prices-v4",
)
DEFAULT_SEC_ROOTS = (
    REPO_ROOT / "data/raw/free-point-in-time/sec-pit-v1",
    REPO_ROOT / "data/raw/free-point-in-time/sec-lineage-v1",
)
DEFAULT_OUTPUT = REPO_ROOT / "data/raw/free-point-in-time/reconciled-lineage-v1.json"
US_EXCHANGES = frozenset(
    {"New York Stock Exchange", "Nasdaq", "NYSE American", "NYSE Arca", "OTC Markets Group"}
)
SEC_IDENTITY_OVERRIDES = {
    "ADS": ("0001101215", "ALLIANCE DATA SYSTEMS", ("BFH",)),
    "BK": ("0001390777", "BANK OF NEW YORK MELLON", ("BNY",)),
    "ANTM": ("0001156039", "ANTHEM", ("ELV",)),
    "ARNC": ("0000004281", "ARCONIC", ("HWM",)),
    "CTL": ("0000018926", "CENTURYLINK", ("LUMN",)),
    "CBS": ("0000813828", "CBS CORP", ("PARA",)),
    "DISCA": ("0001437107", "DISCOVERY", ("WBD",)),
    "FBHS": ("0001519751", "FORTUNE BRANDS HOME & SECURITY", ("FBIN",)),
    "GPS": ("0000039911", "GAP INC", ("GAP",)),
    "HCP": ("0000765880", "HCP", ("DOC",)),
    "HRS": ("0000202058", "HARRIS", ("LHX",)),
    "JEC": ("0000052988", "JACOBS ENGINEERING", ("J",)),
    "KORS": ("0001530721", "MICHAEL KORS", ("CPRI",)),
    "LB": ("0000701985", "L BRANDS", ("BBWI",)),
    "MMC": ("0000062709", "MARSH & MCLENNAN", ("MRSH",)),
    "PKI": ("0000031791", "PERKINELMER", ("RVTY",)),
    "RE": ("0001095073", "EVEREST RE GROUP", ("EG",)),
    "SYMC": ("0000849399", "SYMANTEC", ("GEN",)),
    "TMK": ("0000320335", "TORCHMARK", ("GL",)),
    "UTX": ("0000101829", "UNITED TECHNOLOGIES", ("RTX",)),
    "VIAC": ("0000813828", "VIACOMCBS", ("PARA",)),
    "WLTW": ("0001140536", "WILLIS TOWERS WATSON", ("WTW",)),
    "BTUUQ": ("0001064728", "PEABODY ENERGY", ("BTU",)),
    "CCE": ("0001491675", "COCA-COLA ENTERPRISES", ()),
    "CVC": ("0001053112", "CABLEVISION SYSTEMS", ()),
    "CXO": ("0001358071", "CONCHO RESOURCES", ()),
    "DTV": ("0001465112", "DIRECTV", ()),
    "DWDP": ("0001666700", "DOWDUPONT", ("DD",)),
    "DXC": ("0000023082", "COMPUTER SCIENCES", ("CSC",)),
    "ESV": ("0000314808", "ENSCO", ("VAL",)),
    "FTR": ("0000020520", "FRONTIER COMMUNICATIONS", ()),
    "HAR": ("0000800459", "HARMAN INTERNATIONAL", ()),
    "HFC": ("0000048039", "HOLLYFRONTIER", ()),
    "IGT": ("0000353944", "INTERNATIONAL GAME TECHNOLOGY", ()),
    "SCG": ("0000754737", "SCANA", ()),
    "TSS": ("0000721683", "TOTAL SYSTEM SERVICES", ()),
    "WYND": ("0001361658", "WYNDHAM WORLDWIDE", ("TNL",)),
    "XL": ("0000875159", "XL GROUP", ()),
}
SEC_TRANSITION_OVERRIDES = {
    "FOX": (
        ("0001308161", "TWENTY-FIRST CENTURY FOX", "0001-01-01", "2019-03-18"),
        ("0001754301", "FOX CORP", "2019-03-19", None),
    ),
    "FOXA": (
        ("0001308161", "TWENTY-FIRST CENTURY FOX", "0001-01-01", "2019-03-18"),
        ("0001754301", "FOX CORP", "2019-03-19", None),
    ),
    "IR": (
        ("0001466258", "INGERSOLL-RAND", "0001-01-01", "2020-02-28"),
        ("0001699150", "INGERSOLL RAND", "2020-03-02", None),
    ),
}


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def _date(value: Optional[str]) -> Optional[date]:
    if not value or not value[:4].isdigit():
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _overlaps(
    start: Optional[str], end: Optional[str], episode_start: date, episode_end: date
) -> bool:
    parsed_start = _date(start)
    parsed_end = _date(end)
    return (parsed_start is None or parsed_start <= episode_end) and (
        parsed_end is None or parsed_end >= episode_start
    )


def _contiguous_alias(
    target_start: Optional[str],
    target_end: Optional[str],
    alias_start: Optional[str],
    alias_end: Optional[str],
    *,
    maximum_gap_days: int = 366,
) -> bool:
    pairs = ((_date(target_end), _date(alias_start)), (_date(alias_end), _date(target_start)))
    return any(
        left is not None
        and right is not None
        and 0 <= (right - left).days <= maximum_gap_days
        for left, right in pairs
    )


def _covers_episode(
    prices: Sequence[Mapping[str, Any]],
    episode_start: date,
    episode_end: date,
    *,
    tolerance_days: int,
) -> bool:
    intervals = sorted(
        (
            max(episode_start, date.fromisoformat(str(row["first_price_date"]))),
            min(episode_end, date.fromisoformat(str(row["last_price_date"]))),
        )
        for row in prices
        if date.fromisoformat(str(row["first_price_date"])) <= episode_end
        and date.fromisoformat(str(row["last_price_date"])) >= episode_start
    )
    if not intervals or (intervals[0][0] - episode_start).days > tolerance_days:
        return False
    covered_end = intervals[0][1]
    for interval_start, interval_end in intervals[1:]:
        if (interval_start - covered_end).days > tolerance_days:
            return False
        covered_end = max(covered_end, interval_end)
    return (episode_end - covered_end).days <= tolerance_days


def _price_records(price_roots: Sequence[Path]) -> dict[str, list[dict[str, Any]]]:
    records: dict[str, list[dict[str, Any]]] = {}
    for root in price_roots:
        for path in sorted(root.glob("batch-*/*/complete.json")):
            row = json.loads(path.read_text())
            value = {
                "ticker": str(row["ticker"]),
                "first_price_date": row["first_price_date"],
                "last_price_date": row["last_price_date"],
                "price_row_count": row["price_row_count"],
                "completion_path": str(path.resolve()),
                "completion_sha256": _sha256(path.read_bytes()),
            }
            records.setdefault(value["ticker"], []).append(value)
    return records


def _sec_submission_evidence(sec_roots: Sequence[Path]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for root in sec_roots:
        for completion_path in sorted(root.glob("CIK*/complete.json")):
            completion = json.loads(completion_path.read_text())
            source = completion.get("submissions")
            if not source:
                continue
            raw_path = completion_path.parent / source["path"]
            body = raw_path.read_bytes()
            if _sha256(body) != source["sha256"]:
                raise ValueError("SEC submission evidence does not reproduce")
            document = json.loads(body)
            names = sorted(
                {
                    str(document.get("name") or "").upper(),
                    *[
                        str(row.get("name") or "").upper()
                        for row in document.get("formerNames", [])
                    ],
                }
                - {""}
            )
            evidence[str(completion["cik"])] = {
                "names": names,
                "path": str(raw_path.resolve()),
                "sha256": source["sha256"],
            }
    return evidence


def _require_sec_identity(
    sec_evidence: Mapping[str, Mapping[str, Any]],
    *,
    ticker: str,
    cik: str,
    required_name: str,
) -> Mapping[str, Any]:
    evidence = sec_evidence.get(cik)
    if evidence is None or not any(required_name in name for name in evidence["names"]):
        raise ValueError(f"SEC identity override does not reproduce for {ticker}")
    return evidence


def reconcile_lineage(
    *,
    lineage_body: bytes,
    wikidata_body: bytes,
    identifier_body: bytes,
    price_roots: Sequence[Path],
    sec_roots: Sequence[Path] = DEFAULT_SEC_ROOTS,
    tolerance_days: int = 7,
) -> dict[str, Any]:
    lineage = json.loads(lineage_body)
    wikidata = json.loads(wikidata_body)
    identifiers = json.loads(identifier_body)
    prices = _price_records(price_roots)
    sec_evidence = _sec_submission_evidence(sec_roots)
    identifier_by_ticker = {row["ticker"]: row for row in identifiers["mappings"]}
    tickers_by_figi: dict[str, set[str]] = {}
    for row in identifiers["mappings"]:
        if row.get("share_class_figi"):
            tickers_by_figi.setdefault(str(row["share_class_figi"]), set()).add(
                str(row["ticker"])
            )
    bindings_by_ticker: dict[str, list[dict[str, Any]]] = {}
    for row in wikidata["bindings"]:
        bindings_by_ticker.setdefault(str(row["targetTicker"]), []).append(row)

    reconciled = []
    for episode in lineage["episodes"]:
        ticker = str(episode["ticker"])
        episode_start = date.fromisoformat(episode["effective_from"])
        episode_end = date.fromisoformat(episode["effective_to"])
        direct = episode["status"] == "direct_ticker_verified"
        companies: dict[str, dict[str, Any]] = {}
        for row in bindings_by_ticker.get(ticker, []):
            if row.get("targetExchangeLabel") not in US_EXCHANGES or not _overlaps(
                row.get("targetStart"), row.get("targetEnd"), episode_start, episode_end
            ):
                continue
            company = str(row["company"])
            target = companies.setdefault(
                company,
                {
                    "wikidata_entity": company,
                    "name": row.get("companyLabel"),
                    "cik": row.get("cik"),
                    "target_start": row.get("targetStart"),
                    "target_end": row.get("targetEnd"),
                    "alias_evidence": [],
                },
            )
            if row.get("aliasTicker") and row.get("aliasExchangeLabel") in US_EXCHANGES:
                target["alias_evidence"].append(
                    {
                        "ticker": str(row["aliasTicker"]),
                        "start": row.get("aliasStart"),
                        "end": row.get("aliasEnd"),
                    }
                )

        if direct and not companies:
            companies["direct-tiingo-metadata"] = {
                "wikidata_entity": None,
                "name": episode.get("metadata_name"),
                "cik": identifier_by_ticker[ticker].get("cik"),
                "target_start": episode.get("start_date"),
                "target_end": episode.get("end_date"),
                "alias_evidence": [
                    {
                        "ticker": ticker,
                        "start": episode.get("start_date"),
                        "end": episode.get("end_date"),
                    }
                ],
            }
        override = SEC_IDENTITY_OVERRIDES.get(ticker)
        transition_override = SEC_TRANSITION_OVERRIDES.get(ticker)
        if override is not None:
            cik, required_name, verified_aliases = override
            evidence = _require_sec_identity(
                sec_evidence, ticker=ticker, cik=cik, required_name=required_name
            )
            companies = {
                f"sec-cik:{cik}": {
                    "wikidata_entity": None,
                    "name": required_name.title(),
                    "cik": cik,
                    "target_start": episode["effective_from"],
                    "target_end": episode["effective_to"],
                    "alias_evidence": [],
                    "verified_aliases": list(verified_aliases),
                    "sec_submission_evidence": evidence,
                }
            }
        elif transition_override is not None:
            companies = {}
            for cik, required_name, segment_start, segment_end in transition_override:
                evidence = _require_sec_identity(
                    sec_evidence, ticker=ticker, cik=cik, required_name=required_name
                )
                companies[f"sec-cik:{cik}"] = {
                    "wikidata_entity": None,
                    "name": required_name.title(),
                    "cik": cik,
                    "target_start": max(episode["effective_from"], segment_start),
                    "target_end": min(episode["effective_to"], segment_end)
                    if segment_end
                    else episode["effective_to"],
                    "alias_evidence": [],
                    "verified_aliases": [],
                    "sec_submission_evidence": evidence,
                }
        candidates = []
        for company in companies.values():
            alias_tickers = {ticker, *company.pop("verified_aliases", [])}
            for alias in company.pop("alias_evidence"):
                if alias["ticker"] == ticker or _contiguous_alias(
                    company.get("target_start"),
                    company.get("target_end"),
                    alias.get("start"),
                    alias.get("end"),
                ):
                    alias_tickers.add(alias["ticker"])
            target_figi = episode.get("candidate_share_class_figi")
            if target_figi:
                alias_tickers.update(tickers_by_figi.get(str(target_figi), set()))
            price_segments = []
            for alias in sorted(alias_tickers):
                for price in prices.get(alias, []):
                    first = date.fromisoformat(price["first_price_date"])
                    last = date.fromisoformat(price["last_price_date"])
                    if first <= episode_end and last >= episode_start:
                        price_segments.append(price)
            company["aliases"] = sorted(alias_tickers)
            company["usable_prices"] = (
                price_segments
                if _covers_episode(
                    price_segments,
                    episode_start,
                    episode_end,
                    tolerance_days=tolerance_days,
                )
                else []
            )
            candidates.append(company)

        ready_candidates = [row for row in candidates if row["usable_prices"]]
        if transition_override is not None:
            status = "identity_verified_price_missing"
            selected = {"segments": candidates}
        elif len(ready_candidates) == 1 and (len(candidates) == 1 or direct):
            status = "ready_for_bundle"
            selected = ready_candidates[0]
        elif len(candidates) > 1:
            status = "identity_transition_or_collision"
            selected = None
        elif len(candidates) == 1:
            status = "identity_verified_price_missing"
            selected = candidates[0]
        else:
            status = "identity_unresolved"
            selected = None
        reconciled.append(
            {
                "episode_id": episode["episode_id"],
                "ticker": ticker,
                "effective_from": episode["effective_from"],
                "effective_to": episode["effective_to"],
                "status": status,
                "selected_identity": selected,
                "candidates": candidates,
            }
        )
    episode_tickers = {row["ticker"] for row in reconciled}
    additional_identities = []
    for ticker in sorted(SEC_IDENTITY_OVERRIDES.keys() - episode_tickers):
        cik, required_name, verified_aliases = SEC_IDENTITY_OVERRIDES[ticker]
        evidence = _require_sec_identity(
            sec_evidence, ticker=ticker, cik=cik, required_name=required_name
        )
        additional_identities.append(
            {
                "ticker": ticker,
                "status": "identity_verified",
                "cik": cik,
                "name": required_name.title(),
                "aliases": sorted({ticker, *verified_aliases}),
                "sec_submission_evidence": evidence,
            }
        )
    return {
        "schema_version": "free-pit-reconciled-lineage-v1",
        "publication_prohibited": True,
        "lineage_registry_sha256": _sha256(lineage_body),
        "wikidata_registry_sha256": _sha256(wikidata_body),
        "identifier_registry_sha256": _sha256(identifier_body),
        "episode_count": len(reconciled),
        "additional_identity_count": len(additional_identities),
        "additional_identities": additional_identities,
        "ready_for_bundle_count": sum(
            row["status"] == "ready_for_bundle" for row in reconciled
        ),
        "episodes": reconciled,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reconcile frozen lineage evidence.")
    parser.add_argument("--lineage", type=Path, default=DEFAULT_LINEAGE)
    parser.add_argument("--wikidata", type=Path, default=DEFAULT_WIKIDATA)
    parser.add_argument("--identifiers", type=Path, default=DEFAULT_IDENTIFIERS)
    parser.add_argument("--price-root", type=Path, action="append")
    parser.add_argument("--sec-root", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        document = reconcile_lineage(
            lineage_body=args.lineage.read_bytes(),
            wikidata_body=args.wikidata.read_bytes(),
            identifier_body=args.identifiers.read_bytes(),
            price_roots=tuple(args.price_root or DEFAULT_PRICE_ROOTS),
            sec_roots=tuple(args.sec_root or DEFAULT_SEC_ROOTS),
        )
        body = _json_bytes(document)
        _atomic_write(args.output, body)
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"lineage reconciliation failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"ready={document['ready_for_bundle_count']}/"
        f"{document['episode_count']} sha256={_sha256(body)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
