"""Evaluate stored model predictions against an adjusted-close benchmark.

Single prediction:
    python pipelines/evaluate_predictions.py \
      --prediction-id PREDICTION_ID \
      --benchmark SPY

Batch mode:
    python pipelines/evaluate_predictions.py --benchmark SPY
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional, Sequence

import _bootstrap  # noqa: F401
from _common import open_research_database, utc_now

from sqlalchemy import select

from quantfore_research.db import session_scope
from quantfore_research.evaluation import (
    OutcomeResult,
    calculate_forward_outcome,
    parse_horizon,
)
from quantfore_research.models import (
    ModelOutcome,
    ModelPrediction,
    Price,
    Security,
    SourceSnapshot,
)


PRICE_QUANT = Decimal("0.000001")
RETURN_QUANT = Decimal("0.00000001")


@dataclass(frozen=True)
class PriceSnapshotSelection:
    snapshot: SourceSnapshot
    prices: tuple[Price, ...]


@dataclass(frozen=True)
class EvaluationReport:
    status: str
    lines: tuple[str, ...]


class ImmaturePrediction(ValueError):
    """Raised internally when no security snapshot has enough future prices."""

    def __init__(self, *, required_observations: int, available_observations: int):
        self.required_observations = required_observations
        self.available_observations = available_observations
        super().__init__(
            f"requires {required_observations} future observations; "
            f"found {available_observations}"
        )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate stored model predictions.")
    parser.add_argument(
        "--prediction-id",
        help="Evaluate one prediction. Omit to evaluate all stored predictions.",
    )
    parser.add_argument("--benchmark", required=True, help="Benchmark ticker, e.g. SPY.")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    return parser.parse_args(argv)


def decimal_text(value: object) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return format(decimal_value.normalize(), "f")


def canonical_decimal_text(value: object, *, quantum: Decimal) -> str:
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    return decimal_text(decimal_value.quantize(quantum))


def immutable_outcome_hash(
    *,
    prediction: ModelPrediction,
    ticker: str,
    benchmark: Security,
    security_price_snapshot_id: str,
    benchmark_price_snapshot_id: str,
    outcome: object,
) -> str:
    """Build the canonical SHA-256 hash for one outcome and its lineage."""

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


def load_price_snapshot_candidates(
    session,
    *,
    security_id: str,
) -> list[PriceSnapshotSelection]:
    snapshots = list(
        session.scalars(
            select(SourceSnapshot)
            .join(Price, Price.source_snapshot_id == SourceSnapshot.snapshot_id)
            .where(Price.security_id == security_id)
            .where(Price.adj_close.is_not(None))
            .distinct()
            .order_by(
                SourceSnapshot.retrieved_at.desc(),
                SourceSnapshot.created_at.desc(),
                SourceSnapshot.snapshot_id.desc(),
            )
        )
    )
    return [
        PriceSnapshotSelection(
            snapshot=snapshot,
            prices=tuple(
                session.scalars(
                    select(Price)
                    .where(Price.security_id == security_id)
                    .where(Price.source_snapshot_id == snapshot.snapshot_id)
                    .where(Price.adj_close.is_not(None))
                    .order_by(Price.date)
                )
            ),
        )
        for snapshot in snapshots
    ]


def load_stored_snapshot_prices(
    session,
    *,
    security_id: str,
    snapshot_id: str,
    lineage_name: str,
) -> tuple[Price, ...]:
    if session.get(SourceSnapshot, snapshot_id) is None:
        raise ValueError(f"stored {lineage_name} snapshot does not exist: {snapshot_id}")
    prices = tuple(
        session.scalars(
            select(Price)
            .where(Price.security_id == security_id)
            .where(Price.source_snapshot_id == snapshot_id)
            .where(Price.adj_close.is_not(None))
            .order_by(Price.date)
        )
    )
    if not prices:
        raise ValueError(
            f"stored {lineage_name} snapshot {snapshot_id} has no adjusted-close prices"
        )
    return prices


def _future_prices(prices: Sequence[Price], *, prediction_date) -> list[Price]:
    return [price for price in prices if price.date > prediction_date]


def _snapshots_align(
    security_prices: Sequence[Price],
    benchmark_prices: Sequence[Price],
    *,
    prediction_date,
    required_observations: int,
) -> bool:
    security_future = _future_prices(
        security_prices,
        prediction_date=prediction_date,
    )
    benchmark_future = _future_prices(
        benchmark_prices,
        prediction_date=prediction_date,
    )
    evaluation_dates = [
        price.date for price in security_future[:required_observations]
    ]
    if len(evaluation_dates) < required_observations or not benchmark_future:
        return False
    if benchmark_future[0].date != evaluation_dates[0]:
        return False
    benchmark_dates = [
        price.date
        for price in benchmark_future
        if evaluation_dates[0] <= price.date <= evaluation_dates[-1]
    ]
    return benchmark_dates == evaluation_dates


def select_complete_snapshot_pair(
    session,
    *,
    prediction: ModelPrediction,
    ticker: str,
    benchmark: Security,
) -> tuple[PriceSnapshotSelection, PriceSnapshotSelection]:
    required_observations = parse_horizon(prediction.horizon) + 1
    security_candidates = load_price_snapshot_candidates(
        session,
        security_id=prediction.security_id,
    )
    security_complete = [
        candidate
        for candidate in security_candidates
        if len(
            _future_prices(
                candidate.prices,
                prediction_date=prediction.asof_date,
            )
        )
        >= required_observations
    ]
    if not security_complete:
        available = max(
            (
                len(
                    _future_prices(
                        candidate.prices,
                        prediction_date=prediction.asof_date,
                    )
                )
                for candidate in security_candidates
            ),
            default=0,
        )
        raise ImmaturePrediction(
            required_observations=required_observations,
            available_observations=available,
        )

    benchmark_candidates = load_price_snapshot_candidates(
        session,
        security_id=benchmark.security_id,
    )
    if not benchmark_candidates:
        raise ValueError(
            f"no adjusted-close price snapshots found for benchmark {benchmark.ticker}"
        )
    benchmark_complete = [
        candidate
        for candidate in benchmark_candidates
        if len(
            _future_prices(
                candidate.prices,
                prediction_date=prediction.asof_date,
            )
        )
        >= required_observations
    ]
    if not benchmark_complete:
        available = max(
            len(
                _future_prices(
                    candidate.prices,
                    prediction_date=prediction.asof_date,
                )
            )
            for candidate in benchmark_candidates
        )
        raise ValueError(
            f"benchmark {benchmark.ticker} has insufficient future prices for "
            f"prediction {prediction.prediction_id}: requires {required_observations}, "
            f"found {available}"
        )

    for security_selection in security_complete:
        for benchmark_selection in benchmark_complete:
            if _snapshots_align(
                security_selection.prices,
                benchmark_selection.prices,
                prediction_date=prediction.asof_date,
                required_observations=required_observations,
            ):
                return security_selection, benchmark_selection

    raise ValueError(
        "no complete aligned price snapshot pair found for "
        f"ticker={ticker} benchmark={benchmark.ticker} "
        f"prediction_id={prediction.prediction_id}"
    )


def _calculated_outcome(
    *,
    prediction: ModelPrediction,
    security_prices: Sequence[Price],
    benchmark_prices: Sequence[Price],
) -> OutcomeResult:
    return calculate_forward_outcome(
        security_prices,
        benchmark_prices,
        prediction_date=prediction.asof_date,
        horizon=prediction.horizon,
    )


def _conflicting_rerun(prediction: ModelPrediction, detail: str) -> ValueError:
    return ValueError(
        "conflicting outcome rerun; refusing to overwrite "
        f"prediction_id={prediction.prediction_id}: {detail}"
    )


def validate_existing_outcome(
    session,
    *,
    prediction: ModelPrediction,
    ticker: str,
    benchmark: Security,
    existing: ModelOutcome,
) -> EvaluationReport:
    if existing.benchmark_security_id != benchmark.security_id:
        raise _conflicting_rerun(
            prediction,
            f"stored benchmark_security_id={existing.benchmark_security_id}, "
            f"requested benchmark_security_id={benchmark.security_id}",
        )

    stored_hash = immutable_outcome_hash(
        prediction=prediction,
        ticker=ticker,
        benchmark=benchmark,
        security_price_snapshot_id=existing.security_price_snapshot_id,
        benchmark_price_snapshot_id=existing.benchmark_price_snapshot_id,
        outcome=existing,
    )
    if stored_hash != existing.immutable_hash:
        raise _conflicting_rerun(
            prediction,
            "stored outcome fields do not match its immutable hash",
        )

    security_prices = load_stored_snapshot_prices(
        session,
        security_id=prediction.security_id,
        snapshot_id=existing.security_price_snapshot_id,
        lineage_name="security price",
    )
    benchmark_prices = load_stored_snapshot_prices(
        session,
        security_id=benchmark.security_id,
        snapshot_id=existing.benchmark_price_snapshot_id,
        lineage_name="benchmark price",
    )
    try:
        recalculated = _calculated_outcome(
            prediction=prediction,
            security_prices=security_prices,
            benchmark_prices=benchmark_prices,
        )
    except ValueError as exc:
        raise _conflicting_rerun(
            prediction,
            f"stored snapshot lineage cannot reproduce the outcome: {exc}",
        ) from exc
    recalculated_hash = immutable_outcome_hash(
        prediction=prediction,
        ticker=ticker,
        benchmark=benchmark,
        security_price_snapshot_id=existing.security_price_snapshot_id,
        benchmark_price_snapshot_id=existing.benchmark_price_snapshot_id,
        outcome=recalculated,
    )
    if recalculated_hash != existing.immutable_hash:
        raise _conflicting_rerun(
            prediction,
            "stored snapshot data now produces a different outcome",
        )

    return EvaluationReport(
        status="identical",
        lines=(
            "outcome already exists; skipping "
            f"prediction_id={prediction.prediction_id} ticker={ticker} "
            f"horizon={prediction.horizon} immutable_hash={existing.immutable_hash}",
        ),
    )


def evaluate_prediction(
    session,
    *,
    prediction: ModelPrediction,
    benchmark: Security,
    evaluated_at: Optional[datetime] = None,
) -> EvaluationReport:
    ticker = prediction.security.ticker
    existing = session.scalar(
        select(ModelOutcome).where(
            ModelOutcome.prediction_id == prediction.prediction_id
        )
    )
    if existing is not None:
        return validate_existing_outcome(
            session,
            prediction=prediction,
            ticker=ticker,
            benchmark=benchmark,
            existing=existing,
        )

    try:
        security_selection, benchmark_selection = select_complete_snapshot_pair(
            session,
            prediction=prediction,
            ticker=ticker,
            benchmark=benchmark,
        )
    except ImmaturePrediction as exc:
        return EvaluationReport(
            status="immature",
            lines=(
                "prediction immature; skipping "
                f"prediction_id={prediction.prediction_id} ticker={ticker} "
                f"horizon={prediction.horizon} "
                f"required_observations={exc.required_observations} "
                f"available_observations={exc.available_observations}",
            ),
        )

    calculated = _calculated_outcome(
        prediction=prediction,
        security_prices=security_selection.prices,
        benchmark_prices=benchmark_selection.prices,
    )
    immutable_hash = immutable_outcome_hash(
        prediction=prediction,
        ticker=ticker,
        benchmark=benchmark,
        security_price_snapshot_id=security_selection.snapshot.snapshot_id,
        benchmark_price_snapshot_id=benchmark_selection.snapshot.snapshot_id,
        outcome=calculated,
    )
    outcome = ModelOutcome(
        prediction_id=prediction.prediction_id,
        benchmark_security_id=benchmark.security_id,
        security_price_snapshot_id=security_selection.snapshot.snapshot_id,
        benchmark_price_snapshot_id=benchmark_selection.snapshot.snapshot_id,
        entry_date=calculated.entry_date,
        exit_date=calculated.exit_date,
        security_entry_price=calculated.security_entry_price,
        security_exit_price=calculated.security_exit_price,
        benchmark_entry_price=calculated.benchmark_entry_price,
        benchmark_exit_price=calculated.benchmark_exit_price,
        realised_return=calculated.realised_return,
        benchmark_return=calculated.benchmark_return,
        excess_return=calculated.excess_return,
        max_drawdown=calculated.max_drawdown,
        evaluated_at=evaluated_at or utc_now(),
        immutable_hash=immutable_hash,
    )
    session.add(outcome)
    session.flush()

    return EvaluationReport(
        status="evaluated",
        lines=(
            f"evaluated prediction ticker={ticker} horizon={prediction.horizon}",
            f"entry_date={calculated.entry_date} exit_date={calculated.exit_date}",
            "realised_return="
            f"{decimal_text(calculated.realised_return)} "
            f"benchmark_return={decimal_text(calculated.benchmark_return)}",
            "excess_return="
            f"{decimal_text(calculated.excess_return)} "
            f"max_drawdown={decimal_text(calculated.max_drawdown)}",
            "prediction_id="
            f"{prediction.prediction_id} "
            f"security_price_snapshot_id={security_selection.snapshot.snapshot_id} "
            f"benchmark_price_snapshot_id={benchmark_selection.snapshot.snapshot_id}",
        ),
    )


def prediction_ids_to_evaluate(session, prediction_id: Optional[str]) -> list[str]:
    if prediction_id:
        prediction = session.get(ModelPrediction, prediction_id)
        if prediction is None:
            raise ValueError(f"unknown prediction: {prediction_id}")
        return [prediction.prediction_id]
    return list(
        session.scalars(
            select(ModelPrediction.prediction_id).order_by(
                ModelPrediction.asof_date,
                ModelPrediction.prediction_id,
            )
        )
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    benchmark_ticker = args.benchmark.upper().strip()
    if not benchmark_ticker:
        raise ValueError("--benchmark is required")

    session_factory = open_research_database(args.database_url)
    with session_factory() as session:
        benchmark = session.scalar(
            select(Security).where(Security.ticker == benchmark_ticker)
        )
        if benchmark is None:
            raise ValueError(f"unknown benchmark: {benchmark_ticker}")
        benchmark_id = benchmark.security_id
        prediction_ids = prediction_ids_to_evaluate(session, args.prediction_id)

    if not prediction_ids:
        print("no stored predictions found; nothing to evaluate")
        return 0

    for prediction_id in prediction_ids:
        with session_scope(session_factory) as session:
            prediction = session.get(ModelPrediction, prediction_id)
            benchmark = session.get(Security, benchmark_id)
            if prediction is None:
                raise ValueError(f"prediction disappeared during evaluation: {prediction_id}")
            if benchmark is None:
                raise ValueError(
                    f"benchmark disappeared during evaluation: {benchmark_ticker}"
                )
            report = evaluate_prediction(
                session,
                prediction=prediction,
                benchmark=benchmark,
            )
        print("\n".join(report.lines))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
