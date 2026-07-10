"""Generate the Sprint 9.2 cohort funnel and stock-month explanations."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from datetime import date, datetime, timezone
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

from quantfore_research.validation.cohort_funnel import (
    Sprint9CohortFunnelAudit,
    audit_sprint9_cohort_funnel,
)


DEFAULT_DATABASE = Path(
    "data/raw/free-point-in-time/sprint8-prelock-v9/research.db"
)
DEFAULT_BACKTEST = Path("reports/backtests/pit_multifactor_baseline_v1.json")
DEFAULT_COMPARISON = Path("reports/comparisons/price-vs-multifactor-v1.json")
DEFAULT_CLOSURE = Path("reports/reproducibility/sprint8-closure-v1.json")
DEFAULT_HOLDOUT_LOCK = Path("experiments/multifactor-holdout-lock-v1.json")
DEFAULT_CONTRACT = Path("docs/research/multifactor-baseline-v1.md")
DEFAULT_FUNDAMENTAL_AUDIT = Path("reports/data-audits/pit-fundamentals-v1.json")
DEFAULT_EQUITY_MANIFEST = Path(
    "data/raw/free-point-in-time/composite-equity-bundle-v1/manifest.json"
)
DEFAULT_FUNDAMENTAL_MANIFEST = Path(
    "data/raw/free-point-in-time/sec-fundamentals-bundle-v1/manifest.json"
)
DEFAULT_JSON_OUTPUT = Path("reports/data-audits/sprint9-cohort-funnel-v1.json")
DEFAULT_MARKDOWN_OUTPUT = Path("reports/data-audits/sprint9-cohort-funnel-v1.md")
DEFAULT_EXPLANATIONS_OUTPUT = Path(
    "reports/data-audits/sprint9-cohort-funnel-explanations-v1.jsonl.gz"
)


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

    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def _write_atomic(path: Path, payload: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(path)
    return hashlib.sha256(payload).hexdigest()


def _write_explanations(
    path: Path, rows: Sequence[Mapping[str, Any]]
) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as raw_handle:
        with gzip.GzipFile(
            filename="", mode="wb", fileobj=raw_handle, mtime=0
        ) as handle:
            for row in rows:
                payload = (
                    json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
                ).encode("utf-8")
                handle.write(payload)
    temporary.replace(path)
    return _sha256_file(path), len(rows)


def _percent(value: Any) -> str:
    if value is None:
        return "—"
    return f"{float(value) * 100:.4f}%"


def _count(value: Any) -> str:
    return f"{int(value):,}"


def render_markdown(document: Mapping[str, Any]) -> str:
    audit = document["audit"]
    totals = audit["funnel_totals"]
    diagnoses = audit["diagnoses"]
    breadth = audit["breadth_assessment"]
    unique = audit["unique_security_counts"]
    cohort_count = int(audit["scope"]["monthly_cohorts"])
    family_pass = int(totals["minimum_family_pass"])
    family_and_coverage_pass = int(totals["family_and_coverage_pass"])
    eligible_scores = int(totals["eligible_final_scores"])
    monthly_breadth = diagnoses["monthly_breadth"]
    lines = [
        "# Sprint 9.2 Coverage and Cohort Audit v1",
        "",
        "`claims_eligible=false`",
        "",
        f"- Decision: `{document['decision']}`",
        f"- Evidence generated: `{document['generated_at']}`",
        f"- Code revision: `{document['code_revision']}`",
        f"- Universe: `{audit['universe_id']}`",
        f"- Window: `{audit['scope']['start']}` through `{audit['scope']['end']}`",
        f"- Monthly cohorts: `{audit['scope']['monthly_cohorts']}`",
        f"- Authoritative warehouse: `{document['warehouse']['path']}`",
        "- Stock-month explanation ledger: "
        f"[`{document['explanation_ledger']['path']}`]({Path(document['explanation_ledger']['path']).name})",
        "",
        "## Decision",
        "",
        "> **Sprint 8 evidence is not broad enough to trust as an S&P 500 "
        "multi-factor result.**",
        "",
        f"The point-in-time universe contains `{_count(totals['universe_members'])}` "
        f"security-months from `{_count(unique['universe_members'])}` unique securities "
        f"across {cohort_count} cohorts. All of them have an exact positive "
        "price on the prediction date and all have complete 19-component raw and "
        f"normalized ledgers. Only `{_count(family_pass)}` security-months reach four "
        f"available factor families; `{_count(family_pass - family_and_coverage_pass)}` "
        "of those then fail the 70% component-coverage rule. The remaining "
        f"`{_count(eligible_scores)}` become predictions, mature outcomes, and final evaluation "
        "records without further loss.",
        "",
        f"Full-window final-score coverage is `{_percent(totals['final_score_coverage'])}` "
        f"(`{_count(eligible_scores)} / {_count(totals['universe_members'])}`), with "
        f"`{monthly_breadth['months_with_zero_eligible_scores']} / {cohort_count}` months "
        "producing no score and no month reaching the frozen 90% requirement. All "
        f"{_count(eligible_scores)} evaluated observations are "
        "labelled Financials.",
        "",
        "## Reconciled processing funnel",
        "",
        "These stages are nested and reconcile exactly:",
        "",
        "| Stage | Security-months | Drop from prior stage | Retained from universe |",
        "| --- | ---: | ---: | ---: |",
    ]
    funnel = [
        ("Point-in-time universe members", totals["universe_members"]),
        ("Complete 19-component raw feature sets", totals["complete_raw_feature_sets"]),
        (
            "Entered monthly scoring with 19 normalized components",
            totals["complete_normalized_feature_sets"],
        ),
        ("At least four available factor families", totals["minimum_family_pass"]),
        ("At least 70% coverage after family pass", totals["family_and_coverage_pass"]),
        ("Eligible final scores", totals["eligible_final_scores"]),
        ("Security-months with prediction records", totals["prediction_security_months"]),
        (
            "Security-months with mature 126-session outcomes",
            totals["mature_outcome_security_months_126d"],
        ),
        ("Final 126-session evaluation observations", totals["evaluated_observations_126d"]),
    ]
    prior = None
    universe = int(totals["universe_members"])
    for label, raw_count in funnel:
        value = int(raw_count)
        drop = 0 if prior is None else prior - value
        lines.append(
            f"| {label} | {_count(value)} | {_count(drop)} | "
            f"{_percent(value / universe if universe else None)} |"
        )
        prior = value

    lines.extend(
        [
            "",
            f"The `{_count(totals['prediction_records'])}` prediction records and "
            f"`{_count(totals['mature_outcome_records'])}` mature outcome records are "
            f"four horizons for the same {_count(eligible_scores)} security-months. "
            "At the primary 126-session horizon there are exactly "
            f"{_count(eligible_scores)} predictions, {_count(totals['mature_outcome_security_months_126d'])} "
            f"mature outcomes, and {_count(totals['evaluated_observations_126d'])} "
            "evaluated observations.",
            "",
            "## Data availability diagnostics",
            "",
            "These checkpoints overlap and therefore are not subtracted as a nested "
            "funnel:",
            "",
            "| Checkpoint | Security-months | Share of universe | Meaning |",
            "| --- | ---: | ---: | --- |",
            f"| Exact positive close and adjusted close on prediction date | {_count(totals['exact_prediction_date_prices'])} | {_percent(totals['exact_prediction_date_prices'] / universe)} | Raw price presence is not the cause of the 60-row result. |",
            f"| At least one model-available fundamental fact | {_count(totals['model_available_fundamental_facts'])} | {_percent(totals['model_available_fundamental_facts'] / universe)} | A raw fact exists before the prediction timestamp. |",
            f"| At least one usable price-derived component | {_count(totals['usable_price_features'])} | {_percent(totals['usable_price_features'] / universe)} | `{_count(universe - totals['usable_price_features'])}` rows have prices but insufficient usable lookback features. |",
            f"| At least one usable fundamental-derived component | {_count(totals['usable_fundamental_features'])} | {_percent(totals['usable_fundamental_features'] / universe)} | Raw facts often do not satisfy TTM, growth, unit, or denominator requirements. |",
            f"| At least one usable component of both types | {_count(totals['both_price_and_fundamental_features'])} | {_percent(totals['both_price_and_fundamental_features'] / universe)} | Necessary but far from sufficient for score eligibility. |",
            "",
            "### Unique-security cross-check",
            "",
            "| Checkpoint | Unique securities |",
            "| --- | ---: |",
            f"| Appeared in a point-in-time cohort | {_count(unique['universe_members'])} |",
            f"| Exact prediction-date price | {_count(unique['exact_prediction_date_prices'])} |",
            f"| Model-available fundamental fact | {_count(unique['model_available_fundamental_facts'])} |",
            f"| Usable fundamental feature | {_count(unique['usable_fundamental_features'])} |",
            f"| Passed four-family minimum | {_count(unique['minimum_family_pass'])} |",
            f"| Eligible final score / final evaluation | {_count(unique['eligible_final_scores'])} |",
            "",
            "## Exclusive disposition of every stock-month",
            "",
            f"Every one of the {_count(universe)} expected rows has exactly one primary disposition:",
            "",
            "| Primary reason code | Rows | Meaning |",
            "| --- | ---: | --- |",
        ]
    )
    reason_meanings = {
        "BELOW_MINIMUM_AVAILABLE_FAMILIES": "Fewer than four factor families are available; this rule is checked first.",
        "BELOW_MINIMUM_COMPONENT_COVERAGE": "Four families are available, but fewer than 70% of applicable components are valid.",
        "INCLUDED_IN_FINAL_EVALUATION": "The row passes both score gates and has all predictions and mature outcomes.",
    }
    for code, value in audit["primary_reason_counts"].items():
        lines.append(
            f"| `{code}` | {_count(value)} | {reason_meanings.get(code, 'See explanation ledger.')} |"
        )

    lines.extend(
        [
            "",
            "Each JSONL explanation also records price/fundamental diagnostics, "
            "family availability, component coverage, every missing component and "
            "its stored reason, prediction horizons, and outcome status. There are "
            "no unclassified stock-months.",
            "",
            "## Component evidence behind the exclusions",
            "",
            "| Stored component reason | Components |",
            "| --- | ---: |",
        ]
    )
    for code, value in sorted(
        audit["component_reason_counts"].items(),
        key=lambda item: (-int(item[1]), item[0]),
    ):
        lines.append(f"| `{code}` | {_count(value)} |")

    lines.extend(
        [
            "",
            "The dominant failure is `INSUFFICIENT_HISTORY`, not missing prediction-date "
            "prices. `SECTOR_UNKNOWN` affects specialized accounting features, while "
            "`NOT_APPLICABLE` is the intentional sector mask and is excluded from the "
            "component-coverage denominator.",
            "",
            "## Family availability",
            "",
            "| Available-family pattern | Security-months |",
            "| --- | ---: |",
        ]
    )
    for pattern, value in sorted(
        audit["family_availability_patterns"].items(),
        key=lambda item: (-int(item[1]), item[0]),
    ):
        lines.append(f"| `{pattern}` | {_count(value)} |")

    financial = diagnoses["financials_only"]
    lines.extend(
        [
            "",
            "## Why the final result only contains Financials",
            "",
            f"All `{financial['financials_eligible_observations']}` eligible rows are "
            "labelled `Financials`; every other sector has zero eligible scores. The "
            "financial-sector applicability mask removes nine industrial-accounting "
            "components, leaving ten applicable components. Each eligible row has "
            "value, growth, momentum, and risk available, while quality is entirely "
            "`NOT_APPLICABLE`.",
            "",
            f"The {len(audit['eligible_security_summary'])} eligible securities are:",
            "",
            "| Ticker | Classification | Industry code | Eligible months | First | Last |",
            "| --- | --- | --- | ---: | --- | --- |",
        ]
    )
    for row in audit["eligible_security_summary"]:
        lines.append(
            f"| `{row['ticker']}` | `{row['classification_system']}` | "
            f"`{row['industry']}` | "
            f"{_count(row['eligible_months'])} | `{row['first_eligible_date']}` | "
            f"`{row['last_eligible_date']}` |"
        )
    lines.extend(
        [
            "",
            "Four names (`AMT`, `EXR`, `REG`, and `VNO`) carry SIC industry code "
            "`6798`; `HIG` carries `6331`. Under the stored `SEC_SIC_TO_GICS_V1` "
            "classification they are labelled Financials, so the financial mask—not "
            "the contract's separate GICS REIT mask—is applied to the SIC 6798 names. "
            "This classification/treatment issue belongs in Sprint 9.6.",
            "",
            "### Sector coverage",
            "",
            "| Sector | Universe stock-months | Eligible scores | Coverage |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for row in sorted(
        audit["sector_summary"],
        key=lambda item: (-int(item["eligible_final_scores"]), item["sector"]),
    ):
        lines.append(
            f"| {row['sector']} | {_count(row['universe_stock_months'])} | "
            f"{_count(row['eligible_final_scores'])} | "
            f"{_percent(row['final_score_coverage'])} |"
        )

    empty_bottom = diagnoses["empty_bottom_quintile"]
    holdout = diagnoses["holdout_breadth"]
    lines.extend(
        [
            "",
            "## Why quintile 1 is empty",
            "",
            f"Only `{monthly_breadth['months_with_any_eligible_score']}` of {cohort_count} months "
            "have any eligible score. Nonempty cohorts contain one to "
            f"`{monthly_breadth['maximum_eligible_scores']}` securities; none contains "
            "five. The evaluator assigns `ceil(ascending_average_rank * 5 / "
            "cohort_size)`. With cohort sizes 1, 2, 3, and 4, the lowest-ranked "
            "security falls into quintile 5, 3, 2, and 2 respectively. Quintile 1 is "
            "therefore mathematically impossible in every evaluated month.",
            "",
            f"Reason code: `{empty_bottom['reason_code']}`.",
            "",
            "## Holdout-specific breadth",
            "",
            f"The frozen holdout contains `{holdout['monthly_cohorts']}` cohorts and "
            f"`{_count(holdout['universe_stock_months'])}` expected stock-months. Only "
            f"`{_count(holdout['minimum_family_pass'])}` reach four families and "
            f"`{_count(holdout['eligible_final_scores'])}` receive an eligible final score, "
            f"for `{_percent(holdout['final_score_coverage'])}` coverage. Those four "
            "scores occur in January through April 2022; the other holdout months have "
            "none. This is why the locked holdout gates cannot be evaluated.",
            "",
            "## Monthly cohort table",
            "",
            "| Date | Universe | Exact price | Fundamental fact | Usable fundamental | Four families | Eligible score | Predictions | Evaluated 126d | Score coverage |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in audit["monthly_cohorts"]:
        counts = row["counts"]
        lines.append(
            f"| `{row['prediction_date']}` | {_count(counts.get('universe_members', 0))} | "
            f"{_count(counts.get('exact_prediction_date_prices', 0))} | "
            f"{_count(counts.get('model_available_fundamental_facts', 0))} | "
            f"{_count(counts.get('usable_fundamental_features', 0))} | "
            f"{_count(counts.get('minimum_family_pass', 0))} | "
            f"{_count(counts.get('eligible_final_scores', 0))} | "
            f"{_count(counts.get('prediction_security_months', 0))} | "
            f"{_count(counts.get('evaluated_observations_126d', 0))} | "
            f"{_percent(row['final_score_coverage'])} |"
        )

    lines.extend(
        [
            "",
            "## Explain any stock/month",
            "",
            "The explanation ledger contains one row for every expected stock/month. "
            "A targeted lookup can be reproduced from the authoritative warehouse:",
            "",
            "```bash",
            "python pipelines/audit_sprint9_cohort_funnel.py \\",
            "  --explain REG --asof-date 2022-05-31 --explain-only",
            "```",
            "",
            "The returned row states the exclusive primary disposition and the exact "
            "component-level reasons. Ticker or permanent security ID may be used.",
            "",
            "## Integrity checks",
            "",
            "| Check | Passed |",
            "| --- | --- |",
        ]
    )
    for name, value in audit["integrity_checks"].items():
        lines.append(f"| {name.replace('_', ' ').title()} | `{str(value).lower()}` |")

    lines.extend(
        [
            "",
            "## Claims boundary",
            "",
            breadth["conclusion"],
            "",
            "This is an internal cohort and data-coverage audit. It does not establish "
            "predictive value, investability, outperformance, suitability, or investment "
            "advice. `claims_eligible=false` remains mandatory.",
            "",
            "## Evidence provenance",
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
            "The 21 GB pre-lock warehouse is intentionally bound by the deterministic "
            "normalization-run and security-month fingerprints recorded in the JSON, "
            "rather than duplicated into the report repository.",
            "",
        ]
    )
    return "\n".join(lines)


def build_document(
    audit: Sprint9CohortFunnelAudit,
    *,
    generated_at: datetime,
    code_revision: Optional[str],
    source_artifacts: Sequence[Mapping[str, str]],
    explanation_path: Path,
    explanation_sha256: str,
    explanation_rows: int,
    warehouse_path: str,
) -> dict[str, Any]:
    return {
        "audit_id": "sprint9-cohort-funnel-v1",
        "schema_version": "sprint9_cohort_funnel_v1",
        "claims_eligible": False,
        "generated_at": generated_at.astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "code_revision": code_revision,
        "decision": audit.decision,
        "source_artifacts": list(source_artifacts),
        "warehouse": {
            "path": warehouse_path,
            "binding": audit.audit["warehouse_fingerprint"],
            "file_sha256_omitted": True,
            "reason": "Large derived warehouse is bound by deterministic run and security-month fingerprints.",
        },
        "explanation_ledger": {
            "format": "gzip_json_lines",
            "path": repository_relative_path(explanation_path),
            "row_count": explanation_rows,
            "sha256": explanation_sha256,
            "uncompressed_content_sha256": audit.audit["warehouse_fingerprint"][
                "security_month_explanation_sha256"
            ],
            "lookup_key": ["security_id_or_ticker", "prediction_date"],
        },
        "audit": audit.to_dict(),
    }


def _find_explanations(
    rows: Sequence[Mapping[str, Any]],
    identifier: str,
    asof_date: date,
) -> list[Mapping[str, Any]]:
    normalized = identifier.strip().upper()
    return [
        row
        for row in rows
        if str(row["prediction_date"]) == asof_date.isoformat()
        and (
            str(row["security_id"]).upper() == normalized
            or str(row["ticker"]).upper() == normalized
        )
    ]


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit the Sprint 8 security-month cohort funnel."
    )
    parser.add_argument(
        "--database-url",
        help="Read-only authoritative Sprint 8 warehouse URL.",
    )
    parser.add_argument("--database-path", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--universe-id", default="sp500-pit-v1")
    parser.add_argument("--backtest-json", type=Path, default=DEFAULT_BACKTEST)
    parser.add_argument("--comparison-json", type=Path, default=DEFAULT_COMPARISON)
    parser.add_argument("--closure-json", type=Path, default=DEFAULT_CLOSURE)
    parser.add_argument("--holdout-lock-json", type=Path, default=DEFAULT_HOLDOUT_LOCK)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument(
        "--fundamental-audit", type=Path, default=DEFAULT_FUNDAMENTAL_AUDIT
    )
    parser.add_argument(
        "--equity-manifest", type=Path, default=DEFAULT_EQUITY_MANIFEST
    )
    parser.add_argument(
        "--fundamental-manifest", type=Path, default=DEFAULT_FUNDAMENTAL_MANIFEST
    )
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument(
        "--explanations-output", type=Path, default=DEFAULT_EXPLANATIONS_OUTPUT
    )
    parser.add_argument("--generated-at", type=_parse_timestamp)
    parser.add_argument("--explain", help="Ticker or permanent security ID to explain.")
    parser.add_argument("--asof-date", type=date.fromisoformat)
    parser.add_argument("--explain-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if bool(args.explain) != bool(args.asof_date):
        print("--explain and --asof-date must be provided together", file=sys.stderr)
        return 2
    if args.explain_only and not args.explain:
        print("--explain-only requires --explain and --asof-date", file=sys.stderr)
        return 2
    try:
        sources = [
            args.backtest_json,
            args.comparison_json,
            args.closure_json,
            args.holdout_lock_json,
            args.contract,
            args.fundamental_audit,
            args.equity_manifest,
            args.fundamental_manifest,
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
            audit = audit_sprint9_cohort_funnel(
                session,
                backtest_document=_load_json(args.backtest_json),
                comparison_document=_load_json(args.comparison_json),
                closure_document=_load_json(args.closure_json),
                holdout_lock_document=_load_json(args.holdout_lock_json),
                universe_id=args.universe_id,
            )

        if args.explain:
            matches = _find_explanations(
                audit.explanations, args.explain, args.asof_date
            )
            if not matches:
                print(
                    f"no cohort row found for {args.explain} on {args.asof_date}",
                    file=sys.stderr,
                )
                return 2
            print(json.dumps(matches[0], indent=2, sort_keys=True))
            if args.explain_only:
                return 0

        explanation_sha, explanation_rows = _write_explanations(
            args.explanations_output, audit.explanations
        )
        generated_at = args.generated_at or datetime.now(timezone.utc)
        document = build_document(
            audit,
            generated_at=generated_at,
            code_revision=get_code_revision(),
            source_artifacts=source_artifacts,
            explanation_path=args.explanations_output,
            explanation_sha256=explanation_sha,
            explanation_rows=explanation_rows,
            warehouse_path=(
                repository_relative_path(args.database_path)
                if args.database_url is None
                else "external-read-only-database"
            ),
        )
        json_payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        )
        markdown_payload = render_markdown(document).encode("utf-8")
        json_sha = _write_atomic(args.json_output, json_payload)
        markdown_sha = _write_atomic(args.markdown_output, markdown_payload)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"Sprint 9 cohort audit failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"decision={audit.decision} stock_months={explanation_rows} "
        f"json_sha256={json_sha} markdown_sha256={markdown_sha} "
        f"explanations_sha256={explanation_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
