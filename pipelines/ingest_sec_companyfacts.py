"""Ingest SEC EDGAR companyfacts into securities, filings, and fundamentals.

Example:
    python pipelines/ingest_sec_companyfacts.py MSFT --cik 0000789019
"""

from __future__ import annotations

import argparse
import json
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import (
        DEFAULT_RAW_DIR,
        fetch_bytes,
        get_or_create_security,
        open_research_database,
        parse_date,
        parse_filed_date,
        sha256_bytes,
        timestamp_slug,
        utc_now,
        write_raw_payload,
    )
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        DEFAULT_RAW_DIR,
        fetch_bytes,
        get_or_create_security,
        open_research_database,
        parse_date,
        parse_filed_date,
        sha256_bytes,
        timestamp_slug,
        utc_now,
        write_raw_payload,
    )

from sqlalchemy import func, or_, select

from quantfore_research.db import session_scope
from quantfore_research.models import (
    Filing,
    Fundamental,
    Security,
    SecurityIdentifier,
)
from quantfore_research.snapshots import record_source_snapshot


DEFAULT_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "NetIncomeLoss",
    "Assets",
    "Liabilities",
    "EntityCommonStockSharesOutstanding",
]
STANDARDIZED_CONCEPTS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "Revenues": "revenue",
    "NetIncomeLoss": "net_income_common",
    "Assets": "total_assets",
    "Liabilities": "total_liabilities",
    "EntityCommonStockSharesOutstanding": "common_shares",
}
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
SEC_USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "QuantforeAIResearch/0.1 research@quantfore.ai",
)


def normalize_cik(value: str) -> str:
    digits = "".join(character for character in value if character.isdigit())
    if not digits:
        raise ValueError("CIK must contain digits")
    return digits.zfill(10)


def sec_archive_uri(cik: str, accession_no: str) -> str:
    cik_number = str(int(cik))
    accession_compact = accession_no.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik_number}/{accession_compact}/{accession_no}.txt"
    )


def filing_storage_uri(ticker: str, accession_no: str, retrieved_at) -> str:
    accession_slug = accession_no.replace("/", "-")
    return (
        f"raw/sec/filings/{ticker}/{accession_slug}/"
        f"{timestamp_slug(retrieved_at)}.txt"
    )


def resolve_sec_security(session, *, ticker: str, name: str, cik: str) -> Security:
    """Prefer the permanent Sprint 7 identity over current ticker matching."""

    candidates = list(
        session.scalars(
            select(Security)
            .outerjoin(
                SecurityIdentifier,
                SecurityIdentifier.security_id == Security.security_id,
            )
            .where(
                or_(
                    Security.cik == cik,
                    (
                        func.upper(SecurityIdentifier.identifier_type) == "CIK"
                    )
                    & (SecurityIdentifier.identifier_value == cik),
                )
            )
            .distinct()
        ).all()
    )
    if len(candidates) > 1:
        raise ValueError(f"CIK {cik} maps ambiguously to multiple securities")
    if candidates:
        return candidates[0]
    return get_or_create_security(
        session,
        ticker=ticker,
        name=name,
        cik=cik,
    )


def fiscal_period(item: dict[str, Any]) -> Optional[str]:
    fy = item.get("fy")
    fp = item.get("fp")
    if fy and fp:
        return f"{fy}-{fp}"
    if fy:
        return str(fy)
    return None


def period_type(item: dict[str, Any]) -> str:
    """Classify SEC facts without conflating quarterly and annual values."""

    start = parse_date(item.get("start"))
    end = parse_date(item.get("end"))
    if start is not None and end is not None:
        duration_days = (end - start).days + 1
        if duration_days <= 120:
            return "QUARTERLY"
        if duration_days >= 300:
            return "ANNUAL"
    form = str(item.get("form") or "").upper()
    fiscal_label = str(item.get("fp") or "").upper()
    if form.startswith("10-K") or fiscal_label == "FY":
        return "ANNUAL"
    return "QUARTERLY"


def supported_period(item: dict[str, Any]) -> bool:
    """Reject YTD flow contexts that cannot be represented as Q/annual/TTM."""

    start = parse_date(item.get("start"))
    end = parse_date(item.get("end"))
    if start is None or end is None:
        return True
    duration_days = (end - start).days + 1
    return duration_days <= 120 or duration_days >= 300


def fiscal_quarter(item: dict[str, Any]) -> Optional[int]:
    if period_type(item) != "QUARTERLY":
        return None
    labels = (str(item.get("fp") or "").upper(), str(item.get("frame") or "").upper())
    for label in labels:
        for quarter in range(1, 5):
            if f"Q{quarter}" in label:
                return quarter
    return None


def revision_versions(
    facts: Sequence[tuple[str, str, dict[str, Any]]],
) -> dict[tuple[str, str, str, str, str], int]:
    """Number later accessions for one fact identity without rewriting v1."""

    accessions_by_fact: dict[tuple[str, str, str, str], set[tuple[str, str]]] = {}
    for concept, unit, item in facts:
        accession = str(item.get("accn") or "")
        end = str(item.get("end") or "")
        filed = str(item.get("filed") or "")
        key = (concept, unit, end, period_type(item))
        accessions_by_fact.setdefault(key, set()).add((filed, accession))

    versions: dict[tuple[str, str, str, str, str], int] = {}
    for key, dated_accessions in accessions_by_fact.items():
        for version, (_, accession) in enumerate(sorted(dated_accessions), start=1):
            versions[(*key, accession)] = version
    return versions


