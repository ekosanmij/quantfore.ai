"""Build and store the baseline research score for one ticker.

Example:
    python pipelines/build_baseline_score.py MSFT --asof-date 2026-06-24
"""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from typing import Optional, Sequence

import _bootstrap  # noqa: F401
from _common import open_research_database, parse_date

from sqlalchemy import select

from quantfore_research.db import session_scope
from quantfore_research.models import (
    Feature,
    FeatureSet,
    ModelPrediction,
    ScoreDriver as ScoreDriverRow,
    Security,
)
from quantfore_research.scoring import (
    BASELINE_MODEL_VERSION,
    REQUIRED_FEATURE_NAMES,
    calculate_baseline_score,
)


DEFAULT_HORIZON = "unspecified"
FEATURE_SET_NAME = "baseline_features"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build baseline research score.")
    parser.add_argument("ticker")
    parser.add_argument("--asof-date", required=True, help="YYYY-MM-DD score as-of date.")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    parser.add_argument("--feature-set-id", help="Use an explicit baseline feature set.")
    parser.add_argument("--horizon", default=DEFAULT_HORIZON)
    parser.add_argument("--model-version", default=BASELINE_MODEL_VERSION)
    return parser.parse_args(argv)


def decimal_text(value: object) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(decimal_value.normalize(), "f")


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


def immutable_prediction_hash(
    *,
    model_version: str,
    ticker: str,
    security_id: str,
    asof_date,
    horizon: str,
    feature_set_id: str,
    score,
) -> str:
    payload = {
        "model_version": model_version,
        "ticker": ticker,
        "security_id": security_id,
        "asof_date": asof_date.isoformat(),
        "horizon": horizon,
        "score": decimal_text(score.score),
        "confidence": decimal_text(score.confidence),
        "action_label": score.action_label,
        "feature_set_id": feature_set_id,
        "drivers": [
            {
                "driver_name": driver.driver_name,
                "contribution": decimal_text(driver.contribution),
                "evidence_uri": driver.evidence_uri,
            }
            for driver in sorted(score.drivers, key=lambda item: item.driver_name)
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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

        existing_prediction = session.scalar(
            select(ModelPrediction)
            .where(ModelPrediction.model_version == args.model_version)
            .where(ModelPrediction.security_id == security.security_id)
            .where(ModelPrediction.asof_date == asof_date)
            .where(ModelPrediction.horizon == args.horizon)
        )
        if existing_prediction is not None:
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
        feature_values = load_feature_values(
            session,
            feature_set_id=feature_set.feature_set_id,
            security_id=security.security_id,
            asof_date=asof_date,
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

        prediction = ModelPrediction(
            model_version=args.model_version,
            security_id=security.security_id,
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
