"""Build and store baseline price features for one ticker.

Example:
    python pipelines/build_baseline_features.py MSFT --asof-date 2026-06-24
"""

from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

import _bootstrap  # noqa: F401
from _common import open_research_database, parse_date

from sqlalchemy import select

from quantfore_research.db import session_scope
from quantfore_research.features import (
    FEATURE_VERSION,
    calculate_baseline_price_features,
)
from quantfore_research.models import Feature, FeatureSet, Price, Security, SourceSnapshot


FEATURE_SET_NAME = "baseline_features"


def get_code_commit() -> Optional[str]:
    try:
        return (
            subprocess.check_output(
                ["git", "rev-parse", "--short", "HEAD"],
                stderr=subprocess.DEVNULL,
                text=True,
            )
            .strip()
            or None
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None


def available_at_for(asof_date) -> datetime:
    return datetime(asof_date.year, asof_date.month, asof_date.day, tzinfo=timezone.utc)


def default_feature_set_id(*, ticker: str, asof_date, version: str) -> str:
    return f"{FEATURE_SET_NAME}_{version}_{ticker}_{asof_date.isoformat()}"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build baseline price features.")
    parser.add_argument("ticker")
    parser.add_argument("--asof-date", required=True, help="YYYY-MM-DD feature as-of date.")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--feature-set-id", help="Override the generated feature set ID.")
    parser.add_argument(
        "--source-snapshot-id",
        help=(
            "Price source snapshot to use. Defaults to the latest snapshot with "
            "adjusted-close prices for the ticker on or before --asof-date."
        ),
    )
    parser.add_argument("--version", default=FEATURE_VERSION)
    return parser.parse_args(argv)


def select_price_source_snapshot(
    session,
    *,
    security_id: str,
    ticker: str,
    asof_date,
    source_snapshot_id: Optional[str],
) -> SourceSnapshot:
    candidate_stmt = (
        select(SourceSnapshot)
        .join(Price, Price.source_snapshot_id == SourceSnapshot.snapshot_id)
        .where(Price.security_id == security_id)
        .where(Price.date <= asof_date)
        .where(Price.adj_close.is_not(None))
    )

    if source_snapshot_id:
        snapshot = session.get(SourceSnapshot, source_snapshot_id)
        if snapshot is None:
            raise ValueError(f"unknown source snapshot: {source_snapshot_id}")

        matching_snapshot = session.scalar(
            candidate_stmt.where(SourceSnapshot.snapshot_id == source_snapshot_id).limit(1)
        )
        if matching_snapshot is None:
            raise ValueError(
                f"source snapshot {source_snapshot_id} has no adjusted-close prices "
                f"for {ticker} on or before {asof_date}"
            )
        return snapshot

    snapshot = session.scalar(
        candidate_stmt.order_by(
            SourceSnapshot.retrieved_at.desc(),
            SourceSnapshot.created_at.desc(),
            SourceSnapshot.snapshot_id.desc(),
        ).limit(1)
    )
    if snapshot is None:
        raise ValueError(
            f"no adjusted-close price snapshot found for {ticker} on or before {asof_date}"
        )
    return snapshot


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    ticker = args.ticker.upper().strip()
    asof_date = parse_date(args.asof_date)
    if asof_date is None:
        raise ValueError("--asof-date is required")

    session_factory = open_research_database(args.database_url)
    with session_scope(session_factory) as session:
        security = session.scalar(select(Security).where(Security.ticker == ticker))
        if security is None:
            raise ValueError(f"unknown ticker: {ticker}")

        source_snapshot = select_price_source_snapshot(
            session,
            security_id=security.security_id,
            ticker=ticker,
            asof_date=asof_date,
            source_snapshot_id=args.source_snapshot_id,
        )
        feature_set_id = args.feature_set_id or default_feature_set_id(
            ticker=ticker,
            asof_date=asof_date,
            version=args.version,
        )
        existing_feature_set = session.get(FeatureSet, feature_set_id)
        if existing_feature_set is not None:
            if existing_feature_set.source_snapshot_id == source_snapshot.snapshot_id:
                print(
                    "feature set already exists; skipping "
                    f"ticker={ticker} asof_date={asof_date} "
                    f"feature_set_id={feature_set_id} "
                    f"source_snapshot_id={source_snapshot.snapshot_id}"
                )
                return 0

            raise ValueError(
                f"feature set {feature_set_id} already exists for source snapshot "
                f"{existing_feature_set.source_snapshot_id}, but selected source snapshot "
                f"{source_snapshot.snapshot_id}; pass --feature-set-id for a new run or "
                "--source-snapshot-id to pin the original snapshot"
            )

        prices = list(
            session.scalars(
                select(Price)
                .where(Price.security_id == security.security_id)
                .where(Price.source_snapshot_id == source_snapshot.snapshot_id)
                .where(Price.date <= asof_date)
                .where(Price.adj_close.is_not(None))
                .order_by(Price.date)
            )
        )

        calculated_features = calculate_baseline_price_features(
            prices,
            asof_date=asof_date,
        )
        feature_set = FeatureSet(
            feature_set_id=feature_set_id,
            name=FEATURE_SET_NAME,
            version=args.version,
            asof_date=asof_date,
            config_json={
                "ticker": ticker,
                "features": sorted(calculated_features),
                "lookbacks": {"skip_days": 21, "six_month_days": 126, "twelve_month_days": 252},
                "price_field": "adj_close",
                "source_snapshot_id": source_snapshot.snapshot_id,
            },
            source_snapshot_id=source_snapshot.snapshot_id,
            code_commit=get_code_commit(),
        )
        session.add(feature_set)
        available_at = available_at_for(asof_date)

        for feature_name, value in calculated_features.items():
            session.add(
                Feature(
                    feature_set_id=feature_set.feature_set_id,
                    security_id=security.security_id,
                    asof_date=asof_date,
                    available_at=available_at,
                    feature_name=feature_name,
                    value=Decimal(value),
                    version=args.version,
                    source_snapshot_id=source_snapshot.snapshot_id,
                    source_hash=source_snapshot.source_hash,
                )
            )

    print(
        f"stored {len(calculated_features)} baseline features "
        f"ticker={ticker} asof_date={asof_date} "
        f"feature_set_id={feature_set_id} source_snapshot_id={source_snapshot.snapshot_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