def iter_selected_facts(
    payload: dict[str, Any],
    *,
    concepts: set[str],
    max_records_per_concept: int,
) -> Iterable[tuple[str, str, dict[str, Any]]]:
    for taxonomy in payload.get("facts", {}).values():
        for concept, concept_payload in taxonomy.items():
            if concept not in concepts:
                continue
            for unit, observations in concept_payload.get("units", {}).items():
                sorted_observations = sorted(
                    observations,
                    key=lambda item: (item.get("filed") or "", item.get("end") or ""),
                    reverse=True,
                )
                for item in sorted_observations[:max_records_per_concept]:
                    yield concept, unit, item


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest SEC companyfacts for one company.")
    parser.add_argument("ticker", nargs="?", default="MSFT")
    parser.add_argument("--cik", default="0000789019")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--user-agent", default=SEC_USER_AGENT)
    parser.add_argument(
        "--concept",
        action="append",
        dest="concepts",
        help="SEC concept to keep. May be passed multiple times.",
    )
    parser.add_argument("--max-records-per-concept", type=int, default=20)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    ticker = args.ticker.upper().strip()
    cik = normalize_cik(args.cik)
    concepts = set(args.concepts or DEFAULT_CONCEPTS)
    retrieved_at = utc_now()
    url = SEC_COMPANYFACTS_URL.format(cik=cik)
    payload_bytes = fetch_bytes(url, user_agent=args.user_agent)
    payload = json.loads(payload_bytes)
    source_hash = sha256_bytes(payload_bytes)
    storage_uri = f"raw/sec/companyfacts/{ticker}/{timestamp_slug(retrieved_at)}.json"
    write_raw_payload(args.raw_dir, storage_uri, payload_bytes)

    company_name = payload.get("entityName") or ticker
    session_factory = open_research_database(args.database_url)
    filings_seen = set()
    fundamentals_inserted = 0

    with session_scope(session_factory) as session:
        security = resolve_sec_security(
            session,
            ticker=ticker,
            name=company_name,
            cik=cik,
        )
        snapshot = record_source_snapshot(
            session,
            vendor="SEC EDGAR",
            dataset=f"companyfacts_{ticker}",
            retrieved_at=retrieved_at,
            license_tag="public_source",
            source_hash=source_hash,
            storage_uri=storage_uri,
        )

        selected_facts = [
            fact
            for fact in iter_selected_facts(
                payload,
                concepts=concepts,
                max_records_per_concept=args.max_records_per_concept,
            )
            if supported_period(fact[2])
        ]
        versions = revision_versions(selected_facts)

        for concept, unit, item in selected_facts:
            accession_no = item.get("accn")
            form_type = item.get("form")
            filed_at = parse_filed_date(item.get("filed"))
            period_end = parse_date(item.get("end"))

            if not all((accession_no, form_type, filed_at, period_end)):
                continue

            if accession_no and accession_no not in filings_seen:
                existing_filing = session.scalar(
                    select(Filing).where(Filing.accession_no == accession_no)
                )
                if existing_filing is None:
                    source_url = sec_archive_uri(cik, accession_no)
                    filing_uri = filing_storage_uri(ticker, accession_no, retrieved_at)
                    filing_payload = fetch_bytes(source_url, user_agent=args.user_agent)
                    filing_hash = sha256_bytes(filing_payload)
                    write_raw_payload(args.raw_dir, filing_uri, filing_payload)
                    filing_snapshot = record_source_snapshot(
                        session,
                        vendor="SEC EDGAR",
                        dataset=f"filing_{ticker}_{accession_no}",
                        retrieved_at=retrieved_at,
                        license_tag="public_source",
                        source_hash=filing_hash,
                        storage_uri=filing_uri,
                    )
                    session.add(
                        Filing(
                            security_id=security.security_id,
                            form_type=form_type or "UNKNOWN",
                            filed_at=filed_at or retrieved_at,
                            period_end=period_end,
                            accession_no=accession_no,
                            storage_uri=filing_uri,
                            source_url=source_url,
                            source_snapshot_id=filing_snapshot.snapshot_id,
                        )
                    )
                filings_seen.add(accession_no)

            try:
                value = Decimal(str(item["val"]))
            except (KeyError, InvalidOperation):
                continue

            session.add(
                Fundamental(
                    security_id=security.security_id,
                    fiscal_period=fiscal_period(item),
                    metric=concept,
                    concept=concept,
                    standardized_concept=STANDARDIZED_CONCEPTS.get(
                        concept, f"unmapped:{concept}"
                    ),
                    value=value,
                    unit=unit,
                    period_end=period_end,
                    fiscal_period_end=period_end,
                    fiscal_year=int(item.get("fy") or period_end.year),
                    fiscal_quarter=fiscal_quarter(item),
                    period_type=period_type(item),
                    filed_at=filed_at,
                    # Companyfacts is an independent reconciliation source,
                    # not the licensed historical availability feed.  Its
                    # values become model-visible only when this exact
                    # snapshot was retrieved.
                    available_at=retrieved_at,
                    vendor_available_at=retrieved_at,
                    model_available_at=retrieved_at,
                    form_type=form_type,
                    accession_no=accession_no,
                    filing_accession=accession_no,
                    revision_version=versions[
                        (concept, unit, str(item.get("end") or ""), period_type(item), accession_no)
                    ],
                    source_snapshot_id=snapshot.snapshot_id,
                    source_hash=source_hash,
                )
            )
            fundamentals_inserted += 1

    print(
        f"ingested SEC companyfacts ticker={ticker} "
        f"fundamentals={fundamentals_inserted} "
        f"filings_seen={len(filings_seen)} "
        f"snapshot_id={snapshot.snapshot_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
