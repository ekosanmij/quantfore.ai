"""Build and audit the outcome-blind Model V2 accounting-history expansion."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import DEFAULT_RAW_DIR, get_code_revision, repository_relative_path
    from build_sec_point_in_time_fundamental_bundle import (
        _equity_identities,
        _filing_evidence,
        _load_pinned_json,
        _sha256,
        _timestamp,
        _warehouse_decimal,
    )
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        DEFAULT_RAW_DIR,
        get_code_revision,
        repository_relative_path,
    )
    from pipelines.build_sec_point_in_time_fundamental_bundle import (  # type: ignore
        _equity_identities,
        _filing_evidence,
        _load_pinned_json,
        _sha256,
        _timestamp,
        _warehouse_decimal,
    )

from quantfore_research.validation.accounting_coverage import (
    ACCOUNTING_HISTORY_VERSION,
    COMPONENT_FAMILIES,
    COMPONENT_REQUIREMENTS,
    AccountingFact,
    component_statuses,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EQUITY_BUNDLE = DEFAULT_RAW_DIR / "free-point-in-time/composite-equity-bundle-v1"
DEFAULT_SEC_ROOT = DEFAULT_RAW_DIR / "free-point-in-time/sec-pit-v1"
DEFAULT_FILING_ROOT = DEFAULT_RAW_DIR / "free-point-in-time/sec-filing-evidence-v1"
DEFAULT_OUTPUT = DEFAULT_RAW_DIR / "free-point-in-time/sec-fundamentals-bundle-v2"
DEFAULT_DATABASE = DEFAULT_RAW_DIR / "free-point-in-time/sprint8-prelock-v9/research.db"
DEFAULT_REPORT = Path("reports/data-audits/model-v2-accounting-coverage-v1.json")
DEFAULT_MARKDOWN = Path("reports/data-audits/model-v2-accounting-coverage-v1.md")
BUFFER_START = date(2012, 1, 1)
WINDOW_END = date(2025, 6, 30)
FORMULA_VERSION = "sec-discrete-quarter-normalization-v2"
MATERIAL_IMPROVEMENT_FLOOR = 0.10


CONCEPT_SOURCES = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
        "Revenues",
    ),
    "gross_profit": ("GrossProfit",),
    "ebit": ("OperatingIncomeLoss",),
    "net_income_common": (
        "NetIncomeLossAvailableToCommonStockholdersBasic",
        "NetIncomeLoss",
        "ProfitLoss",
    ),
    "diluted_eps": ("EarningsPerShareDiluted",),
    "cash_from_operations": (
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ),
    "capital_expenditure": (
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireOtherPropertyPlantAndEquipment",
    ),
    "total_assets": ("Assets",),
    "total_debt": (
        "LongTermDebtAndFinanceLeaseObligations",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermDebt",
        "LongTermDebtNoncurrent",
    ),
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    "shareholders_equity": (
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ),
    "income_tax_expense": ("IncomeTaxExpenseBenefit",),
    "pretax_income": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    # Candidate specialist-branch inputs. Exact Model V2 formulas remain a 10.4 lock.
    "loans_and_leases_net": ("LoansAndLeasesReceivableNetReportedAmount",),
    "customer_deposits": ("Deposits",),
    "net_interest_income": ("InterestIncomeExpenseNet",),
    "credit_loss_provision": (
        "ProvisionForLoanLeaseAndOtherLosses",
        "ProvisionForLoanAndLeaseLosses",
        "ProvisionForLoanLossesExpensed",
    ),
    "premiums_earned_net": ("PremiumsEarnedNet",),
    "policyholder_benefits_claims_net": ("PolicyholderBenefitsAndClaimsIncurredNet",),
    "net_investment_income": ("NetInvestmentIncome", "InvestmentIncomeNet"),
    "real_estate_investment_property_net": ("RealEstateInvestmentPropertyNet",),
    "depreciation_and_amortization": (
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "Depreciation",
    ),
    "investment_real_estate_sale_gain_loss": (
        "GainsLossesOnSalesOfInvestmentRealEstate",
    ),
    "interest_expense": ("InterestExpense", "InterestExpenseNonoperating"),
}
SOURCE_TO_STANDARD = {
    source: (standard, priority)
    for standard, sources in CONCEPT_SOURCES.items()
    for priority, source in enumerate(sources)
}
EXPECTED_UNIT = {
    "diluted_eps": "USD/shares",
    "diluted_shares": "shares",
}
_QUARTER = re.compile(r"Q([1-4])")


@dataclass(frozen=True)
class Observation:
    standard_concept: str
    source_concept: str
    concept_priority: int
    start: Optional[date]
    end: date
    duration_kind: str
    fiscal_year: int
    quarter_hint: Optional[int]
    form_type: str
    filing_accession: str
    accepted_at: datetime
    model_available_at: datetime
    value: str
    unit: str
    companyfacts_sha256: str
    filing_index_sha256: str

    @property
    def decimal_value(self) -> Decimal:
        return Decimal(self.value)

    def input_lineage(self) -> dict[str, Any]:
        return {
            "source_concept": self.source_concept,
            "start": self.start.isoformat() if self.start else None,
            "end": self.end.isoformat(),
            "filing_accession": self.filing_accession,
            "accepted_at": self.accepted_at.isoformat().replace("+00:00", "Z"),
            "value": self.value,
            "unit": self.unit,
            "companyfacts_sha256": self.companyfacts_sha256,
            "filing_index_sha256": self.filing_index_sha256,
        }


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return hashlib.sha256(payload).hexdigest()


def _quarter_hint(item: Mapping[str, Any], form_type: str) -> Optional[int]:
    for label in (str(item.get("fp") or "").upper(), str(item.get("frame") or "").upper()):
        match = _QUARTER.search(label)
        if match:
            return int(match.group(1))
    if form_type.startswith("10-K") and item.get("start"):
        start = date.fromisoformat(str(item["start"]))
        end = date.fromisoformat(str(item["end"]))
        if (end - start).days + 1 <= 120:
            return 4
    return None


def _duration_kind(item: Mapping[str, Any]) -> str:
    if not item.get("start"):
        return "INSTANT"
    start = date.fromisoformat(str(item["start"]))
    end = date.fromisoformat(str(item["end"]))
    duration = (end - start).days + 1
    if duration <= 120:
        return "DISCRETE"
    if duration < 300:
        return "YTD"
    return "ANNUAL"


def _cumulative_quarter(observation: Observation) -> Optional[int]:
    if observation.start is None:
        return None
    duration = (observation.end - observation.start).days + 1
    if duration <= 120:
        return 1
    if duration <= 210:
        return 2
    if duration < 300:
        return 3
    return 4


def _expected_unit(concept: str) -> str:
    return EXPECTED_UNIT.get(concept, "USD")


def _observations_for_cik(
    *,
    cik: str,
    completion_path: Path,
    filing_rows: Mapping[tuple[str, str], Mapping[str, Any]],
    counts: Counter[str],
) -> list[Observation]:
    completion = json.loads(completion_path.read_text())
    company_meta = completion.get("companyfacts")
    if not isinstance(company_meta, dict):
        counts["missing_company_source"] += 1
        return []
    company_path = completion_path.parent / str(company_meta["path"])
    body = company_path.read_bytes()
    company_hash = _sha256(body)
    if company_hash != company_meta["sha256"]:
        raise ValueError(f"companyfacts source does not reproduce for CIK {cik}")
    payload = json.loads(body)
    observations = []
    for taxonomy in sorted(payload.get("facts", {})):
        facts = payload["facts"][taxonomy]
        for source_concept in sorted(facts):
            mapping = SOURCE_TO_STANDARD.get(source_concept)
            if mapping is None:
                continue
            standard, priority = mapping
            for unit in sorted(facts[source_concept].get("units", {})):
                if unit.lower() != _expected_unit(standard).lower():
                    counts["unsupported_unit"] += len(
                        facts[source_concept]["units"][unit]
                    )
                    continue
                for item in facts[source_concept]["units"][unit]:
                    accession = str(item.get("accn") or "")
                    evidence = filing_rows.get((cik, accession))
                    if evidence is None:
                        counts["missing_filing_evidence"] += 1
                        continue
                    form = str(item.get("form") or evidence.get("form") or "").upper()
                    if not (form.startswith("10-K") or form.startswith("10-Q")):
                        continue
                    try:
                        end = date.fromisoformat(str(item.get("end") or ""))
                        start = (
                            date.fromisoformat(str(item["start"]))
                            if item.get("start")
                            else None
                        )
                    except ValueError:
                        counts["invalid_period"] += 1
                        continue
                    accepted = _timestamp(str(evidence["accepted_at"]))
                    if not (BUFFER_START <= end <= WINDOW_END) or accepted.date() > WINDOW_END:
                        continue
                    value = _warehouse_decimal(item.get("val"))
                    if value is None:
                        counts["invalid_numeric"] += 1
                        continue
                    observations.append(
                        Observation(
                            standard_concept=standard,
                            source_concept=source_concept,
                            concept_priority=priority,
                            start=start,
                            end=end,
                            duration_kind=_duration_kind(item),
                            fiscal_year=int(item.get("fy") or end.year),
                            quarter_hint=_quarter_hint(item, form),
                            form_type=form,
                            filing_accession=accession,
                            accepted_at=accepted,
                            model_available_at=accepted + timedelta(hours=1),
                            value=value,
                            unit=str(unit),
                            companyfacts_sha256=company_hash,
                            filing_index_sha256=str(evidence["sha256"]),
                        )
                    )
    counts["source_observation_count"] += len(observations)
    return observations


def _lineage_payload(
    *,
    derivation_type: str,
    formula: str,
    inputs: Sequence[Observation],
) -> dict[str, Any]:
    return {
        "derivation_type": derivation_type,
        "formula": formula,
        "formula_version": FORMULA_VERSION,
        "inputs": [item.input_lineage() for item in inputs],
    }


def _candidate(
    *,
    observation: Observation,
    period_type: str,
    fiscal_quarter: Optional[int],
    value: str,
    derivation_type: str,
    formula: str,
    inputs: Sequence[Observation],
) -> dict[str, Any]:
    lineage = _lineage_payload(
        derivation_type=derivation_type, formula=formula, inputs=inputs
    )
    lineage_hash = hashlib.sha256(
        json.dumps(lineage, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    source_hashes = sorted(
        {
            value
            for item in inputs
            for value in (item.companyfacts_sha256, item.filing_index_sha256)
        }
    )
    return {
        "fiscal_period_end": observation.end.isoformat(),
        "fiscal_year": observation.fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "period_type": period_type,
        "form_type": observation.form_type,
        "filing_accession": observation.filing_accession,
        "filed_at": observation.accepted_at.isoformat().replace("+00:00", "Z"),
        "accepted_at": observation.accepted_at.isoformat().replace("+00:00", "Z"),
        "public_release_at": None,
        "vendor_available_at": observation.accepted_at.isoformat().replace(
            "+00:00", "Z"
        ),
        "model_available_at": observation.model_available_at.isoformat().replace(
            "+00:00", "Z"
        ),
        "concept": observation.standard_concept,
        "source_concept": observation.source_concept,
        "concept_priority": observation.concept_priority,
        "value": value,
        "unit": observation.unit,
        "derivation_type": derivation_type,
        "formula_version": FORMULA_VERSION,
        "formula_lineage_sha256": lineage_hash,
        "source_hashes": source_hashes,
        "_lineage": lineage,
    }


def _prefer_candidate(
    prior: dict[str, Any], candidate: dict[str, Any], counts: Counter[str]
) -> dict[str, Any]:
    prior_reported = prior["derivation_type"] == "REPORTED"
    candidate_reported = candidate["derivation_type"] == "REPORTED"
    if prior_reported != candidate_reported:
        counts["reported_derived_collisions"] += 1
        return prior if prior_reported else candidate
    prior_key = (int(prior["concept_priority"]), prior["source_concept"])
    candidate_key = (int(candidate["concept_priority"]), candidate["source_concept"])
    if prior["value"] != candidate["value"]:
        counts["same_accession_value_conflicts"] += 1
    return prior if prior_key <= candidate_key else candidate


def _issuer_rows(
    observations: Sequence[Observation], counts: Counter[str]
) -> list[dict[str, Any]]:
    candidates: dict[tuple[str, str, str, str], dict[str, dict[str, Any]]] = defaultdict(dict)

    def add(row: dict[str, Any]) -> None:
        identity = (
            row["fiscal_period_end"],
            row["period_type"],
            row["concept"],
            row["unit"],
        )
        prior = candidates[identity].get(row["filing_accession"])
        candidates[identity][row["filing_accession"]] = (
            row if prior is None else _prefer_candidate(prior, row, counts)
        )

    for observation in observations:
        if observation.duration_kind == "YTD":
            continue
        if observation.duration_kind == "ANNUAL":
            add(
                _candidate(
                    observation=observation,
                    period_type="ANNUAL",
                    fiscal_quarter=None,
                    value=observation.value,
                    derivation_type="REPORTED",
                    formula="reported annual value",
                    inputs=(observation,),
                )
            )
            continue
        quarter = observation.quarter_hint
        if observation.duration_kind == "INSTANT":
            if observation.form_type.startswith("10-K"):
                period_type = "ANNUAL"
                quarter = None
            elif quarter in {1, 2, 3, 4}:
                period_type = "QUARTERLY"
            else:
                counts["instant_quarter_unresolved"] += 1
                continue
        else:
            period_type = "QUARTERLY"
            if quarter not in {1, 2, 3, 4}:
                counts["discrete_quarter_unresolved"] += 1
                continue
        add(
            _candidate(
                observation=observation,
                period_type=period_type,
                fiscal_quarter=quarter,
                value=observation.value,
                derivation_type="REPORTED",
                formula="reported value",
                inputs=(observation,),
            )
        )

    cumulative: dict[tuple[str, str, date], list[Observation]] = defaultdict(list)
    for observation in observations:
        if observation.start is not None:
            cumulative[
                (observation.standard_concept, observation.unit, observation.start)
            ].append(observation)
    for values in cumulative.values():
        # Collapse synonymous concepts for the same filing and period using the
        # accounting-priority order declared above.
        canonical: dict[tuple[date, str], Observation] = {}
        for observation in sorted(
            values,
            key=lambda item: (
                item.end,
                item.filing_accession,
                item.concept_priority,
                item.source_concept,
            ),
        ):
            key = (observation.end, observation.filing_accession)
            canonical.setdefault(key, observation)
        values = list(canonical.values())
        for current in values:
            current_quarter = _cumulative_quarter(current)
            if current_quarter not in {2, 3, 4}:
                continue
            prior_values = [
                item
                for item in values
                if _cumulative_quarter(item) == current_quarter - 1
                and item.end < current.end
                and item.model_available_at <= current.model_available_at
                and 45 <= (current.end - item.end).days <= 150
            ]
            if not prior_values:
                counts[f"missing_q{current_quarter - 1}_cumulative_input"] += 1
                continue
            prior = max(
                prior_values,
                key=lambda item: (
                    item.end,
                    item.model_available_at,
                    -item.concept_priority,
                    item.filing_accession,
                ),
            )
            try:
                value = _warehouse_decimal(current.decimal_value - prior.decimal_value)
            except InvalidOperation:
                value = None
            if value is None:
                counts["invalid_derived_numeric"] += 1
                continue
            row = _candidate(
                observation=current,
                period_type="QUARTERLY",
                fiscal_quarter=current_quarter,
                value=value,
                derivation_type="YTD_DELTA"
                if current_quarter < 4
                else "ANNUAL_MINUS_YTD",
                formula=(
                    f"reported cumulative Q{current_quarter} minus reported "
                    f"cumulative Q{current_quarter - 1}"
                ),
                inputs=(current, prior),
            )
            add(row)
            counts[f"derived_q{current_quarter}_candidates"] += 1

    output = []
    for identity in sorted(candidates):
        versions = sorted(
            candidates[identity].values(),
            key=lambda row: (row["model_available_at"], row["filing_accession"]),
        )
        if not versions or versions[0]["form_type"].endswith("/A"):
            counts["orphan_amendment_identity"] += 1
            continue
        period_end_year = date.fromisoformat(versions[0]["fiscal_period_end"]).year
        reported_year = int(versions[0]["fiscal_year"])
        canonical_year = (
            reported_year
            if abs(reported_year - period_end_year) <= 1
            else period_end_year
        )
        canonical_quarter = versions[0]["fiscal_quarter"]
        for revision, row in enumerate(versions, start=1):
            row["fiscal_year"] = canonical_year
            row["fiscal_quarter"] = canonical_quarter
            row["revision_version"] = revision
            output.append(row)
            counts[
                "normalized_issuer_derived_row_count"
                if row["derivation_type"] != "REPORTED"
                else "normalized_issuer_reported_row_count"
            ] += 1
    output.sort(
        key=lambda row: (
            row["fiscal_period_end"],
            row["period_type"],
            row["concept"],
            row["unit"],
            row["revision_version"],
        )
    )
    return output


class Coverage:
    def __init__(self) -> None:
        self.stock_months = 0
        self.component_reasons = {
            component: Counter() for component in COMPONENT_REQUIREMENTS
        }
        self.family_all_ready = Counter()
        self.ready_component_total = 0

    def add(self, statuses: Mapping[str, Optional[str]]) -> None:
        self.stock_months += 1
        ready_families = defaultdict(list)
        for component, reason in statuses.items():
            state = reason or "READY"
            self.component_reasons[component][state] += 1
            if reason is None:
                self.ready_component_total += 1
            ready_families[COMPONENT_FAMILIES[component]].append(reason is None)
        for family, values in ready_families.items():
            if all(values):
                self.family_all_ready[family] += 1

    def document(self) -> dict[str, Any]:
        components = []
        for component in sorted(self.component_reasons):
            reasons = self.component_reasons[component]
            ready = reasons["READY"]
            components.append(
                {
                    "component": component,
                    "family": COMPONENT_FAMILIES[component],
                    "ready": ready,
                    "ready_rate": ready / self.stock_months if self.stock_months else None,
                    "reason_counts": dict(sorted(reasons.items())),
                }
            )
        return {
            "stock_months": self.stock_months,
            "component_opportunities": self.stock_months * len(COMPONENT_REQUIREMENTS),
            "ready_component_opportunities": self.ready_component_total,
            "ready_component_rate": self.ready_component_total
            / (self.stock_months * len(COMPONENT_REQUIREMENTS))
            if self.stock_months
            else None,
            "components": components,
            "all_accounting_components_ready_by_family": {
                family: {
                    "stock_months": self.family_all_ready[family],
                    "rate": self.family_all_ready[family] / self.stock_months
                    if self.stock_months
                    else None,
                }
                for family in sorted(set(COMPONENT_FAMILIES.values()))
            },
        }


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _accounting_facts(rows: Sequence[Mapping[str, Any]]) -> list[AccountingFact]:
    return [
        AccountingFact(
            fiscal_period_end=date.fromisoformat(str(row["fiscal_period_end"])),
            period_type=str(row["period_type"]),
            concept=str(row["concept"]),
            unit=str(row["unit"]),
            model_available_at=_parse_datetime(str(row["model_available_at"])),
            revision_version=int(row["revision_version"]),
            record_id=str(row.get("formula_lineage_sha256") or row.get("record_id") or index),
        )
        for index, row in enumerate(rows)
    ]


def _load_denominator(
    database: Path,
) -> tuple[dict[str, list[date]], dict[str, list[date]], dict[str, str]]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        dates_by_security: dict[str, list[date]] = defaultdict(list)
        for row in connection.execute(
            "SELECT security_id, asof_date FROM multifactor_scores ORDER BY asof_date, security_id"
        ):
            dates_by_security[row["security_id"]].append(date.fromisoformat(row["asof_date"]))
        vendor_to_security = {}
        for row in connection.execute(
            """
            SELECT security_id, identifier_value
            FROM security_identifiers
            WHERE identifier_type = 'FIGI_SHARE_CLASS'
            ORDER BY security_id, valid_from
            """
        ):
            vendor_to_security[str(row["identifier_value"])] = str(row["security_id"])
        dates_by_vendor = {
            vendor_id: dates_by_security.get(security_id, [])
            for vendor_id, security_id in vendor_to_security.items()
        }
    return dates_by_security, dates_by_vendor, vendor_to_security


def _audit_v1(database: Path, dates_by_security: Mapping[str, Sequence[date]]) -> Coverage:
    coverage = Coverage()
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        for security_id, dates in dates_by_security.items():
            rows = list(
                connection.execute(
                    """
                    SELECT fiscal_period_end, period_type, standardized_concept AS concept,
                           unit, model_available_at, revision_version,
                           fundamental_id AS record_id
                    FROM fundamentals
                    WHERE security_id = ?
                    ORDER BY model_available_at, fiscal_period_end, fundamental_id
                    """,
                    (security_id,),
                )
            )
            facts = [
                AccountingFact(
                    fiscal_period_end=date.fromisoformat(row["fiscal_period_end"]),
                    period_type=row["period_type"],
                    concept=row["concept"],
                    unit=row["unit"],
                    model_available_at=_parse_datetime(row["model_available_at"]),
                    revision_version=int(row["revision_version"]),
                    record_id=row["record_id"],
                )
                for row in rows
            ]
            for prediction_date in dates:
                coverage.add(component_statuses(facts, prediction_date))
    return coverage


def _write_bundle(
    *,
    equity_bundle: Path,
    expected_equity_manifest_hash: str,
    sec_root: Path,
    expected_sec_registry_hash: str,
    filing_root: Path,
    expected_filing_plan_hash: str,
    output: Path,
    created_at: datetime,
    database: Path,
) -> tuple[dict[str, Any], Coverage, Coverage]:
    equity_manifest_body, _ = _load_pinned_json(
        equity_bundle / "manifest.json", expected_equity_manifest_hash
    )
    sec_registry_body, sec_registry = _load_pinned_json(
        sec_root / "registry.json", expected_sec_registry_hash
    )
    filing_registry_body = (filing_root / "registry.json").read_bytes()
    filing_registry = json.loads(filing_registry_body)
    if sec_registry.get("status") != "complete" or filing_registry.get("status") != "complete":
        raise ValueError("SEC source registries must be complete")
    if filing_registry.get("filing_plan_sha256") != expected_filing_plan_hash:
        raise ValueError("SEC filing evidence is bound to a different plan")

    identities = _equity_identities(equity_bundle)
    filing_rows = _filing_evidence(filing_root, expected_filing_plan_hash)
    completions = {
        path.parent.name.removeprefix("CIK"): path
        for path in sec_root.glob("CIK*/complete.json")
    }
    dates_by_security, dates_by_vendor, vendor_to_security = _load_denominator(database)
    baseline = _audit_v1(database, dates_by_security)
    expanded = Coverage()
    audited_security_ids = set()

    output.mkdir(parents=True, exist_ok=True)
    facts_path = output / "fundamentals.json"
    lineage_path = output / "formula-lineage.jsonl.gz"
    facts_tmp = facts_path.with_suffix(".json.tmp")
    lineage_tmp = lineage_path.with_suffix(".gz.tmp")
    counts: Counter[str] = Counter()
    first_fact = True
    with facts_tmp.open("wb") as facts_handle, lineage_tmp.open("wb") as lineage_raw:
        facts_handle.write(b"[\n")
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=lineage_raw, mtime=0
        ) as lineage_handle:
            for cik, vendor_ids in identities.items():
                completion = completions.get(cik)
                if completion is None:
                    counts["missing_company_source"] += len(vendor_ids)
                    for vendor_id in vendor_ids:
                        security_id = vendor_to_security.get(vendor_id)
                        if security_id:
                            audited_security_ids.add(security_id)
                        for prediction_date in dates_by_vendor.get(vendor_id, ()):
                            expanded.add(component_statuses((), prediction_date))
                    continue
                observations = _observations_for_cik(
                    cik=cik,
                    completion_path=completion,
                    filing_rows=filing_rows,
                    counts=counts,
                )
                issuer_rows = _issuer_rows(observations, counts)
                for vendor_id in vendor_ids:
                    vendor_rows = []
                    for source_row in issuer_rows:
                        row = dict(source_row)
                        lineage = row.pop("_lineage")
                        row.pop("concept_priority", None)
                        row["vendor_id"] = vendor_id
                        vendor_rows.append(row)
                        payload = json.dumps(
                            row, sort_keys=True, separators=(",", ":")
                        ).encode("utf-8")
                        if not first_fact:
                            facts_handle.write(b",\n")
                        facts_handle.write(payload)
                        first_fact = False
                        lineage_row = {
                            "vendor_id": vendor_id,
                            "fiscal_period_end": row["fiscal_period_end"],
                            "period_type": row["period_type"],
                            "concept": row["concept"],
                            "unit": row["unit"],
                            "revision_version": row["revision_version"],
                            "formula_lineage_sha256": row[
                                "formula_lineage_sha256"
                            ],
                            **lineage,
                        }
                        lineage_handle.write(
                            (
                                json.dumps(
                                    lineage_row,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                                + "\n"
                            ).encode("utf-8")
                        )
                        counts["fact_count"] += 1
                        counts["lineage_row_count"] += 1
                        counts[
                            "derived_fact_count"
                            if row["derivation_type"] != "REPORTED"
                            else "reported_fact_count"
                        ] += 1
                        counts[f"concept_fact_count::{row['concept']}"] += 1
                    security_id = vendor_to_security.get(vendor_id)
                    if security_id:
                        audited_security_ids.add(security_id)
                    facts = _accounting_facts(vendor_rows)
                    for prediction_date in dates_by_vendor.get(vendor_id, ()):
                        expanded.add(component_statuses(facts, prediction_date))
        facts_handle.write(b"\n]\n")
    facts_tmp.replace(facts_path)
    lineage_tmp.replace(lineage_path)

    # Securities without a FIGI or source identity remain explicit SOURCE_MISSING.
    for security_id, dates in dates_by_security.items():
        if security_id in audited_security_ids:
            continue
        counts["denominator_security_without_v2_identity"] += 1
        for prediction_date in dates:
            expanded.add(component_statuses((), prediction_date))

    facts_hash = _sha256_file(facts_path)
    lineage_hash = _sha256_file(lineage_path)
    field_names = (
        "vendor_id",
        "fiscal_period_end",
        "fiscal_year",
        "fiscal_quarter",
        "period_type",
        "form_type",
        "filing_accession",
        "filed_at",
        "accepted_at",
        "public_release_at",
        "vendor_available_at",
        "model_available_at",
        "revision_version",
        "concept",
        "value",
        "unit",
    )
    manifest = {
        "schema_version": "point-in-time-fundamentals-bundle-v1",
        "bundle_version": "sec-fundamentals-bundle-v2",
        "vendor": "SEC EDGAR Primary",
        "dataset": "model_v2_companyfacts_with_discrete_quarter_repairs_v1",
        "license_tag": "public_source_internal_research",
        "license_evidence_uri": "https://www.sec.gov/os/accessing-edgar-data",
        "vendor_identifier_type": "FIGI_SHARE_CLASS",
        "concept_map_version": "sec-companyfacts-model-v2-v1",
        "field_map": {name: name for name in field_names},
        "concept_map": {concept: concept for concept in sorted(CONCEPT_SOURCES)},
        "fundamentals_file": {
            "path": facts_path.name,
            "sha256": facts_hash,
            "retrieved_at": created_at.isoformat().replace("+00:00", "Z"),
            "source_uri": "private://sec-edgar/companyfacts-and-filing-index-evidence",
        },
        "formula_lineage_file": {
            "path": lineage_path.name,
            "sha256": lineage_hash,
            "row_count": counts["lineage_row_count"],
            "deterministic_gzip_mtime": 0,
        },
        "accounting_history_contract": {
            "version": ACCOUNTING_HISTORY_VERSION,
            "formula_version": FORMULA_VERSION,
            "fiscal_buffer_start": BUFFER_START.isoformat(),
            "window_end": WINDOW_END.isoformat(),
            "outcome_columns_read": [],
            "selection_basis": "accounting meaning, SEC availability, and missingness only",
            "equity_manifest_sha256": _sha256(equity_manifest_body),
            "sec_source_registry_sha256": _sha256(sec_registry_body),
            "filing_evidence_registry_sha256": _sha256(filing_registry_body),
            "filing_plan_sha256": expected_filing_plan_hash,
            "normalization_counts": dict(sorted(counts.items())),
        },
        "standardized_concept_sources": {
            key: list(value) for key, value in sorted(CONCEPT_SOURCES.items())
        },
    }
    manifest_body = _json_bytes(manifest)
    _atomic_write(output / "manifest.json", manifest_body)
    return (
        {
            "manifest": manifest,
            "manifest_sha256": _sha256(manifest_body),
            "facts_sha256": facts_hash,
            "lineage_sha256": lineage_hash,
            "counts": dict(sorted(counts.items())),
        },
        baseline,
        expanded,
    )


def _coverage_comparison(baseline: Mapping[str, Any], expanded: Mapping[str, Any]) -> dict[str, Any]:
    baseline_components = {row["component"]: row for row in baseline["components"]}
    expanded_components = {row["component"]: row for row in expanded["components"]}
    return {
        component: {
            "sprint9_accounting_input_ready_rate": baseline_components[component][
                "ready_rate"
            ],
            "model_v2_accounting_input_ready_rate": expanded_components[component][
                "ready_rate"
            ],
            "absolute_improvement": expanded_components[component]["ready_rate"]
            - baseline_components[component]["ready_rate"],
        }
        for component in sorted(baseline_components)
    }


def render_markdown(document: Mapping[str, Any]) -> str:
    baseline = document["coverage"]["sprint9_bundle_v1"]
    expanded = document["coverage"]["model_v2_bundle_v2"]
    counts = document["bundle"]["normalization_counts"]
    lines = [
        "# Model V2 Accounting Coverage v1",
        "",
        "`claims_eligible=false`",
        "",
        f"- Decision: `{document['decision']}`",
        f"- Intended stock-months: `{expanded['stock_months']:,}`",
        "- Sprint 9 accounting-input readiness: "
        f"`{baseline['ready_component_rate'] * 100:.2f}%`",
        "- Model V2 accounting-input readiness: "
        f"`{expanded['ready_component_rate'] * 100:.2f}%`",
        "- Absolute improvement: "
        f"`{document['material_improvement']['absolute_improvement'] * 100:.2f} pp`",
        "",
        "## Decision",
        "",
        "The accounting-history expansion derives Q2 and Q3 discrete cash-flow "
        "periods from adjacent filed YTD facts and Q4 from filed annual minus Q3 "
        "YTD. Every derived row is available no earlier than its latest input "
        "filing and carries input accessions, values, units, raw hashes, formula "
        "version, and a content-addressed lineage record. No return or score field "
        "is read.",
        "",
        "## Like-for-like accounting readiness",
        "",
        "| Component | Family | Sprint 9 | Model V2 | Improvement |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    comparison = document["component_comparison"]
    expanded_components = {row["component"]: row for row in expanded["components"]}
    for component, values in comparison.items():
        lines.append(
            f"| `{component}` | {expanded_components[component]['family']} | "
            f"{values['sprint9_accounting_input_ready_rate'] * 100:.2f}% | "
            f"{values['model_v2_accounting_input_ready_rate'] * 100:.2f}% | "
            f"{values['absolute_improvement'] * 100:+.2f} pp |"
        )
    lines.extend(
        [
            "",
            "These rates test only whether the point-in-time accounting inputs "
            "needed by the frozen generic formulas exist. They do not test values, "
            "returns, branch normalization, or final score eligibility.",
            "",
            "## Remaining missingness",
            "",
            "| Component | Largest remaining reason | Stock-months |",
            "| --- | --- | ---: |",
        ]
    )
    for row in expanded["components"]:
        missing = {
            key: value
            for key, value in row["reason_counts"].items()
            if key != "READY"
        }
        reason, count = (
            max(missing.items(), key=lambda item: item[1])
            if missing
            else ("NONE", 0)
        )
        lines.append(f"| `{row['component']}` | `{reason}` | {count:,} |")
    lines.extend(
        [
            "",
            "Missing rows remain missing. The audit never fills a value from a "
            "later filing, a different unit, another issuer, or a cross-branch median.",
            "",
            "## Bundle reconciliation",
            "",
            f"The v2 bundle contains `{counts['fact_count']:,}` facts: "
            f"`{counts['reported_fact_count']:,}` reported facts and "
            f"`{counts['derived_fact_count']:,}` accepted derived facts. Its "
            f"formula-lineage ledger contains exactly `{counts['lineage_row_count']:,}` "
            "rows. Direct reported values won "
            f"`{counts['reported_derived_collisions']:,}` reported/derived collisions. "
            f"The declared concept-priority order resolved "
            f"`{counts['same_accession_value_conflicts']:,}` same-accession synonym "
            "collisions; those alternatives are not averaged or selected by coverage.",
            "",
            "## Controls",
            "",
            "- Filing `accepted_at + 1 hour` remains the earliest model-availability time.",
            "- Later comparative filings create append-only revisions; they never rewrite an earlier as-of view.",
            "- Direct discrete facts take precedence over derived values when both exist.",
            "- Synonymous SEC concepts use the declared accounting-priority order in the bundle manifest.",
            "- Missing source, unit conflicts, stale filings, insufficient quarterly history, insufficient prior TTM history, and insufficient balance-sheet history remain distinct reasons.",
            "- Momentum and risk remain price-derived; no accounting proxy is invented for them.",
            "- Candidate bank, insurer, and REIT concepts are preserved for the Sprint 10.4 formula lock, but no branch formula is selected here.",
            f"- The pass threshold was fixed at a `{document['material_improvement']['minimum_absolute_improvement'] * 100:.0f}` percentage-point like-for-like readiness gain; the observed gain was `{document['material_improvement']['absolute_improvement'] * 100:.2f}` points.",
            "",
            "## Artifacts",
            "",
            f"- Bundle: `{document['bundle']['path']}`",
            f"- Bundle manifest SHA-256: `{document['bundle']['manifest_sha256']}`",
            f"- Fundamentals SHA-256: `{document['bundle']['fundamentals_sha256']}`",
            f"- Formula-lineage SHA-256: `{document['bundle']['formula_lineage_sha256']}`",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--equity-bundle", type=Path, default=DEFAULT_EQUITY_BUNDLE)
    parser.add_argument("--expected-equity-manifest-hash", required=True)
    parser.add_argument("--sec-root", type=Path, default=DEFAULT_SEC_ROOT)
    parser.add_argument("--expected-sec-registry-hash", required=True)
    parser.add_argument("--filing-root", type=Path, default=DEFAULT_FILING_ROOT)
    parser.add_argument("--expected-filing-plan-hash", required=True)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument(
        "--refresh-report-only",
        action="store_true",
        help="Refresh report code metadata after verifying the already-built bundle.",
    )
    parser.add_argument("--created-at", required=True, type=_timestamp)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        if args.refresh_report_only:
            report = json.loads(args.report_output.read_text(encoding="utf-8"))
            manifest_path = args.output / "manifest.json"
            manifest_body = manifest_path.read_bytes()
            manifest = json.loads(manifest_body)
            if _sha256(manifest_body) != report["bundle"]["manifest_sha256"]:
                raise ValueError("existing accounting bundle manifest hash differs")
            for report_key, manifest_key in (
                ("fundamentals_sha256", "fundamentals_file"),
                ("formula_lineage_sha256", "formula_lineage_file"),
            ):
                path = args.output / manifest[manifest_key]["path"]
                actual = _sha256_file(path)
                if actual != report["bundle"][report_key] or actual != manifest[
                    manifest_key
                ]["sha256"]:
                    raise ValueError(f"existing accounting bundle {manifest_key} differs")
            report["code_revision"] = get_code_revision()
            _atomic_write(args.report_output, _json_bytes(report))
            _atomic_write(
                args.markdown_output, render_markdown(report).encode("utf-8")
            )
            print(
                json.dumps(
                    {
                        "decision": report["decision"],
                        "manifest_sha256": report["bundle"]["manifest_sha256"],
                        "refreshed_report_only": True,
                    },
                    sort_keys=True,
                )
            )
            return 0 if report["decision"] == "PASS" else 1
        bundle, baseline_coverage, expanded_coverage = _write_bundle(
            equity_bundle=args.equity_bundle,
            expected_equity_manifest_hash=args.expected_equity_manifest_hash,
            sec_root=args.sec_root,
            expected_sec_registry_hash=args.expected_sec_registry_hash,
            filing_root=args.filing_root,
            expected_filing_plan_hash=args.expected_filing_plan_hash,
            output=args.output,
            created_at=args.created_at,
            database=args.database,
        )
        baseline = baseline_coverage.document()
        expanded = expanded_coverage.document()
        absolute_improvement = (
            expanded["ready_component_rate"] - baseline["ready_component_rate"]
        )
        materially_improved = absolute_improvement >= MATERIAL_IMPROVEMENT_FLOOR
        report = {
            "schema_version": "model-v2-accounting-coverage-v1",
            "claims_eligible": False,
            "generated_at": args.created_at.isoformat().replace("+00:00", "Z"),
            "code_revision": get_code_revision(),
            "decision": "PASS" if materially_improved else "FAIL",
            "material_improvement": {
                "minimum_absolute_improvement": MATERIAL_IMPROVEMENT_FLOOR,
                "absolute_improvement": absolute_improvement,
                "passes": materially_improved,
            },
            "bundle": {
                "path": repository_relative_path(args.output),
                "manifest_sha256": bundle["manifest_sha256"],
                "fundamentals_sha256": bundle["facts_sha256"],
                "formula_lineage_sha256": bundle["lineage_sha256"],
                "normalization_counts": bundle["counts"],
            },
            "source_bindings": bundle["manifest"]["accounting_history_contract"],
            "coverage": {
                "sprint9_bundle_v1": baseline,
                "model_v2_bundle_v2": expanded,
            },
            "component_comparison": _coverage_comparison(baseline, expanded),
            "outcome_blinding": {
                "return_or_outcome_columns_read": [],
                "score_value_columns_read": [],
                "denominator_columns_read": ["security_id", "asof_date"],
                "selection_basis": "accounting validity and point-in-time coverage",
            },
        }
        _atomic_write(args.report_output, _json_bytes(report))
        _atomic_write(args.markdown_output, render_markdown(report).encode("utf-8"))
    except (KeyError, OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"Model V2 accounting bundle build failed: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "decision": report["decision"],
                "fact_count": bundle["counts"]["fact_count"],
                "derived_fact_count": bundle["counts"]["derived_fact_count"],
                "sprint9_ready_rate": baseline["ready_component_rate"],
                "model_v2_ready_rate": expanded["ready_component_rate"],
                "absolute_improvement": absolute_improvement,
                "manifest_sha256": bundle["manifest_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0 if report["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
