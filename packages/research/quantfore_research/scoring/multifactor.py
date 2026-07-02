"""Frozen cross-sectional normalization and scoring for Sprint 8.5."""

from __future__ import annotations

import math
import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantfore_research.features.multifactor import (
    APPLICABLE,
    FEATURE_DEFINITIONS,
    HIGHER,
    MultiFactorFeatureBatch,
)
from quantfore_research.models import (
    Feature,
    MultiFactorScore,
    NormalizationRun,
    NormalizedFeature,
)


NORMALIZATION_VERSION = "multifactor-normalization-v1"
FAMILY_WEIGHTS = {
    "value": Decimal("0.20"),
    "quality": Decimal("0.20"),
    "growth": Decimal("0.20"),
    "momentum": Decimal("0.20"),
    "risk": Decimal("0.20"),
}
WINSOR_LOWER = Decimal("0.025")
WINSOR_UPPER = Decimal("0.975")
MINIMUM_SECTOR_SAMPLE = 10
MINIMUM_FAMILIES = 4
MINIMUM_COMPONENT_COVERAGE = Decimal("0.70")
NORMALIZATION_ID_NAMESPACE = uuid.UUID("e7016d6c-9dcf-5bb8-b0ee-f796c46cdf26")


@dataclass(frozen=True)
class NormalizedComponent:
    security_id: str
    feature_name: str
    family: str
    raw_value: Optional[Decimal]
    winsorized_value: Optional[Decimal]
    standardized_value: Optional[Decimal]
    directed_value: Optional[Decimal]
    contribution: Optional[Decimal]
    applicability_status: str
    missing_reason: Optional[str]
    normalization_scope: str
    group_label: Optional[str]
    group_count: int
    group_mean: Optional[Decimal]
    group_std: Optional[Decimal]
    winsor_lower: Optional[Decimal]
    winsor_upper: Optional[Decimal]


@dataclass(frozen=True)
class SecurityMultiFactorScore:
    security_id: str
    sector: Optional[str]
    eligible: bool
    final_score: Optional[Decimal]
    composite_z: Optional[Decimal]
    family_z: Mapping[str, Optional[Decimal]]
    family_scores: Mapping[str, Optional[Decimal]]
    family_available: Mapping[str, bool]
    renormalized_weights: Mapping[str, Decimal]
    applicable_component_count: int
    valid_component_count: int
    component_coverage: Decimal
    available_family_count: int
    missingness: Mapping[str, Mapping[str, Optional[str]]]
    components: tuple[NormalizedComponent, ...]


@dataclass(frozen=True)
class MultiFactorCohortScore:
    prediction_date: object
    normalization_version: str
    minimum_sector_sample: int
    winsor_lower: Decimal
    winsor_upper: Decimal
    scores: tuple[SecurityMultiFactorScore, ...]

    def by_security(self) -> dict[str, SecurityMultiFactorScore]:
        return {row.security_id: row for row in self.scores}


def _percentile(values: Sequence[Decimal], percentile: Decimal) -> Decimal:
    if not values:
        raise ValueError("cannot calculate a percentile without values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = Decimal(len(ordered) - 1) * percentile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    fraction = position - Decimal(lower_index)
    return ordered[lower_index] + (
        ordered[upper_index] - ordered[lower_index]
    ) * fraction


def _population_stats(values: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    if not values:
        raise ValueError("normalization group cannot be empty")
    mean = sum(values, Decimal("0")) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values))
    return mean, variance.sqrt()


def _phi(value: Decimal) -> Decimal:
    result = Decimal(
        str(Decimal("0.5") * Decimal(str(1 + math.erf(float(value) / math.sqrt(2)))))
    ) * Decimal("100")
    return max(Decimal("0"), min(Decimal("100"), result))


