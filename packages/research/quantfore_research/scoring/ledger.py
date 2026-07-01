"""Canonical hashing helpers for immutable model predictions."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal


def decimal_text(value: object) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(decimal_value.normalize(), "f")


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
    """Build the canonical SHA-256 hash for a prediction and its drivers."""

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
