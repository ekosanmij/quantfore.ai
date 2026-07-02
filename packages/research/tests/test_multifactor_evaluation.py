import hashlib
import json
from datetime import date
from decimal import Decimal

import pytest

from pipelines.evaluate_multifactor_baseline import validate_holdout_lock
from quantfore_research.evaluation.multifactor import (
    MultiFactorEvaluationObservation,
    evaluate_multifactor_baseline,
)


def observations(*, year=2021):
    rows = []
    horizons = ("21d", "63d", "126d", "252d")
    for month in range(1, 13):
        prediction_date = date(year, month, 28)
        for security_index in range(10):
            score = Decimal(security_index) * Decimal("10")
            excess = Decimal(security_index - 4) / Decimal("100")
            benchmark = Decimal("-0.01") if month % 4 == 0 else Decimal("0.02")
            realised = benchmark + excess
            for horizon in horizons:
                rows.append(
                    MultiFactorEvaluationObservation(
                        security_id=f"security-{security_index}",
                        ticker=f"T{security_index}",
                        prediction_date=prediction_date,
                        sector="Technology" if security_index < 5 else "Energy",
                        score=score,
                        family_scores={
                            "value": score,
                            "quality": score + Decimal("1"),
                            "growth": score + Decimal("2"),
                            "momentum": score + Decimal("3"),
                            "risk": Decimal("100") - score,
                        },
                        component_coverage=(
                            Decimal("0.95")
                            if security_index < 8
                            else Decimal("0.75")
                        ),
                        missing_reasons=(
                            () if security_index < 8 else ("SOURCE_MISSING",)
                        ),
                        horizon=horizon,
                        excess_return=excess,
                        realised_return=realised,
                        benchmark_return=benchmark,
                        max_drawdown=-Decimal(security_index + 1) / Decimal("100"),
                        delisted_outcome=(security_index == 0 and month == 12),
                    )
                )
    return tuple(rows)


def test_frozen_evaluation_covers_all_required_diagnostics():
    report = evaluate_multifactor_baseline(observations())

    assert set(report["horizons"]) == {"21d", "63d", "126d", "252d"}
    primary = report["horizons"]["126d"]
    assert primary["mean_rank_ic"] == 1.0
    assert primary["quintile_returns_monotonic"] is True
    assert primary["top_minus_bottom_spread"] > 0
    assert primary["non_overlapping_rank_ic"]["stride_months"] == 6
    assert set(primary["turnover_and_one_way_costs"]["cost_sensitivity"]) == {
        "10_bps",
        "25_bps",
        "50_bps",
    }
    assert "year_stability" in primary
    assert "sector_stability" in primary
    assert "drawdown_and_downside_capture" in primary
    assert primary["delisted_security_contribution"]["observation_count"] == 1
    assert set(report["family_score_correlations"]) == {
        "value",
        "quality",
        "growth",
        "momentum",
        "risk",
    }
    missingness = report["missingness_and_coverage_bias"]
    assert missingness["missing_reason_counts"]["SOURCE_MISSING"] > 0
    assert missingness["evaluated_by_coverage_band"]["at_least_90_percent"] > 0


def test_nonoverlap_stride_tracks_each_outcome_horizon():
    report = evaluate_multifactor_baseline(observations())

    assert {
        horizon: report["horizons"][horizon]["non_overlapping_rank_ic"][
            "stride_months"
        ]
        for horizon in report["horizons"]
    } == {"21d": 1, "63d": 3, "126d": 6, "252d": 12}


def test_holdout_cannot_run_without_exact_committed_lock(tmp_path):
    holdout_rows = observations(year=2022)
    with pytest.raises(ValueError, match="requires"):
        validate_holdout_lock(
            holdout_rows, lock_path=None, expected_lock_hash=None
        )

    lock = {
        "contract_version": "multifactor-baseline-v1",
        "feature_version": "multifactor-v1",
        "normalization_version": "multifactor-normalization-v1",
        "holdout_start": "2022-01-01",
        "holdout_end": "2025-12-31",
        "claims_eligible": False,
        "locked_at": "2026-01-01T00:00:00Z",
        "code_commit": "abc123",
        "source_snapshot_hashes": ["a" * 64],
        "promotion_thresholds": {"mean_rank_ic": "0.03"},
    }
    body = (json.dumps(lock, sort_keys=True) + "\n").encode()
    path = tmp_path / "lock.json"
    path.write_bytes(body)
    lock_hash = hashlib.sha256(body).hexdigest()

    assert validate_holdout_lock(
        holdout_rows,
        lock_path=path,
        expected_lock_hash=lock_hash,
    ) == lock_hash

    with pytest.raises(ValueError, match="SHA-256"):
        validate_holdout_lock(
            holdout_rows,
            lock_path=path,
            expected_lock_hash="0" * 64,
        )


def test_development_evaluation_does_not_require_holdout_lock():
    assert validate_holdout_lock(
        observations(year=2021), lock_path=None, expected_lock_hash=None
    ) is None
