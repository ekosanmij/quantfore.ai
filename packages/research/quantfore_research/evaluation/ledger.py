"""Canonical hashing helpers for immutable model outcomes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional


PRICE_QUANT = Decimal("0.000001")
RETURN_QUANT = Decimal("0.00000001")


def decimal_text(value: object) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(decimal_value.normalize(), "f")


def canonical_decimal_text(value: object, *, quantum: Decimal) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return decimal_text(decimal_value.quantize(quantum))


def normalized_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def canonical_datetime_text(value: datetime) -> str:
    return normalized_utc(value).isoformat(timespec="microseconds").replace(
        "+00:00",
        "Z",
    )


def immutable_outcome_hash(
    *,
    prediction,
    ticker: str,
    benchmark,
    security_price_snapshot_id: str,
    benchmark_price_snapshot_id: str,
    outcome: object,
    evaluated_at: Optional[datetime] = None,
) -> str:
    """Build the canonical SHA-256 hash for one outcome and its lineage."""

    outcome_evaluated_at = evaluated_at or getattr(outcome, "evaluated_at", None)
    if outcome_evaluated_at is None:
        raise ValueError("evaluated_at is required for the immutable outcome hash")

    payload = {
        "prediction_id": prediction.prediction_id,
        "prediction_immutable_hash": prediction.immutable_hash,
        "model_version": prediction.model_version,
        "ticker": ticker,
        "security_id": prediction.security_id,
        "asof_date": prediction.asof_date.isoformat(),
        "horizon": prediction.horizon,
        "benchmark_ticker": benchmark.ticker,
        "benchmark_security_id": benchmark.security_id,
        "security_price_snapshot_id": security_price_snapshot_id,
        "benchmark_price_snapshot_id": benchmark_price_snapshot_id,
        "evaluated_at": canonical_datetime_text(outcome_evaluated_at),
        "entry_date": outcome.entry_date.isoformat(),
        "exit_date": outcome.exit_date.isoformat(),
        "security_entry_price": canonical_decimal_text(
            outcome.security_entry_price,
            quantum=PRICE_QUANT,
        ),
        "security_exit_price": canonical_decimal_text(
            outcome.security_exit_price,
            quantum=PRICE_QUANT,
        ),
        "benchmark_entry_price": canonical_decimal_text(
            outcome.benchmark_entry_price,
            quantum=PRICE_QUANT,
        ),
        "benchmark_exit_price": canonical_decimal_text(
            outcome.benchmark_exit_price,
            quantum=PRICE_QUANT,
        ),
        "realised_return": canonical_decimal_text(
            outcome.realised_return,
            quantum=RETURN_QUANT,
        ),
        "benchmark_return": canonical_decimal_text(
            outcome.benchmark_return,
            quantum=RETURN_QUANT,
        ),
        "excess_return": canonical_decimal_text(
            outcome.excess_return,
            quantum=RETURN_QUANT,
        ),
        "max_drawdown": canonical_decimal_text(
            outcome.max_drawdown,
            quantum=RETURN_QUANT,
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
