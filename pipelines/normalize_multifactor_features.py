"""Normalize one monthly raw-feature cohort and store Sprint 8.5 scores."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from typing import Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import get_code_revision, open_research_database
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import get_code_revision, open_research_database  # type: ignore

from sqlalchemy import select

from quantfore_research.db import session_scope
from quantfore_research.features.multifactor import (
    DEFINITIONS_BY_NAME,
    MULTIFACTOR_FEATURE_SET_NAME,
    MULTIFACTOR_FEATURE_VERSION,
    MultiFactorFeatureBatch,
    RawFeature,
)
from quantfore_research.models import Feature, FeatureSet, UniverseDefinition
from quantfore_research.scoring.multifactor import (
    MINIMUM_SECTOR_SAMPLE,
    NORMALIZATION_VERSION,
    normalize_multifactor_cohort,
    store_multifactor_cohort_scores,
    store_multifactor_predictions,
)
from quantfore_research.validation.leakage import expected_point_in_time_cohort


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def load_stored_cohort(
    session,
    *,
    universe_id: str,
    prediction_timestamp: datetime,
) -> tuple[
    tuple[MultiFactorFeatureBatch, ...],
    dict[tuple[str, str], str],
    tuple[str, ...],
]:
    universe = session.get(UniverseDefinition, universe_id)
    if universe is None:
        raise ValueError(f"unknown universe: {universe_id}")
    feature_sets = list(
        session.scalars(
            select(FeatureSet).where(
                FeatureSet.name == MULTIFACTOR_FEATURE_SET_NAME,
                FeatureSet.version == MULTIFACTOR_FEATURE_VERSION,
                FeatureSet.asof_date == prediction_timestamp.date(),
            )
        ).all()
    )
    if not feature_sets:
        raise ValueError("no raw multi-factor feature sets exist for prediction date")
    set_by_id = {row.feature_set_id: row for row in feature_sets}
    features = list(
        session.scalars(
            select(Feature).where(Feature.feature_set_id.in_(set_by_id))
        ).all()
    )
    rows_by_security: dict[str, list[Feature]] = {}
    set_ids_by_security: dict[str, set[str]] = {}
    for row in features:
        rows_by_security.setdefault(row.security_id, []).append(row)
        set_ids_by_security.setdefault(row.security_id, set()).add(row.feature_set_id)
    if any(len(values) != 1 for values in set_ids_by_security.values()):
        raise ValueError("security has multiple raw feature sets for prediction date")

    expected = expected_point_in_time_cohort(
        session,
        universe_id=universe_id,
        prediction_timestamp=prediction_timestamp,
    )
    expected_ids = {row.security.security_id for row in expected}
    if set(rows_by_security) != expected_ids:
        missing = sorted(expected_ids - set(rows_by_security))
        extra = sorted(set(rows_by_security) - expected_ids)
        raise ValueError(
            f"raw feature cohort does not match historical universe; "
            f"missing={missing!r} extra={extra!r}"
        )

    batches = []
    raw_feature_ids = {}
    used_feature_set_ids = []
    for security_id in sorted(rows_by_security):
        rows = rows_by_security[security_id]
        feature_set_id = next(iter(set_ids_by_security[security_id]))
        feature_set = set_by_id[feature_set_id]
        configured_timestamp = feature_set.config_json.get("prediction_timestamp")
        if configured_timestamp != prediction_timestamp.isoformat().replace("+00:00", "Z"):
            # Older serializers retain +00:00; accept that exact equivalent.
            if configured_timestamp != prediction_timestamp.isoformat():
                raise ValueError(
                    f"feature set {feature_set_id} has a different prediction timestamp"
                )
        by_name = {row.feature_name: row for row in rows}
        if set(by_name) != set(DEFINITIONS_BY_NAME):
            raise ValueError(f"feature set {feature_set_id} is incomplete")
        raw_features = []
        for name, definition in DEFINITIONS_BY_NAME.items():
            row = by_name[name]
            raw_features.append(
                RawFeature(
                    definition=definition,
                    value=row.raw_value,
                    status=row.applicability_status,
                    missing_reason=row.missing_reason,
                    inputs=(),
                )
            )
            raw_feature_ids[(security_id, name)] = row.feature_id
        batches.append(
            MultiFactorFeatureBatch(
                security_id=security_id,
                benchmark_security_id=universe.benchmark_security_id,
                prediction_timestamp=prediction_timestamp,
                sector=feature_set.config_json.get("sector"),
                industry=feature_set.config_json.get("industry"),
                features=tuple(raw_features),
            )
        )
        used_feature_set_ids.append(feature_set_id)
    return tuple(batches), raw_feature_ids, tuple(sorted(used_feature_set_ids))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize and score one monthly Sprint 8 feature cohort."
    )
    parser.add_argument("--universe-id", default="sp500-pit-v1")
    parser.add_argument("--prediction-timestamp", required=True, type=_timestamp)
    parser.add_argument("--normalization-run-id")
    parser.add_argument(
        "--minimum-sector-sample", type=int, default=MINIMUM_SECTOR_SAMPLE
    )
    parser.add_argument("--database-url")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        session_factory = open_research_database(args.database_url)
        with session_scope(session_factory) as session:
            batches, raw_feature_ids, feature_set_ids = load_stored_cohort(
                session,
                universe_id=args.universe_id,
                prediction_timestamp=args.prediction_timestamp,
            )
            result = normalize_multifactor_cohort(
                batches, minimum_sector_sample=args.minimum_sector_sample
            )
            run_id = args.normalization_run_id or (
                f"{NORMALIZATION_VERSION}_{args.universe_id}_"
                f"{args.prediction_timestamp.date().isoformat()}"
            )
            store_multifactor_cohort_scores(
                session,
                result=result,
                normalization_run_id=run_id,
                universe_id=args.universe_id,
                raw_feature_ids=raw_feature_ids,
                source_feature_set_ids=feature_set_ids,
                code_commit=get_code_revision(),
            )
            predictions = store_multifactor_predictions(
                session,
                result=result,
                normalization_run_id=run_id,
                raw_feature_ids=raw_feature_ids,
            )
        eligible = sum(row.eligible for row in result.scores)
        print(
            f"normalization_run_id={run_id} cohort={len(result.scores)} "
            f"eligible={eligible} ineligible={len(result.scores) - eligible} "
            f"predictions={len(predictions)}"
        )
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"multi-factor normalization failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
