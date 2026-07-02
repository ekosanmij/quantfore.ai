"""Build the canonical warehouse-verified Sprint 8.7 comparison report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import get_code_revision, open_research_database
    from evaluate_multifactor_baseline import validate_holdout_lock
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import get_code_revision, open_research_database  # type: ignore
    from pipelines.evaluate_multifactor_baseline import validate_holdout_lock  # type: ignore

from quantfore_research.evaluation.multifactor_comparison import (
    build_multifactor_comparison,
)
from quantfore_research.evaluation.multifactor_warehouse import (
    load_verified_comparison_ledger,
)


DEFAULT_OUTPUT = Path("reports/comparisons/price-vs-multifactor-v1.json")


def _date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare verified equal-weight, Sprint 7, and Sprint 8 baselines."
    )
    parser.add_argument("--database-url")
    parser.add_argument("--normalization-run-id", action="append")
    parser.add_argument("--universe-id")
    parser.add_argument("--start-date", type=_date)
    parser.add_argument("--end-date", type=_date)
    parser.add_argument("--price-model-version", default="baseline_v0.1")
    parser.add_argument("--lock-json", type=Path)
    parser.add_argument("--expected-lock-hash")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--generated-at")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        session_factory = open_research_database(args.database_url)
        with session_factory() as session:
            ledger = load_verified_comparison_ledger(
                session,
                normalization_run_ids=args.normalization_run_id,
                universe_id=args.universe_id,
                start_date=args.start_date,
                end_date=args.end_date,
                price_model_version=args.price_model_version,
            )
        lock_hash = validate_holdout_lock(
            ledger.evaluation_ledger,
            lock_path=args.lock_json,
            expected_lock_hash=args.expected_lock_hash,
        )
        comparison = build_multifactor_comparison(ledger.observations)
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
            "warehouse_lineage": ledger.lineage_dict(),
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
