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
    parser.add_argument("--version", default=FEATURE_VERSION)
    return parser.parse_args(argv)


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

        prices = list(
            session.scalars(
                select(Price)
                .where(Price.security_id == security.security_id)
                .where(Price.date <= asof_date)
                .where(Price.adj_close.is_not(None))
                .order_by(Price.date)
            )
        )
        source_snapshot_ids = {price.source_snapshot_id for price in prices}
        if len(source_snapshot_ids) != 1:
            raise ValueError(
                "baseline feature build requires prices from exactly one source snapshot; "
                f"found {len(source_snapshot_ids)}"
            )

        source_snapshot_id = next(iter(source_snapshot_ids))
        source_snapshot = session.scalar(
            select(SourceSnapshot).where(SourceSnapshot.snapshot_id == source_snapshot_id)
        )
        if source_snapshot is None:
            raise ValueError(f"missing source snapshot: {source_snapshot_id}")

        calculated_features = calculate_baseline_price_features(
            prices,
            asof_date=asof_date,
        )
        feature_set_id = args.feature_set_id or default_feature_set_id(
            ticker=ticker,
            asof_date=asof_date,
            version=args.version,
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
        f"ticker={ticker} asof_date={asof_date} feature_set_id={feature_set_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
