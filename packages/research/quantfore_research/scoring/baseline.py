"""Baseline scoring heuristic for Sprint 3."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Mapping


BASELINE_MODEL_VERSION = "baseline_v0.1"
SCORE_QUANT = Decimal("0.000001")
REQUIRED_FEATURE_NAMES = (
    "momentum_6_1",
    "momentum_12_1",
    "return_21d",
    "volatility_126d",
)


@dataclass(frozen=True)
class ScoreDriver:
    """Contribution from one input feature or rule to the final score."""

    driver_name: str
    contribution: Decimal
    evidence_uri: str


@dataclass(frozen=True)
class BaselineScore:
    """Research score output produced by the baseline scoring model."""

    score: Decimal
    confidence: Decimal
    action_label: str
    drivers: tuple[ScoreDriver, ...]


def _to_decimal(value: object, *, feature_name: str) -> Decimal:
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{feature_name} must be numeric") from exc


def _clamp(value: Decimal, lower: Decimal, upper: Decimal) -> Decimal:
    return max(lower, min(upper, value))


def _score_label(score: Decimal) -> str:
    if score >= Decimal("80"):
        return "watch_positive"
    if score >= Decimal("60"):
        return "favourable_setup"
    if score >= Decimal("40"):
        return "neutral"
    if score >= Decimal("20"):
        return "watch_negative"
    return "thesis_risk_review"


def _confidence_from_score(score: Decimal) -> Decimal:
    signal_strength = _clamp(
        abs(score - Decimal("50")) / Decimal("50"),
        Decimal("0"),
        Decimal("1"),
    )
    return (Decimal("0.50") + (signal_strength * Decimal("0.40"))).quantize(SCORE_QUANT)


def calculate_baseline_score(features: Mapping[str, object]) -> BaselineScore:
    """Calculate the baseline research score from audited feature values."""

    missing_features = [
        feature_name
        for feature_name in REQUIRED_FEATURE_NAMES
        if feature_name not in features
    ]
    if missing_features:
        missing = ", ".join(missing_features)
        raise ValueError(f"missing baseline score features: {missing}")

    feature_values = {
        feature_name: _to_decimal(features[feature_name], feature_name=feature_name)
        for feature_name in REQUIRED_FEATURE_NAMES
    }
    if feature_values["volatility_126d"] < 0:
        raise ValueError("volatility_126d cannot be negative")

    raw_drivers = (
        (
            "momentum_6_1",
            _clamp(
                feature_values["momentum_6_1"] * Decimal("40"),
                Decimal("-25"),
                Decimal("25"),
            ),
        ),
        (
            "momentum_12_1",
            _clamp(
                feature_values["momentum_12_1"] * Decimal("30"),
                Decimal("-25"),
                Decimal("25"),
            ),
        ),
        (
            "return_21d",
            _clamp(
                feature_values["return_21d"] * Decimal("20"),
                Decimal("-10"),
                Decimal("10"),
            ),
        ),
        (
            "volatility_126d",
            _clamp(
                feature_values["volatility_126d"] * Decimal("-200"),
                Decimal("-30"),
                Decimal("0"),
            ),
        ),
    )
    drivers = tuple(
        ScoreDriver(
            driver_name=driver_name,
            contribution=contribution.quantize(SCORE_QUANT),
            evidence_uri=f"feature:{driver_name}",
        )
        for driver_name, contribution in raw_drivers
    )

    raw_score = Decimal("50") + sum(
        (driver.contribution for driver in drivers),
        Decimal("0"),
    )
    score = _clamp(raw_score, Decimal("0"), Decimal("100")).quantize(SCORE_QUANT)
    return BaselineScore(
        score=score,
        confidence=_confidence_from_score(score),
        action_label=_score_label(score),
        drivers=drivers,
    )
