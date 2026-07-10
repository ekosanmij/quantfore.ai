"""Seal one complete monthly batch in the live-forward shadow ledger.

Example:
    python pipelines/create_shadow_predictions.py \
      --prediction-timestamp 2026-07-31T20:00:00Z \
      --normalization-run-id multifactor-v2-2026-07-31 \
      --executable-lock experiments/multifactor-v2-executable-lock-v1.json
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import (
        REPOSITORY_ROOT,
        open_research_database,
    )
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        REPOSITORY_ROOT,
        open_research_database,
    )

from quantfore_research.db import session_scope
from quantfore_research.shadow import (
    create_shadow_prediction_batch,
    load_executable_shadow_lock,
)


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def committed_lock_evidence(
    path: Path,
    *,
    locked_code_commit: str,
    repository_root: Path = REPOSITORY_ROOT,
) -> tuple[str, str]:
    """Prove HEAD contains a lock-only change over the locked implementation."""

    resolved = path.resolve()
    root = repository_root.resolve()
    try:
        relative = resolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise RuntimeError(
            "executable lock must be committed inside the repository"
        ) from exc
    if not relative.startswith("experiments/") or resolved.suffix != ".json":
        raise RuntimeError(
            "executable lock must be a committed experiments/*.json file"
        )
    try:
        execution_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        status = subprocess.check_output(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=root,
            text=True,
        ).splitlines()
        source_changes = [
            line for line in status if not line[3:].startswith("reports/")
        ]
        if source_changes:
            raise RuntimeError(
                "shadow predictions require a clean committed source tree"
            )
        committed_payload = subprocess.check_output(
            ["git", "show", f"{execution_commit}:{relative}"],
            cwd=root,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                locked_code_commit,
                execution_commit,
            ],
            cwd=root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        changed_paths = subprocess.check_output(
            [
                "git",
                "diff",
                "--name-only",
                f"{locked_code_commit}..{execution_commit}",
            ],
            cwd=root,
            text=True,
        ).splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise RuntimeError(
            "executable lock must descend from the locked implementation commit"
        ) from exc
    if set(changed_paths) - {relative}:
        raise RuntimeError(
            "source changed after the implementation commit outside the lock file"
        )
    if resolved.read_bytes() != committed_payload:
        raise RuntimeError(
            "working executable lock bytes differ from the running commit"
        )
    return relative, execution_commit


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seal one immutable monthly shadow prediction batch."
    )
    parser.add_argument("--universe-id", default="sp500-pit-v1")
    parser.add_argument("--prediction-timestamp", required=True, type=_timestamp)
    parser.add_argument("--normalization-run-id", required=True)
    parser.add_argument("--executable-lock", required=True, type=Path)
    parser.add_argument("--database-url")
    return parser.parse_args(argv)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    recorded_at: Optional[datetime] = None,
) -> int:
    args = parse_args(argv)
    try:
        lock_path = args.executable_lock.resolve()
        executable_lock, executable_lock_hash = load_executable_shadow_lock(lock_path)
        implementation = executable_lock.get("implementation")
        if not isinstance(implementation, dict):
            raise ValueError("executable lock must contain implementation bindings")
        code_commit = implementation.get("code_commit")
        if not isinstance(code_commit, str):
            raise ValueError("executable lock must declare implementation.code_commit")
        lock_uri, execution_commit = committed_lock_evidence(
            lock_path, locked_code_commit=code_commit
        )
        session_factory = open_research_database(args.database_url)
        with session_scope(session_factory) as session:
            result = create_shadow_prediction_batch(
                session,
                universe_id=args.universe_id,
                normalization_run_id=args.normalization_run_id,
                prediction_timestamp=args.prediction_timestamp,
                executable_lock=executable_lock,
                executable_lock_uri=lock_uri,
                executable_lock_hash=executable_lock_hash,
                code_commit=code_commit,
                execution_commit=execution_commit,
                recorded_at=recorded_at,
            )
        status = "created" if result.created else "already_sealed"
        print(
            f"shadow_batch_status={status} batch_id={result.batch_id} "
            f"batch_hash={result.batch_hash} "
            f"expected_members={result.expected_member_count} "
            f"scored={result.scored_count} excluded={result.excluded_count}"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"shadow prediction batch failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
