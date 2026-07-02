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
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Sequence

import _bootstrap  # noqa: F401
from _common import open_research_database, utc_now

from sqlalchemy import select

from quantfore_research.db import session_scope
from quantfore_research.evaluation import (
    OutcomeResult,
    canonical_datetime_text,
    calculate_forward_outcome,
    decimal_text,
    immutable_outcome_hash,
    normalized_utc,
    parse_horizon,
)
from quantfore_research.models import (
    ModelOutcome,
    ModelPrediction,
    Price,
    Security,
    SourceSnapshot,
)
from quantfore_research.ingest.point_in_time_equities import deterministic_id


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

    def __init__(
        self,
        *,
        required_observations: int,
        available_observations: int,
        exit_date: Optional[date] = None,
    ):
        self.required_observations = required_observations
        self.available_observations = available_observations
        self.exit_date = exit_date
        super().__init__(
            f"requires {required_observations} future observations; "
            f"found {available_observations}"
        )


class BenchmarkUnavailable(ValueError):
    """Raised internally when benchmark prices are absent or still incomplete."""


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate stored model predictions.")
    parser.add_argument(
        "--prediction-id",
        help="Evaluate one prediction. Omit to evaluate all stored predictions.",
    )
    parser.add_argument("--benchmark", required=True, help="Benchmark ticker, e.g. SPY.")
    parser.add_argument("--database-url", help="Override QUANTFORE_DATABASE_URL.")
    return parser.parse_args(argv)


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


def _evaluation_dates(
    prices: Sequence[Price],
    *,
    prediction_date,
    required_observations: int,
) -> list[date]:
    return [
        price.date
        for price in _future_prices(prices, prediction_date=prediction_date)[
            :required_observations
        ]
    ]


