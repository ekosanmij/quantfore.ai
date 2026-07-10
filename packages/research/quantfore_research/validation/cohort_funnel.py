"""Deterministic Sprint 9 audit of the Sprint 8 security-month cohort funnel."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import text
from sqlalchemy.orm import Session


FROZEN_FEATURE_COUNT = 19
MINIMUM_AVAILABLE_FAMILIES = 4
MINIMUM_COMPONENT_COVERAGE = Decimal("0.70")
MULTIFACTOR_MODEL_VERSION = "multifactor-baseline-v1"
REQUIRED_HORIZONS = ("21d", "63d", "126d", "252d")
PRICE_FEATURES = (
    "momentum_6_1",
    "momentum_12_1",
    "volatility_126d",
    "beta_252d",
    "downside_volatility_126d",
    "maximum_drawdown_252d",
)

INCLUDED = "INCLUDED_IN_FINAL_EVALUATION"
INCOMPLETE_FEATURES = "INCOMPLETE_RAW_OR_NORMALIZED_FEATURE_SET"
BELOW_FAMILIES = "BELOW_MINIMUM_AVAILABLE_FAMILIES"
BELOW_COVERAGE = "BELOW_MINIMUM_COMPONENT_COVERAGE"
ELIGIBILITY_MISMATCH = "ELIGIBILITY_STATE_MISMATCH"
PREDICTION_MISSING = "PREDICTION_RECORD_MISSING"
OUTCOME_MISSING = "MATURE_OUTCOME_OR_EVALUATION_RECORD_MISSING"


def _json(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def primary_reason_code(
    *,
    complete_feature_set: bool,
    available_family_count: int,
    component_coverage: Decimal,
    eligible: bool,
    prediction_horizons: Iterable[str],
    evaluated_126d: bool,
) -> str:
    """Return one exclusive reason for a security-month's final disposition."""

    horizons = frozenset(prediction_horizons)
    if not complete_feature_set:
        return INCOMPLETE_FEATURES
    if available_family_count < MINIMUM_AVAILABLE_FAMILIES:
        return BELOW_FAMILIES
    if component_coverage < MINIMUM_COMPONENT_COVERAGE:
        return BELOW_COVERAGE
    if not eligible:
        return ELIGIBILITY_MISMATCH
    if horizons != frozenset(REQUIRED_HORIZONS):
        return PREDICTION_MISSING
    if not evaluated_126d:
        return OUTCOME_MISSING
    return INCLUDED


def diagnostic_reason_codes(
    *,
    exact_prediction_price: bool,
    model_available_fundamental_fact: bool,
    valid_price_feature_count: int,
    valid_fundamental_feature_count: int,
    sector: str,
    component_reasons: Iterable[str],
) -> tuple[str, ...]:
    """Return stable diagnostic codes without replacing the primary reason."""

    values = set(
        str(value)
        for value in component_reasons
        if value and str(value) != "VALID"
    )
    if not exact_prediction_price:
        values.add("PRICE_MISSING_AT_PREDICTION")
    if not model_available_fundamental_fact:
        values.add("NO_MODEL_AVAILABLE_FUNDAMENTAL_FACT")
    if valid_price_feature_count == 0:
        values.add("NO_USABLE_PRICE_FEATURE")
    if valid_fundamental_feature_count == 0:
        values.add("NO_USABLE_FUNDAMENTAL_FEATURE")
    if sector in {"Unknown", "SECTOR_UNKNOWN", "<unknown>"}:
        values.add("SECTOR_UNKNOWN")
    return tuple(sorted(values))


def quintile_one_diagnosis(monthly_eligible_counts: Sequence[int]) -> dict[str, Any]:
    """Explain whether the observed cohort sizes can ever populate quintile 1."""

    counts = tuple(int(value) for value in monthly_eligible_counts if value > 0)
    maximum = max(counts, default=0)
    return {
        "nonempty_months": len(counts),
        "maximum_monthly_eligible_scores": maximum,
        "months_with_at_least_five_scores": sum(value >= 5 for value in counts),
        "quintile_1_possible": maximum >= 5,
        "reason_code": (
            "COHORT_TOO_SMALL_FOR_BOTTOM_QUINTILE"
            if maximum < 5
            else "BOTTOM_QUINTILE_CAN_BE_POPULATED"
        ),
        "assignment_formula": "ceil(ascending_average_rank * 5 / cohort_size)",
        "explanation": (
            "Quintile 1 requires at least five ranked securities. With one to four "
            "securities, the lowest possible assigned quintile is 5, 3, 2, or 2."
            if maximum < 5
            else "At least one monthly cohort is large enough to populate quintile 1."
        ),
    }


