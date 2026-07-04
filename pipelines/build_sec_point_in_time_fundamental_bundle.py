"""Build the amended zero-cost Sprint 8 bundle from frozen SEC evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EQUITY_BUNDLE = (
    REPO_ROOT / "data/raw/free-point-in-time/composite-equity-bundle-v1"
)
DEFAULT_SEC_ROOT = REPO_ROOT / "data/raw/free-point-in-time/sec-pit-v1"
DEFAULT_FILING_ROOT = (
    REPO_ROOT / "data/raw/free-point-in-time/sec-filing-evidence-v1"
)
DEFAULT_OUTPUT = (
    REPO_ROOT / "data/raw/free-point-in-time/sec-fundamentals-bundle-v1"
)
BUFFER_START = date(2012, 1, 1)
WINDOW_END = date(2025, 6, 30)
WINDOW_START = date(2017, 1, 1)

CONCEPT_MAP = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "Revenues": "revenue",
    "SalesRevenueNet": "revenue",
    "GrossProfit": "gross_profit",
    "OperatingIncomeLoss": "ebit",
    "NetIncomeLoss": "net_income_common",
    "NetIncomeLossAvailableToCommonStockholdersBasic": "net_income_common",
    "ProfitLoss": "net_income_common",
    "EarningsPerShareDiluted": "diluted_eps",
    "NetCashProvidedByUsedInOperatingActivities": "cash_from_operations",
    "PaymentsToAcquirePropertyPlantAndEquipment": "capital_expenditure",
    "Assets": "total_assets",
    "LongTermDebtAndFinanceLeaseObligations": "total_debt",
    "LongTermDebtAndCapitalLeaseObligations": "total_debt",
    "LongTermDebt": "total_debt",
    "CashAndCashEquivalentsAtCarryingValue": "cash_and_equivalents",
    "StockholdersEquity": "shareholders_equity",
    "IncomeTaxExpenseBenefit": "income_tax_expense",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": "pretax_income",
    "WeightedAverageNumberOfDilutedSharesOutstanding": "diluted_shares",
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


def _load_pinned_json(path: Path, expected_hash: str) -> tuple[bytes, Any]:
    body = path.read_bytes()
    if _sha256(body) != expected_hash.lower():
        raise ValueError(f"SHA-256 does not match: {path}")
    return body, json.loads(body)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _period_type(item: Mapping[str, Any]) -> Optional[str]:
    start_text = item.get("start")
    end_text = item.get("end")
    if start_text and end_text:
        duration = (date.fromisoformat(str(end_text)) - date.fromisoformat(str(start_text))).days + 1
        if duration <= 120:
            return "QUARTERLY"
        if duration >= 300:
            return "ANNUAL"
        return None
    form = str(item.get("form") or "").upper()
    if form.startswith("10-K"):
        return "ANNUAL"
    if form.startswith("10-Q"):
        return "QUARTERLY"
    return None


def _quarter(item: Mapping[str, Any], period_type: str) -> Optional[int]:
    if period_type == "ANNUAL":
        return None
    for label in (str(item.get("fp") or "").upper(), str(item.get("frame") or "").upper()):
        for quarter in range(1, 5):
            if f"Q{quarter}" in label:
                return quarter
    return None


def _warehouse_decimal(value: Any) -> Optional[str]:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not parsed.is_finite():
        return None
    fractional_digits = max(0, -parsed.as_tuple().exponent)
    integer_digits = max(1, parsed.adjusted() + 1)
    if fractional_digits > 6 or integer_digits > 18:
        return None
    return format(parsed, "f")


def _canonical_fiscal_year(versions: Sequence[Mapping[str, Any]]) -> int:
    """Keep one plausible issuer fiscal year across all comparative revisions."""

    period_end_year = date.fromisoformat(str(versions[0]["fiscal_period_end"])).year
    first_reported_fiscal_year = int(versions[0]["fiscal_year"])
    return (
        first_reported_fiscal_year
        if abs(first_reported_fiscal_year - period_end_year) <= 1
        else period_end_year
    )


def _equity_identities(equity_bundle: Path) -> dict[str, tuple[str, ...]]:
    document = json.loads((equity_bundle / "securities.json").read_text())
    grouped: dict[str, set[str]] = defaultdict(set)
    for row in document:
        cik = str(row.get("cik") or "")
        vendor_id = str(row.get("vendor_id") or "")
        if vendor_id:
            grouped[cik or "__NO_CIK__"].add(vendor_id)
    return {key: tuple(sorted(values)) for key, values in sorted(grouped.items())}


def _filing_evidence(root: Path, expected_plan_hash: str) -> dict[tuple[str, str], dict[str, Any]]:
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(root.glob("CIK*/*.complete.json")):
        row = json.loads(path.read_text())
        if (
            row.get("schema_version") != "free-pit-sec-filing-evidence-v1"
            or row.get("filing_plan_sha256") != expected_plan_hash
        ):
            raise ValueError(f"invalid SEC filing evidence: {path}")
        raw_path = path.parent / str(row["path"])
        if not raw_path.is_file() or _sha256(raw_path.read_bytes()) != row["sha256"]:
            raise ValueError(f"SEC filing evidence does not reproduce: {path}")
        key = (str(row["cik"]), str(row["accession"]))
        if key in result and result[key] != row:
            raise ValueError(f"duplicate SEC filing evidence: {key}")
        result[key] = row
    return result


def _sic_sector(value: str) -> str:
    sic = int(value)
    if 100 <= sic <= 999 or 2000 <= sic <= 2199 or 5400 <= sic <= 5499:
        return "Consumer Staples"
    if 1000 <= sic <= 1299 or 1400 <= sic <= 1499 or 2400 <= sic <= 2699:
        return "Materials"
    if 1300 <= sic <= 1399 or 2900 <= sic <= 2999:
        return "Energy"
    if 2830 <= sic <= 2839 or 3840 <= sic <= 3859 or 8000 <= sic <= 8099:
        return "Health Care"
    if 3570 <= sic <= 3579 or 3660 <= sic <= 3679 or 7370 <= sic <= 7379:
        return "Information Technology"
    if 4800 <= sic <= 4899 or 2700 <= sic <= 2799:
        return "Communication Services"
    if 4900 <= sic <= 4999:
        return "Utilities"
    if 6500 <= sic <= 6599:
        return "Real Estate"
    if 6000 <= sic <= 6499 or 6700 <= sic <= 6799:
        return "Financials"
    if 2200 <= sic <= 2399 or 2500 <= sic <= 2599 or 5200 <= sic <= 5999 or 7000 <= sic <= 7999:
        return "Consumer Discretionary"
    return "Industrials"


def _classification_rows(
    identities: Mapping[str, Sequence[str]],
    filing_rows: Mapping[tuple[str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    by_cik: dict[str, list[tuple[datetime, str, str]]] = defaultdict(list)
    for (cik, accession), row in filing_rows.items():
        sic = str(row.get("sic") or "")
        if len(sic) != 4 or not sic.isdigit():
            continue
        accepted = _timestamp(str(row["accepted_at"]))
        if accepted.date() <= WINDOW_END:
            by_cik[cik].append((accepted, sic, accession))

    output: list[dict[str, Any]] = []
    for cik, vendor_ids in identities.items():
        events = sorted(by_cik.get(cik, ()))
        if not events:
            for vendor_id in vendor_ids:
                output.append(
                    {
                        "vendor_id": vendor_id,
                        "sector": "Unknown",
                        "industry": None,
                        "classification_system": "SEC_SIC_TO_GICS_V1",
                        "effective_from": WINDOW_START.isoformat(),
                        "effective_to": WINDOW_END.isoformat(),
                        "model_available_at": f"{WINDOW_START.isoformat()}T00:00:00Z",
                        "filing_accession": None,
                    }
                )
            continue
        before = [row for row in events if row[0].date() <= WINDOW_START]
        selected = ([before[-1]] if before else []) + [
            row for row in events if row[0].date() > WINDOW_START
        ]
        changes: list[tuple[datetime, str, str, str]] = []
        for accepted, sic, accession in selected:
            sector = _sic_sector(sic)
            if changes and changes[-1][1:] == (sector, sic, accession):
                continue
            if changes and changes[-1][1] == sector and changes[-1][2] == sic:
                continue
            changes.append((accepted, sector, sic, accession))
        for index, (accepted, sector, sic, accession) in enumerate(changes):
            effective_from = max(WINDOW_START, accepted.date())
            if index == 0 and effective_from > WINDOW_START:
                for vendor_id in vendor_ids:
                    output.append(
                        {
                            "vendor_id": vendor_id,
                            "sector": "Unknown",
                            "industry": None,
                            "classification_system": "SEC_SIC_TO_GICS_V1",
                            "effective_from": WINDOW_START.isoformat(),
                            "effective_to": (effective_from - timedelta(days=1)).isoformat(),
                            "model_available_at": f"{WINDOW_START.isoformat()}T00:00:00Z",
                            "filing_accession": None,
                        }
                    )
            next_date = (
                max(WINDOW_START, changes[index + 1][0].date())
                if index + 1 < len(changes)
                else None
            )
            effective_to = next_date - timedelta(days=1) if next_date else WINDOW_END
            if effective_to < effective_from:
                continue
            for vendor_id in vendor_ids:
                output.append(
                    {
                        "vendor_id": vendor_id,
                        "sector": sector,
                        "industry": sic,
                        "classification_system": "SEC_SIC_TO_GICS_V1",
                        "effective_from": effective_from.isoformat(),
                        "effective_to": effective_to.isoformat(),
                        "model_available_at": accepted.isoformat().replace("+00:00", "Z"),
                        "filing_accession": accession,
                    }
                )
    return sorted(
        output,
        key=lambda row: (row["vendor_id"], row["effective_from"], row["filing_accession"]),
    )


def _company_rows(
    *,
    sec_root: Path,
    identities: Mapping[str, Sequence[str]],
    filing_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    source_registry: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    candidates: dict[tuple[str, str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)
    counts = defaultdict(int)
    completions = {
        str(path.parent.name.removeprefix("CIK")): path
        for path in sec_root.glob("CIK*/complete.json")
    }
    if len(completions) != int(source_registry["complete_cik_count"]):
        raise ValueError("SEC company source registry is incomplete")

    for cik, vendor_ids in identities.items():
        completion_path = completions.get(cik)
        if completion_path is None:
            counts["missing_company_source"] += 1
            continue
        completion = json.loads(completion_path.read_text())
        company_meta = completion.get("companyfacts")
        if not isinstance(company_meta, dict):
            raise ValueError(f"companyfacts metadata missing for CIK {cik}")
        company_path = completion_path.parent / str(company_meta["path"])
        body = company_path.read_bytes()
        if _sha256(body) != company_meta["sha256"]:
            raise ValueError(f"companyfacts source does not reproduce for CIK {cik}")
        payload = json.loads(body)
        for taxonomy in payload.get("facts", {}).values():
            for concept, concept_payload in taxonomy.items():
                if concept not in CONCEPT_MAP:
                    continue
                for unit, observations in concept_payload.get("units", {}).items():
                    for item in observations:
                        accession = str(item.get("accn") or "")
                        evidence = filing_rows.get((cik, accession))
                        if evidence is None:
                            counts["missing_filing_evidence"] += 1
                            continue
                        accepted = _timestamp(str(evidence["accepted_at"]))
                        end_text = str(item.get("end") or "")
                        try:
                            period_end = date.fromisoformat(end_text)
                        except ValueError:
                            counts["invalid_period_end"] += 1
                            continue
                        if not (BUFFER_START <= period_end <= WINDOW_END) or accepted.date() > WINDOW_END:
                            continue
                        period_type = _period_type(item)
                        quarter = _quarter(item, period_type) if period_type else None
                        if period_type is None or (period_type == "QUARTERLY" and quarter is None):
                            counts["unsupported_period"] += 1
                            continue
                        value = _warehouse_decimal(item.get("val"))
                        if value is None:
                            counts["invalid_numeric"] += 1
                            continue
                        form = str(item.get("form") or evidence.get("form") or "").upper()
                        if not (form.startswith("10-K") or form.startswith("10-Q")):
                            continue
                        model_available = accepted + timedelta(hours=1)
                        for vendor_id in vendor_ids:
                            row = {
                                "vendor_id": vendor_id,
                                "fiscal_period_end": period_end.isoformat(),
                                "fiscal_year": int(item.get("fy") or period_end.year),
                                "fiscal_quarter": quarter,
                                "period_type": period_type,
                                "form_type": form,
                                "filing_accession": accession,
                                "filed_at": accepted.isoformat().replace("+00:00", "Z"),
                                "accepted_at": accepted.isoformat().replace("+00:00", "Z"),
                                "public_release_at": None,
                                "vendor_available_at": accepted.isoformat().replace("+00:00", "Z"),
                                "model_available_at": model_available.isoformat().replace("+00:00", "Z"),
                                "concept": concept,
                                "value": value,
                                "unit": str(unit),
                            }
                            identity = (vendor_id, end_text, period_type, concept, str(unit))
                            prior = candidates[identity].get(accession)
                            if prior is not None and prior != row:
                                candidates[identity][accession] = {"ambiguous": True}
                                counts["ambiguous_accession"] += 1
                            elif prior is None:
                                candidates[identity][accession] = row

    rows: list[dict[str, Any]] = []
    for identity in sorted(candidates):
        versions = [
            row for row in candidates[identity].values() if not row.get("ambiguous")
        ]
        versions.sort(key=lambda row: (row["accepted_at"], row["filing_accession"]))
        if not versions or versions[0]["form_type"].endswith("/A"):
            counts["orphan_amendment_identity"] += 1
            continue
        canonical_fiscal_year = _canonical_fiscal_year(versions)
        for revision, row in enumerate(versions, start=1):
            # SEC Companyfacts carries the filing's `fy` on comparative facts,
            # so a later filing can otherwise relabel an older issuer-period.
            row["fiscal_year"] = canonical_fiscal_year
            row["revision_version"] = revision
            rows.append(row)
    rows.sort(
        key=lambda row: (
            row["vendor_id"],
            row["fiscal_period_end"],
            row["period_type"],
            row["concept"],
            row["unit"],
            row["revision_version"],
        )
    )
    counts["fact_count"] = len(rows)
    return rows, dict(sorted(counts.items()))


def build_bundle(
    *,
    equity_bundle: Path,
    expected_equity_manifest_hash: str,
    sec_root: Path,
    expected_sec_registry_hash: str,
    filing_root: Path,
    expected_filing_plan_hash: str,
    output: Path,
    created_at: datetime,
) -> dict[str, Any]:
    equity_manifest_body, _ = _load_pinned_json(
        equity_bundle / "manifest.json", expected_equity_manifest_hash
    )
    sec_registry_body, sec_registry = _load_pinned_json(
        sec_root / "registry.json", expected_sec_registry_hash
    )
    if sec_registry.get("status") != "complete":
        raise ValueError("SEC company source registry is incomplete")
    filing_registry_body = (filing_root / "registry.json").read_bytes()
    filing_registry = json.loads(filing_registry_body)
    if (
        filing_registry.get("status") != "complete"
        or filing_registry.get("filing_plan_sha256") != expected_filing_plan_hash
        or filing_registry.get("accounted_filing_count")
        != filing_registry.get("requested_filing_count")
    ):
        raise ValueError("SEC filing evidence registry is incomplete")

    identities = _equity_identities(equity_bundle)
    evidence = _filing_evidence(filing_root, expected_filing_plan_hash)
    if len(evidence) != int(filing_registry["complete_filing_count"]):
        raise ValueError("SEC filing completion count does not match registry")
    rows, counts = _company_rows(
        sec_root=sec_root,
        identities=identities,
        filing_rows=evidence,
        source_registry=sec_registry,
    )
    if not rows:
        raise ValueError("SEC normalization produced no facts")
    classifications = _classification_rows(identities, evidence)
    if not classifications:
        raise ValueError("SEC normalization produced no classifications")
    facts_body = _json_bytes(rows)
    classifications_body = _json_bytes(classifications)
    _atomic_write(output / "fundamentals.json", facts_body)
    _atomic_write(output / "classifications.json", classifications_body)
    field_map = {name: name for name in (
        "vendor_id", "fiscal_period_end", "fiscal_year", "fiscal_quarter",
        "period_type", "form_type", "filing_accession", "filed_at",
        "accepted_at", "public_release_at", "vendor_available_at",
        "model_available_at", "revision_version", "concept", "value", "unit",
    )}
    manifest = {
        "schema_version": "point-in-time-fundamentals-bundle-v1",
        "vendor": "SEC EDGAR Primary",
        "dataset": "companyfacts_with_filing_acceptance_v1",
        "license_tag": "public_source_internal_research",
        "license_evidence_uri": "https://www.sec.gov/os/accessing-edgar-data",
        "vendor_identifier_type": "FIGI_SHARE_CLASS",
        "concept_map_version": "sec-companyfacts-v1",
        "field_map": field_map,
        "concept_map": CONCEPT_MAP,
        "fundamentals_file": {
            "path": "fundamentals.json",
            "sha256": _sha256(facts_body),
            "retrieved_at": created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_uri": "private://sec-edgar/companyfacts-and-filing-index-evidence",
        },
        "classifications_file": {
            "path": "classifications.json",
            "sha256": _sha256(classifications_body),
            "source_uri": "private://sec-edgar/filing-index-sic-evidence",
            "classification_system": "SEC_SIC_TO_GICS_V1",
        },
        "amended_contract": {
            "primary_source": "SEC EDGAR",
            "window_start": "2017-01-01",
            "window_end": WINDOW_END.isoformat(),
            "fiscal_buffer_start": BUFFER_START.isoformat(),
            "equity_manifest_sha256": _sha256(equity_manifest_body),
            "sec_source_registry_sha256": _sha256(sec_registry_body),
            "filing_evidence_registry_sha256": _sha256(filing_registry_body),
            "filing_plan_sha256": expected_filing_plan_hash,
            "normalization_counts": counts,
            "classification_count": len(classifications),
        },
    }
    manifest_body = _json_bytes(manifest)
    _atomic_write(output / "manifest.json", manifest_body)
    return {
        "manifest_sha256": _sha256(manifest_body),
        "facts_sha256": _sha256(facts_body),
        "classifications_sha256": _sha256(classifications_body),
        "classification_count": len(classifications),
        **counts,
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--equity-bundle", type=Path, default=DEFAULT_EQUITY_BUNDLE)
    parser.add_argument("--expected-equity-manifest-hash", required=True)
    parser.add_argument("--sec-root", type=Path, default=DEFAULT_SEC_ROOT)
    parser.add_argument("--expected-sec-registry-hash", required=True)
    parser.add_argument("--filing-root", type=Path, default=DEFAULT_FILING_ROOT)
    parser.add_argument("--expected-filing-plan-hash", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--created-at", required=True, type=_timestamp)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        result = build_bundle(
            equity_bundle=args.equity_bundle,
            expected_equity_manifest_hash=args.expected_equity_manifest_hash,
            sec_root=args.sec_root,
            expected_sec_registry_hash=args.expected_sec_registry_hash,
            filing_root=args.filing_root,
            expected_filing_plan_hash=args.expected_filing_plan_hash,
            output=args.output,
            created_at=args.created_at,
        )
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"SEC fundamental bundle build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
