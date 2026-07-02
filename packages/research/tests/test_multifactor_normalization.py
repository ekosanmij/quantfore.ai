from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, inspect, select

from quantfore_research.db import build_engine, create_schema, make_session_factory
from quantfore_research.features.multifactor import (
    APPLICABLE,
    FEATURE_DEFINITIONS,
    HIGHER,
    MISSING,
    MULTIFACTOR_FEATURE_VERSION,
    NOT_APPLICABLE,
    MultiFactorFeatureBatch,
    RawFeature,
)
from quantfore_research.models import (
    Feature,
    FeatureSet,
    MultiFactorScore,
    NormalizationRun,
    NormalizedFeature,
    Security,
    SourceSnapshot,
    UniverseDefinition,
)
from quantfore_research.scoring.multifactor import (
    NORMALIZATION_VERSION,
    normalize_multifactor_cohort,
    store_multifactor_cohort_scores,
)


PREDICTION = datetime(2021, 1, 29, 23, 59, tzinfo=timezone.utc)
HASH = "9" * 64


def make_batch(
    index,
    *,
    sector="Technology",
    missing=(),
    not_applicable=(),
    identical=False,
):
    features = []
    for feature_index, definition in enumerate(FEATURE_DEFINITIONS):
        if definition.name in not_applicable:
            status = NOT_APPLICABLE
            value = None
            reason = "NOT_APPLICABLE"
        elif definition.name in missing:
            status = MISSING
            value = None
            reason = "SOURCE_MISSING"
        else:
            status = APPLICABLE
            reason = None
            rank_value = Decimal("1") if identical else Decimal(index)
            value = (
                rank_value
                if definition.direction == HIGHER
                else Decimal("100") - rank_value
            )
            value += Decimal(feature_index) / Decimal("1000")
        features.append(RawFeature(definition, value, status, reason, ()))
    return MultiFactorFeatureBatch(
        security_id=f"security-{index:02d}",
        benchmark_security_id="benchmark",
        prediction_timestamp=PREDICTION,
        sector=sector,
        industry=None,
        features=tuple(features),
    )


def test_winsorization_sector_standardization_direction_and_percentile_score():
    batches = [make_batch(index) for index in range(12)] + [
        make_batch(12, sector="Energy"),
        make_batch(13, sector="Energy"),
        make_batch(14, sector="Energy"),
    ]
    result = normalize_multifactor_cohort(batches)
    scores = result.by_security()

    tech_component = next(
        row
        for row in scores["security-11"].components
        if row.feature_name == "revenue_growth"
    )
    energy_component = next(
        row
        for row in scores["security-14"].components
        if row.feature_name == "revenue_growth"
    )
    risk_component = next(
        row
        for row in scores["security-11"].components
        if row.feature_name == "volatility_126d"
    )

    assert tech_component.normalization_scope == "SECTOR"
    assert tech_component.group_count == 12
    assert energy_component.normalization_scope == "UNIVERSE"
    assert energy_component.group_count == 15
    assert energy_component.winsorized_value == energy_component.winsor_upper
    assert energy_component.raw_value > energy_component.winsorized_value
    assert risk_component.directed_value == -risk_component.standardized_value
    assert scores["security-00"].final_score == Decimal("0")
    assert max(score.final_score for score in result.scores) == Decimal("100")
    assert scores["security-14"].final_score > Decimal("50")
    assert all(score.eligible for score in result.scores)
    for score in result.scores:
        assert sum(
            (
                component.contribution
                for component in score.components
                if component.contribution is not None
            ),
            Decimal("0"),
        ) == score.composite_z
        assert all(
            value is None or Decimal("0") <= value <= Decimal("100")
            for value in score.family_scores.values()
        )


def test_four_family_weight_renormalization_and_seventy_percent_gate():
    quality = {
        definition.name
        for definition in FEATURE_DEFINITIONS
        if definition.family == "quality"
    }
    four_family = normalize_multifactor_cohort(
        [make_batch(1, missing=quality)]
    ).scores[0]
    below_coverage = normalize_multifactor_cohort(
        [make_batch(1, missing=quality | {"revenue_growth"})]
    ).scores[0]

    assert four_family.eligible is True
    assert four_family.available_family_count == 4
    assert four_family.renormalized_weights["quality"] == 0
    assert {
        value
        for family, value in four_family.renormalized_weights.items()
        if family != "quality"
    } == {Decimal("0.25")}
    assert four_family.valid_component_count == 14
    assert four_family.component_coverage >= Decimal("0.70")
    assert four_family.final_score == Decimal("50")
    assert below_coverage.eligible is False
    assert below_coverage.final_score is None
    assert below_coverage.component_coverage < Decimal("0.70")
    assert below_coverage.missingness["revenue_growth"]["reason"] == "SOURCE_MISSING"