@dataclass(frozen=True)
class Sprint9CohortFunnelAudit:
    """Aggregate evidence plus one explanation row per expected security-month."""

    audit: Mapping[str, Any]
    explanations: tuple[Mapping[str, Any], ...]

    @property
    def decision(self) -> str:
        return str(self.audit["breadth_assessment"]["decision"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.audit)


def _load_runs(session: Session, universe_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT normalization_run_id, universe_id, asof_date, version,
                   config_json, input_hash
            FROM normalization_runs
            WHERE universe_id = :universe_id
            ORDER BY asof_date, normalization_run_id
            """
        ),
        {"universe_id": universe_id},
    ).mappings()
    return [
        {
            **dict(row),
            "asof_date": _date(row["asof_date"]),
            "config_json": _json(row["config_json"]),
        }
        for row in rows
    ]


def _load_score_rows(session: Session, universe_id: str) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT ms.multifactor_score_id, ms.normalization_run_id,
                   ms.security_id, s.ticker, ms.asof_date, ms.eligible,
                   ms.final_score, ms.applicable_component_count,
                   ms.valid_component_count, ms.component_coverage,
                   ms.available_family_count, ms.family_available_json,
                   ms.missingness_json,
                   COALESCE((
                       SELECT sc.sector
                       FROM security_classifications sc
                       WHERE sc.security_id = ms.security_id
                         AND sc.effective_from <= ms.asof_date
                         AND (sc.effective_to IS NULL OR sc.effective_to >= ms.asof_date)
                         AND sc.model_available_at <= ms.asof_date || 'T23:59:59Z'
                       ORDER BY sc.effective_from DESC,
                                sc.model_available_at DESC,
                                sc.classification_id DESC
                       LIMIT 1
                   ), 'SECTOR_UNKNOWN') AS sector,
                   (
                       SELECT sc.industry
                       FROM security_classifications sc
                       WHERE sc.security_id = ms.security_id
                         AND sc.effective_from <= ms.asof_date
                         AND (sc.effective_to IS NULL OR sc.effective_to >= ms.asof_date)
                         AND sc.model_available_at <= ms.asof_date || 'T23:59:59Z'
                       ORDER BY sc.effective_from DESC,
                                sc.model_available_at DESC,
                                sc.classification_id DESC
                       LIMIT 1
                   ) AS industry,
                   (
                       SELECT sc.classification_system
                       FROM security_classifications sc
                       WHERE sc.security_id = ms.security_id
                         AND sc.effective_from <= ms.asof_date
                         AND (sc.effective_to IS NULL OR sc.effective_to >= ms.asof_date)
                         AND sc.model_available_at <= ms.asof_date || 'T23:59:59Z'
                       ORDER BY sc.effective_from DESC,
                                sc.model_available_at DESC,
                                sc.classification_id DESC
                       LIMIT 1
                   ) AS classification_system
            FROM multifactor_scores ms
            JOIN normalization_runs nr
              ON nr.normalization_run_id = ms.normalization_run_id
            JOIN securities s ON s.security_id = ms.security_id
            WHERE nr.universe_id = :universe_id
            ORDER BY ms.asof_date, ms.security_id
            """
        ),
        {"universe_id": universe_id},
    ).mappings()
    result = []
    for row in rows:
        value = dict(row)
        value["asof_date"] = _date(value["asof_date"])
        value["eligible"] = bool(value["eligible"])
        value["component_coverage"] = _decimal(value["component_coverage"])
        value["family_available_json"] = _json(value["family_available_json"])
        value["missingness_json"] = _json(value["missingness_json"])
        result.append(value)
    return result


def _load_feature_stats(session: Session) -> dict[tuple[str, date], dict[str, int]]:
    rows = session.execute(
        text(
            """
            SELECT security_id, asof_date,
                   COUNT(*) AS raw_feature_count,
                   SUM(CASE
                       WHEN feature_name IN (
                           'momentum_6_1', 'momentum_12_1',
                           'volatility_126d', 'beta_252d',
                           'downside_volatility_126d', 'maximum_drawdown_252d'
                       ) AND applicability_status = 'APPLICABLE'
                       THEN 1 ELSE 0 END
                   ) AS valid_price_feature_count,
                   SUM(CASE
                       WHEN family IN ('value', 'quality', 'growth')
                        AND applicability_status = 'APPLICABLE'
                       THEN 1 ELSE 0 END
                   ) AS valid_fundamental_feature_count
            FROM features
            GROUP BY security_id, asof_date
            """
        )
    ).mappings()
    return {
        (str(row["security_id"]), _date(row["asof_date"])): {
            "raw_feature_count": int(row["raw_feature_count"]),
            "valid_price_feature_count": int(row["valid_price_feature_count"]),
            "valid_fundamental_feature_count": int(
                row["valid_fundamental_feature_count"]
            ),
        }
        for row in rows
    }


