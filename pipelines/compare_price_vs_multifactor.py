"""Build the canonical Sprint 8.7 aligned baseline-comparison report."""

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
    from evaluate_multifactor_baseline import validate_holdout_lock
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import get_code_revision  # type: ignore
    from pipelines.evaluate_multifactor_baseline import validate_holdout_lock  # type: ignore

from quantfore_research.evaluation.multifactor_comparison import (
    AttributionComponent,
    MultiModelObservation,
    build_multifactor_comparison,
)


DEFAULT_OUTPUT = Path("reports/comparisons/price-vs-multifactor-v1.json")


def _optional_decimal(value: Any) -> Optional[Decimal]:
    return None if value is None else Decimal(str(value))


def _component(row: dict[str, Any]) -> AttributionComponent:
    normalization = dict(row.get("normalization") or {})
    return AttributionComponent(
        name=str(row["name"]),
        family=str(row["family"]),
        contribution=_optional_decimal(row.get("contribution")),
        raw_value=_optional_decimal(row.get("raw_value")),
        directed_value=_optional_decimal(row.get("directed_value")),
        normalization_scope=str(
            row.get("normalization_scope") or normalization.get("scope") or "NONE"
        ),
        group_label=(
            str(row.get("group_label") or normalization.get("group_label"))
            if row.get("group_label") is not None
            or normalization.get("group_label") is not None
            else None
        ),
        group_count=int(
            row.get("group_count", normalization.get("group_count", 0))
        ),
        group_mean=_optional_decimal(
            row.get("group_mean", normalization.get("group_mean"))
        ),
        group_std=_optional_decimal(
            row.get("group_std", normalization.get("group_std"))
        ),
        missing_reason=(
            str(row["missing_reason"]) if row.get("missing_reason") else None
        ),
        evidence_refs=tuple(
            sorted(str(value) for value in row.get("source_evidence_refs", []))
        ),
    )


def load_comparison_ledger(path: Path) -> tuple[MultiModelObservation, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document.get("observations") if isinstance(document, dict) else document
    if not isinstance(rows, list):
        raise ValueError("comparison ledger must be an array or contain observations[]")
    result = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"comparison observation {index} must be an object")
        family_z = dict(row.get("family_z") or {})
        family_scores = dict(row.get("family_scores") or {})
        result.append(
            MultiModelObservation(
                security_id=str(row["security_id"]),
                ticker=str(row["ticker"]),
                prediction_date=date.fromisoformat(str(row["prediction_date"])),
                sector=str(row.get("sector") or "Unknown"),
                price_score=_optional_decimal(row.get("price_score")),
                multifactor_score=_optional_decimal(row.get("multifactor_score")),
                family_z={
                    str(key): _optional_decimal(value)
                    for key, value in family_z.items()
                },
                family_scores={
                    str(key): _optional_decimal(value)
                    for key, value in family_scores.items()
                },
                missing_data_flags=dict(row.get("missing_data_flags") or {}),
                components=tuple(
                    _component(dict(value)) for value in row.get("components", [])
                ),
                excess_return=_optional_decimal(row.get("excess_return")),
                realised_return=_optional_decimal(row.get("realised_return")),
                benchmark_return=_optional_decimal(row.get("benchmark_return")),
                max_drawdown=_optional_decimal(row.get("max_drawdown")),
                delisted_outcome=bool(row.get("delisted_outcome", False)),
            )
        )
    return tuple(result)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare aligned equal-weight, Sprint 7, and Sprint 8 baselines."
    )
    parser.add_argument("comparison_ledger_json", type=Path)
    parser.add_argument("--lock-json", type=Path)
    parser.add_argument("--expected-lock-hash")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--generated-at")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        observations = load_comparison_ledger(args.comparison_ledger_json)
        lock_hash = validate_holdout_lock(
            observations,
            lock_path=args.lock_json,
            expected_lock_hash=args.expected_lock_hash,
        )
        comparison = build_multifactor_comparison(observations)
        generated_at = (
            datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
            if args.generated_at
            else datetime.now(timezone.utc)
        )
        document = {
            "report_id": "price-vs-multifactor-v1",
            "claims_eligible": False,
            "generated_at": generated_at.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "code_revision": get_code_revision(),
            "holdout_lock_sha256": lock_hash,
            "comparison_ledger_sha256": hashlib.sha256(
                args.comparison_ledger_json.read_bytes()
            ).hexdigest(),
            "comparison": comparison,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(args.output)
        print(
            f"report={args.output} aligned="
            f"{comparison['alignment']['aligned_observations']} "
            f"sha256={hashlib.sha256(payload).hexdigest()}"
        )
        return 0
    except (KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"baseline comparison failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
