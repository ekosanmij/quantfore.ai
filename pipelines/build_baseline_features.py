"""Build and store baseline price features for one ticker.

Example:
    python pipelines/build_baseline_features.py MSFT --asof-date 2026-06-24
"""

from __future__ import annotations

import argparse
import hashlib
import json
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
from quantfore_research.models import (
    Feature,
    FeatureSet,
    Price,
    Security,
    SourceSnapshot,
    UniverseDefinition,
    UniverseMembership,
)
from quantfore_research.validation.leakage import (
    construct_point_in_time_baseline_features,
    prediction_timestamp_for_date,
)


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


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("prediction timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


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
    parser.add_argument(
        "--universe-id",
        help="Enable the point-in-time membership and ticker guard.",
    )
    parser.add_argument(
        "--prediction-timestamp",
        type=parse_timestamp,
        help="UTC availability boundary; defaults to end of --asof-date.",
    )
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
        point_in_time_config = None
        prediction_timestamp = args.prediction_timestamp or prediction_timestamp_for_date(
            asof_date
        )
        if prediction_timestamp.date() != asof_date:
            raise ValueError("--prediction-timestamp date must equal --asof-date")
        if args.universe_id:
            point_in_time_result = construct_point_in_time_baseline_features(
                session,
                universe_id=args.universe_id,
                ticker=ticker,
                prediction_timestamp=prediction_timestamp,
                source_snapshot_id=args.source_snapshot_id,
            )
            security = point_in_time_result.inputs.context.security
            source_snapshot = point_in_time_result.inputs.source_snapshot
            calculated_features = point_in_time_result.values
            evidence_document = [
                {
                    "input_type": item.input_type,
                    "record_id": item.record_id,
                    "security_id": item.security_id,
                    "model_available_at": item.model_available_at.isoformat(),
                    "membership_effective_from": (
                        item.membership_effective_from.isoformat()
                        if item.membership_effective_from is not None
                        else None
                    ),
                    "membership_effective_to": (
                        item.membership_effective_to.isoformat()
                        if item.membership_effective_to is not None
                        else None
                    ),
                    "price_date": (
                        item.price_date.isoformat()
                        if item.price_date is not None
                        else None
                    ),
                    "source_snapshot_id": item.source_snapshot_id,
                }
                for item in point_in_time_result.inputs.evidence
            ]
            evidence_hash = hashlib.sha256(
                json.dumps(
                    evidence_document, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest()
            point_in_time_config = {
                "enabled": True,
                "universe_id": args.universe_id,
                "prediction_timestamp": prediction_timestamp.isoformat(),
                "membership_id": (
                    point_in_time_result.inputs.context.membership.membership_id
                ),
                "ticker_alias_id": (
                    point_in_time_result.inputs.context.ticker_alias.ticker_alias_id
                ),
                "price_input_count": len(point_in_time_result.inputs.prices),
                "maximum_price_date": max(
                    row.date for row in point_in_time_result.inputs.prices
                ).isoformat(),
                "evidence_sha256": evidence_hash,
            }
        else:
            if args.prediction_timestamp is not None:
                raise ValueError(
                    "--prediction-timestamp requires --universe-id"
                )
            security = session.scalar(select(Security).where(Security.ticker == ticker))
            if security is None:
                raise ValueError(f"unknown ticker: {ticker}")
            protected_universe_id = session.scalar(
                select(UniverseDefinition.universe_id)
                .join(
                    UniverseMembership,
                    UniverseMembership.universe_id
                    == UniverseDefinition.universe_id,
                )
                .where(UniverseMembership.security_id == security.security_id)
                .where(UniverseMembership.effective_from <= asof_date)
                .where(
                    (UniverseMembership.effective_to.is_(None))
                    | (UniverseMembership.effective_to >= asof_date)
                )
                .where(UniverseDefinition.window_start <= asof_date)
                .where(UniverseDefinition.window_end >= asof_date)
                .limit(1)
            )
            if protected_universe_id is not None:
                raise ValueError(
                    "point-in-time member/date requires --universe-id="
                    f"{protected_universe_id} and its historical ticker"
                )
            source_snapshot = select_price_source_snapshot(
                session,
                security_id=security.security_id,
                ticker=ticker,
                asof_date=asof_date,
                source_snapshot_id=args.source_snapshot_id,
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
        feature_set_id = args.feature_set_id or default_feature_set_id(
            ticker=ticker,
            asof_date=asof_date,
            version=args.version,
        )
        existing_feature_set = session.get(FeatureSet, feature_set_id)
        if existing_feature_set is not None:
            if existing_feature_set.source_snapshot_id == source_snapshot.snapshot_id:
                existing_point_in_time = existing_feature_set.config_json.get(
                    "point_in_time"
                )
                if existing_point_in_time != point_in_time_config:
                    raise ValueError(
                        f"feature set {feature_set_id} has different point-in-time "
                        "evidence; use a new --feature-set-id"
                    )
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

        config_json = {
            "ticker": ticker,
            "features": sorted(calculated_features),
            "lookbacks": {
                "skip_days": 21,
                "six_month_days": 126,
                "twelve_month_days": 252,
            },
            "price_field": "adj_close",
            "source_snapshot_id": source_snapshot.snapshot_id,
        }
        if point_in_time_config is not None:
            config_json["point_in_time"] = point_in_time_config
        feature_set = FeatureSet(
            feature_set_id=feature_set_id,
            name=FEATURE_SET_NAME,
            version=args.version,
            asof_date=asof_date,
            config_json=config_json,
            source_snapshot_id=source_snapshot.snapshot_id,
            code_commit=get_code_commit(),
        )
        session.add(feature_set)
        available_at = (
            prediction_timestamp
            if point_in_time_config is not None
            else available_at_for(asof_date)
        )

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