def _validate_snapshot_timing(
    selection: PriceSnapshotSelection,
    *,
    exit_date: date,
    evaluated_at: datetime,
    lineage_name: str,
) -> None:
    retrieved_at = normalized_utc(selection.snapshot.retrieved_at)
    if retrieved_at.date() < exit_date:
        raise ValueError(
            f"{lineage_name} snapshot {selection.snapshot.snapshot_id} was retrieved "
            f"on {retrieved_at.date()} before evaluation exit date {exit_date}"
        )
    if retrieved_at > evaluated_at:
        raise ValueError(
            f"{lineage_name} snapshot {selection.snapshot.snapshot_id} was retrieved "
            f"at {canonical_datetime_text(retrieved_at)} after evaluated_at "
            f"{canonical_datetime_text(evaluated_at)}"
        )


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
    evaluated_at: datetime,
) -> tuple[PriceSnapshotSelection, PriceSnapshotSelection]:
    evaluated_at = normalized_utc(evaluated_at)
    required_observations = parse_horizon(prediction.horizon) + 1
    security_candidates = load_price_snapshot_candidates(
        session,
        security_id=prediction.security_id,
    )
    future_counts = [
        len(
            _future_prices(
                candidate.prices,
                prediction_date=prediction.asof_date,
            )
        )
        for candidate in security_candidates
    ]
    security_complete = [
        candidate
        for candidate, count in zip(security_candidates, future_counts)
        if count >= required_observations
    ]
    if not security_complete:
        raise ImmaturePrediction(
            required_observations=required_observations,
            available_observations=max(future_counts, default=0),
        )

    security_mature: list[PriceSnapshotSelection] = []
    future_exit_dates: list[date] = []
    for candidate in security_complete:
        evaluation_dates = _evaluation_dates(
            candidate.prices,
            prediction_date=prediction.asof_date,
            required_observations=required_observations,
        )
        exit_date = evaluation_dates[-1]
        if exit_date > evaluated_at.date():
            future_exit_dates.append(exit_date)
            continue
        _validate_snapshot_timing(
            candidate,
            exit_date=exit_date,
            evaluated_at=evaluated_at,
            lineage_name="security price",
        )
        security_mature.append(candidate)

    if not security_mature:
        raise ImmaturePrediction(
            required_observations=required_observations,
            available_observations=max(future_counts, default=0),
            exit_date=min(future_exit_dates) if future_exit_dates else None,
        )

    benchmark_candidates = load_price_snapshot_candidates(
        session,
        security_id=benchmark.security_id,
    )
    if not benchmark_candidates:
        raise BenchmarkUnavailable(
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
        raise BenchmarkUnavailable(
            f"benchmark {benchmark.ticker} has insufficient future prices for "
            f"prediction {prediction.prediction_id}: requires {required_observations}, "
            f"found {available}"
        )

    for security_selection in security_mature:
        security_exit_date = _evaluation_dates(
            security_selection.prices,
            prediction_date=prediction.asof_date,
            required_observations=required_observations,
        )[-1]
        for benchmark_selection in benchmark_complete:
            _validate_snapshot_timing(
                benchmark_selection,
                exit_date=security_exit_date,
                evaluated_at=evaluated_at,
                lineage_name="benchmark price",
            )
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

    stored_evaluated_at = normalized_utc(existing.evaluated_at)
    if existing.exit_date > stored_evaluated_at.date():
        raise _conflicting_rerun(
            prediction,
            f"stored exit_date={existing.exit_date} is after "
            f"evaluated_at={canonical_datetime_text(stored_evaluated_at)}",
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
    security_snapshot = session.get(
        SourceSnapshot,
        existing.security_price_snapshot_id,
    )
    benchmark_snapshot = session.get(
        SourceSnapshot,
        existing.benchmark_price_snapshot_id,
    )
    _validate_snapshot_timing(
        PriceSnapshotSelection(security_snapshot, security_prices),
        exit_date=existing.exit_date,
        evaluated_at=stored_evaluated_at,
        lineage_name="security price",
    )
    _validate_snapshot_timing(
        PriceSnapshotSelection(benchmark_snapshot, benchmark_prices),
        exit_date=existing.exit_date,
        evaluated_at=stored_evaluated_at,
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
        evaluated_at=stored_evaluated_at,
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
    evaluation_timestamp = normalized_utc(evaluated_at or utc_now())
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
            evaluated_at=evaluation_timestamp,
        )
    except ImmaturePrediction as exc:
        timing = ""
        if exc.exit_date is not None:
            timing = (
                f" exit_date={exc.exit_date} "
                f"evaluated_at={evaluation_timestamp.date()}"
            )
        return EvaluationReport(
            status="immature",
            lines=(
                "prediction immature; skipping "
                f"prediction_id={prediction.prediction_id} ticker={ticker} "
                f"horizon={prediction.horizon} "
                f"required_observations={exc.required_observations} "
                f"available_observations={exc.available_observations}"
                f"{timing}",
            ),
        )
    except BenchmarkUnavailable as exc:
        return EvaluationReport(
            status="benchmark_unavailable",
            lines=(
                "benchmark unavailable; skipping "
                f"prediction_id={prediction.prediction_id} ticker={ticker} "
                f"horizon={prediction.horizon} benchmark={benchmark.ticker} "
                f"detail={exc}",
            ),
        )

    calculated = _calculated_outcome(
        prediction=prediction,
        security_prices=security_selection.prices,
        benchmark_prices=benchmark_selection.prices,
    )
    if calculated.exit_date > evaluation_timestamp.date():
        raise RuntimeError(
            "outcome selection violated maturity contract: "
            f"exit_date={calculated.exit_date} "
            f"evaluated_at={evaluation_timestamp.date()}"
        )
    immutable_hash = immutable_outcome_hash(
        prediction=prediction,
        ticker=ticker,
        benchmark=benchmark,
        security_price_snapshot_id=security_selection.snapshot.snapshot_id,
        benchmark_price_snapshot_id=benchmark_selection.snapshot.snapshot_id,
        outcome=calculated,
        evaluated_at=evaluation_timestamp,
    )
    outcome = ModelOutcome(
        outcome_id=deterministic_id("pit_outcome", prediction.prediction_id),
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
        evaluated_at=evaluation_timestamp,
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
