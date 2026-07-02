"""Scoring modules that turn audited features into model beliefs."""

from quantfore_research.scoring.baseline import (
    ACTION_LABELS,
    BASELINE_MODEL_VERSION,
    RESEARCH_LABEL_BANDS,
    REQUIRED_FEATURE_NAMES,
    BaselineScore,
    ResearchLabelBand,
    ScoreDriver,
    action_label_for_score,
    calculate_baseline_score,
)
from quantfore_research.scoring.ledger import decimal_text, immutable_prediction_hash
from quantfore_research.scoring.multifactor import (
    FAMILY_WEIGHTS,
    MINIMUM_COMPONENT_COVERAGE,
    MINIMUM_FAMILIES,
    MINIMUM_SECTOR_SAMPLE,
    NORMALIZATION_VERSION,
    MultiFactorCohortScore,
    NormalizedComponent,
    SecurityMultiFactorScore,
    normalization_input_hash,
    normalize_multifactor_cohort,
    store_multifactor_cohort_scores,
)

__all__ = [
    "ACTION_LABELS",
    "BASELINE_MODEL_VERSION",
    "RESEARCH_LABEL_BANDS",
    "REQUIRED_FEATURE_NAMES",
    "BaselineScore",
    "FAMILY_WEIGHTS",
    "MINIMUM_COMPONENT_COVERAGE",
    "MINIMUM_FAMILIES",
    "MINIMUM_SECTOR_SAMPLE",
    "MultiFactorCohortScore",
    "NORMALIZATION_VERSION",
    "NormalizedComponent",
    "ResearchLabelBand",
    "ScoreDriver",
    "SecurityMultiFactorScore",
    "action_label_for_score",
    "calculate_baseline_score",
    "decimal_text",
    "immutable_prediction_hash",
    "normalization_input_hash",
    "normalize_multifactor_cohort",
    "store_multifactor_cohort_scores",
]
