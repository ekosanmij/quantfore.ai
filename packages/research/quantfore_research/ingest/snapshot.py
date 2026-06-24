"""Create a source_snapshots row for an ingestion run.

Example:
    python -m quantfore_research.ingest.snapshot \
      --vendor fred \
      --dataset macro/series/DGS10 \
      --license-tag public-fred \
      --storage-uri s3://quantfore-raw/fred/DGS10/2026-06-24.json
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from typing import Optional, Sequence

from quantfore_research.db import (
    build_engine,
    create_schema,
    make_session_factory,
    session_scope,
)
from quantfore_research.snapshots import record_source_snapshot, sha256_text


def _parse_retrieved_at(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None

    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record a Quantfore source snapshot for an ingestion run.",
    )
    parser.add_argument("--vendor", default="manual", help="Data vendor or source name.")
    parser.add_argument(
        "--dataset",
        default="bootstrap",
        help="Vendor dataset or internal dataset key.",
    )
    parser.add_argument(
        "--license-tag",
        default="internal",
        help="License or usage-rights tag.",
    )
    parser.add_argument(
        "--storage-uri",
        default="local://bootstrap/source_snapshots",
        help="Raw snapshot URI.",
    )
    parser.add_argument(
        "--hash",
        dest="source_hash",
        help=(
            "Content hash for the raw snapshot. If omitted, a bootstrap "
            "metadata hash is derived from the supplied fields."
        ),
    )
    parser.add_argument("--retrieved-at", help="ISO-8601 retrieval timestamp. Defaults to now.")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL for this run.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    retrieved_at = _parse_retrieved_at(args.retrieved_at) or datetime.now(timezone.utc)
    source_hash = args.source_hash or sha256_text(
        "|".join(
            [
                args.vendor,
                args.dataset,
                args.storage_uri,
                retrieved_at.isoformat(),
            ]
        )
    )

    engine = build_engine(database_url=args.database_url)
    create_schema(engine)
    session_factory = make_session_factory(engine)

    with session_scope(session_factory) as session:
        snapshot = record_source_snapshot(
            session,
            vendor=args.vendor,
            dataset=args.dataset,
            license_tag=args.license_tag,
            source_hash=source_hash,
            storage_uri=args.storage_uri,
            retrieved_at=retrieved_at,
        )

    print(
        "created source_snapshots record "
        f"snapshot_id={snapshot.snapshot_id} "
        f"vendor={snapshot.vendor} "
        f"dataset={snapshot.dataset} "
        f"hash={snapshot.source_hash}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
