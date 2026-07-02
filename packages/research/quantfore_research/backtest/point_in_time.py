"""Dynamic-universe Sprint 7 baseline backtest execution."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from quantfore_research.backtest.contract import BACKTEST_CONTRACT
from quantfore_research.evaluation import (
    OutcomeResult,
    calculate_forward_outcome,
    immutable_outcome_hash,
    normalized_utc,
)
from quantfore_research.features import FEATURE_VERSION, NotEnoughPriceHistory
from quantfore_research.models import (
    DelistingEvent,
    ExperimentRegistry,
    Feature,
    FeatureSet,
    ModelOutcome,
    ModelPrediction,
    Price,
    Security,
    ScoreDriver as ScoreDriverRow,
    SourceSnapshot,
    UniverseDefinition,
)
from quantfore_research.ingest.point_in_time_equities import deterministic_id
from quantfore_research.scoring import (
    calculate_baseline_score,
    immutable_prediction_hash,
)
from quantfore_research.validation.leakage import (
    PointInTimeLeakageError,
    PointInTimeSecurityContext,
    construct_point_in_time_baseline_features,
    expected_point_in_time_cohort,
    prediction_timestamp_for_date,
    validate_point_in_time_cohort,
)


PIT_DATASET_KIND = "point_in_time"
PIT_FEATURE_SET_NAME = "pit_baseline_features"
PIT_HYPOTHESIS_ID = "H7_pit_baseline_score_excess_return"
PIT_HYPOTHESIS = (
    "The unchanged baseline score is evaluated over the historically eligible "
    "point-in-time S&P 500 universe."
)
DEFAULT_MINIMUM_COHORT_COVERAGE = Decimal("0.95")
FEATURE_VALUE_QUANT = Decimal("0.0000000001")


class DynamicOutcomeUnavailable(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        self.reason_code = reason_code
        super().__init__(message)


@dataclass(frozen=True)
class DynamicUniverseExclusion:
    prediction_date: date
    security_id: str
    ticker: str
    membership_id: str
    stage: str
    reason_code: str
    detail: str
    membership_source_snapshot_id: str
    membership_source_hash: str
    ticker_source_snapshot_id: str
    price_source_snapshot_id: Optional[str] = None
    price_source_hash: Optional[str] = None
    prediction_id: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values["prediction_date"] = self.prediction_date.isoformat()
        return values


@dataclass(frozen=True)
class DynamicUniverseEvaluation:
    prediction_date: date
    security_id: str
    ticker: str
    membership_id: str
    prediction_id: str
    outcome_hash: str
    entry_date: date
    exit_date: date
    outcome_kind: str
    delisting_event_id: Optional[str]
    delisting_return: Optional[Decimal]
    membership_source_snapshot_id: str
    membership_source_hash: str
    ticker_source_snapshot_id: str
    price_source_snapshot_id: str
    price_source_hash: str

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        for key in ("prediction_date", "entry_date", "exit_date"):
            values[key] = values[key].isoformat()
        if self.delisting_return is not None:
            values["delisting_return"] = str(self.delisting_return)
        return values


@dataclass(frozen=True)
class DynamicUniverseCohort:
    prediction_date: date
    expected_security_ids: tuple[str, ...]
    feature_security_ids: tuple[str, ...]
    evaluated_security_ids: tuple[str, ...]
    evaluations: tuple[DynamicUniverseEvaluation, ...]
    exclusions: tuple[DynamicUniverseExclusion, ...]

    @property
    def expected_count(self) -> int:
        return len(self.expected_security_ids)

    @property
    def feature_count(self) -> int:
        return len(self.feature_security_ids)

    @property
    def evaluated_count(self) -> int:
        return len(self.evaluated_security_ids)

    @property
    def coverage(self) -> Decimal:
        if not self.expected_count:
            return Decimal("0")
        return Decimal(self.evaluated_count) / Decimal(self.expected_count)

    @property
    def delisted_outcome_count(self) -> int:
        return sum(row.outcome_kind == "delisting" for row in self.evaluations)

    def to_dict(self) -> dict[str, Any]:
        reason_counts: dict[str, int] = {}
        for row in self.exclusions:
            reason_counts[row.reason_code] = reason_counts.get(row.reason_code, 0) + 1
        return {
            "prediction_date": self.prediction_date.isoformat(),
            "expected_count": self.expected_count,
            "feature_count": self.feature_count,
            "evaluated_count": self.evaluated_count,
            "coverage": str(self.coverage),
            "delisted_outcome_count": self.delisted_outcome_count,
            "expected_security_ids": list(self.expected_security_ids),
            "feature_security_ids": list(self.feature_security_ids),
            "evaluated_security_ids": list(self.evaluated_security_ids),
            "exclusion_reason_counts": dict(sorted(reason_counts.items())),
            "evaluations": [row.to_dict() for row in self.evaluations],
            "exclusions": [row.to_dict() for row in self.exclusions],
        }


@dataclass(frozen=True)
class DynamicUniverseBacktestResult:
    experiment_id: str
    universe_id: str
    model_version: str
    benchmark_security_id: str
    benchmark_ticker: str
    start_date: date
    end_date: date
    prediction_dates: tuple[date, ...]
    cohorts: tuple[DynamicUniverseCohort, ...]
    source_snapshot_ids: tuple[str, ...]
    minimum_coverage: Decimal
    audit_sha256: Optional[str]
    created_predictions: int
    existing_predictions: int
    created_outcomes: int
    existing_outcomes: int

    @property
    def coverage_gate_passed(self) -> bool:
        return bool(self.cohorts) and all(
            cohort.coverage >= self.minimum_coverage for cohort in self.cohorts
        )

    @property
    def prediction_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    row.prediction_id
                    for cohort in self.cohorts
                    for row in cohort.evaluations
                }
                | {
                    row.prediction_id
                    for cohort in self.cohorts
                    for row in cohort.exclusions
                    if row.prediction_id is not None
                }
            )
        )

    @property
    def outcome_hashes(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                row.outcome_hash
                for cohort in self.cohorts
                for row in cohort.evaluations
            )
        )

    def to_manifest(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "dataset_kind": PIT_DATASET_KIND,
            "universe_id": self.universe_id,
            "model_version": self.model_version,
            "benchmark_security_id": self.benchmark_security_id,
            "benchmark_ticker": self.benchmark_ticker,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "prediction_dates": [day.isoformat() for day in self.prediction_dates],
            "minimum_coverage": str(self.minimum_coverage),
            "audit_sha256": self.audit_sha256,
            "coverage_gate_passed": self.coverage_gate_passed,
            "source_snapshot_ids": list(self.source_snapshot_ids),
            "prediction_ids": list(self.prediction_ids),
            "outcome_hashes": list(self.outcome_hashes),
            "prediction_count": len(self.prediction_ids),
            "outcome_count": len(self.outcome_hashes),
            "cohorts": [cohort.to_dict() for cohort in self.cohorts],
        }


def _select_price_snapshot(
    session: Session,
    *,
    security_id: str,
    pinned_snapshot_id: Optional[str] = None,
) -> SourceSnapshot:
    statement = (
        select(SourceSnapshot)
        .join(Price, Price.source_snapshot_id == SourceSnapshot.snapshot_id)
        .where(Price.security_id == security_id)
        .where(Price.adj_close.is_not(None))
    )
    if pinned_snapshot_id is not None:
        statement = statement.where(SourceSnapshot.snapshot_id == pinned_snapshot_id)
    snapshots = session.scalars(statement.distinct()).all()
    if not snapshots:
        raise ValueError(f"no adjusted price snapshot for security {security_id}")
    scored = []
    for snapshot in snapshots:
        row_count = session.scalar(
            select(func.count())
            .select_from(Price)
            .where(Price.security_id == security_id)
            .where(Price.source_snapshot_id == snapshot.snapshot_id)
            .where(Price.adj_close.is_not(None))
        )
        scored.append((int(row_count or 0), snapshot))
    return max(
        scored,
        key=lambda item: (
            item[0], normalized_utc(item[1].retrieved_at), item[1].snapshot_id
        ),
    )[1]


def _snapshot_prices(
    session: Session, *, security_id: str, snapshot_id: str
) -> tuple[Price, ...]:
    return tuple(
        session.scalars(
            select(Price)
            .where(Price.security_id == security_id)
            .where(Price.source_snapshot_id == snapshot_id)
            .where(Price.adj_close.is_not(None))
            .order_by(Price.date, Price.price_id)
        )
    )


def _monthly_prediction_dates(
    benchmark_prices: Sequence[Price], *, start_date: date, end_date: date
) -> tuple[date, ...]:
    from quantfore_research.backtest.execution import discover_eligible_prediction_dates

    return discover_eligible_prediction_dates(
        benchmark_prices,
        start_date=start_date,
        end_date=end_date,
        contract=BACKTEST_CONTRACT,
    )


def _feature_set_id(security_id: str, prediction_date: date) -> str:
    return f"pit_{FEATURE_VERSION}_{security_id}_{prediction_date.isoformat()}"


def _evidence_hash(result: Any) -> str:
    rows = [
        {
            "input_type": row.input_type,
            "record_id": row.record_id,
            "security_id": row.security_id,
            "model_available_at": normalized_utc(row.model_available_at).isoformat(),
            "membership_effective_from": (
                row.membership_effective_from.isoformat()
                if row.membership_effective_from is not None
                else None
            ),
            "membership_effective_to": (
                row.membership_effective_to.isoformat()
                if row.membership_effective_to is not None
                else None
            ),
            "price_date": row.price_date.isoformat() if row.price_date else None,
            "source_snapshot_id": row.source_snapshot_id,
        }
        for row in result.inputs.evidence
    ]
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _store_feature_set(
    session: Session,
    *,
    feature_result: Any,
    code_commit: Optional[str],
) -> FeatureSet:
    context = feature_result.inputs.context
    prediction_date = context.prediction_timestamp.date()
    feature_set_id = _feature_set_id(context.security.security_id, prediction_date)
    config = {
        "ticker": context.ticker_alias.ticker,
        "features": sorted(feature_result.values),
        "lookbacks": {
            "skip_days": 21,
            "six_month_days": 126,
            "twelve_month_days": 252,
        },
        "price_field": "adj_close",
        "source_snapshot_id": feature_result.inputs.source_snapshot.snapshot_id,
        "point_in_time": {
            "enabled": True,
            "universe_id": context.universe_id,
            "prediction_timestamp": context.prediction_timestamp.isoformat(),
            "membership_id": context.membership.membership_id,
            "ticker_alias_id": context.ticker_alias.ticker_alias_id,
            "price_input_count": len(feature_result.inputs.prices),
            "maximum_price_date": max(
                row.date for row in feature_result.inputs.prices
            ).isoformat(),
            "evidence_sha256": _evidence_hash(feature_result),
        },
    }
    existing = session.get(FeatureSet, feature_set_id)
    if existing is not None:
        if (
            existing.name != PIT_FEATURE_SET_NAME
            or existing.version != FEATURE_VERSION
            or existing.asof_date != prediction_date
            or existing.source_snapshot_id
            != feature_result.inputs.source_snapshot.snapshot_id
            or existing.config_json != config
        ):
            raise ValueError(f"conflicting point-in-time feature set {feature_set_id}")
        stored = {
            row.feature_name: row.value.quantize(FEATURE_VALUE_QUANT)
            for row in session.scalars(
                select(Feature).where(Feature.feature_set_id == feature_set_id)
            )
        }
        expected = {
            name: Decimal(value).quantize(FEATURE_VALUE_QUANT)
            for name, value in feature_result.values.items()
        }
        if stored != expected:
            raise ValueError(f"point-in-time features do not reproduce: {feature_set_id}")
        return existing
    feature_set = FeatureSet(
        feature_set_id=feature_set_id,
        name=PIT_FEATURE_SET_NAME,
        version=FEATURE_VERSION,
        asof_date=prediction_date,
        config_json=config,
        source_snapshot_id=feature_result.inputs.source_snapshot.snapshot_id,
        code_commit=code_commit,
    )
    session.add(feature_set)
    for name, value in feature_result.values.items():
        session.add(
            Feature(
                feature_set_id=feature_set_id,
                security_id=context.security.security_id,
                asof_date=prediction_date,
                available_at=context.prediction_timestamp,
                feature_name=name,
                value=Decimal(value),
                version=FEATURE_VERSION,
                source_snapshot_id=feature_result.inputs.source_snapshot.snapshot_id,
                source_hash=feature_result.inputs.source_snapshot.source_hash,
            )
        )
    session.flush()
    return feature_set


def _maximum_drawdown(values: Sequence[Decimal]) -> Decimal:
    peak = values[0]
    result = Decimal("0")
    for value in values[1:]:
        peak = max(peak, value)
        drawdown = (value / peak - Decimal("1")) if peak else Decimal("-1")
        result = min(result, drawdown)
    return result


def _store_prediction(
    session: Session,
    *,
    security: Security,
    ticker: str,
    prediction_date: date,
    feature_set: FeatureSet,
    feature_values: Mapping[str, Decimal],
    model_version: str,
) -> tuple[ModelPrediction, bool]:
    score = calculate_baseline_score(feature_values)
    prediction_id = deterministic_id(
        "pit_prediction",
        model_version,
        security.security_id,
        prediction_date,
        BACKTEST_CONTRACT.horizon,
    )
    immutable_hash = immutable_prediction_hash(
        model_version=model_version,
        ticker=ticker,
        security_id=security.security_id,
        asof_date=prediction_date,
        horizon=BACKTEST_CONTRACT.horizon,
        feature_set_id=feature_set.feature_set_id,
        score=score,
    )
    existing = session.get(ModelPrediction, prediction_id)
    if existing is None:
        existing = session.scalar(
            select(ModelPrediction)
            .where(ModelPrediction.model_version == model_version)
            .where(ModelPrediction.security_id == security.security_id)
            .where(ModelPrediction.asof_date == prediction_date)
            .where(ModelPrediction.horizon == BACKTEST_CONTRACT.horizon)
        )
    if existing is not None:
        if (
            existing.prediction_id != prediction_id
            or existing.immutable_hash != immutable_hash
            or existing.feature_set_id != feature_set.feature_set_id
        ):
            raise ValueError(
                "conflicting point-in-time prediction for "
                f"{ticker} on {prediction_date}"
            )
        return existing, False
    prediction = ModelPrediction(
        prediction_id=prediction_id,
        model_version=model_version,
        security_id=security.security_id,
        feature_set_id=feature_set.feature_set_id,
        asof_date=prediction_date,
        horizon=BACKTEST_CONTRACT.horizon,
        score=score.score,
        confidence=score.confidence,
        action_label=score.action_label,
        immutable_hash=immutable_hash,
    )
    session.add(prediction)
    session.flush()
    for driver in score.drivers:
        session.add(
            ScoreDriverRow(
                driver_id=deterministic_id(
                    "pit_score_driver", prediction_id, driver.driver_name
                ),
                prediction_id=prediction_id,
                driver_name=driver.driver_name,
                contribution=driver.contribution,
                evidence_uri=driver.evidence_uri,
            )
        )
    session.flush()
    return prediction, True


def _calculate_outcome(
    *,
    security_prices: Sequence[Price],
    benchmark_prices: Sequence[Price],
    prediction_date: date,
    delistings: Sequence[DelistingEvent],
) -> tuple[OutcomeResult, str, Optional[DelistingEvent]]:
    benchmark_future = [row for row in benchmark_prices if row.date > prediction_date]
    if len(benchmark_future) < BACKTEST_CONTRACT.evaluation_sessions:
        raise DynamicOutcomeUnavailable(
            "BENCHMARK_EXIT_UNAVAILABLE",
            "benchmark has insufficient future observations",
        )
    benchmark_window = benchmark_future[: BACKTEST_CONTRACT.evaluation_sessions]
    entry_date = benchmark_window[0].date
    target_exit_date = benchmark_window[-1].date
    delisting = next(
        (
            row
            for row in sorted(delistings, key=lambda item: item.delisting_date)
            if prediction_date < row.delisting_date <= target_exit_date
        ),
        None,
    )
    if delisting is None:
        security_by_date = {row.date: row for row in security_prices}
        if entry_date not in security_by_date:
            raise DynamicOutcomeUnavailable(
                "ENTRY_UNAVAILABLE",
                "security has no adjusted price on the benchmark entry session",
            )
        if target_exit_date not in security_by_date:
            raise DynamicOutcomeUnavailable(
                "EXIT_UNAVAILABLE",
                "security has no adjusted price on the target exit session",
            )
        missing_dates = [
            row.date for row in benchmark_window if row.date not in security_by_date
        ]
        if missing_dates:
            raise DynamicOutcomeUnavailable(
                "MISSING_OUTCOME_DATA",
                "security is missing aligned observations inside the outcome window",
            )
        try:
            return (
                calculate_forward_outcome(
                    security_prices,
                    benchmark_prices,
                    prediction_date=prediction_date,
                    horizon=BACKTEST_CONTRACT.horizon,
                ),
                "standard",
                None,
            )
        except ValueError as exc:
            message = str(exc)
            raise DynamicOutcomeUnavailable("MISSING_OUTCOME_DATA", message) from exc

    if delisting.delisting_return is None:
        raise DynamicOutcomeUnavailable(
            "DELISTING_RETURN_UNAVAILABLE",
            "security delists inside the horizon without a terminal return",
        )
    by_date = {row.date: row for row in security_prices}
    entry = by_date.get(entry_date)
    if entry is None or entry.adj_close is None:
        raise DynamicOutcomeUnavailable(
            "ENTRY_UNAVAILABLE", "security has no adjusted entry price"
        )
    path = [
        row
        for row in security_prices
        if entry_date <= row.date <= delisting.delisting_date
        and row.adj_close is not None
    ]
    if not path:
        raise DynamicOutcomeUnavailable(
            "EXIT_UNAVAILABLE", "delisted security has no terminal price history"
        )
    multiplier = Decimal("1") + Decimal(delisting.delisting_return)
    if multiplier < 0:
        raise DynamicOutcomeUnavailable(
            "INVALID_DELISTING_RETURN", "delisting return is less than -100%"
        )
    terminal_price = Decimal(path[-1].adj_close) * multiplier
    benchmark_exit_candidates = [
        row for row in benchmark_window if row.date <= delisting.delisting_date
    ]
    if not benchmark_exit_candidates:
        raise DynamicOutcomeUnavailable(
            "BENCHMARK_EXIT_UNAVAILABLE",
            "no benchmark observation exists by the delisting date",
        )
    benchmark_entry = benchmark_window[0]
    benchmark_exit = benchmark_exit_candidates[-1]
    assert benchmark_entry.adj_close is not None and benchmark_exit.adj_close is not None
    security_return = terminal_price / Decimal(entry.adj_close) - Decimal("1")
    benchmark_return = (
        Decimal(benchmark_exit.adj_close) / Decimal(benchmark_entry.adj_close)
        - Decimal("1")
    )
    drawdown_values = [Decimal(row.adj_close) for row in path]
    drawdown_values.append(terminal_price)
    return (
        OutcomeResult(
            entry_date=entry_date,
            exit_date=delisting.delisting_date,
            security_entry_price=Decimal(entry.adj_close),
            security_exit_price=terminal_price,
            benchmark_entry_price=Decimal(benchmark_entry.adj_close),
            benchmark_exit_price=Decimal(benchmark_exit.adj_close),
            realised_return=security_return,
            benchmark_return=benchmark_return,
            excess_return=security_return - benchmark_return,
            max_drawdown=_maximum_drawdown(drawdown_values),
        ),
        "delisting",
        delisting,
    )


def _store_outcome(
    session: Session,
    *,
    prediction: Any,
    ticker: str,
    benchmark: Security,
    security_snapshot: SourceSnapshot,
    benchmark_snapshot: SourceSnapshot,
    calculated: OutcomeResult,
    evaluated_at: datetime,
) -> tuple[ModelOutcome, bool]:
    existing = session.scalar(
        select(ModelOutcome).where(ModelOutcome.prediction_id == prediction.prediction_id)
    )
    evaluation_timestamp = normalized_utc(evaluated_at)
    expected_hash = immutable_outcome_hash(
        prediction=prediction,
        ticker=ticker,
        benchmark=benchmark,
        security_price_snapshot_id=security_snapshot.snapshot_id,
        benchmark_price_snapshot_id=benchmark_snapshot.snapshot_id,
        outcome=calculated,
        evaluated_at=(
            normalized_utc(existing.evaluated_at)
            if existing is not None
            else evaluation_timestamp
        ),
    )
    if existing is not None:
        if existing.immutable_hash != expected_hash:
            raise ValueError(
                f"stored dynamic outcome does not reproduce: {prediction.prediction_id}"
            )
        return existing, False
    outcome = ModelOutcome(
        outcome_id=deterministic_id("pit_outcome", prediction.prediction_id),
        prediction_id=prediction.prediction_id,
        benchmark_security_id=benchmark.security_id,
        security_price_snapshot_id=security_snapshot.snapshot_id,
        benchmark_price_snapshot_id=benchmark_snapshot.snapshot_id,
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
        immutable_hash=expected_hash,
    )
    session.add(outcome)
    session.flush()
    return outcome, True


def _exclusion(
    context: PointInTimeSecurityContext,
    *,
    prediction_date: date,
    stage: str,
    reason_code: str,
    detail: str,
    snapshot: Optional[SourceSnapshot] = None,
    prediction_id: Optional[str] = None,
) -> DynamicUniverseExclusion:
    return DynamicUniverseExclusion(
        prediction_date=prediction_date,
        security_id=context.security.security_id,
        ticker=context.ticker_alias.ticker,
        membership_id=context.membership.membership_id,
        stage=stage,
        reason_code=reason_code,
        detail=detail,
        membership_source_snapshot_id=context.membership.source_snapshot_id,
        membership_source_hash=context.membership.source_hash,
        ticker_source_snapshot_id=context.ticker_alias.source_snapshot_id,
        price_source_snapshot_id=snapshot.snapshot_id if snapshot else None,
        price_source_hash=snapshot.source_hash if snapshot else None,
        prediction_id=prediction_id,
    )


def _register_experiment(
    session: Session,
    *,
    result: DynamicUniverseBacktestResult,
    code_commit: Optional[str],
    result_uri: str,
) -> ExperimentRegistry:
    snapshot_hashes = sorted(
        session.get(SourceSnapshot, snapshot_id).source_hash
        for snapshot_id in result.source_snapshot_ids
    )
    data_hash = hashlib.sha256(
        json.dumps(
            {
                "universe_id": result.universe_id,
                "source_snapshot_hashes": snapshot_hashes,
                "audit_sha256": result.audit_sha256,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    config = {
        "hypothesis": PIT_HYPOTHESIS,
        "dataset_kind": PIT_DATASET_KIND,
        "claims_eligible": False,
        "universe_id": result.universe_id,
        "benchmark": result.benchmark_ticker,
        "model_version": result.model_version,
        "feature_version": FEATURE_VERSION,
        "features": [
            "momentum_6_1",
            "momentum_12_1",
            "return_21d",
            "volatility_126d",
        ],
        "horizon": BACKTEST_CONTRACT.horizon,
        "frequency": BACKTEST_CONTRACT.frequency,
        "minimum_cohort_coverage": str(result.minimum_coverage),
        "audit_sha256": result.audit_sha256,
        "date_range": {
            "start": result.start_date.isoformat(),
            "end": result.end_date.isoformat(),
        },
        "cohort_expected_counts": {
            row.prediction_date.isoformat(): row.expected_count for row in result.cohorts
        },
    }
    expected = {
        "hypothesis_id": PIT_HYPOTHESIS_ID,
        "data_snapshot_hash": data_hash,
        "code_commit": code_commit,
        "config_json": config,
        "result_uri": result_uri,
        "notes": (
            "Point-in-time dynamic-universe baseline; claims_eligible=false until "
            "all Sprint 7 validation gates pass."
        ),
    }
    existing = session.get(ExperimentRegistry, result.experiment_id)
    if existing is not None:
        conflicts = [
            key for key, value in expected.items() if getattr(existing, key) != value
        ]
        if conflicts:
            raise ValueError(
                "conflicting point-in-time experiment registration: "
                + ",".join(conflicts)
            )
        return existing
    experiment = ExperimentRegistry(experiment_id=result.experiment_id, **expected)
    session.add(experiment)
    session.flush()
    return experiment


def run_dynamic_universe_backtest(
    session: Session,
    *,
    experiment_id: str,
    universe_id: str,
    start_date: date,
    end_date: date,
    price_source_snapshot_id: Optional[str] = None,
    price_snapshot_ids_by_security: Optional[Mapping[str, str]] = None,
    minimum_coverage: Decimal = DEFAULT_MINIMUM_COHORT_COVERAGE,
    model_version: str = BACKTEST_CONTRACT.model_version,
    code_commit: Optional[str] = None,
    evaluated_at: Optional[datetime] = None,
    result_uri: str = "reports/backtests/pit_baseline_v0_1.json",
    audit_sha256: Optional[str] = None,
) -> DynamicUniverseBacktestResult:
    """Run the unchanged baseline over monthly historical membership cohorts."""

    if not experiment_id.strip():
        raise ValueError("experiment_id is required")
    if not Decimal("0") <= minimum_coverage <= Decimal("1"):
        raise ValueError("minimum_coverage must be between 0 and 1")
    if model_version != BACKTEST_CONTRACT.model_version:
        raise ValueError(
            f"Sprint 7 model must remain {BACKTEST_CONTRACT.model_version}"
        )
    if start_date > end_date:
        raise ValueError("start_date cannot be after end_date")
    if price_source_snapshot_id is not None and price_snapshot_ids_by_security:
        if set(price_snapshot_ids_by_security.values()) != {price_source_snapshot_id}:
            raise ValueError(
                "global price snapshot conflicts with audited security bindings"
            )
    universe = session.get(UniverseDefinition, universe_id)
    if universe is None:
        raise ValueError(f"unknown universe: {universe_id}")
    benchmark = session.get(Security, universe.benchmark_security_id)
    if benchmark is None or benchmark.ticker != "SPY":
        raise ValueError("point-in-time universe benchmark must resolve to SPY")
    benchmark_snapshot = _select_price_snapshot(
        session,
        security_id=benchmark.security_id,
        pinned_snapshot_id=(
            price_snapshot_ids_by_security.get(benchmark.security_id)
            if price_snapshot_ids_by_security is not None
            else price_source_snapshot_id
        ),
    )
    if (
        price_snapshot_ids_by_security is not None
        and benchmark.security_id not in price_snapshot_ids_by_security
    ):
        raise ValueError("audited price binding is missing the benchmark")
    benchmark_prices = _snapshot_prices(
        session,
        security_id=benchmark.security_id,
        snapshot_id=benchmark_snapshot.snapshot_id,
    )
    prediction_dates = _monthly_prediction_dates(
        benchmark_prices, start_date=start_date, end_date=end_date
    )
    if not prediction_dates:
        raise ValueError("no eligible monthly prediction dates")
    evaluation_timestamp = normalized_utc(
        evaluated_at or benchmark_snapshot.retrieved_at
    )
    cohorts: list[DynamicUniverseCohort] = []
    source_snapshot_ids = {
        benchmark_snapshot.snapshot_id,
        universe.source_snapshot_id,
    }
    created_predictions = existing_predictions = 0
    created_outcomes = existing_outcomes = 0

    for prediction_date in prediction_dates:
        timestamp = prediction_timestamp_for_date(prediction_date)
        contexts = expected_point_in_time_cohort(
            session,
            universe_id=universe_id,
            prediction_timestamp=timestamp,
        )
        validate_point_in_time_cohort(
            session,
            universe_id=universe_id,
            prediction_timestamp=timestamp,
            candidate_security_ids=[row.security.security_id for row in contexts],
        )
        expected_ids = tuple(row.security.security_id for row in contexts)
        feature_ids: list[str] = []
        evaluated_ids: list[str] = []
        evaluations: list[DynamicUniverseEvaluation] = []
        exclusions: list[DynamicUniverseExclusion] = []
        for context in contexts:
            source_snapshot_ids.update(
                {
                    context.membership.source_snapshot_id,
                    context.ticker_alias.source_snapshot_id,
                }
            )
            try:
                if (
                    price_snapshot_ids_by_security is not None
                    and context.security.security_id
                    not in price_snapshot_ids_by_security
                ):
                    raise ValueError(
                        "audited price binding is missing this historical security"
                    )
                selected_security_snapshot = _select_price_snapshot(
                    session,
                    security_id=context.security.security_id,
                    pinned_snapshot_id=(
                        price_snapshot_ids_by_security.get(
                            context.security.security_id
                        )
                        if price_snapshot_ids_by_security is not None
                        else price_source_snapshot_id
                    ),
                )
            except ValueError as exc:
                exclusions.append(
                    _exclusion(
                        context,
                        prediction_date=prediction_date,
                        stage="features",
                        reason_code="MISSING_FEATURE_DATA",
                        detail=str(exc),
                    )
                )
                continue
            try:
                feature_result = construct_point_in_time_baseline_features(
                    session,
                    universe_id=universe_id,
                    ticker=context.ticker_alias.ticker,
                    prediction_timestamp=timestamp,
                    source_snapshot_id=selected_security_snapshot.snapshot_id,
                )
            except NotEnoughPriceHistory as exc:
                exclusions.append(
                    _exclusion(
                        context,
                        prediction_date=prediction_date,
                        stage="features",
                        reason_code="INSUFFICIENT_HISTORY",
                        detail=str(exc),
                    )
                )
                continue
            except PointInTimeLeakageError:
                raise
            except ValueError as exc:
                exclusions.append(
                    _exclusion(
                        context,
                        prediction_date=prediction_date,
                        stage="features",
                        reason_code="MISSING_FEATURE_DATA",
                        detail=str(exc),
                    )
                )
                continue
            snapshot = feature_result.inputs.source_snapshot
            source_snapshot_ids.add(snapshot.snapshot_id)
            feature_set = _store_feature_set(
                session, feature_result=feature_result, code_commit=code_commit
            )
            feature_ids.append(context.security.security_id)
            prediction, prediction_created = _store_prediction(
                session,
                security=context.security,
                ticker=context.ticker_alias.ticker,
                prediction_date=prediction_date,
                feature_set=feature_set,
                feature_values=feature_result.values,
                model_version=model_version,
            )
            created_predictions += int(prediction_created)
            existing_predictions += int(not prediction_created)
            all_security_prices = _snapshot_prices(
                session,
                security_id=context.security.security_id,
                snapshot_id=snapshot.snapshot_id,
            )
            delistings = session.scalars(
                select(DelistingEvent)
                .where(DelistingEvent.security_id == context.security.security_id)
                .order_by(DelistingEvent.delisting_date)
            ).all()
            try:
                calculated, outcome_kind, delisting = _calculate_outcome(
                    security_prices=all_security_prices,
                    benchmark_prices=benchmark_prices,
                    prediction_date=prediction_date,
                    delistings=delistings,
                )
            except DynamicOutcomeUnavailable as exc:
                exclusions.append(
                    _exclusion(
                        context,
                        prediction_date=prediction_date,
                        stage="outcome",
                        reason_code=exc.reason_code,
                        detail=str(exc),
                        snapshot=snapshot,
                        prediction_id=prediction.prediction_id,
                    )
                )
                continue
            outcome, outcome_created = _store_outcome(
                session,
                prediction=prediction,
                ticker=context.ticker_alias.ticker,
                benchmark=benchmark,
                security_snapshot=snapshot,
                benchmark_snapshot=benchmark_snapshot,
                calculated=calculated,
                evaluated_at=evaluation_timestamp,
            )
            created_outcomes += int(outcome_created)
            existing_outcomes += int(not outcome_created)
            evaluated_ids.append(context.security.security_id)
            if delisting is not None:
                source_snapshot_ids.add(delisting.source_snapshot_id)
            evaluations.append(
                DynamicUniverseEvaluation(
                    prediction_date=prediction_date,
                    security_id=context.security.security_id,
                    ticker=context.ticker_alias.ticker,
                    membership_id=context.membership.membership_id,
                    prediction_id=prediction.prediction_id,
                    outcome_hash=outcome.immutable_hash,
                    entry_date=outcome.entry_date,
                    exit_date=outcome.exit_date,
                    outcome_kind=outcome_kind,
                    delisting_event_id=(
                        delisting.delisting_event_id if delisting else None
                    ),
                    delisting_return=(
                        delisting.delisting_return if delisting else None
                    ),
                    membership_source_snapshot_id=(
                        context.membership.source_snapshot_id
                    ),
                    membership_source_hash=context.membership.source_hash,
                    ticker_source_snapshot_id=context.ticker_alias.source_snapshot_id,
                    price_source_snapshot_id=snapshot.snapshot_id,
                    price_source_hash=snapshot.source_hash,
                )
            )
        cohorts.append(
            DynamicUniverseCohort(
                prediction_date=prediction_date,
                expected_security_ids=tuple(sorted(expected_ids)),
                feature_security_ids=tuple(sorted(feature_ids)),
                evaluated_security_ids=tuple(sorted(evaluated_ids)),
                evaluations=tuple(
                    sorted(evaluations, key=lambda row: row.security_id)
                ),
                exclusions=tuple(
                    sorted(
                        exclusions,
                        key=lambda row: (row.security_id, row.stage, row.reason_code),
                    )
                ),
            )
        )

    result = DynamicUniverseBacktestResult(
        experiment_id=experiment_id,
        universe_id=universe_id,
        model_version=model_version,
        benchmark_security_id=benchmark.security_id,
        benchmark_ticker=benchmark.ticker,
        start_date=start_date,
        end_date=end_date,
        prediction_dates=prediction_dates,
        cohorts=tuple(cohorts),
        source_snapshot_ids=tuple(sorted(source_snapshot_ids)),
        minimum_coverage=minimum_coverage,
        audit_sha256=audit_sha256,
        created_predictions=created_predictions,
        existing_predictions=existing_predictions,
        created_outcomes=created_outcomes,
        existing_outcomes=existing_outcomes,
    )
    _register_experiment(
        session,
        result=result,
        code_commit=code_commit,
        result_uri=result_uri,
    )
    return result


def build_dynamic_universe_report(
    session: Session, *, result: DynamicUniverseBacktestResult
) -> dict[str, Any]:
    predictions = []
    if result.prediction_ids:
        from quantfore_research.backtest.baseline import BacktestObservation, summarize_backtest
        from quantfore_research.models import ModelPrediction

        rows = session.execute(
            select(ModelPrediction, Security, ModelOutcome)
            .join(Security, Security.security_id == ModelPrediction.security_id)
            .outerjoin(
                ModelOutcome,
                ModelOutcome.prediction_id == ModelPrediction.prediction_id,
            )
            .where(ModelPrediction.prediction_id.in_(result.prediction_ids))
        ).all()
        predictions = [
            BacktestObservation(
                ticker=security.ticker,
                prediction_date=prediction.asof_date,
                score=prediction.score,
                action_label=prediction.action_label,
                excess_return=(outcome.excess_return if outcome is not None else None),
            )
            for prediction, security, outcome in rows
        ]
        summary = summarize_backtest(predictions)
    else:
        summary = None
    return {
        "schema_version": "pit_dynamic_universe_baseline_v1",
        "claims_eligible": False,
        "configuration": {
            "experiment_id": result.experiment_id,
            "dataset_kind": PIT_DATASET_KIND,
            "universe_id": result.universe_id,
            "model_version": result.model_version,
            "feature_version": FEATURE_VERSION,
            "features": [
                "momentum_6_1",
                "momentum_12_1",
                "return_21d",
                "volatility_126d",
            ],
            "horizon": BACKTEST_CONTRACT.horizon,
            "frequency": BACKTEST_CONTRACT.frequency,
            "minimum_cohort_coverage": str(result.minimum_coverage),
        },
        "coverage_gate_passed": result.coverage_gate_passed,
        "cohorts": [row.to_dict() for row in result.cohorts],
        "source_snapshot_ids": list(result.source_snapshot_ids),
        "observation_counts": {
            "expected": sum(row.expected_count for row in result.cohorts),
            "features_built": sum(row.feature_count for row in result.cohorts),
            "evaluated": sum(row.evaluated_count for row in result.cohorts),
            "delisted_outcomes": sum(
                row.delisted_outcome_count for row in result.cohorts
            ),
        },
        "metrics": (
            {
                "coverage": summary.coverage,
                "mean_rank_ic": summary.mean_rank_ic,
                "median_rank_ic": summary.median_rank_ic,
                "top_minus_bottom_spread": summary.top_minus_bottom_spread,
                "quintile_returns": {
                    str(key): value
                    for key, value in summary.average_excess_return_by_quintile.items()
                },
            }
            if summary is not None
            else None
        ),
        "manifest": result.to_manifest(),
    }
