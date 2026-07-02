"""Pure forward-outcome evaluation for stored research predictions."""

from quantfore_research.evaluation.outcomes import (
    SUPPORTED_HORIZONS,
    NotEnoughFuturePrices,
    OutcomeResult,
    PricePoint,
    calculate_forward_outcome,
    calculate_max_drawdown,
    parse_horizon,
)
from quantfore_research.evaluation.ledger import (
    PRICE_QUANT,
    RETURN_QUANT,
    canonical_datetime_text,
    canonical_decimal_text,
    decimal_text,
    immutable_outcome_hash,
    normalized_utc,
)
from quantfore_research.evaluation.comparative import (
    ComparativeObservation,
    UniverseCohort,
    analyze_dataset,
    build_comparative_evidence,
    compare_universes,
)

__all__ = [
    "SUPPORTED_HORIZONS",
    "PRICE_QUANT",
    "RETURN_QUANT",
    "NotEnoughFuturePrices",
    "OutcomeResult",
    "PricePoint",
    "calculate_forward_outcome",
    "calculate_max_drawdown",
    "parse_horizon",
    "canonical_datetime_text",
    "canonical_decimal_text",
    "decimal_text",
    "immutable_outcome_hash",
    "normalized_utc",
    "ComparativeObservation",
    "UniverseCohort",
    "analyze_dataset",
    "build_comparative_evidence",
    "compare_universes",
]
