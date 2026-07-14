"""Build the frozen Model V2 point-in-time subtype classification ledger."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import DEFAULT_RAW_DIR, get_code_revision, repository_relative_path
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        DEFAULT_RAW_DIR,
        get_code_revision,
        repository_relative_path,
    )

from quantfore_research.classification.point_in_time_subtypes import (
    ACTIVE_BRANCHES,
    CLASSIFICATION_SYSTEM,
    CLASSIFICATION_VERSION,
    WikipediaClassificationEvidence,
    parse_wikipedia_constituent_classifications,
    route_point_in_time_subtype,
)


DEFAULT_DATABASE = DEFAULT_RAW_DIR / "free-point-in-time/sprint8-prelock-v9/research.db"
DEFAULT_SAMPLE_REGISTRIES = (
    DEFAULT_RAW_DIR / "free-point-in-time/wikipedia-subtype-samples-v1/registry.json",
    DEFAULT_RAW_DIR / "free-point-in-time/wikipedia-membership-samples-v1/registry.json",
)
DEFAULT_LEDGER = Path(
    "experiments/model-v2-point-in-time-subtype-classification-v1.jsonl.gz"
)
DEFAULT_REPORT = Path(
    "reports/data-audits/model-v2-subtype-classification-coverage-v1.json"
)
MINIMUM_COVERAGE = 0.98


@dataclass(frozen=True)
class DatedEvidence:
    as_of_date: date
    revision_id: int
    registry_path: str
    registry_sha256: str
    response_path: str
    response_sha256: str
    ticker: str
    cik: Optional[str]
    sector: str
    subindustry: str
    matched_by: str


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_atomic(path: Path, body: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)
    return _sha256_bytes(body)


def _write_jsonl_gzip(path: Path, rows: Iterable[Mapping[str, Any]]) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as handle:
            for row in rows:
                handle.write(
                    (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode(
                        "utf-8"
                    )
                )
                count += 1
    temporary.replace(path)
    return _sha256_file(path), count


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _normalize_cik(value: Optional[str]) -> Optional[str]:
    if not value or not value.strip().isdigit():
        return None
    return value.strip().zfill(10)


def _load_registries(
    paths: Sequence[Path],
) -> tuple[list[tuple[dict[str, Any], tuple[WikipediaClassificationEvidence, ...]]], list[dict[str, Any]]]:
    samples = []
    sources = []
    seen: set[tuple[str, int]] = set()
    for registry_path in paths:
        registry_body = registry_path.read_bytes()
        registry = json.loads(registry_body)
        registry_hash = _sha256_bytes(registry_body)
        for item in registry["samples"]:
            key = (item["as_of_date"], int(item["revision_id"]))
            if key in seen:
                continue
            seen.add(key)
            response_path = registry_path.parent / item["path"]
            response_body = response_path.read_bytes()
            response_hash = _sha256_bytes(response_body)
            if response_hash != item["sha256"]:
                raise ValueError(f"Wikipedia response hash mismatch: {response_path}")
            response = json.loads(response_body)
            if int(response["parse"]["revid"]) != int(item["revision_id"]):
                raise ValueError(f"Wikipedia response revision mismatch: {response_path}")
            parsed = parse_wikipedia_constituent_classifications(
                response["parse"]["wikitext"]["*"]
            )
            metadata = {
                "as_of_date": item["as_of_date"],
                "revision_id": int(item["revision_id"]),
                "registry_path": repository_relative_path(registry_path),
                "registry_sha256": registry_hash,
                "response_path": repository_relative_path(response_path),
                "response_sha256": response_hash,
            }
            samples.append((metadata, parsed))
        sources.append(
            {
                "path": repository_relative_path(registry_path),
                "sha256": registry_hash,
                "sample_count": int(registry["sample_count"]),
                "membership_identity_exact_match": bool(
                    registry["all_identity_exact_match"]
                ),
            }
        )
    samples.sort(key=lambda value: (value[0]["as_of_date"], value[0]["revision_id"]))
    return samples, sources


def _load_warehouse(connection: sqlite3.Connection) -> dict[str, Any]:
    connection.row_factory = sqlite3.Row
    securities = {
        row["security_id"]: dict(row)
        for row in connection.execute(
            "SELECT security_id, ticker, name, cik FROM securities ORDER BY security_id"
        )
    }
    aliases = [
        dict(row)
        for row in connection.execute(
            """
            SELECT security_id, ticker, effective_from, effective_to, announced_at
            FROM ticker_aliases
            ORDER BY security_id, effective_from, ticker
            """
        )
    ]
    classifications: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(
        """
        SELECT c.classification_id, c.security_id, c.sector, c.industry,
               c.classification_system, c.effective_from, c.effective_to,
               c.model_available_at, c.source_snapshot_id, c.source_hash,
               s.storage_uri AS source_storage_uri
        FROM security_classifications c
        JOIN source_snapshots s ON s.snapshot_id = c.source_snapshot_id
        ORDER BY c.security_id, c.effective_from, c.model_available_at,
                 c.classification_id
        """
    ):
        classifications[row["security_id"]].append(dict(row))
    denominator = [
        (row["security_id"], row["asof_date"])
        for row in connection.execute(
            """
            SELECT security_id, asof_date
            FROM multifactor_scores
            ORDER BY asof_date, security_id
            """
        )
    ]
    return {
        "securities": securities,
        "aliases": aliases,
        "classifications": classifications,
        "denominator": denominator,
    }


def _active_aliases(
    *, aliases: Sequence[Mapping[str, Any]], as_of_date: date
) -> dict[str, set[str]]:
    result: dict[str, set[str]] = defaultdict(set)
    as_of = as_of_date.isoformat()
    for alias in aliases:
        if alias["effective_from"] > as_of:
            continue
        if alias["effective_to"] and alias["effective_to"] < as_of:
            continue
        if str(alias["announced_at"])[:10] > as_of:
            continue
        result[str(alias["ticker"]).upper().replace(".", "-")].add(
            str(alias["security_id"])
        )
    return result


def _map_explicit_evidence(
    *,
    samples: Sequence[tuple[dict[str, Any], tuple[WikipediaClassificationEvidence, ...]]],
    securities: Mapping[str, Mapping[str, Any]],
    aliases: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, list[DatedEvidence]], list[dict[str, Any]]]:
    cik_map: dict[str, set[str]] = defaultdict(set)
    current_ticker_map: dict[str, set[str]] = defaultdict(set)
    for security_id, security in securities.items():
        cik = _normalize_cik(security.get("cik"))
        if cik:
            cik_map[cik].add(security_id)
        current_ticker_map[
            str(security["ticker"]).upper().replace(".", "-")
        ].add(security_id)

    mapped: dict[str, list[DatedEvidence]] = defaultdict(list)
    unresolved: list[dict[str, Any]] = []
    for metadata, records in samples:
        sample_date = date.fromisoformat(metadata["as_of_date"])
        alias_map = _active_aliases(aliases=aliases, as_of_date=sample_date)
        for record in records:
            cik_candidates = cik_map.get(record.cik or "", set())
            dated_ticker_candidates = set(alias_map.get(record.ticker, set()))
            warehouse_ticker_candidates = set(
                current_ticker_map.get(record.ticker, set())
            )
            ticker_candidates = dated_ticker_candidates | warehouse_ticker_candidates
            candidate: Optional[str] = None
            matched_by = ""
            if len(cik_candidates) == 1 and (
                not ticker_candidates or ticker_candidates == cik_candidates
            ):
                candidate = next(iter(cik_candidates))
                if not ticker_candidates:
                    matched_by = "CIK"
                elif candidate in dated_ticker_candidates:
                    matched_by = "CIK_AND_DATED_TICKER"
                else:
                    matched_by = "CIK_AND_WAREHOUSE_TICKER"
            elif len(ticker_candidates) == 1 and not cik_candidates:
                candidate = next(iter(ticker_candidates))
                matched_by = (
                    "DATED_TICKER"
                    if candidate in dated_ticker_candidates
                    else "WAREHOUSE_TICKER"
                )
            elif len(cik_candidates) > 1 and len(ticker_candidates) == 1:
                ticker_candidate = next(iter(ticker_candidates))
                if ticker_candidate in cik_candidates:
                    candidate = ticker_candidate
                    matched_by = (
                        "CIK_DISAMBIGUATED_BY_DATED_TICKER"
                        if candidate in dated_ticker_candidates
                        else "CIK_DISAMBIGUATED_BY_WAREHOUSE_TICKER"
                    )
            if candidate is None:
                unresolved.append(
                    {
                        "as_of_date": metadata["as_of_date"],
                        "revision_id": metadata["revision_id"],
                        "ticker": record.ticker,
                        "cik": record.cik,
                        "cik_candidate_count": len(cik_candidates),
                        "ticker_candidate_count": len(ticker_candidates),
                        "reason": "IDENTITY_CONFLICT"
                        if cik_candidates and ticker_candidates
                        else "IDENTITY_UNRESOLVED",
                    }
                )
                continue
            mapped[candidate].append(
                DatedEvidence(
                    as_of_date=sample_date,
                    revision_id=int(metadata["revision_id"]),
                    registry_path=metadata["registry_path"],
                    registry_sha256=metadata["registry_sha256"],
                    response_path=metadata["response_path"],
                    response_sha256=metadata["response_sha256"],
                    ticker=record.ticker,
                    cik=record.cik,
                    sector=record.sector,
                    subindustry=record.subindustry,
                    matched_by=matched_by,
                )
            )
    for values in mapped.values():
        values.sort(key=lambda value: (value.as_of_date, value.revision_id))
    return mapped, unresolved


def _base_classification(
    values: Sequence[Mapping[str, Any]], as_of_date: date
) -> tuple[Optional[dict[str, Any]], bool]:
    as_of = as_of_date.isoformat()
    candidates = [
        value
        for value in values
        if value["effective_from"] <= as_of
        and (value["effective_to"] is None or value["effective_to"] >= as_of)
        and str(value["model_available_at"])[:10] <= as_of
    ]
    if not candidates:
        return None, False
    latest_key = max(
        (value["effective_from"], str(value["model_available_at"]))
        for value in candidates
    )
    latest = [
        value
        for value in candidates
        if (value["effective_from"], str(value["model_available_at"])) == latest_key
    ]
    labels = {(value["sector"], value["industry"]) for value in latest}
    return dict(latest[-1]), len(labels) > 1


def _explicit_classification(
    values: Sequence[DatedEvidence], as_of_date: date
) -> tuple[Optional[DatedEvidence], bool]:
    candidates = [value for value in values if value.as_of_date <= as_of_date]
    if not candidates:
        return None, False
    latest_date = max(value.as_of_date for value in candidates)
    latest = [value for value in candidates if value.as_of_date == latest_date]
    labels = {(value.sector, value.subindustry) for value in latest}
    return latest[-1], len(labels) > 1


def _source_lineage(value: Optional[Mapping[str, Any]]) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    return {
        key: value[key]
        for key in (
            "classification_id",
            "classification_system",
            "sector",
            "industry",
            "effective_from",
            "effective_to",
            "model_available_at",
            "source_snapshot_id",
            "source_hash",
            "source_storage_uri",
        )
    }


def build_ledger(
    *,
    warehouse: Mapping[str, Any],
    explicit: Mapping[str, Sequence[DatedEvidence]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    securities = warehouse["securities"]
    classifications = warehouse["classifications"]
    rows = []
    monthly: dict[str, Counter[str]] = defaultdict(Counter)
    branches: Counter[str] = Counter(
        {branch: 0 for branch in (*sorted(ACTIVE_BRANCHES), "OTHER_FINANCIAL", "UNKNOWN")}
    )
    reasons: Counter[str] = Counter()
    denominator_digest = hashlib.sha256()
    for security_id, as_of_text in warehouse["denominator"]:
        as_of_date = date.fromisoformat(as_of_text)
        denominator_digest.update(f"{as_of_text}|{security_id}\n".encode("utf-8"))
        base, base_conflict = _base_classification(
            classifications.get(security_id, ()), as_of_date
        )
        evidence, evidence_conflict = _explicit_classification(
            explicit.get(security_id, ()), as_of_date
        )
        route = route_point_in_time_subtype(
            sector=base["sector"] if base else None,
            sic=base["industry"] if base else None,
            explicit_sector=evidence.sector if evidence else None,
            explicit_subindustry=evidence.subindustry if evidence else None,
            conflict=base_conflict or evidence_conflict,
        )
        security = securities[security_id]
        explicit_lineage = None
        if evidence is not None:
            explicit_lineage = asdict(evidence)
            explicit_lineage["as_of_date"] = evidence.as_of_date.isoformat()
        row = {
            "security_id": security_id,
            "ticker": security["ticker"],
            "prediction_date": as_of_text,
            "classification_version": CLASSIFICATION_VERSION,
            "classification_system": CLASSIFICATION_SYSTEM,
            "sector_branch": route.sector_branch,
            "subtype": route.subtype,
            "known_subtype": route.known_subtype,
            "classification_eligible": route.classification_eligible,
            "routing_rule": route.routing_rule,
            "reason_codes": list(route.reason_codes),
            "base_classification": _source_lineage(base),
            "explicit_classification_evidence": explicit_lineage,
        }
        rows.append(row)
        branches[route.sector_branch] += 1
        monthly[as_of_text]["total"] += 1
        monthly[as_of_text]["known" if route.known_subtype else "unknown"] += 1
        monthly[as_of_text][
            "classification_eligible"
            if route.classification_eligible
            else "classification_excluded"
        ] += 1
        reasons.update(route.reason_codes)

    total = len(rows)
    known = sum(1 for row in rows if row["known_subtype"])
    eligible = sum(1 for row in rows if row["classification_eligible"])
    monthly_rows = []
    for as_of_text, counts in sorted(monthly.items()):
        month_total = counts["total"]
        month_known = counts["known"]
        monthly_rows.append(
            {
                "prediction_date": as_of_text,
                "total": month_total,
                "known": month_known,
                "unknown": counts["unknown"],
                "classification_eligible": counts["classification_eligible"],
                "classification_excluded": counts["classification_excluded"],
                "known_coverage": month_known / month_total,
                "passes_98_percent": month_known / month_total >= MINIMUM_COVERAGE,
            }
        )
    metrics = {
        "total_stock_months": total,
        "known_subtype_stock_months": known,
        "unknown_subtype_stock_months": total - known,
        "classification_eligible_stock_months": eligible,
        "classification_excluded_stock_months": total - eligible,
        "known_subtype_coverage": known / total if total else None,
        "minimum_monthly_known_subtype_coverage": min(
            row["known_coverage"] for row in monthly_rows
        ),
        "months_passing_98_percent": sum(
            1 for row in monthly_rows if row["passes_98_percent"]
        ),
        "month_count": len(monthly_rows),
        "branch_stock_month_counts": dict(sorted(branches.items())),
        "reason_code_stock_month_counts": dict(sorted(reasons.items())),
        "monthly": monthly_rows,
        "denominator_key_sha256": denominator_digest.hexdigest(),
    }
    return rows, metrics


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--sample-registry",
        action="append",
        type=Path,
        help="Override the default revision-pinned sample registries; repeat as needed.",
    )
    parser.add_argument("--ledger-output", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument(
        "--generated-at",
        type=_parse_timestamp,
        default=datetime.now(timezone.utc),
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    registry_paths = tuple(args.sample_registry or DEFAULT_SAMPLE_REGISTRIES)
    try:
        samples, sources = _load_registries(registry_paths)
        uri = f"file:{args.database.resolve()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as connection:
            connection.execute("PRAGMA query_only=ON")
            warehouse = _load_warehouse(connection)
        explicit, unresolved = _map_explicit_evidence(
            samples=samples,
            securities=warehouse["securities"],
            aliases=warehouse["aliases"],
        )
        rows, metrics = build_ledger(warehouse=warehouse, explicit=explicit)
        ledger_hash, ledger_rows = _write_jsonl_gzip(args.ledger_output, rows)
        aggregate_pass = metrics["known_subtype_coverage"] >= MINIMUM_COVERAGE
        every_month_pass = (
            metrics["months_passing_98_percent"] == metrics["month_count"]
        )
        report = {
            "schema_version": "model-v2-subtype-classification-coverage-v1",
            "claims_eligible": False,
            "generated_at": args.generated_at.isoformat().replace("+00:00", "Z"),
            "code_revision": get_code_revision(),
            "classification_version": CLASSIFICATION_VERSION,
            "classification_system": CLASSIFICATION_SYSTEM,
            "decision": "PASS" if aggregate_pass and every_month_pass else "FAIL",
            "pass_criteria": {
                "minimum_aggregate_known_subtype_coverage": MINIMUM_COVERAGE,
                "minimum_monthly_known_subtype_coverage": MINIMUM_COVERAGE,
                "aggregate_pass": aggregate_pass,
                "every_month_pass": every_month_pass,
            },
            "scope": {
                "definition": "All outcome-blind security/date keys in multifactor_scores",
                "start": rows[0]["prediction_date"] if rows else None,
                "end": rows[-1]["prediction_date"] if rows else None,
            },
            "warehouse": {
                "path": repository_relative_path(args.database),
                "opened_read_only": True,
            },
            "source_registries": sources,
            "explicit_evidence_mapping": {
                "mapped_security_count": len(explicit),
                "unresolved_identity_record_count": len(unresolved),
                "unresolved_identity_records": unresolved,
            },
            "ledger": {
                "path": repository_relative_path(args.ledger_output),
                "sha256": ledger_hash,
                "row_count": ledger_rows,
                "deterministic_gzip_mtime": 0,
            },
            "metrics": metrics,
            "outcome_blinding": {
                "return_or_outcome_columns_read": [],
                "score_value_columns_read": [],
                "denominator_columns_read": ["security_id", "asof_date"],
            },
        }
        _write_atomic(args.report_output, _json_bytes(report))
    except (KeyError, OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        print(f"Subtype ledger build failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"rows={ledger_rows} coverage={metrics['known_subtype_coverage']:.6f} "
        f"months={metrics['months_passing_98_percent']}/{metrics['month_count']} "
        f"decision={report['decision']}"
    )
    return 0 if report["decision"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