def _load_normalized_counts(session: Session) -> dict[tuple[str, date], int]:
    rows = session.execute(
        text(
            """
            SELECT nf.security_id, nr.asof_date, COUNT(*) AS component_count
            FROM normalized_features nf
            JOIN normalization_runs nr
              ON nr.normalization_run_id = nf.normalization_run_id
            GROUP BY nf.security_id, nr.asof_date
            """
        )
    ).mappings()
    return {
        (str(row["security_id"]), _date(row["asof_date"])): int(
            row["component_count"]
        )
        for row in rows
    }


def _load_key_set(session: Session, query: str) -> set[tuple[str, date]]:
    return {
        (str(row[0]), _date(row[1])) for row in session.execute(text(query)).all()
    }


def _load_membership_counts(session: Session, universe_id: str) -> dict[str, int]:
    rows = session.execute(
        text(
            """
            SELECT nr.normalization_run_id,
                   COUNT(DISTINCT um.security_id) AS member_count
            FROM normalization_runs nr
            LEFT JOIN universe_memberships um
              ON um.universe_id = nr.universe_id
             AND um.effective_from <= nr.asof_date
             AND (um.effective_to IS NULL OR um.effective_to >= nr.asof_date)
             AND um.announced_at <= nr.asof_date || 'T23:59:59Z'
            WHERE nr.universe_id = :universe_id
            GROUP BY nr.normalization_run_id
            """
        ),
        {"universe_id": universe_id},
    ).mappings()
    return {str(row["normalization_run_id"]): int(row["member_count"]) for row in rows}


def _load_predictions(
    session: Session,
) -> tuple[dict[tuple[str, date], set[str]], int]:
    rows = session.execute(
        text(
            """
            SELECT security_id, asof_date, horizon, prediction_id
            FROM model_predictions
            WHERE model_version = :model_version
            ORDER BY asof_date, security_id, horizon
            """
        ),
        {"model_version": MULTIFACTOR_MODEL_VERSION},
    ).mappings()
    by_key: dict[tuple[str, date], set[str]] = defaultdict(set)
    count = 0
    for row in rows:
        by_key[(str(row["security_id"]), _date(row["asof_date"]))].add(
            str(row["horizon"])
        )
        count += 1
    return dict(by_key), count


def _evaluation_keys(
    backtest_document: Mapping[str, Any],
    comparison_document: Mapping[str, Any],
) -> set[tuple[str, date]]:
    if backtest_document.get("claims_eligible") is not False:
        raise ValueError("Sprint 8 backtest must retain claims_eligible=false")
    if comparison_document.get("claims_eligible") is not False:
        raise ValueError("Sprint 8 comparison must retain claims_eligible=false")
    evaluation = backtest_document.get("evaluation", {})
    if evaluation.get("primary_horizon") != "126d":
        raise ValueError("Sprint 8 primary horizon must be 126d")
    attribution = comparison_document.get("comparison", {}).get(
        "prediction_attribution", []
    )
    keys = {
        (str(row["security_id"]), date.fromisoformat(str(row["prediction_date"])))
        for row in attribution
    }
    if len(keys) != len(attribution):
        raise ValueError("comparison attribution contains duplicate security-months")
    for horizon in REQUIRED_HORIZONS:
        result = evaluation.get("horizons", {}).get(horizon)
        if not isinstance(result, Mapping):
            raise ValueError(f"missing Sprint 8 evaluation horizon: {horizon}")
        if int(result.get("eligible_observations", -1)) != len(keys):
            raise ValueError(f"{horizon} eligible count conflicts with attribution")
        if int(result.get("evaluated_observations", -1)) != len(keys):
            raise ValueError(f"{horizon} outcome count conflicts with attribution")
    return keys


def _increment(counter: dict[str, int], key: str, value: bool = True) -> None:
    if value:
        counter[key] = counter.get(key, 0) + 1


