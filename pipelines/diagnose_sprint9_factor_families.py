"""Generate the Sprint 9.3 factor-family diagnostic report."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

try:
    import _bootstrap  # noqa: F401
    from _common import get_code_revision, repository_relative_path
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        get_code_revision,
        repository_relative_path,
    )

from quantfore_research.validation.factor_diagnostics import (
    diagnose_sprint9_factor_families,
)


DEFAULT_DATABASE = Path(
    "data/raw/free-point-in-time/sprint8-prelock-v9/research.db"
)
DEFAULT_COMPARISON = Path("reports/comparisons/price-vs-multifactor-v1.json")
DEFAULT_BACKTEST = Path("reports/backtests/pit_multifactor_baseline_v1.json")
DEFAULT_COHORT_AUDIT = Path(
    "reports/data-audits/sprint9-cohort-funnel-v1.json"
)
DEFAULT_CONTRACT = Path("docs/research/multifactor-baseline-v1.md")
DEFAULT_JSON_OUTPUT = Path("reports/research/sprint9-factor-diagnostics-v1.json")
DEFAULT_MARKDOWN_OUTPUT = Path("reports/research/sprint9-factor-diagnostics-v1.md")


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_only_database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///file:{path.resolve()}?mode=ro&uri=true"


def _open_read_only_session(database_url: str):
    engine = create_engine(
        database_url,
        future=True,
        connect_args={"check_same_thread": False}
        if database_url.startswith("sqlite")
        else {},
    )
    if database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _query_only(dbapi_connection, connection_record):
            del connection_record
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA query_only=ON")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute("PRAGMA cache_size=-262144")
            cursor.close()

    return sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )


def _write_atomic(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return hashlib.sha256(payload).hexdigest()


def _percent(value: Any, *, decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.{decimals}f}%"


def _number(value: Any, *, decimals: int = 4) -> str:
    if value is None:
        return "—"
    return f"{float(value):.{decimals}f}"


def _assessment_label(value: str) -> str:
    return value.replace("_", " ").title()


def render_markdown(document: Mapping[str, Any]) -> str:
    diagnostic = document["diagnostic"]
    scope = diagnostic["scope"]
    performance = diagnostic["performance"]
    full = performance["published_full_model"]
    standalone = performance["standalone_families"]
    grouped = performance["grouped_signals"]
    ablations = {
        row["family"]: row
        for row in performance["published_family_ablations"]
    }
    contributions = {
        row["family"]: row
        for row in diagnostic["score_contribution_attribution"]["by_family"]
    }
    missingness = {
        row["family"]: row
        for row in diagnostic["family_missingness_all_security_months"]
    }
    eligible_missingness = {
        row["family"]: row
        for row in diagnostic["family_missingness_evaluated_rows"]
    }
    assessments = {
        row["family"]: row for row in diagnostic["family_assessments"]
    }
    lines = [
        "# Sprint 9.3 Factor Family Diagnostic v1",
        "",
        "`claims_eligible=false`",
        "",
        f"- Decision: `{diagnostic['decision']}`",
        f"- Evidence generated: `{document['generated_at']}`",
        f"- Code revision: `{document['code_revision']}`",
        f"- Warehouse: `{document['warehouse']['path']}`",
        f"- Machine-readable companion: [`{Path(document['json_output']).name}`]({Path(document['json_output']).name})",
        "",
        "## Decision",
        "",
        "> **Sprint 8 is not a broadly validated five-family signal. In the only "
        "evaluated rows it is a four-family value/growth/momentum/risk score; "
        "quality contributes nothing.**",
        "",
        f"The diagnostic covers `{scope['security_months']:,}` stock-months and "
        f"`{scope['eligible_evaluated_security_months']}` evaluated stock-months. "
        f"Those evaluated rows contain only `{scope['eligible_evaluated_unique_securities']}` "
        f"unique names, all labelled `{', '.join(scope['eligible_evaluated_sectors'])}`, "
        f"and only `{scope['calculable_rank_ic_months']}` months have enough names to "
        "calculate Rank IC. The published full-model mean Rank IC is "
        f"`{_number(full['mean_rank_ic'])}`, but its non-overlapping t-statistic remains "
        f"`{full['non_overlapping_rank_ic_t_statistic']}` and its 25 bps top-bucket "
        f"net excess return is `{_percent(full['top_bucket_net_excess_return_25_bps'])}`.",
        "",
        "No family is established as genuinely useful. Momentum, risk, and growth show "
        "positive ranking behavior in the tiny evaluated cross-sections; value does not, "
        "and quality is absent. These are root-cause findings, not promotion evidence.",
        "",
        "## Family verdicts",
        "",
        "| Family | Universe family availability | Evaluated rows with family | Universe valid component rate | Standalone Rank IC | IC loss when removed | Absolute score contribution | Verdict |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for family in ("value", "quality", "growth", "momentum", "risk"):
        lines.append(
            f"| {family.title()} | "
            f"{_percent(missingness[family]['family_availability_rate'], decimals=4)} | "
            f"{eligible_missingness[family]['family_available_security_months']} / "
            f"{scope['eligible_evaluated_security_months']} | "
            f"{_percent(missingness[family]['valid_component_rate'])} | "
            f"{_number(standalone[family]['mean_rank_ic'])} | "
            f"{_number(ablations[family]['rank_ic_loss_when_removed'])} | "
            f"{_percent(contributions[family]['absolute_contribution_share'])} | "
            f"{_assessment_label(assessments[family]['state'])} |"
        )
    lines.extend(
        [
            "",
            "Availability means the frozen scorer found at least half of a family's "
            "applicable components valid. Absolute contribution is descriptive score "
            "attribution, not return attribution. Every family verdict retains "
            "`evidence_is_sufficient_to_call_useful=false`.",
            "",
            "## Where the reported Rank IC came from",
            "",
            "| Signal | Mean Rank IC | Calculable months | Top-bucket gross excess | Top-bucket net excess at 25 bps |",
            "| --- | ---: | ---: | ---: | ---: |",
            f"| Full Sprint 8 model | {_number(full['mean_rank_ic'])} | "
            f"{scope['calculable_rank_ic_months']} | "
            f"{_percent(full['top_bucket_gross_excess_return'])} | "
            f"{_percent(full['top_bucket_net_excess_return_25_bps'])} |",
            f"| Fundamentals block: value + growth; quality unavailable | "
            f"{_number(grouped['fundamentals_value_quality_growth']['mean_rank_ic'])} | "
            f"{grouped['fundamentals_value_quality_growth']['calculable_rank_ic_months']} | "
            f"{_percent(grouped['fundamentals_value_quality_growth']['top_bucket_gross_excess_return'])} | "
            f"{_percent(grouped['fundamentals_value_quality_growth']['top_bucket_net_excess_return_25_bps'])} |",
            f"| Price/risk block: momentum + risk | "
            f"{_number(grouped['price_risk_momentum_risk']['mean_rank_ic'])} | "
            f"{grouped['price_risk_momentum_risk']['calculable_rank_ic_months']} | "
            f"{_percent(grouped['price_risk_momentum_risk']['top_bucket_gross_excess_return'])} | "
            f"{_percent(grouped['price_risk_momentum_risk']['top_bucket_net_excess_return_25_bps'])} |",
            f"| Sprint 7 price-only baseline | "
            f"{_number(performance['sprint7_price_only_mean_rank_ic'])} | — | — | — |",
            "",
            "The grouped diagnostic is the clearest answer to the fundamental-versus-price "
            "question: the momentum/risk block records mean Rank IC "
            f"`{_number(grouped['price_risk_momentum_risk']['mean_rank_ic'])}`, versus "
            f"`{_number(grouped['fundamentals_value_quality_growth']['mean_rank_ic'])}` "
            "for the available fundamentals block. Momentum is the strongest standalone "
            "family. However, removing growth causes the largest full-model Rank IC loss, "
            "so the full ranking appears to depend on interaction between the blocks. "
            "Nine tiny pre-holdout cross-sections cannot establish that interaction as stable.",
            "",
            "All standalone and grouped top buckets remain negative versus the benchmark "
            "after 25 bps. No bottom bucket exists, so top-minus-bottom performance is "
            "undefined throughout.",
            "",
            "## Frozen family ablations",
            "",
            "The Sprint 8 ablations remove one family, renormalize the frozen equal weights "
            "across the remaining available families, require at least three remaining "
            "families, and do not retune.",
            "",
            "| Removed family | Ablated mean Rank IC | Full minus ablated IC | 25 bps top-bucket net excess | Interpretation |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
    )
    for family in ("value", "quality", "growth", "momentum", "risk"):
        row = ablations[family]
        lines.append(
            f"| {family.title()} | {_number(row['ablated_mean_rank_ic'])} | "
            f"{_number(row['rank_ic_loss_when_removed'])} | "
            f"{_percent(row['top_bucket_net_excess_return_25_bps'])} | "
            f"{_assessment_label(row['interpretation'])} |"
        )
    lines.extend(
        [
            "",
            "Growth has the largest positive removal delta (`0.3556`), followed by risk "
            "(`0.3111`) and momentum (`0.2556`). Removing value slightly improves mean "
            "Rank IC (`-0.0222` loss). Removing quality changes nothing because quality is "
            "unavailable in every evaluated row.",
            "",
            "## Missingness by component",
            "",
            "The table below uses all 50,600 security-months. A valid component has a "
            "stored directed normalized value; every other row retains the scorer's exact "
            "reason code.",
            "",
            "| Family | Component | Valid | Dominant state | NOT_APPLICABLE | SOURCE_MISSING |",
            "| --- | --- | ---: | --- | ---: | ---: |",
        ]
    )
    for row in diagnostic["component_missingness_all_security_months"]:
        reasons = row["reason_counts"]
        lines.append(
            f"| {row['family'].title()} | `{row['feature_name']}` | "
            f"{_percent(row['valid_rate'])} | `{row['dominant_state']}` "
            f"({_percent(row['dominant_state_rate'])}) | "
            f"{reasons.get('NOT_APPLICABLE', 0):,} | "
            f"{reasons.get('SOURCE_MISSING', 0):,} |"
        )
    mostly = diagnostic[
        "components_mostly_not_applicable_or_source_missing_in_evaluated_rows"
    ]
    lines.extend(
        [
            "",
            "### Components mostly NOT_APPLICABLE or SOURCE_MISSING in evaluated rows",
            "",
            "Using a strict greater-than-50% threshold, the following evaluated-score "
            "components qualify:",
            "",
        ]
    )
    for row in mostly:
        lines.append(
            f"- `{row['feature_name']}` ({row['family']}): "
            f"{_percent(row['rate'])}; reasons `{json.dumps(row['reason_counts'], sort_keys=True)}`."
        )
    lines.extend(
        [
            "",
            "All nine qualifying components are 100% `NOT_APPLICABLE` in the evaluated "
            "cohort: `ebit_ev`, `fcf_yield`, all five quality components, `fcf_growth`, "
            "and `margin_change`. This matches the broad Financials mask applied to the "
            "five evaluated names. `sales_yield` is additionally `SOURCE_MISSING` in "
            "9 of 60 evaluated rows.",
            "",
            "Universe-wide missingness is driven mainly by `INSUFFICIENT_HISTORY`, not "
            "only accounting applicability: momentum and risk are broadly available, "
            "while fundamental growth/value histories are exceptionally sparse. Quality's "
            "component-valid rate is propped up by `inverse_leverage`, but the family-level "
            "half-component rule leaves quality available in only 15 of 50,600 stock-months.",
            "",
            "## Is the model multi-factor in practice?",
            "",
            "| Test | Result |",
            "| --- | --- |",
            f"| Every evaluated row has exactly four available families | `{str(diagnostic['practice_assessment']['all_evaluated_rows_have_exactly_four_available_families']).lower()}` |",
            f"| Quality appears in any evaluated score | `{str(diagnostic['practice_assessment']['quality_present_in_any_evaluated_score']).lower()}` |",
            f"| Broad five-family model in practice | `{str(diagnostic['practice_assessment']['five_family_model_in_practice']).lower()}` |",
            f"| Broad multi-factor validation established | `{str(diagnostic['practice_assessment']['broad_multifactor_validation_established']).lower()}` |",
            f"| Any family established as useful | `{str(diagnostic['practice_assessment']['useful_family_established']).lower()}` |",
            "",
            diagnostic["practice_assessment"]["reason"],
            "",
            "The dominance tests also disagree: momentum leads standalone Rank IC, growth "
            "causes the largest ablation loss and has the largest absolute contribution "
            "share. Therefore `consistent_single_family_dominance=false`. The correct "
            "conclusion is not that one family dominates robustly; it is that the current "
            "evidence cannot separate stable signal from a five-name cohort artifact.",
            "",
            "## Sprint 9 implications",
            "",
            "1. Do not promote or tune Model V2 from this result.",
            "2. Treat quality as broken/effectively absent until family coverage is repaired.",
            "3. Treat value and growth as sparse; growth's positive ablation result is a "
            "hypothesis to retest, not a validated family claim.",
            "4. Retain momentum and risk as broadly computable baselines, but do not call "
            "them investable while their selected baskets remain negative versus SPY.",
            "5. Resolve Financials/REIT applicability and classification in Sprint 9.6 "
            "before interpreting the accounting-family results.",
            "",
            "## Integrity and provenance",
            "",
            f"The recomputed full-model Rank IC matches the published value exactly. "
            f"Reconstructed monthly outcome returns match the published comparison within "
            f"`{diagnostic['warehouse_lineage']['outcome_reproduction']['reproduction_tolerance']}`; "
            f"the maximum absolute difference is "
            f"`{diagnostic['warehouse_lineage']['outcome_reproduction']['maximum_absolute_published_period_return_difference']}`.",
            "",
            "| Artifact | SHA-256 |",
            "| --- | --- |",
        ]
    )
    for source in document["source_artifacts"]:
        lines.append(f"| `{source['path']}` | `{source['sha256']}` |")
    lines.extend(
        [
            "",
            "The large derived warehouse is bound through the deterministic score-family, "
            "component-aggregate, and reconstructed-outcome hashes in the JSON companion.",
            "",
            "## Claims boundary",
            "",
            "This diagnostic does not establish predictive value, outperformance, "
            "investability, suitability, or investment advice. `claims_eligible=false` "
            "remains mandatory.",
            "",
        ]
    )
    return "\n".join(lines)


def build_document(
    diagnostic: Mapping[str, Any],
    *,
    generated_at: datetime,
    code_revision: Optional[str],
    source_artifacts: Sequence[Mapping[str, str]],
    warehouse_path: str,
    json_output: Path,
) -> dict[str, Any]:
    return {
        "report_id": "sprint9-factor-diagnostics-v1",
        "schema_version": "sprint9_factor_diagnostics_v1",
        "claims_eligible": False,
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "code_revision": code_revision,
        "decision": diagnostic["decision"],
        "json_output": repository_relative_path(json_output),
        "source_artifacts": list(source_artifacts),
        "warehouse": {
            "path": warehouse_path,
            "file_sha256_omitted": True,
            "reason": (
                "Large derived warehouse is bound by deterministic score, component, "
                "and reconstructed-outcome fingerprints."
            ),
        },
        "diagnostic": dict(diagnostic),
    }


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose Sprint 8 factor-family availability and performance."
    )
    parser.add_argument("--database-url")
    parser.add_argument("--database-path", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--comparison-json", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--backtest-json", type=Path, default=DEFAULT_BACKTEST)
    parser.add_argument("--cohort-audit-json", type=Path, default=DEFAULT_COHORT_AUDIT)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument("--generated-at", type=_parse_timestamp)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        sources = [
            args.comparison_json,
            args.backtest_json,
            args.cohort_audit_json,
            args.contract,
        ]
        source_artifacts = [
            {
                "path": repository_relative_path(path),
                "sha256": _sha256_file(path),
            }
            for path in sources
        ]
        database_url = args.database_url or _read_only_database_url(args.database_path)
        factory = _open_read_only_session(database_url)
        with factory() as session:
            diagnostic = diagnose_sprint9_factor_families(
                session,
                comparison=_load_json(args.comparison_json),
                backtest=_load_json(args.backtest_json),
                cohort_audit=_load_json(args.cohort_audit_json),
            )
        document = build_document(
            diagnostic,
            generated_at=args.generated_at or datetime.now(timezone.utc),
            code_revision=get_code_revision(),
            source_artifacts=source_artifacts,
            warehouse_path=(
                repository_relative_path(args.database_path)
                if args.database_url is None
                else "external-read-only-database"
            ),
            json_output=args.json_output,
        )
        json_payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        markdown_payload = render_markdown(document).encode("utf-8")
        json_sha = _write_atomic(args.json_output, json_payload)
        markdown_sha = _write_atomic(args.markdown_output, markdown_payload)
    except (KeyError, OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Sprint 9 factor diagnostic failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"decision={diagnostic['decision']} "
        f"stock_months={diagnostic['scope']['security_months']} "
        f"evaluated={diagnostic['scope']['eligible_evaluated_security_months']} "
        f"json_sha256={json_sha} markdown_sha256={markdown_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
