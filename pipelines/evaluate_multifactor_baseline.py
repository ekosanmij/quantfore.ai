"""Evaluate Sprint 8 exclusively from verified warehouse records."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import REPOSITORY_ROOT, get_code_revision, open_research_database
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        REPOSITORY_ROOT,
        get_code_revision,
        open_research_database,
    )

from quantfore_research.evaluation.multifactor import evaluate_multifactor_baseline
from quantfore_research.evaluation.multifactor_warehouse import (
    VerifiedEvaluationLedger,
    load_verified_evaluation_ledger,
)


DEFAULT_OUTPUT = Path("reports/backtests/pit_multifactor_baseline_v1.json")
HOLDOUT_START = date(2022, 1, 1)
HOLDOUT_END = date(2025, 12, 31)
FROZEN_PROMOTION_THRESHOLDS = {
    "mean_rank_ic_minimum": "0.03",
    "net_top_minus_bottom_after_25_bps_positive": True,
    "positive_rank_ic_years_minimum": 3,
    "maximum_positive_year_contribution": "0.50",
    "maximum_positive_sector_contribution": "0.50",
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _git_lock_evidence(
    lock_path: Path,
    *,
    code_commit: str,
    repository_root: Path = REPOSITORY_ROOT,
) -> tuple[str, bytes, datetime]:
    try:
        head = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repository_root, text=True
        ).strip()
        status = subprocess.check_output(
            [
                "git",
                "status",
                "--porcelain=v1",
                "--untracked-files=all",
                "--ignore-submodules=none",
            ],
            cwd=repository_root,
            text=True,
        )
        relative = lock_path.resolve().relative_to(repository_root.resolve()).as_posix()
        committed = subprocess.check_output(
            ["git", "show", f"{head}:{relative}"], cwd=repository_root
        )
        committed_at_text = subprocess.check_output(
            ["git", "show", "-s", "--format=%cI", head],
            cwd=repository_root,
            text=True,
        ).strip()
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", code_commit, head],
            cwd=repository_root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        changes = subprocess.check_output(
            ["git", "diff", "--name-only", f"{code_commit}..{head}"],
            cwd=repository_root,
            text=True,
        ).splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError) as exc:
        raise ValueError(
            "holdout lock must be committed inside the current Git revision"
        ) from exc
    if status:
        raise ValueError("holdout evaluation requires a clean Git worktree")
    if set(changes) - {relative}:
        raise ValueError(
            "executing source differs from the locked code commit outside the lock file"
        )
    return code_commit, committed, datetime.fromisoformat(committed_at_text)


def validate_holdout_lock(
    ledger: VerifiedEvaluationLedger,
    *,
    lock_path: Optional[Path],
    expected_lock_hash: Optional[str],
    current_code_revision: Optional[str] = None,
    committed_lock_body: Optional[bytes] = None,
    lock_commit_time: Optional[datetime] = None,
) -> Optional[str]:
    """Bind holdout access to an exact pre-evaluation committed experiment lock."""

    uses_holdout = any(
        HOLDOUT_START <= row.prediction_date <= HOLDOUT_END
        for row in ledger.observations
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
    locked_code_commit = lock.get("code_commit")
    if not isinstance(locked_code_commit, str) or len(locked_code_commit) != 40:
        if current_code_revision is None:
            raise ValueError("holdout lock code_commit must be a full Git commit")
    if current_code_revision is None:
        current_code_revision, committed_lock_body, lock_commit_time = (
            _git_lock_evidence(lock_path, code_commit=locked_code_commit)
        )
    if committed_lock_body != body:
        raise ValueError("holdout lock differs from the copy committed at HEAD")
    if lock_commit_time is None:
        raise ValueError("holdout lock commit time is required")
    required = {
        "lock_version": "multifactor-holdout-lock-v1",
        "contract_version": "multifactor-baseline-v1",
        "feature_version": "multifactor-v1",
        "normalization_version": "multifactor-normalization-v1",
        "model_version": "multifactor-baseline-v1",
        "holdout_start": "2022-01-01",
        "holdout_end": "2025-12-31",
        "claims_eligible": False,
        "promotion_thresholds": FROZEN_PROMOTION_THRESHOLDS,
        "source_snapshot_hashes": list(ledger.source_snapshot_hashes),
        "normalization_run_ids": list(ledger.normalization_run_ids),
        "score_ledger_sha256": ledger.score_ledger_sha256,
        "code_commit": current_code_revision,
    }
    for key, expected in required.items():
        if lock.get(key) != expected:
            raise ValueError(f"holdout lock {key} does not match frozen evidence")
    try:
        locked_at = datetime.fromisoformat(str(lock["locked_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise ValueError("holdout lock locked_at is invalid") from exc
    if _utc(locked_at) > _utc(lock_commit_time):
        raise ValueError("holdout lock timestamp is after its Git commit")
    evaluated_at = ledger.earliest_holdout_evaluated_at
    if evaluated_at is None:
        raise ValueError("holdout evaluation has no verified immutable outcomes")
    if _utc(lock_commit_time) > _utc(evaluated_at):
        raise ValueError("holdout lock commit does not predate holdout evaluation")
    return actual_hash


def _date(value: str) -> date:
    return date.fromisoformat(value)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate verified stored Sprint 8 predictions and outcomes."
    )
    parser.add_argument("--database-url")
    parser.add_argument("--normalization-run-id", action="append")
    parser.add_argument("--universe-id")
    parser.add_argument("--start-date", type=_date)
    parser.add_argument("--end-date", type=_date)
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
            ledger = load_verified_evaluation_ledger(
                session,
                normalization_run_ids=args.normalization_run_id,
                universe_id=args.universe_id,
                start_date=args.start_date,
                end_date=args.end_date,
            )
        lock_hash = validate_holdout_lock(
            ledger,
            lock_path=args.lock_json,
            expected_lock_hash=args.expected_lock_hash,
        )
        evaluation = evaluate_multifactor_baseline(ledger.observations)
        generated_at = (
            datetime.fromisoformat(args.generated_at.replace("Z", "+00:00"))
            if args.generated_at
            else datetime.now(timezone.utc)
        )
        document = {
            "report_id": "pit_multifactor_baseline_v1",
            "claims_eligible": False,
            "generated_at": _utc(generated_at).isoformat().replace("+00:00", "Z"),
            "code_revision": get_code_revision(),
            "holdout_lock_sha256": lock_hash,
            "warehouse_lineage": ledger.lineage_dict(),
            "evaluation": evaluation,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(args.output)
        print(
            f"report={args.output} observations={len(ledger.observations)} "
            f"sha256={hashlib.sha256(payload).hexdigest()}"
        )
        return 0
    except (KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"multi-factor evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
