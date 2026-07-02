"""Build and store the baseline research score for one ticker.

Example:
    python pipelines/build_baseline_score.py MSFT --asof-date 2026-06-24
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional, Sequence

import _bootstrap  # noqa: F401
from _common import open_research_database, parse_date

from sqlalchemy import select

from quantfore_research.db import session_scope
from quantfore_research.evaluation import parse_horizon
from quantfore_research.models import (
    Feature,
    FeatureSet,
    ModelPrediction,
    ScoreDriver as ScoreDriverRow,
    Security,
    UniverseDefinition,
    UniverseMembership,
)
from quantfore_research.scoring import (
    BASELINE_MODEL_VERSION,
    REQUIRED_FEATURE_NAMES,
    calculate_baseline_score,
    decimal_text,
    immutable_prediction_hash,
)
from quantfore_research.validation.leakage import (
    prediction_timestamp_for_date,
    resolve_point_in_time_security,
    validate_stored_feature_inputs,
)


DEFAULT_HORIZON = "126d"
FEATURE_SET_NAME = "baseline_features"


def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("prediction timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build baseline research score.")
    parser.add_argument("ticker")
    parser.add_argument("--asof-date", required=True, help="YYYY-MM-DD score as-of date.")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--feature-set-id", help="Use an explicit baseline feature set.")
    parser.add_argument("--horizon", default=DEFAULT_HORIZON)
    parser.add_argument("--model-version", default=BASELINE_MODEL_VERSION)
    parser.add_argument(
        "--universe-id",
        help="Required when scoring a point-in-time feature set.",
    )
    parser.add_argument(
        "--prediction-timestamp",
        type=parse_timestamp,
        help="UTC availability boundary; defaults to end of --asof-date.",
    )
    return parser.parse_args(argv)


def select_latest_feature_set(
    session,
    *,
    security_id: str,
    ticker: str,
    asof_date,
) -> FeatureSet:
    feature_set = session.scalar(
        select(FeatureSet)
        .join(Feature, Feature.feature_set_id == FeatureSet.feature_set_id)
        .where(FeatureSet.name == FEATURE_SET_NAME)
        .where(Feature.security_id == security_id)
        .where(Feature.asof_date == asof_date)
        .order_by(
            FeatureSet.created_at.desc(),
            FeatureSet.feature_set_id.desc(),
        )
        .limit(1)
    )
    if feature_set is None:
        raise ValueError(f"no baseline feature set found for {ticker} on {asof_date}")
    return feature_set


def select_feature_set(
    session,
    *,
    security_id: str,
    ticker: str,
    asof_date,
    feature_set_id: Optional[str],
) -> FeatureSet:
    if not feature_set_id:
        return select_latest_feature_set(
            session,
            security_id=security_id,
            ticker=ticker,
            asof_date=asof_date,
        )

    feature_set = session.get(FeatureSet, feature_set_id)
    if feature_set is None:
        raise ValueError(f"unknown feature set: {feature_set_id}")
    if feature_set.name != FEATURE_SET_NAME:
        raise ValueError(
            f"feature set {feature_set_id} is not a {FEATURE_SET_NAME} feature set"
        )

    matching_feature = session.scalar(
        select(Feature)
        .where(Feature.feature_set_id == feature_set_id)
        .where(Feature.security_id == security_id)
        .where(Feature.asof_date == asof_date)
        .limit(1)
    )
    if matching_feature is None:
        raise ValueError(
            f"feature set {feature_set_id} has no features for {ticker} on {asof_date}"
        )

    return feature_set


def load_feature_values(
    session,
    *,
    feature_set_id: str,
    security_id: str,
    asof_date,
    prediction_timestamp,
) -> dict[str, Decimal]:
    feature_rows = list(
        session.scalars(
            select(Feature)
            .where(Feature.feature_set_id == feature_set_id)
            .where(Feature.security_id == security_id)
            .where(Feature.asof_date == asof_date)
            .order_by(Feature.feature_name)
        )
    )
    validate_stored_feature_inputs(
        feature_rows, prediction_timestamp=prediction_timestamp
    )
    feature_values = {row.feature_name: row.value for row in feature_rows}
    missing_features = [
        feature_name
        for feature_name in REQUIRED_FEATURE_NAMES
        if feature_name not in feature_values
    ]
    if missing_features:
        missing = ", ".join(missing_features)
        raise ValueError(f"feature set {feature_set_id} missing score features: {missing}")
    return feature_values


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    parse_horizon(args.horizon)
    ticker = args.ticker.upper().strip()
    asof_date = parse_date(args.asof_date)
    if asof_date is None:
        raise ValueError("--asof-date is required")
    prediction_timestamp = args.prediction_timestamp or prediction_timestamp_for_date(
        asof_date
    )
    if prediction_timestamp.date() != asof_date:
        raise ValueError("--prediction-timestamp date must equal --asof-date")
    if args.prediction_timestamp is not None and not args.universe_id:
        raise ValueError("--prediction-timestamp requires --universe-id")

    session_factory = open_research_database(args.database_url)
    with session_scope(session_factory) as session:
        point_in_time_context = None
        if args.universe_id:
            point_in_time_context = resolve_point_in_time_security(
                session,
                universe_id=args.universe_id,
                ticker=ticker,
                prediction_timestamp=prediction_timestamp,
            )
            security = point_in_time_context.security
        else:
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

        existing_prediction = session.scalar(
            select(ModelPrediction)
            .where(ModelPrediction.model_version == args.model_version)
            .where(ModelPrediction.security_id == security.security_id)
            .where(ModelPrediction.asof_date == asof_date)
            .where(ModelPrediction.horizon == args.horizon)
        )
        if existing_prediction is not None and not args.feature_set_id:
            print(
                "prediction already exists; skipping "
                f"ticker={ticker} asof_date={asof_date} "
                f"model_version={args.model_version} horizon={args.horizon} "
                f"prediction_id={existing_prediction.prediction_id}"
            )
            return 0

        feature_set = select_feature_set(
            session,
            security_id=security.security_id,
            ticker=ticker,
            asof_date=asof_date,
            feature_set_id=args.feature_set_id,
        )
        point_in_time_config = feature_set.config_json.get("point_in_time")
        if point_in_time_config and point_in_time_context is None:
            raise ValueError(
                "point-in-time feature set requires --universe-id and historical ticker"
            )
        if point_in_time_context is not None:
            if not isinstance(point_in_time_config, dict) or not point_in_time_config.get(
                "enabled"
            ):
                raise ValueError(
                    "refusing to score a feature set without point-in-time evidence"
                )
            expected = {
                "universe_id": args.universe_id,
                "membership_id": point_in_time_context.membership.membership_id,
                "ticker_alias_id": point_in_time_context.ticker_alias.ticker_alias_id,
                "prediction_timestamp": prediction_timestamp.isoformat(),
            }
            conflicts = [
                key
                for key, value in expected.items()
                if point_in_time_config.get(key) != value
            ]
            if conflicts:
                raise ValueError(
                    "point-in-time feature evidence conflicts in: "
                    + ", ".join(conflicts)
                )
        feature_values = load_feature_values(
            session,
            feature_set_id=feature_set.feature_set_id,
            security_id=security.security_id,
            asof_date=asof_date,
            prediction_timestamp=prediction_timestamp,
        )
        baseline_score = calculate_baseline_score(feature_values)
        immutable_hash = immutable_prediction_hash(
            model_version=args.model_version,
            ticker=ticker,
            security_id=security.security_id,
            asof_date=asof_date,
            horizon=args.horizon,
            feature_set_id=feature_set.feature_set_id,
            score=baseline_score,
        )
        if existing_prediction is not None:
            if existing_prediction.immutable_hash != immutable_hash:
                raise ValueError(
                    "prediction already exists but does not match requested "
                    f"feature_set_id={feature_set.feature_set_id}; refusing to skip "
                    f"ticker={ticker} asof_date={asof_date} "
                    f"model_version={args.model_version} horizon={args.horizon} "
                    f"prediction_id={existing_prediction.prediction_id}"
                )
            print(
                "prediction already exists; skipping "
                f"ticker={ticker} asof_date={asof_date} "
                f"model_version={args.model_version} horizon={args.horizon} "
                f"feature_set_id={feature_set.feature_set_id} "
                f"prediction_id={existing_prediction.prediction_id}"
            )
            return 0

        prediction = ModelPrediction(
            model_version=args.model_version,
            security_id=security.security_id,
            feature_set_id=feature_set.feature_set_id,
            asof_date=asof_date,
            horizon=args.horizon,
            score=baseline_score.score,
            confidence=baseline_score.confidence,
            action_label=baseline_score.action_label,
            immutable_hash=immutable_hash,
        )
        session.add(prediction)
        session.flush()

        for driver in baseline_score.drivers:
            session.add(
                ScoreDriverRow(
                    prediction_id=prediction.prediction_id,
                    driver_name=driver.driver_name,
                    contribution=driver.contribution,
                    evidence_uri=driver.evidence_uri,
                )
            )

        prediction_id = prediction.prediction_id
        feature_set_id = feature_set.feature_set_id

    print(
        f"stored baseline prediction ticker={ticker} asof_date={asof_date} "
        f"model_version={args.model_version} horizon={args.horizon} "
        f"score={decimal_text(baseline_score.score)} action_label={baseline_score.action_label} "
        f"feature_set_id={feature_set_id} prediction_id={prediction_id}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
