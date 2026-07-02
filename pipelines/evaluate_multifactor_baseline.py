"""Evaluate frozen Sprint 8 scores from a source-bound observation ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import get_code_revision
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import get_code_revision  # type: ignore

from quantfore_research.evaluation.multifactor import (
    MultiFactorEvaluationObservation,
    evaluate_multifactor_baseline,
)


DEFAULT_OUTPUT = Path("reports/backtests/pit_multifactor_baseline_v1.json")
HOLDOUT_START = date(2022, 1, 1)
HOLDOUT_END = date(2025, 12, 31)


def _optional_decimal(value: Any) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


def load_observations(path: Path) -> tuple[MultiFactorEvaluationObservation, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document.get("observations") if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise ValueError("observation ledger must be an array or contain observations[]")
    result = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"observation {index} must be an object")
        result.append(
            MultiFactorEvaluationObservation(
                security_id=str(row["security_id"]),
                ticker=str(row["ticker"]),
                prediction_date=date.fromisoformat(str(row["prediction_date"])),
                sector=str(row.get("sector") or "Unknown"),
                score=_optional_decimal(row.get("score")),
                family_scores={
                    str(key): _optional_decimal(value)
                    for key, value in dict(row.get("family_scores") or {}).items()
                },
                component_coverage=Decimal(str(row["component_coverage"])),
                missing_reasons=tuple(
                    sorted(str(value) for value in row.get("missing_reasons", []))
                ),
                horizon=str(row["horizon"]),
                excess_return=_optional_decimal(row.get("excess_return")),
                realised_return=_optional_decimal(row.get("realised_return")),
                benchmark_return=_optional_decimal(row.get("benchmark_return")),
                max_drawdown=_optional_decimal(row.get("max_drawdown")),
                delisted_outcome=bool(row.get("delisted_outcome", False)),
            )
        )
    return tuple(result)


def validate_holdout_lock(
    observations: Sequence[MultiFactorEvaluationObservation],
    *,
    lock_path: Optional[Path],
    expected_lock_hash: Optional[str],
) -> Optional[str]:
    uses_holdout = any(
        HOLDOUT_START <= row.prediction_date <= HOLDOUT_END for row in observations
    )
    if not uses_holdout:
        return None
    if lock_path is None or not expected_lock_hash:
        raise ValueError("holdout evaluation requires --lock-json and --expected-lock-hash")
    body = lock_path.read_bytes()
    actual_hash = hashlib.sha256(body).hexdigest()
    if actual_hash != expected_lock_hash.lower():
        raise ValueError("holdout lock SHA-256 does not match")
    lock = json.loads(body)
    required = {
        "contract_version": "multifactor-baseline-v1",
        "feature_version": "multifactor-v1",
        "normalization_version": "multifactor-normalization-v1",
        "holdout_start": "2022-01-01",
        "holdout_end": "2025-12-31",
        "claims_eligible": False,
    }
    for key, expected in required.items():
        if lock.get(key) != expected:
            raise ValueError(f"holdout lock {key} does not match frozen contract")
    for key in ("locked_at", "code_commit", "source_snapshot_hashes", "promotion_thresholds"):
        if not lock.get(key):
            raise ValueError(f"holdout lock is missing {key}")
    return actual_hash


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate the frozen Sprint 8 baseline.")
    parser.add_argument("observations_json", type=Path)
    parser.add_argument("--lock-json", type=Path)
    parser.add_argument("--expected-lock-hash")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--generated-at")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        observations = load_observations(args.observations_json)
        lock_hash = validate_holdout_lock(
            observations,
            lock_path=args.lock_json,
            expected_lock_hash=args.expected_lock_hash,
        )
        evaluation = evaluate_multifactor_baseline(observations)
        generated_at = (
            datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
            if args.generated_at
            else datetime.now(timezone.utc)
        )
        document = {
            "report_id": "pit_multifactor_baseline_v1",
            "claims_eligible": False,
            "generated_at": generated_at.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "code_revision": get_code_revision(),
            "holdout_lock_sha256": lock_hash,
            "observation_ledger_sha256": hashlib.sha256(
                args.observations_json.read_bytes()
            ).hexdigest(),
            "evaluation": evaluation,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(args.output)
        print(
            f"report={args.output} observations={len(observations)} "
            f"sha256={hashlib.sha256(payload).hexdigest()}"
        )
        return 0
    except (KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"multi-factor evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
