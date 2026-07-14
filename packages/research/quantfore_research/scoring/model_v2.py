"""Branch-local normalization and fixed-weight Model V2 scoring."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_CEILING
from typing import Mapping, Optional, Sequence

from quantfore_research.features.model_v2 import (
    ACTIVE_BRANCHES,
    APPLICABLE,
    BRANCH_FEATURE_DEFINITIONS,
    HIGHER,
    ModelV2FeatureBatch,
)


MODEL_V2_MODEL_VERSION = "multifactor-v2-branch-aware-equal-weight-v1"
MODEL_V2_NORMALIZATION_VERSION = "multifactor-v2-branch-normalization-v1"
FAMILY_WEIGHTS = {
    "value": Decimal("0.20"),
    "quality": Decimal("0.20"),
    "growth": Decimal("0.20"),
    "momentum": Decimal("0.20"),
    "risk": Decimal("0.20"),
}
WINSOR_LOWER = Decimal("0.025")
WINSOR_UPPER = Decimal("0.975")
ZSCORE_CLIP = Decimal("3")
MINIMUM_BRANCH_CROSS_SECTION = 20
MINIMUM_COMPONENT_COVERAGE = Decimal("0.80")
MINIMUM_FAMILY_COMPONENT_COVERAGE = Decimal("0.60")

BRANCH_NORMALIZATION_COHORT_TOO_SMALL = "BRANCH_NORMALIZATION_COHORT_TOO_SMALL"
BRANCH_REQUIRED_FEATURE_MISSING = "BRANCH_REQUIRED_FEATURE_MISSING"
COMPONENT_COVERAGE_BELOW_MINIMUM = "COMPONENT_COVERAGE_BELOW_MINIMUM"
FAMILY_COVERAGE_BELOW_MINIMUM = "FAMILY_COVERAGE_BELOW_MINIMUM"
ALL_FIVE_FAMILIES_REQUIRED = "ALL_FIVE_FAMILIES_REQUIRED"
SECTOR_BRANCH_EXCLUDED = "SECTOR_BRANCH_EXCLUDED"


@dataclass(frozen=True)
class NormalizedModelV2Component:
    security_id: str
    sector_branch: str
    feature_name: str
    family: str
    raw_value: Optional[Decimal]
    winsorized_value: Optional[Decimal]
    standardized_value: Optional[Decimal]
    directed_value: Optional[Decimal]
    input_status: str
    input_reason_code: Optional[str]
    input_reason_detail: Optional[str]
    normalization_reason_code: Optional[str]
    normalization_scope: str
    normalization_group: Optional[str]
    group_count: int
    group_mean: Optional[Decimal]
    group_std: Optional[Decimal]
    winsor_lower: Optional[Decimal]
    winsor_upper: Optional[Decimal]
    lineage_ids: tuple[str, ...]


@dataclass(frozen=True)
class SecurityModelV2Score:
    security_id: str
    prediction_date: object
    sector_branch: str
    classification_id: Optional[str]
    eligible: bool
    exclusion_reason_codes: tuple[str, ...]
    final_score: Optional[Decimal]
    composite_z: Optional[Decimal]
    family_z: Mapping[str, Optional[Decimal]]
    family_scores: Mapping[str, Optional[Decimal]]
    family_available: Mapping[str, bool]
    family_valid_component_counts: Mapping[str, int]
    family_required_component_counts: Mapping[str, int]
    family_minimum_valid_component_counts: Mapping[str, int]
    family_weights: Mapping[str, Decimal]
    required_component_count: int
    valid_component_count: int
    component_coverage: Decimal
    components: tuple[NormalizedModelV2Component, ...]


@dataclass(frozen=True)
class ModelV2CohortScore:
    prediction_date: object
    model_version: str
    normalization_version: str
    minimum_branch_cross_section: int
    scores: tuple[SecurityModelV2Score, ...]

    def by_security(self) -> dict[str, SecurityModelV2Score]:
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


def _normal_score(value: Decimal) -> Decimal:
    score = Decimal(str(50 * (1 + math.erf(float(value) / math.sqrt(2)))))
    return max(Decimal("0"), min(Decimal("100"), score))


def _average_tie_percentiles(values: Mapping[str, Decimal]) -> dict[str, Decimal]:
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
        percentile = Decimal("100") * (average_rank - Decimal("1")) / Decimal(
            len(ordered) - 1
        )
        for position in range(index, end):
            result[ordered[position][0]] = percentile
        index = end
    return result


def _minimum_valid(required: int, fraction: Decimal) -> int:
    return int((Decimal(required) * fraction).to_integral_value(rounding=ROUND_CEILING))


def _empty_score(batch: ModelV2FeatureBatch) -> SecurityModelV2Score:
    reasons = tuple(sorted(set(batch.classification_reason_codes)))
    if not reasons:
        reasons = (SECTOR_BRANCH_EXCLUDED,)
    return SecurityModelV2Score(
        security_id=batch.security_id,
        prediction_date=batch.prediction_date,
        sector_branch=batch.sector_branch,
        classification_id=batch.classification_id,
        eligible=False,
        exclusion_reason_codes=reasons,
        final_score=None,
        composite_z=None,
        family_z={family: None for family in FAMILY_WEIGHTS},
        family_scores={family: None for family in FAMILY_WEIGHTS},
        family_available={family: False for family in FAMILY_WEIGHTS},
        family_valid_component_counts={family: 0 for family in FAMILY_WEIGHTS},
        family_required_component_counts={family: 0 for family in FAMILY_WEIGHTS},
        family_minimum_valid_component_counts={family: 0 for family in FAMILY_WEIGHTS},
        family_weights=dict(FAMILY_WEIGHTS),
        required_component_count=0,
        valid_component_count=0,
        component_coverage=Decimal("0"),
        components=(),
    )


def _validate_cohort(batches: Sequence[ModelV2FeatureBatch]) -> object:
    if not batches:
        raise ValueError("Model V2 cohort cannot be empty")
    dates = {row.prediction_date for row in batches}
    if len(dates) != 1:
        raise ValueError("all Model V2 cohort rows must share one prediction date")
    security_ids = [row.security_id for row in batches]
    if len(security_ids) != len(set(security_ids)):
        raise ValueError("Model V2 cohort contains duplicate securities")
    for batch in batches:
        if not batch.classification_eligible:
            if batch.components:
                raise ValueError("classification-excluded row must not contain components")
            continue
        if batch.sector_branch not in ACTIVE_BRANCHES:
            raise ValueError(f"classification routed to inactive branch: {batch.sector_branch}")
        expected = BRANCH_FEATURE_DEFINITIONS[batch.sector_branch]
        if tuple(row.definition for row in batch.components) != expected:
            raise ValueError(
                f"security {batch.security_id} does not match its locked branch schema"
            )
    return next(iter(dates))


def _normalize_branch(
    batches: Sequence[ModelV2FeatureBatch],
    *,
    minimum_branch_cross_section: int,
    winsor_lower: Decimal,
    winsor_upper: Decimal,
) -> tuple[SecurityModelV2Score, ...]:
    branch = batches[0].sector_branch
    definitions = BRANCH_FEATURE_DEFINITIONS[branch]
    raw_by_security = {row.security_id: row.by_name() for row in batches}
    normalized: dict[tuple[str, str], NormalizedModelV2Component] = {}

    branch_too_small = len(batches) < minimum_branch_cross_section
    for definition in definitions:
        valid = {
            security_id: features[definition.name].value
            for security_id, features in raw_by_security.items()
            if features[definition.name].status == APPLICABLE
            and features[definition.name].value is not None
        }
        valid_values = [value for value in valid.values() if value is not None]
        component_cohort_too_small = (
            branch_too_small or len(valid_values) < minimum_branch_cross_section
        )
        lower = (
            _percentile(valid_values, winsor_lower)
            if valid_values and not component_cohort_too_small
            else None
        )
        upper = (
            _percentile(valid_values, winsor_upper)
            if valid_values and not component_cohort_too_small
            else None
        )
        winsorized = (
            {
                security_id: max(lower, min(upper, value))
                for security_id, value in valid.items()
            }
            if lower is not None and upper is not None
            else {}
        )
        mean, std = (
            _population_stats(list(winsorized.values()))
            if winsorized
            else (None, None)
        )
        for batch in batches:
            raw = raw_by_security[batch.security_id][definition.name]
            normalized_reason = None
            standardized = None
            directed = None
            if raw.status == APPLICABLE and raw.value is not None:
                if component_cohort_too_small:
                    normalized_reason = BRANCH_NORMALIZATION_COHORT_TOO_SMALL
                else:
                    assert mean is not None and std is not None
                    standardized = (
                        Decimal("0")
                        if std == 0
                        else (winsorized[batch.security_id] - mean) / std
                    )
                    directed = standardized if definition.direction == HIGHER else -standardized
                    directed = max(-ZSCORE_CLIP, min(ZSCORE_CLIP, directed))
            normalized[(batch.security_id, definition.name)] = NormalizedModelV2Component(
                security_id=batch.security_id,
                sector_branch=branch,
                feature_name=definition.name,
                family=definition.family,
                raw_value=raw.value,
                winsorized_value=winsorized.get(batch.security_id),
                standardized_value=standardized,
                directed_value=directed,
                input_status=raw.status,
                input_reason_code=raw.reason_code,
                input_reason_detail=raw.reason_detail,
                normalization_reason_code=normalized_reason,
                normalization_scope="NONE" if standardized is None else "BRANCH",
                normalization_group=None if standardized is None else branch,
                group_count=len(valid_values),
                group_mean=mean,
                group_std=std,
                winsor_lower=lower,
                winsor_upper=upper,
                lineage_ids=raw.lineage_ids,
            )

    preliminary = []
    family_definitions = {
        family: tuple(row for row in definitions if row.family == family)
        for family in FAMILY_WEIGHTS
    }
    for batch in batches:
        components = tuple(
            normalized[(batch.security_id, definition.name)] for definition in definitions
        )
        family_z: dict[str, Optional[Decimal]] = {}
        family_available: dict[str, bool] = {}
        family_valid_counts: dict[str, int] = {}
        family_required_counts: dict[str, int] = {}
        family_minimum_counts: dict[str, int] = {}
        for family, family_rows in family_definitions.items():
            required_count = len(family_rows)
            minimum_count = _minimum_valid(
                required_count, MINIMUM_FAMILY_COMPONENT_COVERAGE
            )
            valid = [
                row.directed_value
                for row in components
                if row.family == family and row.directed_value is not None
            ]
            family_required_counts[family] = required_count
            family_minimum_counts[family] = minimum_count
            family_valid_counts[family] = len(valid)
            available = required_count > 0 and len(valid) >= minimum_count
            family_available[family] = available
            family_z[family] = (
                sum(valid, Decimal("0")) / Decimal(len(valid)) if available else None
            )

        valid_count = sum(row.directed_value is not None for row in components)
        required_count = len(components)
        component_coverage = Decimal(valid_count) / Decimal(required_count)
        all_families = all(family_available.values())
        enough_components = component_coverage >= MINIMUM_COMPONENT_COVERAGE
        reasons = set()
        normalization_shortage = any(
            row.normalization_reason_code == BRANCH_NORMALIZATION_COHORT_TOO_SMALL
            for row in components
        )
        if branch_too_small or (
            normalization_shortage and (not enough_components or not all_families)
        ):
            reasons.add(BRANCH_NORMALIZATION_COHORT_TOO_SMALL)
        if any(row.input_status != APPLICABLE for row in components) and (
            not enough_components or not all_families
        ):
            reasons.add(BRANCH_REQUIRED_FEATURE_MISSING)
        if not enough_components:
            reasons.add(COMPONENT_COVERAGE_BELOW_MINIMUM)
        if not all_families:
            reasons.update((FAMILY_COVERAGE_BELOW_MINIMUM, ALL_FIVE_FAMILIES_REQUIRED))
        eligible = not reasons
        composite = (
            sum(
                (
                    family_z[family] * FAMILY_WEIGHTS[family]
                    for family in FAMILY_WEIGHTS
                    if family_z[family] is not None
                ),
                Decimal("0"),
            )
            if eligible
            else None
        )
        preliminary.append(
            SecurityModelV2Score(
                security_id=batch.security_id,
                prediction_date=batch.prediction_date,
                sector_branch=branch,
                classification_id=batch.classification_id,
                eligible=eligible,
                exclusion_reason_codes=tuple(sorted(reasons)),
                final_score=None,
                composite_z=composite,
                family_z=family_z,
                family_scores={
                    family: _normal_score(value) if value is not None else None
                    for family, value in family_z.items()
                },
                family_available=family_available,
                family_valid_component_counts=family_valid_counts,
                family_required_component_counts=family_required_counts,
                family_minimum_valid_component_counts=family_minimum_counts,
                family_weights=dict(FAMILY_WEIGHTS),
                required_component_count=required_count,
                valid_component_count=valid_count,
                component_coverage=component_coverage,
                components=components,
            )
        )

    eligible_count = sum(row.eligible for row in preliminary)
    if eligible_count < minimum_branch_cross_section:
        preliminary = [
            replace(
                row,
                eligible=False,
                exclusion_reason_codes=tuple(
                    sorted(
                        set(row.exclusion_reason_codes)
                        | {BRANCH_NORMALIZATION_COHORT_TOO_SMALL}
                    )
                ),
                composite_z=None,
            )
            for row in preliminary
        ]
    final_scores = _average_tie_percentiles(
        {
            row.security_id: row.composite_z
            for row in preliminary
            if row.eligible and row.composite_z is not None
        }
    )
    return tuple(
        replace(row, final_score=final_scores.get(row.security_id))
        for row in preliminary
    )


def normalize_model_v2_cohort(
    batches: Sequence[ModelV2FeatureBatch],
    *,
    minimum_branch_cross_section: int = MINIMUM_BRANCH_CROSS_SECTION,
    winsor_lower: Decimal = WINSOR_LOWER,
    winsor_upper: Decimal = WINSOR_UPPER,
) -> ModelV2CohortScore:
    """Normalize and score one date, independently inside every active branch."""

    if minimum_branch_cross_section <= 0:
        raise ValueError("minimum_branch_cross_section must be positive")
    if not Decimal("0") <= winsor_lower < winsor_upper <= Decimal("1"):
        raise ValueError("winsor limits are invalid")
    prediction_date = _validate_cohort(batches)
    by_branch: dict[str, list[ModelV2FeatureBatch]] = {}
    excluded = []
    for batch in batches:
        if not batch.classification_eligible:
            excluded.append(_empty_score(batch))
        else:
            by_branch.setdefault(batch.sector_branch, []).append(batch)

    scores = list(excluded)
    for branch in ACTIVE_BRANCHES:
        branch_batches = by_branch.get(branch, [])
        if branch_batches:
            scores.extend(
                _normalize_branch(
                    branch_batches,
                    minimum_branch_cross_section=minimum_branch_cross_section,
                    winsor_lower=winsor_lower,
                    winsor_upper=winsor_upper,
                )
            )
    scores.sort(key=lambda row: row.security_id)
    result = ModelV2CohortScore(
        prediction_date=prediction_date,
        model_version=MODEL_V2_MODEL_VERSION,
        normalization_version=MODEL_V2_NORMALIZATION_VERSION,
        minimum_branch_cross_section=minimum_branch_cross_section,
        scores=tuple(scores),
    )
    for score in result.scores:
        if score.eligible:
            if score.final_score is None or not all(score.family_available.values()):
                raise AssertionError("eligible Model V2 score violates all-family invariant")
            if score.exclusion_reason_codes:
                raise AssertionError("eligible Model V2 score has an exclusion reason")
        elif not score.exclusion_reason_codes:
            raise AssertionError("excluded Model V2 score lacks a stable reason")
        if score.family_weights != FAMILY_WEIGHTS:
            raise AssertionError("Model V2 family weights were changed or renormalized")
        if any(
            row.normalization_scope not in {"NONE", "BRANCH"}
            or (
                row.normalization_scope == "BRANCH"
                and row.normalization_group != score.sector_branch
            )
            for row in score.components
        ):
            raise AssertionError("cross-branch normalization was detected")
    return result
