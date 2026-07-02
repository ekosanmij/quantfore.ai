"""Dataset and universe contracts for synthetic and prototype-real backtests."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from quantfore_research.backtest.contract import BACKTEST_CONTRACT, BacktestContract


SYNTHETIC_DATASET_KIND = "synthetic"
PROTOTYPE_REAL_DATASET_KIND = "prototype_real"
SUPPORTED_DATASET_KINDS = (
    SYNTHETIC_DATASET_KIND,
    PROTOTYPE_REAL_DATASET_KIND,
)
PROTOTYPE_REAL_MODEL_VERSION = "baseline_prototype_real_v0.1"
SYNTHETIC_FEATURE_SET_NAME = "baseline_features"
PROTOTYPE_REAL_FEATURE_SET_NAME = "baseline_features_prototype_real"
REAL_EXPERIMENT_PREFIX = "real_price_"
UNIVERSE_FIELDS = (
    "ticker",
    "company_name",
    "cik",
    "exchange",
    "sector",
    "active_from",
    "active_to",
    "is_benchmark",
    "selection_reason",
)


@dataclass(frozen=True)
class AuditLineage:
    path: str
    sha256: str
    decision: str
    source_snapshot_hashes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "decision": self.decision,
            "source_snapshot_hashes": list(self.source_snapshot_hashes),
        }


@dataclass(frozen=True)
class UniverseDefinition:
    path: str
    sha256: str
    benchmark: str
    ranked_tickers: tuple[str, ...]
    rows: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class BacktestDataset:
    dataset_kind: str
    contract: BacktestContract
    feature_set_name: str
    universe: Optional[UniverseDefinition] = None
    audit: Optional[AuditLineage] = None

    @property
    def is_prototype_real(self) -> bool:
        return self.dataset_kind == PROTOTYPE_REAL_DATASET_KIND


SYNTHETIC_DATASET = BacktestDataset(
    dataset_kind=SYNTHETIC_DATASET_KIND,
    contract=BACKTEST_CONTRACT,
    feature_set_name=SYNTHETIC_FEATURE_SET_NAME,
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_universe_definition(
    path: Path,
    *,
    expected_benchmark: str,
) -> UniverseDefinition:
    """Load the exact CSV bytes and validate benchmark/ranking boundaries."""

    with path.open(encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        if tuple(reader.fieldnames or ()) != UNIVERSE_FIELDS:
            raise ValueError(
                "universe fields must exactly match: " + ",".join(UNIVERSE_FIELDS)
            )
        rows = []
        seen = set()
        for row_number, source_row in enumerate(reader, start=2):
            row = {field: (source_row.get(field) or "").strip() for field in UNIVERSE_FIELDS}
            missing = [field for field, value in row.items() if not value]
            if missing:
                raise ValueError(
                    f"universe row {row_number}: {','.join(missing)} is required"
                )
            row["ticker"] = row["ticker"].upper()
            if row["ticker"] in seen:
                raise ValueError(f"universe contains duplicate ticker {row['ticker']}")
            seen.add(row["ticker"])
            if row["is_benchmark"] not in {"true", "false"}:
                raise ValueError(
                    f"universe row {row_number}: is_benchmark must be true or false"
                )
            rows.append(row)
    if not rows:
        raise ValueError("universe must not be empty")
    benchmark_rows = [row for row in rows if row["is_benchmark"] == "true"]
    if len(benchmark_rows) != 1:
        raise ValueError("universe must contain exactly one benchmark")
    benchmark = benchmark_rows[0]["ticker"]
    if benchmark != expected_benchmark.upper().strip():
        raise ValueError(
            f"universe benchmark {benchmark} does not match --benchmark "
            f"{expected_benchmark.upper().strip()}"
        )
    ranked_tickers = tuple(
        row["ticker"] for row in rows if row["is_benchmark"] == "false"
    )
    if not ranked_tickers:
        raise ValueError("universe must contain ranked securities")
    if benchmark in ranked_tickers:
        raise ValueError("benchmark must be excluded from ranked securities")
    return UniverseDefinition(
        path=str(path),
        sha256=sha256_file(path),
        benchmark=benchmark,
        ranked_tickers=ranked_tickers,
        rows=tuple(rows),
    )


def load_real_data_audit(
    path: Path,
    *,
    universe_sha256: str,
) -> AuditLineage:
    """Require a matching pass or conditional-pass WP6.4 audit."""

    if not path.exists():
        raise ValueError(f"real-data audit file does not exist: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"real-data audit file is invalid: {path}") from exc
    if document.get("dataset_kind") != PROTOTYPE_REAL_DATASET_KIND:
        raise ValueError("real-data audit dataset_kind must be prototype_real")
    if document.get("claims_eligible") is not False:
        raise ValueError("real-data audit must set claims_eligible=false")
    if document.get("universe_file_sha256") != universe_sha256:
        raise ValueError("real-data audit universe hash does not match universe file")
    reconciliation = document.get("reconciliation")
    if not isinstance(reconciliation, dict):
        raise ValueError("real-data audit is missing reconciliation results")
    decision = reconciliation.get("decision")
    if decision == "fail":
        raise ValueError("real-data run refused because the data audit failed")
    if decision not in {"pass", "conditional_pass"}:
        raise ValueError("real-data audit decision must be pass or conditional_pass")
    source_snapshots = document.get("primary_source_snapshots")
    if not isinstance(source_snapshots, list) or not source_snapshots:
        raise ValueError("real-data audit has no primary source snapshots")
    source_snapshot_hashes = []
    for item in source_snapshots:
        if not isinstance(item, dict) or not isinstance(item.get("sha256"), str):
            raise ValueError("real-data audit has invalid source snapshot lineage")
        source_snapshot_hashes.append(item["sha256"])
    return AuditLineage(
        path=str(path),
        sha256=sha256_file(path),
        decision=decision,
        source_snapshot_hashes=tuple(sorted(set(source_snapshot_hashes))),
    )


def resolve_backtest_dataset(
    *,
    dataset_kind: str,
    benchmark: str,
    universe_file: Optional[Path],
    audit_file: Optional[Path],
) -> BacktestDataset:
    """Resolve an unambiguous dataset contract before database access."""

    if dataset_kind not in SUPPORTED_DATASET_KINDS:
        raise ValueError(f"unsupported dataset_kind: {dataset_kind}")
    if dataset_kind == SYNTHETIC_DATASET_KIND:
        if universe_file is not None:
            raise ValueError("synthetic runs must not provide --universe-file")
        if audit_file is not None:
            raise ValueError("synthetic runs must not provide --data-audit-file")
        if benchmark.upper().strip() != BACKTEST_CONTRACT.benchmark:
            raise ValueError(
                f"synthetic benchmark must be {BACKTEST_CONTRACT.benchmark}"
            )
        return SYNTHETIC_DATASET

    if universe_file is None:
        raise ValueError("prototype_real runs require --universe-file")
    if audit_file is None:
        raise ValueError("prototype_real runs require --data-audit-file")
    universe = load_universe_definition(
        universe_file,
        expected_benchmark=benchmark,
    )
    audit = load_real_data_audit(
        audit_file,
        universe_sha256=universe.sha256,
    )
    contract = BacktestContract(
        securities=universe.ranked_tickers,
        benchmark=universe.benchmark,
        frequency=BACKTEST_CONTRACT.frequency,
        rebalance_session=BACKTEST_CONTRACT.rebalance_session,
        minimum_history_sessions=BACKTEST_CONTRACT.minimum_history_sessions,
        evaluation_sessions=BACKTEST_CONTRACT.evaluation_sessions,
        horizon=BACKTEST_CONTRACT.horizon,
        model_version=PROTOTYPE_REAL_MODEL_VERSION,
        minimum_test_periods=BACKTEST_CONTRACT.minimum_test_periods,
        deterministic=True,
    )
    return BacktestDataset(
        dataset_kind=PROTOTYPE_REAL_DATASET_KIND,
        contract=contract,
        feature_set_name=PROTOTYPE_REAL_FEATURE_SET_NAME,
        universe=universe,
        audit=audit,
    )


def validate_experiment_namespace(experiment_id: str, dataset_kind: str) -> None:
    if dataset_kind == PROTOTYPE_REAL_DATASET_KIND:
        if not experiment_id.startswith(REAL_EXPERIMENT_PREFIX):
            raise ValueError(
                f"prototype_real experiment IDs must start with "
                f"{REAL_EXPERIMENT_PREFIX}"
            )
    elif experiment_id.startswith(REAL_EXPERIMENT_PREFIX):
        raise ValueError(
            f"synthetic experiment IDs must not start with {REAL_EXPERIMENT_PREFIX}"
        )
