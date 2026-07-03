"""Prepare a pre-outcome Sprint 8 holdout lock for a subsequent lock-only commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import REPOSITORY_ROOT, open_research_database
    from evaluate_multifactor_baseline import FROZEN_PROMOTION_THRESHOLDS
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import REPOSITORY_ROOT, open_research_database  # type: ignore
    from pipelines.evaluate_multifactor_baseline import (  # type: ignore
        FROZEN_PROMOTION_THRESHOLDS,
    )

from quantfore_research.evaluation.multifactor_warehouse import (
    build_preoutcome_lock_inputs,
)
from quantfore_research.evaluation.multifactor_contract import (
    HOLDOUT_END_TEXT,
    HOLDOUT_START_TEXT,
)


DEFAULT_OUTPUT = Path("experiments/multifactor-holdout-lock-v1.json")


def _clean_head() -> str:
    status = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    if status:
        raise ValueError("holdout lock preparation requires a clean Git worktree")
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=REPOSITORY_ROOT, text=True
    ).strip()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare the frozen Sprint 8 lock before any holdout outcome exists."
    )
    parser.add_argument("--database-url")
    parser.add_argument("--normalization-run-id", action="append")
    parser.add_argument("--universe-id")
    parser.add_argument("--outcome-source-snapshot-id", action="append", required=True)
    parser.add_argument("--locked-at", required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        code_commit = _clean_head()
        locked_at = datetime.fromisoformat(args.locked_at.replace("Z", "+00:00"))
        if locked_at.tzinfo is None:
            raise ValueError("--locked-at must include a timezone")
        factory = open_research_database(args.database_url)
        with factory() as session:
            inputs = build_preoutcome_lock_inputs(
                session,
                outcome_source_snapshot_ids=args.outcome_source_snapshot_id,
                normalization_run_ids=args.normalization_run_id,
                universe_id=args.universe_id,
            )
        document = {
            "lock_version": "multifactor-holdout-lock-v1",
            "contract_version": "multifactor-baseline-v1",
            "feature_version": "multifactor-v1",
            "normalization_version": "multifactor-normalization-v1",
            "model_version": "multifactor-baseline-v1",
            "holdout_start": HOLDOUT_START_TEXT,
            "holdout_end": HOLDOUT_END_TEXT,
            "claims_eligible": False,
            "locked_at": locked_at.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z"),
            "code_commit": code_commit,
            "promotion_thresholds": FROZEN_PROMOTION_THRESHOLDS,
            "source_snapshot_hashes": list(inputs.source_snapshot_hashes),
            "normalization_run_ids": list(inputs.normalization_run_ids),
            "score_ledger_sha256": inputs.score_ledger_sha256,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(args.output)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"holdout lock preparation failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"lock={args.output} sha256={hashlib.sha256(payload).hexdigest()} "
        "next=commit-this-lock-without-other-source-changes"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