def _average_tie_percentiles(
    values: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    if not values:
        return {}
    if len(values) == 1:
        return {next(iter(values)): Decimal("50")}
    ordered = sorted(values.items(), key=lambda item: (item[1], item[0]))
    result: dict[str, Decimal] = {}
    index = 0
    while index < len(ordered):
        end = index + 1
        while end < len(ordered) and ordered[end][1] == ordered[index][1]:
            end += 1
        average_rank = (Decimal(index + 1) + Decimal(end)) / Decimal("2")
        score = Decimal("100") * (average_rank - Decimal("1")) / Decimal(
            len(ordered) - 1
        )
        for position in range(index, end):
            result[ordered[position][0]] = score
        index = end
    return result


def normalize_multifactor_cohort(
    batches: Sequence[MultiFactorFeatureBatch],
    *,
    minimum_sector_sample: int = MINIMUM_SECTOR_SAMPLE,
    winsor_lower: Decimal = WINSOR_LOWER,
    winsor_upper: Decimal = WINSOR_UPPER,
) -> MultiFactorCohortScore:
    """Normalize one complete monthly cohort and create final 0-100 scores."""

    if not batches:
        raise ValueError("multi-factor cohort cannot be empty")
    if minimum_sector_sample <= 0:
        raise ValueError("minimum_sector_sample must be positive")
    if not Decimal("0") <= winsor_lower < winsor_upper <= Decimal("1"):
        raise ValueError("winsor limits are invalid")
    timestamps = {row.prediction_timestamp for row in batches}
    if len(timestamps) != 1:
        raise ValueError("all cohort rows must share one prediction timestamp")
    security_ids = [row.security_id for row in batches]
    if len(security_ids) != len(set(security_ids)):
        raise ValueError("cohort contains duplicate securities")
    required_names = {definition.name for definition in FEATURE_DEFINITIONS}
    by_security = {row.security_id: row for row in batches}
    raw_by_security = {}
    for batch in batches:
        features = batch.by_name()
        if set(features) != required_names:
            raise ValueError(
                f"security {batch.security_id} does not contain the frozen feature set"
            )
        raw_by_security[batch.security_id] = features

    component_rows: dict[tuple[str, str], NormalizedComponent] = {}
    for definition in FEATURE_DEFINITIONS:
        valid = {
            security_id: features[definition.name].value
            for security_id, features in raw_by_security.items()
            if features[definition.name].status == APPLICABLE
            and features[definition.name].value is not None
        }
        valid_values = [value for value in valid.values() if value is not None]
        lower = _percentile(valid_values, winsor_lower) if valid_values else None
        upper = _percentile(valid_values, winsor_upper) if valid_values else None
        winsorized = {
            security_id: max(lower, min(upper, value))
            for security_id, value in valid.items()
        } if lower is not None and upper is not None else {}
        universe_mean, universe_std = (
            _population_stats(list(winsorized.values()))
            if winsorized
            else (None, None)
        )
        sectors: dict[str, list[Decimal]] = {}
        for security_id, value in winsorized.items():
            label = by_security[security_id].sector or "SECTOR_UNKNOWN"
            sectors.setdefault(label, []).append(value)

        for security_id, batch in by_security.items():
            raw = raw_by_security[security_id][definition.name]
            if security_id not in winsorized:
                component_rows[(security_id, definition.name)] = NormalizedComponent(
                    security_id=security_id,
                    feature_name=definition.name,
                    family=definition.family,
                    raw_value=raw.value,
                    winsorized_value=None,
                    standardized_value=None,
                    directed_value=None,
                    contribution=None,
                    applicability_status=raw.status,
                    missing_reason=raw.missing_reason,
                    normalization_scope="NONE",
                    group_label=None,
                    group_count=0,
                    group_mean=None,
                    group_std=None,
                    winsor_lower=lower,
                    winsor_upper=upper,
                )
                continue
            sector_label = batch.sector or "SECTOR_UNKNOWN"
            sector_values = sectors.get(sector_label, [])
            if len(sector_values) >= minimum_sector_sample:
                scope = "SECTOR"
                group_label = sector_label
                group_values = sector_values
            else:
                scope = "UNIVERSE"
                group_label = "UNIVERSE"
                group_values = list(winsorized.values())
            mean, std = _population_stats(group_values)
            standardized = (
                Decimal("0")
                if std == 0
                else (winsorized[security_id] - mean) / std
            )
            directed = standardized if definition.direction == HIGHER else -standardized
            directed = max(Decimal("-3"), min(Decimal("3"), directed))
            component_rows[(security_id, definition.name)] = NormalizedComponent(
                security_id=security_id,
                feature_name=definition.name,
                family=definition.family,
                raw_value=raw.value,
                winsorized_value=winsorized[security_id],
                standardized_value=standardized,
                directed_value=directed,
                contribution=None,
                applicability_status=raw.status,
                missing_reason=raw.missing_reason,
                normalization_scope=scope,
                group_label=group_label,
                group_count=len(group_values),
                group_mean=mean,
                group_std=std,
                winsor_lower=lower,
                winsor_upper=upper,
            )

    preliminary: dict[str, SecurityMultiFactorScore] = {}
    for security_id, batch in by_security.items():
        components = [
            component_rows[(security_id, definition.name)]
            for definition in FEATURE_DEFINITIONS
        ]
        family_z: dict[str, Optional[Decimal]] = {}
        family_available: dict[str, bool] = {}
        for family in FAMILY_WEIGHTS:
            applicable = [
                row for row in components
                if row.family == family and row.applicability_status != "NOT_APPLICABLE"
            ]
            valid = [
                row for row in applicable if row.directed_value is not None
            ]
            required = (len(applicable) + 1) // 2
            available = bool(applicable) and len(valid) >= required
            family_available[family] = available
            family_z[family] = (
                sum((row.directed_value for row in valid), Decimal("0"))
                / Decimal(len(valid))
                if available
                else None
            )
        available_families = [
            family for family, available in family_available.items() if available
        ]
        weights = {
            family: (
                FAMILY_WEIGHTS[family]
                / sum((FAMILY_WEIGHTS[item] for item in available_families), Decimal("0"))
                if family in available_families
                else Decimal("0")
            )
            for family in FAMILY_WEIGHTS
        }
        applicable_count = sum(
            row.applicability_status != "NOT_APPLICABLE" for row in components
        )
        valid_count = sum(row.directed_value is not None for row in components)
        coverage = (
            Decimal(valid_count) / Decimal(applicable_count)
            if applicable_count
            else Decimal("0")
        )
        eligible = (
            len(available_families) >= MINIMUM_FAMILIES
            and coverage >= MINIMUM_COMPONENT_COVERAGE
        )
        composite = (
            sum(
                (family_z[family] * weights[family] for family in available_families),
                Decimal("0"),
            )
            if eligible
            else None
        )
        components_with_contribution = []
        for component in components:
            valid_family_components = [
                row
                for row in components
                if row.family == component.family and row.directed_value is not None
            ]
            contribution = (
                component.directed_value
                * weights[component.family]
                / Decimal(len(valid_family_components))
                if eligible
                and family_available[component.family]
                and component.directed_value is not None
                else None
            )
            components_with_contribution.append(
                replace(component, contribution=contribution)
            )
        if eligible:
            composite = sum(
                (
                    row.contribution
                    for row in components_with_contribution
                    if row.contribution is not None
                ),
                Decimal("0"),
            )
        preliminary[security_id] = SecurityMultiFactorScore(
            security_id=security_id,
            sector=batch.sector,
            eligible=eligible,
            final_score=None,
            composite_z=composite,
            family_z=family_z,
            family_scores={
                family: _phi(value) if value is not None else None
                for family, value in family_z.items()
            },
            family_available=family_available,
            renormalized_weights=weights,
            applicable_component_count=applicable_count,
            valid_component_count=valid_count,
            component_coverage=coverage,
            available_family_count=len(available_families),
            missingness={
                row.feature_name: {
                    "status": row.applicability_status,
                    "reason": row.missing_reason,
                }
                for row in components
                if row.applicability_status != APPLICABLE
            },
            components=tuple(components_with_contribution),
        )
    final_scores = _average_tie_percentiles(
        {
            security_id: row.composite_z
            for security_id, row in preliminary.items()
            if row.eligible and row.composite_z is not None
        }
    )
    scores = tuple(
        replace(preliminary[security_id], final_score=final_scores.get(security_id))
        for security_id in sorted(preliminary)
    )
    return MultiFactorCohortScore(
        prediction_date=next(iter(timestamps)).date(),
        normalization_version=NORMALIZATION_VERSION,
        minimum_sector_sample=minimum_sector_sample,
        winsor_lower=winsor_lower,
        winsor_upper=winsor_upper,
        scores=scores,
    )


def _decimal_json(values: Mapping[str, Optional[Decimal]]) -> dict[str, Optional[str]]:
    return {
        key: (str(value) if value is not None else None)
        for key, value in values.items()
    }


def normalization_input_hash(
    result: MultiFactorCohortScore,
    raw_feature_ids: Mapping[tuple[str, str], str],
) -> str:
    rows = []
    for score in result.scores:
        for component in score.components:
            key = (score.security_id, component.feature_name)
            rows.append(
                {
                    "security_id": score.security_id,
                    "feature_name": component.feature_name,
                    "feature_id": raw_feature_ids.get(key),
                    "raw_value": (
                        str(component.raw_value)
                        if component.raw_value is not None
                        else None
                    ),
                    "status": component.applicability_status,
                    "missing_reason": component.missing_reason,
                }
            )
    document = {
        "version": result.normalization_version,
        "prediction_date": result.prediction_date.isoformat(),
        "minimum_sector_sample": result.minimum_sector_sample,
        "winsor_lower": str(result.winsor_lower),
        "winsor_upper": str(result.winsor_upper),
        "rows": rows,
    }
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def store_multifactor_cohort_scores(
    session: Session,
    *,
    result: MultiFactorCohortScore,
    normalization_run_id: str,
    universe_id: str,
    raw_feature_ids: Mapping[tuple[str, str], str],
    source_feature_set_ids: Sequence[str],
    code_commit: Optional[str] = None,
) -> NormalizationRun:
    """Persist raw/winsorized/z/contribution values and score missingness."""

    expected_keys = {
        (score.security_id, component.feature_name)
        for score in result.scores
        for component in score.components
    }
    if set(raw_feature_ids) != expected_keys:
        raise ValueError("raw feature ID mapping does not match the normalized cohort")
    features = {
        row.feature_id: row
        for row in session.scalars(
            select(Feature).where(Feature.feature_id.in_(set(raw_feature_ids.values())))
        ).all()
    }
    if set(features) != set(raw_feature_ids.values()):
        raise ValueError("normalization references unknown raw feature IDs")
    for key, feature_id in raw_feature_ids.items():
        feature = features[feature_id]
        if (feature.security_id, feature.feature_name) != key:
            raise ValueError("raw feature ID mapping has conflicting identity")
    components_by_key = {
        (score.security_id, component.feature_name): component
        for score in result.scores
        for component in score.components
    }
    raw_quant = Decimal("0.000000000001")
    for key, feature_id in raw_feature_ids.items():
        feature = features[feature_id]
        component = components_by_key[key]
        expected_raw = (
            component.raw_value.quantize(raw_quant)
            if component.raw_value is not None
            else None
        )
        if (
            feature.raw_value != expected_raw
            or feature.applicability_status != component.applicability_status
            or feature.missing_reason != component.missing_reason
        ):
            raise ValueError("stored raw feature does not match normalized input")

    input_hash = normalization_input_hash(result, raw_feature_ids)
    config = {
        "winsor_lower": str(result.winsor_lower),
        "winsor_upper": str(result.winsor_upper),
        "minimum_sector_sample": result.minimum_sector_sample,
        "family_weights": {key: str(value) for key, value in FAMILY_WEIGHTS.items()},
        "minimum_families": MINIMUM_FAMILIES,
        "minimum_component_coverage": str(MINIMUM_COMPONENT_COVERAGE),
    }
    existing = session.get(NormalizationRun, normalization_run_id)
    if existing is not None:
        if (
            existing.universe_id != universe_id
            or existing.asof_date != result.prediction_date
            or existing.version != result.normalization_version
            or existing.config_json != config
            or existing.source_feature_set_ids_json != sorted(source_feature_set_ids)
            or existing.input_hash != input_hash
        ):
            raise ValueError(f"conflicting normalization run {normalization_run_id}")
        normalized_count = len(
            session.scalars(
                select(NormalizedFeature).where(
                    NormalizedFeature.normalization_run_id == normalization_run_id
                )
            ).all()
        )
        score_count = len(
            session.scalars(
                select(MultiFactorScore).where(
                    MultiFactorScore.normalization_run_id == normalization_run_id
                )
            ).all()
        )
        if normalized_count != len(expected_keys) or score_count != len(result.scores):
            raise ValueError(f"incomplete normalization run {normalization_run_id}")
        return existing

    run = NormalizationRun(
        normalization_run_id=normalization_run_id,
        universe_id=universe_id,
        asof_date=result.prediction_date,
        version=result.normalization_version,
        config_json=config,
        source_feature_set_ids_json=sorted(source_feature_set_ids),
        input_hash=input_hash,
        code_commit=code_commit,
    )
    session.add(run)
    session.flush()
    for score in result.scores:
        for component in score.components:
            feature_id = raw_feature_ids[(score.security_id, component.feature_name)]
            normalized_id = str(
                uuid.uuid5(
                    NORMALIZATION_ID_NAMESPACE,
                    f"component|{normalization_run_id}|{feature_id}",
                )
            )
            session.add(
                NormalizedFeature(
                    normalized_feature_id=normalized_id,
                    normalization_run_id=normalization_run_id,
                    feature_id=feature_id,
                    security_id=score.security_id,
                    feature_name=component.feature_name,
                    family=component.family,
                    raw_value=component.raw_value,
                    winsorized_value=component.winsorized_value,
                    standardized_value=component.standardized_value,
                    directed_value=component.directed_value,
                    contribution=component.contribution,
                    applicability_status=component.applicability_status,
                    missing_reason=component.missing_reason,
                    normalization_scope=component.normalization_scope,
                    group_label=component.group_label,
                    group_count=component.group_count,
                    group_mean=component.group_mean,
                    group_std=component.group_std,
                    winsor_lower=component.winsor_lower,
                    winsor_upper=component.winsor_upper,
                )
            )
        score_id = str(
            uuid.uuid5(
                NORMALIZATION_ID_NAMESPACE,
                f"score|{normalization_run_id}|{score.security_id}",
            )
        )
        session.add(
            MultiFactorScore(
                multifactor_score_id=score_id,
                normalization_run_id=normalization_run_id,
                security_id=score.security_id,
                asof_date=result.prediction_date,
                eligible=score.eligible,
                final_score=score.final_score,
                composite_z=score.composite_z,
                applicable_component_count=score.applicable_component_count,
                valid_component_count=score.valid_component_count,
                component_coverage=score.component_coverage,
                available_family_count=score.available_family_count,
                family_z_json=_decimal_json(score.family_z),
                family_scores_json=_decimal_json(score.family_scores),
                family_available_json=dict(score.family_available),
                renormalized_weights_json=_decimal_json(
                    score.renormalized_weights
                ),
                missingness_json=dict(score.missingness),
            )
        )
    session.flush()
    return run