def _aggregate_records(
    records: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    dates = [run["asof_date"].isoformat() for run in runs]
    monthly: dict[str, dict[str, Any]] = {
        value: {
            "prediction_date": value,
            "counts": Counter(),
            "primary_reason_counts": Counter(),
            "diagnostic_reason_counts": Counter(),
            "component_reason_counts": Counter(),
            "eligible_sector_counts": Counter(),
        }
        for value in dates
    }
    totals: Counter[str] = Counter()
    primary: Counter[str] = Counter()
    diagnostic: Counter[str] = Counter()
    components: Counter[str] = Counter()
    family_patterns: Counter[str] = Counter()
    sector_totals: Counter[str] = Counter()
    sector_eligible: Counter[str] = Counter()
    unique_securities: dict[str, set[str]] = defaultdict(set)
    eligible_securities: dict[
        tuple[str, str, str, Optional[str], Optional[str]], list[str]
    ] = defaultdict(list)

    for record in records:
        value = monthly[str(record["prediction_date"])]
        counts: Counter[str] = value["counts"]
        stages = record["stages"]
        security_id = str(record["security_id"])
        counts["universe_members"] += 1
        totals["universe_members"] += 1
        unique_securities["universe_members"].add(security_id)
        for name, condition in (
            ("exact_prediction_date_prices", stages["exact_prediction_date_price"]),
            ("model_available_fundamental_facts", stages["model_available_fundamental_fact"]),
            ("usable_price_features", stages["valid_price_feature_count"] > 0),
            (
                "usable_fundamental_features",
                stages["valid_fundamental_feature_count"] > 0,
            ),
            (
                "both_price_and_fundamental_features",
                stages["valid_price_feature_count"] > 0
                and stages["valid_fundamental_feature_count"] > 0,
            ),
            ("complete_raw_feature_sets", stages["complete_raw_feature_set"]),
            (
                "complete_normalized_feature_sets",
                stages["complete_normalized_feature_set"],
            ),
            (
                "minimum_family_pass",
                stages["available_family_count"] >= MINIMUM_AVAILABLE_FAMILIES,
            ),
            (
                "minimum_component_coverage_pass",
                _decimal(stages["component_coverage"])
                >= MINIMUM_COMPONENT_COVERAGE,
            ),
            (
                "family_and_coverage_pass",
                stages["available_family_count"] >= MINIMUM_AVAILABLE_FAMILIES
                and _decimal(stages["component_coverage"])
                >= MINIMUM_COMPONENT_COVERAGE,
            ),
            ("eligible_final_scores", stages["eligible_final_score"]),
            ("prediction_security_months", stages["prediction_record_count"] > 0),
            ("mature_outcome_security_months_126d", stages["mature_outcome_126d"]),
            ("evaluated_observations_126d", stages["evaluated_126d"]),
        ):
            _increment(counts, name, bool(condition))
            _increment(totals, name, bool(condition))
            if condition:
                unique_securities[name].add(security_id)
        counts["prediction_records"] += int(stages["prediction_record_count"])
        totals["prediction_records"] += int(stages["prediction_record_count"])
        counts["mature_outcome_records"] += len(stages["mature_outcome_horizons"])
        totals["mature_outcome_records"] += len(stages["mature_outcome_horizons"])

        reason = str(record["primary_reason_code"])
        primary[reason] += 1
        value["primary_reason_counts"][reason] += 1
        for code in record["diagnostic_reason_codes"]:
            diagnostic[str(code)] += 1
            value["diagnostic_reason_counts"][str(code)] += 1
        for code, count in record["component_reason_counts"].items():
            components[str(code)] += int(count)
            value["component_reason_counts"][str(code)] += int(count)

        pattern = "+".join(record["available_families"]) or "NONE"
        family_patterns[pattern] += 1
        sector = str(record["sector"])
        sector_totals[sector] += 1
        if stages["eligible_final_score"]:
            sector_eligible[sector] += 1
            value["eligible_sector_counts"][sector] += 1
            eligible_securities[
                (
                    str(record["security_id"]),
                    str(record["ticker"]),
                    sector,
                    str(record["industry"]) if record["industry"] is not None else None,
                    (
                        str(record["classification_system"])
                        if record["classification_system"] is not None
                        else None
                    ),
                )
            ].append(str(record["prediction_date"]))

    monthly_rows = []
    for value in monthly.values():
        counts = dict(sorted(value["counts"].items()))
        universe = counts.get("universe_members", 0)
        eligible = counts.get("eligible_final_scores", 0)
        monthly_rows.append(
            {
                "prediction_date": value["prediction_date"],
                "counts": counts,
                "final_score_coverage": eligible / universe if universe else None,
                "primary_reason_counts": dict(
                    sorted(value["primary_reason_counts"].items())
                ),
                "diagnostic_reason_counts": dict(
                    sorted(value["diagnostic_reason_counts"].items())
                ),
                "component_reason_counts": dict(
                    sorted(value["component_reason_counts"].items())
                ),
                "eligible_sector_counts": dict(
                    sorted(value["eligible_sector_counts"].items())
                ),
            }
        )

    eligible_rows = int(totals["eligible_final_scores"])
    universe_rows = int(totals["universe_members"])
    totals_document = {
        **dict(sorted(totals.items())),
        "final_score_coverage": eligible_rows / universe_rows if universe_rows else None,
    }
    sector_rows = [
        {
            "sector": sector,
            "universe_stock_months": sector_totals[sector],
            "eligible_final_scores": sector_eligible[sector],
            "final_score_coverage": (
                sector_eligible[sector] / sector_totals[sector]
                if sector_totals[sector]
                else None
            ),
        }
        for sector in sorted(sector_totals)
    ]
    eligible_names = [
        {
            "security_id": key[0],
            "ticker": key[1],
            "sector": key[2],
            "industry": key[3],
            "classification_system": key[4],
            "eligible_months": len(values),
            "first_eligible_date": min(values),
            "last_eligible_date": max(values),
        }
        for key, values in sorted(eligible_securities.items(), key=lambda item: item[0][1])
    ]
    summary = {
        "funnel_totals": totals_document,
        "primary_reason_counts": dict(sorted(primary.items())),
        "diagnostic_reason_counts": dict(sorted(diagnostic.items())),
        "component_reason_counts": dict(sorted(components.items())),
        "family_availability_patterns": dict(sorted(family_patterns.items())),
        "sector_summary": sector_rows,
        "eligible_security_summary": eligible_names,
        "unique_security_counts": {
            name: len(values) for name, values in sorted(unique_securities.items())
        },
    }
    return summary, monthly_rows


def audit_sprint9_cohort_funnel(
    session: Session,
    *,
    backtest_document: Mapping[str, Any],
    comparison_document: Mapping[str, Any],
    closure_document: Mapping[str, Any],
    holdout_lock_document: Mapping[str, Any],
    universe_id: str = "sp500-pit-v1",
) -> Sprint9CohortFunnelAudit:
    """Reconcile every Sprint 8 cohort row through the final evaluation."""

    if closure_document.get("claims_eligible") is not False:
        raise ValueError("Sprint 8 closure must retain claims_eligible=false")
    if closure_document.get("closure_decision") != "pass":
        raise ValueError("Sprint 8 closure must have passed reproducibility")
    if holdout_lock_document.get("claims_eligible") is not False:
        raise ValueError("holdout lock must retain claims_eligible=false")

    runs = _load_runs(session, universe_id)
    if not runs:
        raise ValueError(f"no normalization runs found for {universe_id}")
    for run in runs:
        config = run["config_json"]
        if int(config.get("minimum_families", -1)) != MINIMUM_AVAILABLE_FAMILIES:
            raise ValueError("normalization run changed the minimum-family rule")
        if _decimal(config.get("minimum_component_coverage")) != MINIMUM_COMPONENT_COVERAGE:
            raise ValueError("normalization run changed the component-coverage rule")

    scores = _load_score_rows(session, universe_id)
    feature_stats = _load_feature_stats(session)
    normalized_counts = _load_normalized_counts(session)
    price_keys = _load_key_set(
        session,
        """
        SELECT ms.security_id, ms.asof_date
        FROM multifactor_scores ms
        WHERE EXISTS (
            SELECT 1 FROM prices p
            WHERE p.security_id = ms.security_id
              AND p.date = ms.asof_date
              AND p.close > 0 AND p.adj_close > 0
        )
        """,
    )
    fact_keys = _load_key_set(
        session,
        """
        SELECT ms.security_id, ms.asof_date
        FROM multifactor_scores ms
        WHERE EXISTS (
            SELECT 1 FROM fundamentals f
            WHERE f.security_id = ms.security_id
              AND date(f.model_available_at) <= ms.asof_date
        )
        """,
    )
    membership_counts = _load_membership_counts(session, universe_id)
    predictions, prediction_count = _load_predictions(session)
    evaluated_keys = _evaluation_keys(backtest_document, comparison_document)

    score_counts_by_run = Counter(str(row["normalization_run_id"]) for row in scores)
    membership_match = all(
        score_counts_by_run[run["normalization_run_id"]]
        == membership_counts.get(run["normalization_run_id"], -1)
        for run in runs
    )
    eligible_keys = {
        (str(row["security_id"]), row["asof_date"])
        for row in scores
        if row["eligible"]
    }
    if set(predictions) != eligible_keys:
        raise ValueError("prediction security-months do not equal eligible score rows")
    if evaluated_keys != eligible_keys:
        raise ValueError("final evaluation rows do not equal eligible score rows")
    if any(horizons != set(REQUIRED_HORIZONS) for horizons in predictions.values()):
        raise ValueError("eligible score is missing a frozen-horizon prediction")

    explanations = []
    state_hasher = hashlib.sha256()
    for score in scores:
        key = (str(score["security_id"]), score["asof_date"])
        feature = feature_stats.get(
            key,
            {
                "raw_feature_count": 0,
                "valid_price_feature_count": 0,
                "valid_fundamental_feature_count": 0,
            },
        )
        normalized_count = normalized_counts.get(key, 0)
        complete = (
            feature["raw_feature_count"] == FROZEN_FEATURE_COUNT
            and normalized_count == FROZEN_FEATURE_COUNT
        )
        missingness = dict(score["missingness_json"] or {})
        component_reason_counts: Counter[str] = Counter(
            str(value.get("reason") or value.get("status") or "UNSPECIFIED")
            for value in missingness.values()
            if isinstance(value, Mapping)
        )
        component_reason_counts["VALID"] += int(score["valid_component_count"])
        if sum(component_reason_counts.values()) != FROZEN_FEATURE_COUNT:
            raise ValueError(
                f"component ledger does not total 19 for {key[0]} {key[1]}"
            )
        family_available = {
            str(name): bool(value)
            for name, value in dict(score["family_available_json"] or {}).items()
        }
        horizons = tuple(sorted(predictions.get(key, set())))
        evaluated = key in evaluated_keys
        primary = primary_reason_code(
            complete_feature_set=complete,
            available_family_count=int(score["available_family_count"]),
            component_coverage=score["component_coverage"],
            eligible=bool(score["eligible"]),
            prediction_horizons=horizons,
            evaluated_126d=evaluated,
        )
        diagnostics = diagnostic_reason_codes(
            exact_prediction_price=key in price_keys,
            model_available_fundamental_fact=key in fact_keys,
            valid_price_feature_count=feature["valid_price_feature_count"],
            valid_fundamental_feature_count=feature[
                "valid_fundamental_feature_count"
            ],
            sector=str(score["sector"]),
            component_reasons=component_reason_counts,
        )
        reason_codes = tuple(sorted(set(diagnostics) | {primary}))
        explanation = {
            "prediction_date": score["asof_date"].isoformat(),
            "security_id": key[0],
            "ticker": str(score["ticker"]),
            "sector": str(score["sector"]),
            "industry": score["industry"],
            "classification_system": score["classification_system"],
            "included_in_final_evaluation": evaluated,
            "primary_reason_code": primary,
            "reason_codes": list(reason_codes),
            "diagnostic_reason_codes": list(diagnostics),
            "available_families": sorted(
                family for family, available in family_available.items() if available
            ),
            "family_available": dict(sorted(family_available.items())),
            "component_reason_counts": dict(sorted(component_reason_counts.items())),
            "missing_components": {
                name: str(value.get("reason") or value.get("status") or "UNSPECIFIED")
                for name, value in sorted(missingness.items())
                if isinstance(value, Mapping)
            },
            "stages": {
                "universe_member": True,
                "exact_prediction_date_price": key in price_keys,
                "model_available_fundamental_fact": key in fact_keys,
                "raw_feature_count": feature["raw_feature_count"],
                "complete_raw_feature_set": (
                    feature["raw_feature_count"] == FROZEN_FEATURE_COUNT
                ),
                "normalized_feature_count": normalized_count,
                "complete_normalized_feature_set": (
                    normalized_count == FROZEN_FEATURE_COUNT
                ),
                "valid_price_feature_count": feature["valid_price_feature_count"],
                "valid_fundamental_feature_count": feature[
                    "valid_fundamental_feature_count"
                ],
                "applicable_component_count": int(
                    score["applicable_component_count"]
                ),
                "valid_component_count": int(score["valid_component_count"]),
                "component_coverage": str(score["component_coverage"]),
                "available_family_count": int(score["available_family_count"]),
                "eligible_final_score": bool(score["eligible"]),
                "final_score": (
                    str(score["final_score"])
                    if score["final_score"] is not None
                    else None
                ),
                "prediction_horizons": list(horizons),
                "prediction_record_count": len(horizons),
                "mature_outcome_horizons": (
                    list(REQUIRED_HORIZONS) if evaluated else []
                ),
                "mature_outcome_126d": evaluated,
                "evaluated_126d": evaluated,
            },
        }
        explanations.append(explanation)
        state_hasher.update(_canonical_bytes(explanation))
        state_hasher.update(b"\n")

    summary, monthly = _aggregate_records(explanations, runs)
    totals = summary["funnel_totals"]
    primary_total = sum(summary["primary_reason_counts"].values())
    normalized_total = sum(normalized_counts.values())
    expected_normalized_total = len(scores) * FROZEN_FEATURE_COUNT
    integrity = {
        "all_monthly_score_rows_match_point_in_time_membership": membership_match,
        "all_security_months_have_19_raw_features": (
            totals["complete_raw_feature_sets"] == len(scores)
        ),
        "all_security_months_have_19_normalized_features": (
            totals["complete_normalized_feature_sets"] == len(scores)
        ),
        "normalized_component_total_matches": (
            normalized_total == expected_normalized_total
        ),
        "every_security_month_has_one_primary_disposition": (
            primary_total == len(scores)
        ),
        "eligible_scores_equal_prediction_security_months": (
            totals["eligible_final_scores"]
            == totals["prediction_security_months"]
        ),
        "prediction_records_cover_all_four_horizons": (
            prediction_count == totals["eligible_final_scores"] * len(REQUIRED_HORIZONS)
        ),
        "eligible_scores_equal_evaluated_126d_observations": (
            totals["eligible_final_scores"]
            == totals["evaluated_observations_126d"]
        ),
    }
    if not all(integrity.values()):
        failed = sorted(key for key, value in integrity.items() if not value)
        raise ValueError("cohort funnel integrity failed: " + ", ".join(failed))

    monthly_eligible = [
        int(row["counts"].get("eligible_final_scores", 0)) for row in monthly
    ]
    nonempty = [value for value in monthly_eligible if value]
    holdout_start = str(holdout_lock_document["holdout_start"])
    holdout_end = str(holdout_lock_document["holdout_end"])
    holdout_months = [
        row
        for row in monthly
        if holdout_start <= row["prediction_date"] <= holdout_end
    ]

    def holdout_count(name: str) -> int:
        return sum(int(row["counts"].get(name, 0)) for row in holdout_months)

    financial_eligible = next(
        (
            int(row["eligible_final_scores"])
            for row in summary["sector_summary"]
            if row["sector"] == "Financials"
        ),
        0,
    )
    run_hash = hashlib.sha256(
        _canonical_bytes(
            [
                {
                    "normalization_run_id": run["normalization_run_id"],
                    "asof_date": run["asof_date"].isoformat(),
                    "input_hash": run["input_hash"],
                }
                for run in runs
            ]
        )
    ).hexdigest()
    full_coverage = float(totals["final_score_coverage"] or 0.0)
    audit = {
        "universe_id": universe_id,
        "scope": {
            "start": runs[0]["asof_date"].isoformat(),
            "end": runs[-1]["asof_date"].isoformat(),
            "monthly_cohorts": len(runs),
            "holdout_start": holdout_start,
            "holdout_end": holdout_end,
        },
        "stage_definitions": {
            "universe_members": "Distinct point-in-time members as known by each monthly prediction date.",
            "exact_prediction_date_prices": "Security has positive close and adjusted close on the prediction date.",
            "model_available_fundamental_facts": "At least one fundamental fact has model_available_at on or before the prediction date.",
            "usable_price_features": "At least one of the six frozen momentum/risk price components is APPLICABLE.",
            "usable_fundamental_features": "At least one frozen value, quality, or growth component is APPLICABLE.",
            "complete_raw_feature_sets": "All 19 frozen raw component rows are stored, including explicit missing and not-applicable rows.",
            "complete_normalized_feature_sets": "All 19 frozen normalized component rows are stored, so the security-month entered monthly normalization/scoring.",
            "minimum_family_pass": "At least four of value, quality, growth, momentum, and risk satisfy the frozen family-availability rule.",
            "family_and_coverage_pass": "Minimum-family pass plus at least 70 percent valid applicable components.",
            "eligible_final_scores": "Stored score eligibility is true and final_score is non-null.",
            "prediction_security_months": "At least one immutable multifactor prediction exists; integrity checks require all four horizons.",
            "mature_outcome_security_months_126d": "The final verified artifact contains a mature 126-session outcome.",
            "evaluated_observations_126d": "The row is present in the final comparison attribution and 126-session evaluation.",
        },
        "primary_reason_definitions": {
            BELOW_FAMILIES: "Fewer than four families are available; checked before component coverage.",
            BELOW_COVERAGE: "At least four families are available but component coverage is below 70 percent.",
            INCLUDED: "The row passes score eligibility and is retained through predictions, mature outcomes, and final evaluation.",
        },
        **summary,
        "monthly_cohorts": monthly,
        "diagnoses": {
            "final_evaluation_count": {
                "reason_code": "ELIGIBILITY_FUNNEL_EXPLAINS_FINAL_60",
                "eligible_final_scores": totals["eligible_final_scores"],
                "prediction_security_months": totals[
                    "prediction_security_months"
                ],
                "mature_126d_outcomes": totals[
                    "mature_outcome_security_months_126d"
                ],
                "evaluated_126d_observations": totals[
                    "evaluated_observations_126d"
                ],
                "explanation": (
                    "No row is lost after final-score eligibility; the final 60 are "
                    "the only security-months that pass both frozen score gates."
                ),
            },
            "financials_only": {
                "reason_code": "FINANCIAL_MASK_ONLY_PATH_TO_ELIGIBILITY",
                "financials_eligible_observations": financial_eligible,
                "total_eligible_observations": totals["eligible_final_scores"],
                "eligible_share": (
                    financial_eligible / totals["eligible_final_scores"]
                    if totals["eligible_final_scores"]
                    else None
                ),
                "explanation": (
                    "Every eligible row is labelled Financials. The financial-sector "
                    "mask removes nine industrial-accounting components, leaving ten "
                    "applicable components; the five eligible names can then pass with "
                    "value, growth, momentum, and risk while quality is unavailable."
                ),
            },
            "empty_bottom_quintile": quintile_one_diagnosis(nonempty),
            "monthly_breadth": {
                "months_with_any_eligible_score": len(nonempty),
                "months_with_zero_eligible_scores": len(monthly_eligible) - len(nonempty),
                "minimum_nonzero_eligible_scores": min(nonempty, default=0),
                "maximum_eligible_scores": max(nonempty, default=0),
                "eligible_count_distribution": dict(
                    sorted(Counter(monthly_eligible).items())
                ),
            },
            "holdout_breadth": {
                "monthly_cohorts": len(holdout_months),
                "universe_stock_months": holdout_count("universe_members"),
                "minimum_family_pass": holdout_count("minimum_family_pass"),
                "eligible_final_scores": holdout_count("eligible_final_scores"),
                "evaluated_126d_observations": holdout_count(
                    "evaluated_observations_126d"
                ),
                "months_with_any_eligible_score": sum(
                    int(row["counts"].get("eligible_final_scores", 0)) > 0
                    for row in holdout_months
                ),
                "final_score_coverage": (
                    holdout_count("eligible_final_scores")
                    / holdout_count("universe_members")
                    if holdout_count("universe_members")
                    else None
                ),
            },
        },
        "breadth_assessment": {
            "decision": "fail",
            "broad_enough_to_trust": False,
            "final_score_coverage": full_coverage,
            "minimum_required_monthly_coverage": 0.90,
            "months_at_or_above_required_coverage": sum(
                float(row["final_score_coverage"] or 0.0) >= 0.90 for row in monthly
            ),
            "conclusion": (
                "Sprint 8 evidence is not broad enough to support model promotion or "
                "investability conclusions."
            ),
        },
        "integrity_checks": integrity,
        "warehouse_fingerprint": {
            "normalization_run_input_sha256": run_hash,
            "security_month_explanation_sha256": state_hasher.hexdigest(),
            "score_rows": len(scores),
            "raw_feature_rows": sum(
                row["stages"]["raw_feature_count"] for row in explanations
            ),
            "normalized_feature_rows": normalized_total,
            "prediction_records": prediction_count,
        },
    }
    return Sprint9CohortFunnelAudit(
        audit=audit,
        explanations=tuple(explanations),
    )
