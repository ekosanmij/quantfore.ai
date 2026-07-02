"""Database execution engine for historical baseline predictions and outcomes."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Mapping, Optional, Sequence

from sqlalchemy import func, select

from quantfore_research.backtest.baseline import select_monthly_rebalance_dates
from quantfore_research.backtest.contract import BACKTEST_CONTRACT
from quantfore_research.backtest.datasets import (
    SYNTHETIC_DATASET,
    BacktestDataset,
    validate_experiment_namespace,
)
from quantfore_research.evaluation import (
    calculate_forward_outcome,
    immutable_outcome_hash,
    normalized_utc,
    parse_horizon,
)
from quantfore_research.features import (
    FEATURE_VERSION,
    NotEnoughPriceHistory,
    calculate_baseline_price_features,
)
from quantfore_research.models import (
    Feature,
    FeatureSet,
    ExperimentRegistry,
    ModelOutcome,
    ModelPrediction,
    Price,
    ScoreDriver as ScoreDriverRow,
    Security,
    SourceSnapshot,
)
from quantfore_research.scoring import (
    calculate_baseline_score,
    immutable_prediction_hash,
)


FEATURE_SET_NAME = "baseline_features"
FEATURE_VALUE_QUANT = Decimal("0.0000000001")
BASELINE_HYPOTHESIS_ID = "H5_baseline_score_positive_excess_return"
BASELINE_HYPOTHESIS = (
    "Higher baseline scores should be positively associated with subsequent "
    "benchmark-relative returns."
)
SYNTHETIC_EXPERIMENT_NOTE = (
    "Synthetic engineering backtest only; not validation evidence and not "
    "eligible for investment-performance claims."
)
PROTOTYPE_REAL_HYPOTHESIS_ID = "H6_prototype_real_baseline_score_excess_return"
PROTOTYPE_REAL_EXPERIMENT_NOTE = (
    "Prototype real-data trial over a retrospective fixed universe; not "
    "point-in-time universe validation and not eligible for performance claims."
)


@dataclass(frozen=True)
class SkippedObservation:
    """One security/date operation that could not be completed."""

    ticker: str
    prediction_date: date
    stage: str
    reason: str


class OutcomeDataUnavailable(ValueError):
    """Raised when one security cannot produce an aligned matured outcome."""


@dataclass(frozen=True)
class BacktestRunResult:
    """Complete lineage manifest returned by one historical runner invocation."""

    experiment_id: str
    dataset_kind: str
    model_version: str
    benchmark: str
    source_snapshot_ids: tuple[str, ...]
    prediction_dates: tuple[date, ...]
    security_tickers: tuple[str, ...]
    prediction_ids: tuple[str, ...]
    outcome_hashes: tuple[str, ...]
    skipped_observations: tuple[SkippedObservation, ...]
    created_predictions: int
    existing_predictions: int
    created_outcomes: int
    existing_outcomes: int
    universe_file_sha256: Optional[str]
    audit_sha256: Optional[str]

    def to_manifest(self) -> Mapping[str, object]:
        """Return JSON-compatible, deterministically ordered run lineage."""

        return {
            "experiment_id": self.experiment_id,
            "dataset_kind": self.dataset_kind,
            "model_version": self.model_version,
            "benchmark": self.benchmark,
            "universe_file_sha256": self.universe_file_sha256,
            "audit_sha256": self.audit_sha256,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "prediction_dates": [value.isoformat() for value in self.prediction_dates],
            "security_tickers": list(self.security_tickers),
            "prediction_ids": list(self.prediction_ids),
            "outcome_hashes": list(self.outcome_hashes),
            "skipped_observations": [
                {
                    **asdict(value),
                    "prediction_date": value.prediction_date.isoformat(),
                }
                for value in self.skipped_observations
            ],
        }


def register_backtest_experiment(
    session,
    *,
    result: BacktestRunResult,
    source_snapshot: SourceSnapshot,
    source_snapshots: Optional[Sequence[SourceSnapshot]] = None,
    dataset: BacktestDataset = SYNTHETIC_DATASET,
    start_date: date,
    end_date: date,
    horizon: str,
    frequency: str,
    model_version: str,
    code_commit: Optional[str],
    result_uri: str,
) -> ExperimentRegistry:
    """Create or strictly validate one namespaced experiment registry entry."""

    result_uri = result_uri.strip() if isinstance(result_uri, str) else ""
    if not result_uri:
        raise ValueError("experiment result_uri is required")
    snapshots = tuple(source_snapshots or (source_snapshot,))
    config: dict[str, object] = {
        "hypothesis": BASELINE_HYPOTHESIS,
        "model_version": model_version,
        "feature_version": FEATURE_VERSION,
        "universe": list(result.security_tickers),
        "benchmark": result.benchmark,
        "horizon": horizon,
        "date_range": {
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        },
        "frequency": frequency,
        "number_of_securities": len(result.security_tickers),
        "number_of_periods": len(result.prediction_dates),
        "claims_eligible": False,
        "dataset_kind": dataset.dataset_kind,
    }
    if dataset.is_prototype_real:
        assert dataset.universe is not None
        assert dataset.audit is not None
        config.update(
            {
                "universe_definition": [
                    dict(row) for row in dataset.universe.rows
                ],
                "universe_file": dataset.universe.path,
                "universe_file_sha256": dataset.universe.sha256,
                "data_audit_sha256": dataset.audit.sha256,
                "data_audit": dataset.audit.to_dict(),
                "source_snapshot_hashes": sorted(
                    snapshot.source_hash for snapshot in snapshots
                ),
            }
        )
        data_snapshot_hash = hashlib.sha256(
            json.dumps(
                {
                    "source_snapshot_hashes": sorted(
                        snapshot.source_hash for snapshot in snapshots
                    ),
                    "universe_file_sha256": dataset.universe.sha256,
                    "data_audit_sha256": dataset.audit.sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        hypothesis_id = PROTOTYPE_REAL_HYPOTHESIS_ID
        notes = PROTOTYPE_REAL_EXPERIMENT_NOTE
    else:
        data_snapshot_hash = source_snapshot.source_hash
        hypothesis_id = BASELINE_HYPOTHESIS_ID
        notes = SYNTHETIC_EXPERIMENT_NOTE
    expected_values = {
        "hypothesis_id": hypothesis_id,
        "data_snapshot_hash": data_snapshot_hash,
        "code_commit": code_commit,
        "config_json": config,
        "result_uri": result_uri,
        "notes": notes,
    }
    existing = session.get(ExperimentRegistry, result.experiment_id)
    if existing is not None:
        conflicts = [
            field_name
            for field_name, expected in expected_values.items()
            if getattr(existing, field_name) != expected
        ]
        if conflicts:
            raise ValueError(
                "conflicting experiment registration; refusing to overwrite "
                f"experiment_id={result.experiment_id} fields={','.join(conflicts)}"
            )
        return existing

    experiment = ExperimentRegistry(
        experiment_id=result.experiment_id,
        **expected_values,
    )
    session.add(experiment)
    session.flush()
    return experiment


def _snapshot_tickers(session, snapshot_id: str) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            select(Security.ticker)
            .join(Price, Price.security_id == Security.security_id)
            .where(Price.source_snapshot_id == snapshot_id)
            .where(Price.adj_close.is_not(None))
            .distinct()
            .order_by(Security.ticker)
        )
    )


def select_backtest_source_snapshot(
    session,
    *,
    benchmark_ticker: str,
    source_snapshot_id: Optional[str] = None,
    ranked_universe: Optional[Sequence[str]] = None,
) -> SourceSnapshot:
    """Select the snapshot with the broadest matching ranked universe."""

    benchmark = session.scalar(
        select(Security).where(Security.ticker == benchmark_ticker)
    )
    if benchmark is None:
        raise ValueError(f"unknown benchmark: {benchmark_ticker}")

    if source_snapshot_id is not None:
        snapshot = session.get(SourceSnapshot, source_snapshot_id)
        if snapshot is None:
            raise ValueError(f"unknown source snapshot: {source_snapshot_id}")
        tickers = set(_snapshot_tickers(session, snapshot.snapshot_id))
        if benchmark_ticker not in tickers:
            raise ValueError(
                f"source snapshot {source_snapshot_id} has no {benchmark_ticker} prices"
            )
        return snapshot

    candidates = list(
        session.scalars(
            select(SourceSnapshot)
            .join(Price, Price.source_snapshot_id == SourceSnapshot.snapshot_id)
            .where(Price.security_id == benchmark.security_id)
            .where(Price.adj_close.is_not(None))
            .distinct()
        )
    )
    if not candidates:
        raise ValueError(
            f"no adjusted-close price snapshot found for benchmark {benchmark_ticker}"
        )
    ranked_tickers = set(ranked_universe or BACKTEST_CONTRACT.ranked_universe)
    scored_candidates = []
    for snapshot in candidates:
        tickers = set(_snapshot_tickers(session, snapshot.snapshot_id))
        matching_securities = len(tickers.intersection(ranked_tickers))
        scored_candidates.append((matching_securities, snapshot))
    matching_count, selected = max(
        scored_candidates,
        key=lambda value: (
            value[0],
            normalized_utc(value[1].retrieved_at),
            value[1].snapshot_id,
        ),
    )
    if matching_count == 0:
        raise ValueError(
            "no benchmark snapshot contains any Sprint 5 synthetic securities"
        )
    return selected


def select_backtest_source_map(
    session,
    *,
    dataset: BacktestDataset,
    source_snapshot_id: Optional[str] = None,
) -> dict[str, SourceSnapshot]:
    """Resolve one complete price snapshot per required series."""

    contract = dataset.contract
    if not dataset.is_prototype_real:
        snapshot = select_backtest_source_snapshot(
            session,
            benchmark_ticker=contract.benchmark,
            source_snapshot_id=source_snapshot_id,
            ranked_universe=contract.ranked_universe,
        )
        available = set(_snapshot_tickers(session, snapshot.snapshot_id))
        return {
            ticker: snapshot
            for ticker in contract.price_panel_universe
            if ticker in available
        }

    required = contract.price_panel_universe
    assert dataset.audit is not None
    audited_hashes = set(dataset.audit.source_snapshot_hashes)
    if source_snapshot_id is not None:
        snapshot = session.get(SourceSnapshot, source_snapshot_id)
        if snapshot is None:
            raise ValueError(f"unknown source snapshot: {source_snapshot_id}")
        if snapshot.vendor.casefold() != "tiingo":
            raise ValueError("prototype_real price snapshots must use vendor Tiingo")
        if snapshot.source_hash not in audited_hashes:
            raise ValueError("pinned real-data snapshot was not included in the audit")
        available = set(_snapshot_tickers(session, snapshot.snapshot_id))
        missing = sorted(set(required) - available)
        if missing:
            raise ValueError(
                "pinned real-data snapshot is missing tickers: "
                + ",".join(missing)
            )
        return {ticker: snapshot for ticker in required}

    source_map = {}
    for ticker in required:
        candidates = list(
            session.scalars(
                select(SourceSnapshot)
                .join(Price, Price.source_snapshot_id == SourceSnapshot.snapshot_id)
                .join(Security, Security.security_id == Price.security_id)
                .where(Security.ticker == ticker)
                .where(SourceSnapshot.vendor == "Tiingo")
                .where(SourceSnapshot.source_hash.in_(audited_hashes))
                .where(Price.adj_close.is_not(None))
                .distinct()
            )
        )
        if not candidates:
            raise ValueError(f"no Tiingo price snapshot found for {ticker}")
        scored = []
        for snapshot in candidates:
            row_count = session.scalar(
                select(func.count())
                .select_from(Price)
                .join(Security, Security.security_id == Price.security_id)
                .where(Security.ticker == ticker)
                .where(Price.source_snapshot_id == snapshot.snapshot_id)
                .where(Price.adj_close.is_not(None))
            )
            scored.append((int(row_count or 0), snapshot))
        _, selected = max(
            scored,
            key=lambda value: (
                value[0],
                normalized_utc(value[1].retrieved_at),
                value[1].snapshot_id,
            ),
        )
        source_map[ticker] = selected
    return source_map


def _load_snapshot_prices(
    session,
    *,
    security_id: str,
    snapshot_id: str,
) -> tuple[Price, ...]:
    return tuple(
        session.scalars(
            select(Price)
            .where(Price.security_id == security_id)
            .where(Price.source_snapshot_id == snapshot_id)
            .where(Price.adj_close.is_not(None))
            .order_by(Price.date)
        )
    )


def discover_eligible_prediction_dates(
    benchmark_prices: Sequence[Price],
    *,
    start_date: date,
    end_date: date,
    contract=BACKTEST_CONTRACT,
) -> tuple[date, ...]:
    """Discover monthly dates with complete benchmark history and outcomes."""

    dates = [price.date for price in benchmark_prices]
    month_ends = select_monthly_rebalance_dates(
        dates,
        start_date=start_date,
        end_date=end_date,
    )
    eligible_dates = []
    for prediction_date in month_ends:
        history_count = bisect_right(dates, prediction_date)
        future_count = len(dates) - history_count
        if (
            history_count >= contract.minimum_history_sessions
            and future_count >= contract.evaluation_sessions
        ):
            eligible_dates.append(prediction_date)
    return tuple(eligible_dates)


def _feature_set_id(
    *,
    feature_set_name: str,
    ticker: str,
    prediction_date: date,
) -> str:
    return (
        f"{feature_set_name}_{FEATURE_VERSION}_{ticker}_"
        f"{prediction_date.isoformat()}"
    )


def _available_at(prediction_date: date) -> datetime:
    return datetime(
        prediction_date.year,
        prediction_date.month,
        prediction_date.day,
        tzinfo=timezone.utc,
    )


def _store_or_validate_feature_set(
    session,
    *,
    security: Security,
    ticker: str,
    prediction_date: date,
    source_snapshot: SourceSnapshot,
    feature_set_name: str,
    feature_values: Mapping[str, Decimal],
    code_commit: Optional[str],
) -> FeatureSet:
    feature_set_id = _feature_set_id(
        feature_set_name=feature_set_name,
        ticker=ticker,
        prediction_date=prediction_date,
    )
    existing = session.get(FeatureSet, feature_set_id)
    if existing is not None:
        if (
            existing.name != feature_set_name
            or existing.version != FEATURE_VERSION
            or existing.asof_date != prediction_date
            or existing.source_snapshot_id != source_snapshot.snapshot_id
        ):
            raise ValueError(
                f"conflicting historical feature set: {feature_set_id}"
            )
        stored_features = list(
            session.scalars(
                select(Feature)
                .where(Feature.feature_set_id == feature_set_id)
                .where(Feature.security_id == security.security_id)
                .where(Feature.asof_date == prediction_date)
            )
        )
        stored_values = {
            feature.feature_name: feature.value.quantize(FEATURE_VALUE_QUANT)
            for feature in stored_features
        }
        expected_values = {
            name: Decimal(value).quantize(FEATURE_VALUE_QUANT)
            for name, value in feature_values.items()
        }
        if stored_values != expected_values:
            raise ValueError(
                f"stored feature values do not reproduce for {ticker} on {prediction_date}"
            )
        return existing

    feature_set = FeatureSet(
        feature_set_id=feature_set_id,
        name=feature_set_name,
        version=FEATURE_VERSION,
        asof_date=prediction_date,
        config_json={
            "ticker": ticker,
            "features": sorted(feature_values),
            "lookbacks": {
                "skip_days": 21,
                "six_month_days": 126,
                "twelve_month_days": 252,
            },
            "price_field": "adj_close",
            "source_snapshot_id": source_snapshot.snapshot_id,
        },
        source_snapshot_id=source_snapshot.snapshot_id,
        code_commit=code_commit,
    )
    session.add(feature_set)
    for feature_name, value in feature_values.items():
        session.add(
            Feature(
                feature_set_id=feature_set_id,
                security_id=security.security_id,
                asof_date=prediction_date,
                available_at=_available_at(prediction_date),
                feature_name=feature_name,
                value=Decimal(value),
                version=FEATURE_VERSION,
                source_snapshot_id=source_snapshot.snapshot_id,
                source_hash=source_snapshot.source_hash,
            )
        )
    session.flush()
    return feature_set


def _store_or_validate_prediction(
    session,
    *,
    security: Security,
    ticker: str,
    prediction_date: date,
    feature_set: FeatureSet,
    feature_values: Mapping[str, Decimal],
    horizon: str,
    model_version: str,
) -> tuple[ModelPrediction, bool]:
    baseline_score = calculate_baseline_score(feature_values)
    immutable_hash = immutable_prediction_hash(
        model_version=model_version,
        ticker=ticker,
        security_id=security.security_id,
        asof_date=prediction_date,
        horizon=horizon,
        feature_set_id=feature_set.feature_set_id,
        score=baseline_score,
    )
    existing = session.scalar(
        select(ModelPrediction)
        .where(ModelPrediction.model_version == model_version)
        .where(ModelPrediction.security_id == security.security_id)
        .where(ModelPrediction.asof_date == prediction_date)
        .where(ModelPrediction.horizon == horizon)
    )
    if existing is not None:
        if existing.immutable_hash != immutable_hash:
            raise ValueError(
                "conflicting historical prediction; refusing to overwrite "
                f"ticker={ticker} asof_date={prediction_date}"
            )
        return existing, False

    prediction = ModelPrediction(
        model_version=model_version,
        security_id=security.security_id,
        feature_set_id=feature_set.feature_set_id,
        asof_date=prediction_date,
        horizon=horizon,
        score=baseline_score.score,
        confidence=baseline_score.confidence,
        action_label=baseline_score.action_label,
        immutable_hash=immutable_hash,
    )
    session.add(prediction)
    session.flush()
    for driver in baseline_score.drivers:
        session.add(
            ScoreDriverRow(
                prediction_id=prediction.prediction_id,
                driver_name=driver.driver_name,
                contribution=driver.contribution,
                evidence_uri=driver.evidence_uri,
            )
        )
    session.flush()
    return prediction, True


def _store_or_validate_outcome(
    session,
    *,
    prediction: ModelPrediction,
    ticker: str,
    security_prices: Sequence[Price],
    benchmark: Security,
    benchmark_prices: Sequence[Price],
    security_source_snapshot: SourceSnapshot,
    benchmark_source_snapshot: SourceSnapshot,
    evaluated_at: datetime,
) -> tuple[ModelOutcome, bool]:
    try:
        calculated = calculate_forward_outcome(
            security_prices,
            benchmark_prices,
            prediction_date=prediction.asof_date,
            horizon=prediction.horizon,
        )
    except ValueError as exc:
        raise OutcomeDataUnavailable(str(exc)) from exc
    existing = session.scalar(
        select(ModelOutcome).where(
            ModelOutcome.prediction_id == prediction.prediction_id
        )
    )
    if existing is not None:
        if (
            existing.benchmark_security_id != benchmark.security_id
            or existing.security_price_snapshot_id
            != security_source_snapshot.snapshot_id
            or existing.benchmark_price_snapshot_id
            != benchmark_source_snapshot.snapshot_id
        ):
            raise ValueError(
                "conflicting historical outcome lineage; refusing to overwrite "
                f"prediction_id={prediction.prediction_id}"
            )
        expected_hash = immutable_outcome_hash(
            prediction=prediction,
            ticker=ticker,
            benchmark=benchmark,
            security_price_snapshot_id=security_source_snapshot.snapshot_id,
            benchmark_price_snapshot_id=benchmark_source_snapshot.snapshot_id,
            outcome=calculated,
            evaluated_at=normalized_utc(existing.evaluated_at),
        )
        if existing.immutable_hash != expected_hash:
            raise ValueError(
                "stored historical outcome does not reproduce "
                f"prediction_id={prediction.prediction_id}"
            )
        return existing, False

    evaluation_timestamp = normalized_utc(evaluated_at)
    if calculated.exit_date > evaluation_timestamp.date():
        raise ValueError(
            f"outcome exit date {calculated.exit_date} is after evaluated_at "
            f"{evaluation_timestamp.date()}"
        )
    immutable_hash = immutable_outcome_hash(
        prediction=prediction,
        ticker=ticker,
        benchmark=benchmark,
        security_price_snapshot_id=security_source_snapshot.snapshot_id,
        benchmark_price_snapshot_id=benchmark_source_snapshot.snapshot_id,
        outcome=calculated,
        evaluated_at=evaluation_timestamp,
    )
    outcome = ModelOutcome(
        prediction_id=prediction.prediction_id,
        benchmark_security_id=benchmark.security_id,
        security_price_snapshot_id=security_source_snapshot.snapshot_id,
        benchmark_price_snapshot_id=benchmark_source_snapshot.snapshot_id,
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
    return outcome, True


def run_historical_backtest(
    session,
    *,
    experiment_id: str,
    benchmark_ticker: str,
    start_date: date,
    end_date: date,
    horizon: str,
    frequency: str,
    source_snapshot_id: Optional[str] = None,
    model_version: Optional[str] = None,
    code_commit: Optional[str] = None,
    evaluated_at: Optional[datetime] = None,
    result_uri: Optional[str] = None,
    dataset: BacktestDataset = SYNTHETIC_DATASET,
) -> BacktestRunResult:
    """Build and evaluate historical predictions without invoking other CLIs."""

    experiment_id = experiment_id.strip() if isinstance(experiment_id, str) else ""
    if not experiment_id:
        raise ValueError("experiment_id is required")
    validate_experiment_namespace(experiment_id, dataset.dataset_kind)
    contract = dataset.contract
    benchmark_ticker = benchmark_ticker.upper().strip()
    if benchmark_ticker != contract.benchmark:
        raise ValueError(
            f"benchmark must match dataset contract {contract.benchmark}; "
            f"found {benchmark_ticker}"
        )
    scope = "Sprint 5" if not dataset.is_prototype_real else "prototype_real"
    if horizon != contract.horizon:
        raise ValueError(
            f"{scope} horizon must be {contract.horizon}; found {horizon}"
        )
    parse_horizon(horizon)
    if frequency != contract.frequency:
        raise ValueError(
            f"{scope} frequency must be {contract.frequency}; found {frequency}"
        )
    resolved_model_version = model_version or contract.model_version
    if resolved_model_version != contract.model_version:
        raise ValueError(
            f"{scope} model must be {contract.model_version}; "
            f"found {resolved_model_version}"
        )
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date")

    source_map = select_backtest_source_map(
        session,
        dataset=dataset,
        source_snapshot_id=source_snapshot_id,
    )
    benchmark_source_snapshot = source_map.get(benchmark_ticker)
    if benchmark_source_snapshot is None:
        raise ValueError(f"selected price sources have no {benchmark_ticker}")
    benchmark = session.scalar(
        select(Security).where(Security.ticker == benchmark_ticker)
    )
    if benchmark is None:
        raise ValueError(f"unknown benchmark: {benchmark_ticker}")
    benchmark_prices = _load_snapshot_prices(
        session,
        security_id=benchmark.security_id,
        snapshot_id=benchmark_source_snapshot.snapshot_id,
    )
    prediction_dates = discover_eligible_prediction_dates(
        benchmark_prices,
        start_date=start_date,
        end_date=end_date,
        contract=contract,
    )
    if len(prediction_dates) < contract.minimum_test_periods:
        raise ValueError(
            "backtest requires at least "
            f"{contract.minimum_test_periods} eligible monthly periods; "
            f"found {len(prediction_dates)}"
        )

    ranked_with_sources = tuple(
        ticker for ticker in contract.ranked_universe if ticker in source_map
    )
    securities = list(
        session.scalars(
            select(Security)
            .where(Security.ticker.in_(ranked_with_sources))
            .order_by(Security.ticker)
        )
    )
    if not securities:
        raise ValueError("no eligible securities found in the selected price sources")
    if dataset.is_prototype_real and {
        security.ticker for security in securities
    } != set(contract.ranked_universe):
        missing = sorted(
            set(contract.ranked_universe)
            - {security.ticker for security in securities}
        )
        raise ValueError("real-data universe is missing securities: " + ",".join(missing))

    unique_snapshots = {
        snapshot.snapshot_id: snapshot for snapshot in source_map.values()
    }
    latest_retrieval = max(
        normalized_utc(snapshot.retrieved_at)
        for snapshot in unique_snapshots.values()
    )
    evaluation_timestamp = normalized_utc(evaluated_at or latest_retrieval)
    prediction_ids: list[str] = []
    outcome_hashes: list[str] = []
    skipped: list[SkippedObservation] = []
    created_predictions = 0
    existing_predictions = 0
    created_outcomes = 0
    existing_outcomes = 0

    for security in securities:
        ticker = security.ticker
        source_snapshot = source_map[ticker]
        prices = _load_snapshot_prices(
            session,
            security_id=security.security_id,
            snapshot_id=source_snapshot.snapshot_id,
        )
        price_dates = [price.date for price in prices]
        for prediction_date in prediction_dates:
            history_end = bisect_right(price_dates, prediction_date)
            history_prices = prices[:history_end]
            if len(history_prices) < contract.minimum_history_sessions:
                skipped.append(
                    SkippedObservation(
                        ticker,
                        prediction_date,
                        "features",
                        "insufficient price history: requires "
                        f"{contract.minimum_history_sessions}, "
                        f"found {len(history_prices)}",
                    )
                )
                continue
            try:
                feature_values = calculate_baseline_price_features(
                    history_prices,
                    asof_date=prediction_date,
                )
            except NotEnoughPriceHistory as exc:
                skipped.append(
                    SkippedObservation(ticker, prediction_date, "features", str(exc))
                )
                continue

            feature_set = _store_or_validate_feature_set(
                session,
                security=security,
                ticker=ticker,
                prediction_date=prediction_date,
                source_snapshot=source_snapshot,
                feature_set_name=dataset.feature_set_name,
                feature_values=feature_values,
                code_commit=code_commit,
            )
            prediction, prediction_created = _store_or_validate_prediction(
                session,
                security=security,
                ticker=ticker,
                prediction_date=prediction_date,
                feature_set=feature_set,
                feature_values=feature_values,
                horizon=horizon,
                model_version=resolved_model_version,
            )
            prediction_ids.append(prediction.prediction_id)
            if prediction_created:
                created_predictions += 1
            else:
                existing_predictions += 1

            future_count = len(prices) - history_end
            if future_count < contract.evaluation_sessions:
                skipped.append(
                    SkippedObservation(
                        ticker,
                        prediction_date,
                        "outcome",
                        "insufficient future prices: requires "
                        f"{contract.evaluation_sessions}, found {future_count}",
                    )
                )
                continue
            try:
                outcome, outcome_created = _store_or_validate_outcome(
                    session,
                    prediction=prediction,
                    ticker=ticker,
                    security_prices=prices,
                    benchmark=benchmark,
                    benchmark_prices=benchmark_prices,
                    security_source_snapshot=source_snapshot,
                    benchmark_source_snapshot=benchmark_source_snapshot,
                    evaluated_at=evaluation_timestamp,
                )
            except OutcomeDataUnavailable as exc:
                skipped.append(
                    SkippedObservation(ticker, prediction_date, "outcome", str(exc))
                )
                continue
            outcome_hashes.append(outcome.immutable_hash)
            if outcome_created:
                created_outcomes += 1
            else:
                existing_outcomes += 1

    result = BacktestRunResult(
        experiment_id=experiment_id,
        dataset_kind=dataset.dataset_kind,
        model_version=resolved_model_version,
        benchmark=benchmark_ticker,
        source_snapshot_ids=tuple(sorted(unique_snapshots)),
        prediction_dates=prediction_dates,
        security_tickers=tuple(security.ticker for security in securities),
        prediction_ids=tuple(sorted(prediction_ids)),
        outcome_hashes=tuple(sorted(outcome_hashes)),
        skipped_observations=tuple(
            sorted(
                skipped,
                key=lambda value: (
                    value.prediction_date,
                    value.ticker,
                    value.stage,
                    value.reason,
                ),
            )
        ),
        created_predictions=created_predictions,
        existing_predictions=existing_predictions,
        created_outcomes=created_outcomes,
        existing_outcomes=existing_outcomes,
        universe_file_sha256=(
            dataset.universe.sha256 if dataset.universe is not None else None
        ),
        audit_sha256=dataset.audit.sha256 if dataset.audit is not None else None,
    )
    register_backtest_experiment(
        session,
        result=result,
        source_snapshot=benchmark_source_snapshot,
        source_snapshots=tuple(unique_snapshots.values()),
        dataset=dataset,
        start_date=start_date,
        end_date=end_date,
        horizon=horizon,
        frequency=frequency,
        model_version=resolved_model_version,
        code_commit=code_commit,
        result_uri=(
            result_uri
            or f"reports/backtests/{experiment_id}.json"
        ),
    )
    return result
