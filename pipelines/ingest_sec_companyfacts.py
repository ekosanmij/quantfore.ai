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

from sqlalchemy import select

from quantfore_research.db import session_scope
from quantfore_research.models import Filing, Fundamental
from quantfore_research.snapshots import record_source_snapshot


DEFAULT_CONCEPTS = [
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "NetIncomeLoss",
    "Assets",
    "Liabilities",
    "EntityCommonStockSharesOutstanding",
]
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


def fiscal_period(item: dict[str, Any]) -> Optional[str]:
    fy = item.get("fy")
    fp = item.get("fp")
    if fy and fp:
        return f"{fy}-{fp}"
    if fy:
        return str(fy)
    return None


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
        security = get_or_create_security(
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

        for concept, unit, item in iter_selected_facts(
            payload,
            concepts=concepts,
            max_records_per_concept=args.max_records_per_concept,
        ):
            accession_no = item.get("accn")
            form_type = item.get("form")
            filed_at = parse_filed_date(item.get("filed"))
            period_end = parse_date(item.get("end"))

            if accession_no and accession_no not in filings_seen:
                existing_filing = session.scalar(
                    select(Filing).where(Filing.accession_no == accession_no)
                )
                if existing_filing is None:
                    session.add(
                        Filing(
                            security_id=security.security_id,
                            form_type=form_type or "UNKNOWN",
                            filed_at=filed_at or retrieved_at,
                            period_end=period_end,
                            accession_no=accession_no,
                            storage_uri=sec_archive_uri(cik, accession_no),
                            source_snapshot_id=snapshot.snapshot_id,
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
                    value=value,
                    unit=unit,
                    period_end=period_end,
                    filed_at=filed_at,
                    available_at=filed_at,
                    form_type=form_type,
                    accession_no=accession_no,
                    source_snapshot_id=snapshot.snapshot_id,
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
