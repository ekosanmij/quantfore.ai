"""Prepare point-in-time scalar inputs for the branch-aware Model V2 scorer."""

from __future__ import annotations

import argparse
import bisect
import gzip
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from itertools import groupby
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence

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

from quantfore_research.classification.point_in_time_subtypes import CLASSIFICATION_VERSION
from quantfore_research.features.model_v2 import UNIVERSAL_DEFINITIONS, ScalarValue
from quantfore_research.features.model_v2_inputs import (
    AccountingFactValue,
    build_formula_inputs_as_of,
)


DEFAULT_ACCOUNTING_BUNDLE = (
    DEFAULT_RAW_DIR / "free-point-in-time/sec-fundamentals-bundle-v2"
)
DEFAULT_DATABASE = DEFAULT_RAW_DIR / "free-point-in-time/sprint8-prelock-v9/research.db"
DEFAULT_CLASSIFICATION_LEDGER = Path(
    "experiments/model-v2-point-in-time-subtype-classification-v1.jsonl.gz"
)
DEFAULT_OUTPUT = Path("experiments/model-v2-branch-feature-inputs-v1.jsonl.gz")
DEFAULT_MANIFEST = Path("experiments/model-v2-branch-feature-inputs-v1.manifest.json")
DEFAULT_WORK_DATABASE = Path("tmp/model-v2-branch-feature-inputs-v1.sqlite")
UNIVERSAL_NAMES = tuple(row.name for row in UNIVERSAL_DEFINITIONS)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_SOURCES = (
    Path("packages/research/quantfore_research/features/model_v2.py"),
    Path("packages/research/quantfore_research/features/model_v2_inputs.py"),
    Path("pipelines/build_model_v2_score_inputs.py"),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _implementation_sources() -> list[dict[str, str]]:
    return [
        {"path": path.as_posix(), "sha256": _sha256_file(REPOSITORY_ROOT / path)}
        for path in IMPLEMENTATION_SOURCES
    ]


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fact_rows_with_vendor(path: Path) -> Iterator[tuple[str, AccountingFactValue]]:
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            payload = line.strip().rstrip(",")
            if not payload or payload in {"[", "]"}:
                continue
            try:
                row = json.loads(payload)
                yield str(row["vendor_id"]), AccountingFactValue(
                    fiscal_period_end=date.fromisoformat(row["fiscal_period_end"]),
                    period_type=str(row["period_type"]),
                    concept=str(row["concept"]),
                    unit=str(row["unit"]),
                    model_available_at=_parse_datetime(row["model_available_at"]),
                    revision_version=int(row["revision_version"]),
                    record_id=str(row["formula_lineage_sha256"]),
                    value=Decimal(str(row["value"])),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid accounting fact at {path}:{line_number}") from exc


def _classification_rows(path: Path) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid classification row at {path}:{line_number}") from exc
            if row.get("classification_version") != CLASSIFICATION_VERSION:
                raise ValueError("classification ledger version does not match Model V2")
            result[str(row["security_id"])].append(row)
    for rows in result.values():
        rows.sort(key=lambda row: row["prediction_date"])
    return result


def _vendor_security_map(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row["identifier_value"]): str(row["security_id"])
        for row in connection.execute(
            """
            SELECT security_id, identifier_value
            FROM security_identifiers
            WHERE identifier_type = 'FIGI_SHARE_CLASS'
            ORDER BY security_id, valid_from, identifier_value
            """
        )
    }


def _classification_id(row: Mapping[str, Any]) -> str:
    body = json.dumps(row, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"model-v2-classification-sha256:{hashlib.sha256(body).hexdigest()}"


def _security_market_context(
    connection: sqlite3.Connection, security_id: str
) -> tuple[dict[tuple[str, str], dict[str, Any]], list[tuple[str, Any, str]]]:
    universal = {}
    placeholders = ",".join("?" for _ in UNIVERSAL_NAMES)
    for row in connection.execute(
        f"""
        SELECT asof_date, feature_name, raw_value, applicability_status,
               missing_reason, feature_id, available_at
        FROM features
        WHERE security_id = ?
          AND version = 'multifactor-v1'
          AND feature_name IN ({placeholders})
        ORDER BY asof_date, feature_name, feature_id
        """,
        (security_id, *UNIVERSAL_NAMES),
    ):
        key = (str(row["asof_date"]), str(row["feature_name"]))
        if key in universal:
            raise ValueError(f"duplicate universal feature row: {security_id} {key}")
        universal[key] = dict(row)
    prices = [
        (str(row["date"]), row["close"], str(row["price_id"]))
        for row in connection.execute(
            """
            SELECT date, close, price_id
            FROM prices
            WHERE security_id = ? AND close IS NOT NULL
            ORDER BY date, price_id
            """,
            (security_id,),
        )
    ]
    return universal, prices


def _latest_close(
    prices: Sequence[tuple[str, Any, str]], prediction_date: str
) -> Optional[ScalarValue]:
    if not prices:
        return None
    dates = [row[0] for row in prices]
    index = bisect.bisect_right(dates, prediction_date) - 1
    if index < 0:
        return None
    _, value, price_id = prices[index]
    parsed = Decimal(str(value))
    if parsed <= 0:
        return None
    return ScalarValue(parsed, "USD", (price_id,))


def _serialize_scalar(value: ScalarValue) -> dict[str, Any]:
    return {
        "value": str(value.value),
        "unit": value.unit,
        "lineage_ids": list(value.lineage_ids),
    }


def _security_documents(
    *,
    connection: sqlite3.Connection,
    security_id: str,
    classifications: Sequence[Mapping[str, Any]],
    facts: Sequence[AccountingFactValue],
) -> Iterable[dict[str, Any]]:
    universal, prices = _security_market_context(connection, security_id)
    for classification in classifications:
        prediction_date = str(classification["prediction_date"])
        eligible = bool(classification["classification_eligible"])
        document = {
            "security_id": security_id,
            "prediction_date": prediction_date,
            "sector_branch": classification["sector_branch"],
            "classification_eligible": eligible,
            "classification_reason_codes": list(classification["reason_codes"]),
            "classification_id": _classification_id(classification),
        }
        if not eligible:
            yield document
            continue
        accounting = build_formula_inputs_as_of(
            facts,
            date.fromisoformat(prediction_date),
            latest_raw_close=_latest_close(prices, prediction_date),
        )
        document["accounting_inputs"] = {
            name: _serialize_scalar(value) for name, value in sorted(accounting.items())
        }
        price_components = {}
        for name in UNIVERSAL_NAMES:
            row = universal.get((prediction_date, name))
            if (
                row is not None
                and row["applicability_status"] == "APPLICABLE"
                and row["raw_value"] is not None
            ):
                price_components[name] = {
                    "value": str(row["raw_value"]),
                    "unit": "ratio",
                    "lineage_ids": [str(row["feature_id"])],
                }
            else:
                price_components[name] = {
                    "value": None,
                    "reason_code": str(
                        row["missing_reason"] if row is not None else "SOURCE_MISSING"
                    ),
                    "reason_detail": str(
                        row["missing_reason"] if row is not None else "SOURCE_MISSING"
                    ),
                    "lineage_ids": [str(row["feature_id"])] if row is not None else [],
                }
        document["universal_features"] = price_components
        yield document


def _prepare_work_database(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute(
        """
        CREATE TABLE prepared_rows (
            prediction_date TEXT NOT NULL,
            security_id TEXT NOT NULL,
            document TEXT NOT NULL,
            PRIMARY KEY (prediction_date, security_id)
        ) WITHOUT ROWID
        """
    )
    return connection


def _insert_documents(
    connection: sqlite3.Connection, documents: Iterable[Mapping[str, Any]]
) -> int:
    count = 0
    for row in documents:
        connection.execute(
            "INSERT INTO prepared_rows(prediction_date, security_id, document) VALUES (?, ?, ?)",
            (
                row["prediction_date"],
                row["security_id"],
                json.dumps(row, sort_keys=True, separators=(",", ":")),
            ),
        )
        count += 1
    return count


def _write_sorted_output(connection: sqlite3.Connection, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    count = 0
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw_handle, mtime=0) as handle:
            for row in connection.execute(
                "SELECT document FROM prepared_rows ORDER BY prediction_date, security_id"
            ):
                handle.write((str(row[0]) + "\n").encode("utf-8"))
                count += 1
    temporary.replace(path)
    return count


def build_score_inputs(
    *,
    accounting_bundle: Path,
    database: Path,
    classification_ledger: Path,
    output: Path,
    manifest_path: Path,
    work_database: Path,
    keep_work_database: bool = False,
) -> dict[str, Any]:
    facts_path = accounting_bundle / "fundamentals.json"
    bundle_manifest_path = accounting_bundle / "manifest.json"
    classifications = _classification_rows(classification_ledger)
    work = _prepare_work_database(work_database)
    work.row_factory = sqlite3.Row
    database_uri = f"file:{database.resolve()}?mode=ro"
    row_count = 0
    processed_securities = set()
    try:
        with sqlite3.connect(database_uri, uri=True) as warehouse:
            warehouse.row_factory = sqlite3.Row
            warehouse.execute("PRAGMA query_only=ON")
            vendor_to_security = _vendor_security_map(warehouse)
            seen_vendors = set()
            for vendor_id, grouped in groupby(
                _fact_rows_with_vendor(facts_path), key=lambda item: item[0]
            ):
                if vendor_id in seen_vendors:
                    raise ValueError("accounting bundle is not contiguous by vendor_id")
                seen_vendors.add(vendor_id)
                security_id = vendor_to_security.get(vendor_id)
                facts = [fact for _, fact in grouped]
                if security_id is None or security_id not in classifications:
                    continue
                if security_id in processed_securities:
                    raise ValueError("multiple accounting vendors map to one security")
                row_count += _insert_documents(
                    work,
                    _security_documents(
                        connection=warehouse,
                        security_id=security_id,
                        classifications=classifications[security_id],
                        facts=facts,
                    ),
                )
                processed_securities.add(security_id)
                work.commit()
            for security_id in sorted(set(classifications) - processed_securities):
                row_count += _insert_documents(
                    work,
                    _security_documents(
                        connection=warehouse,
                        security_id=security_id,
                        classifications=classifications[security_id],
                        facts=(),
                    ),
                )
            work.commit()
        written = _write_sorted_output(work, output)
        if written != row_count:
            raise AssertionError("prepared input row count does not reconcile")
    finally:
        work.close()

    expected_rows = sum(len(rows) for rows in classifications.values())
    if row_count != expected_rows:
        raise AssertionError(
            f"classification/input reconciliation failed: {row_count} != {expected_rows}"
        )
    manifest = {
        "claims_eligible": False,
        "outcomes_accessed": False,
        "classification_version": CLASSIFICATION_VERSION,
        "universal_feature_version": "multifactor-v1",
        "accounting_selection_version": "model-v2-point-in-time-scalars-v1",
        "inputs": {
            "accounting_bundle_manifest": {
                "path": repository_relative_path(bundle_manifest_path),
                "sha256": _sha256_file(bundle_manifest_path),
            },
            "accounting_facts": {
                "path": repository_relative_path(facts_path),
                "sha256": _sha256_file(facts_path),
            },
            "classification_ledger": {
                "path": repository_relative_path(classification_ledger),
                "sha256": _sha256_file(classification_ledger),
            },
            "warehouse": {
                "path": repository_relative_path(database),
                "sha256": _sha256_file(database),
                "opened_read_only": True,
                "tables_read": [
                    "security_identifiers",
                    "features",
                    "prices",
                ],
            },
        },
        "output": {
            "path": repository_relative_path(output),
            "sha256": _sha256_file(output),
            "rows": row_count,
        },
        "implementation_sources": _implementation_sources(),
        "code_revision": get_code_revision(),
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    if not keep_work_database and work_database.exists():
        work_database.unlink()
        wal = work_database.with_name(work_database.name + "-wal")
        shm = work_database.with_name(work_database.name + "-shm")
        if wal.exists():
            wal.unlink()
        if shm.exists():
            shm.unlink()
    return manifest


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare outcome-blind point-in-time inputs for Model V2 scoring."
    )
    parser.add_argument("--accounting-bundle", type=Path, default=DEFAULT_ACCOUNTING_BUNDLE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument(
        "--classification-ledger", type=Path, default=DEFAULT_CLASSIFICATION_LEDGER
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--work-database", type=Path, default=DEFAULT_WORK_DATABASE)
    parser.add_argument("--keep-work-database", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        manifest = build_score_inputs(
            accounting_bundle=args.accounting_bundle,
            database=args.database,
            classification_ledger=args.classification_ledger,
            output=args.output,
            manifest_path=args.manifest,
            work_database=args.work_database,
            keep_work_database=args.keep_work_database,
        )
    except (OSError, sqlite3.Error, ValueError, AssertionError) as exc:
        print(f"Model V2 input preparation failed: {exc}", file=sys.stderr)
        return 1
    print(f"Model V2 score inputs prepared: rows={manifest['output']['rows']}")
    print(f"Input ledger: {manifest['output']['path']}")
    print(f"Manifest: {repository_relative_path(args.manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