def test_not_applicable_components_are_excluded_from_coverage_denominator():
    masked = {
        "fcf_yield",
        "ebit_ev",
        "roic",
        "gross_profitability",
        "fcf_conversion",
        "inverse_accruals",
        "inverse_leverage",
        "fcf_growth",
        "margin_change",
    }
    score = normalize_multifactor_cohort(
        [make_batch(1, sector="Financials", not_applicable=masked)]
    ).scores[0]

    assert score.applicable_component_count == 10
    assert score.valid_component_count == 10
    assert score.component_coverage == 1
    assert score.eligible is True


def test_tied_composites_receive_average_percentile_rank():
    result = normalize_multifactor_cohort(
        [make_batch(index, identical=True) for index in range(3)]
    )

    assert {score.final_score for score in result.scores} == {Decimal("50")}


def test_normalized_components_and_scores_are_persisted(tmp_path):
    engine = build_engine(database_url="sqlite+pysqlite:///:memory:")
    create_schema(engine)
    session = make_session_factory(engine)()
    snapshot = SourceSnapshot(
        snapshot_id="snapshot",
        vendor="Test",
        dataset="raw-features",
        license_tag="test",
        source_hash=HASH,
        storage_uri="raw/test/features.json",
    )
    benchmark = Security(
        security_id="benchmark", ticker="SPY", name="Benchmark"
    )
    session.add_all([snapshot, benchmark])
    session.flush()
    session.add(
        UniverseDefinition(
            universe_id="test-universe",
            name="Test Universe",
            version="v1",
            description="Normalization test",
            window_start=date(2020, 1, 1),
            window_end=date(2022, 1, 1),
            benchmark_security_id="benchmark",
            benchmark_excluded_from_rankings=True,
            source_snapshot_id="snapshot",
            source_hash=HASH,
        )
    )
    batches = [make_batch(1), make_batch(2)]
    raw_feature_ids = {}
    feature_set_ids = []
    for batch in batches:
        session.add(
            Security(
                security_id=batch.security_id,
                ticker=batch.security_id,
                name=batch.security_id,
            )
        )
        feature_set_id = f"feature-set-{batch.security_id}"
        feature_set_ids.append(feature_set_id)
        session.add(
            FeatureSet(
                feature_set_id=feature_set_id,
                name="pit_multifactor_raw_features",
                version=MULTIFACTOR_FEATURE_VERSION,
                asof_date=PREDICTION.date(),
                config_json={},
                source_snapshot_id="snapshot",
            )
        )
        for raw in batch.features:
            feature_id = f"feature-{batch.security_id}-{raw.definition.name}"
            raw_feature_ids[(batch.security_id, raw.definition.name)] = feature_id
            session.add(
                Feature(
                    feature_id=feature_id,
                    feature_set_id=feature_set_id,
                    security_id=batch.security_id,
                    asof_date=PREDICTION.date(),
                    available_at=PREDICTION,
                    feature_name=raw.definition.name,
                    value=raw.value,
                    raw_value=raw.value,
                    version=MULTIFACTOR_FEATURE_VERSION,
                    family=raw.definition.family,
                    formula_version=MULTIFACTOR_FEATURE_VERSION,
                    formula=raw.definition.formula,
                    direction=raw.definition.direction,
                    applicability_status=raw.status,
                    missing_reason=raw.missing_reason,
                    inputs_json={},
                    source_snapshot_id="snapshot",
                    source_hash=HASH,
                )
            )
    session.commit()
    result = normalize_multifactor_cohort(batches)

    run = store_multifactor_cohort_scores(
        session,
        result=result,
        normalization_run_id="normalization-test",
        universe_id="test-universe",
        raw_feature_ids=raw_feature_ids,
        source_feature_set_ids=feature_set_ids,
        code_commit="test",
    )
    session.commit()
    reused = store_multifactor_cohort_scores(
        session,
        result=result,
        normalization_run_id="normalization-test",
        universe_id="test-universe",
        raw_feature_ids=raw_feature_ids,
        source_feature_set_ids=feature_set_ids,
        code_commit="test",
    )

    assert run.input_hash == reused.input_hash
    assert session.scalar(select(func.count()).select_from(NormalizedFeature)) == 38
    assert session.scalar(select(func.count()).select_from(MultiFactorScore)) == 2
    stored_score = session.scalar(
        select(MultiFactorScore).where(MultiFactorScore.security_id == "security-02")
    )
    assert stored_score.final_score == Decimal("100.000000")
    assert stored_score.family_scores_json["value"] is not None
    stored_component = session.scalar(
        select(NormalizedFeature).where(
            NormalizedFeature.security_id == "security-02",
            NormalizedFeature.feature_name == "revenue_growth",
        )
    )
    assert stored_component.raw_value is not None
    assert stored_component.winsorized_value is not None
    assert stored_component.standardized_value is not None
    assert stored_component.contribution is not None
    assert {
        "normalization_runs",
        "normalized_features",
        "multifactor_scores",
    }.issubset(set(inspect(engine).get_table_names()))
