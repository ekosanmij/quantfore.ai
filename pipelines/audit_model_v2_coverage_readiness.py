"""Prove Model V2 coverage readiness without accessing return outcomes."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence

try:
    import _bootstrap  # noqa: F401
    from _common import DEFAULT_RAW_DIR, get_code_revision, repository_relative_path
    from build_model_v2_score_inputs import (
        DEFAULT_ACCOUNTING_BUNDLE,
        DEFAULT_DATABASE,
        build_score_inputs,
    )
    from build_model_v2_scores import build_scores
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import (  # type: ignore
        DEFAULT_RAW_DIR,
        get_code_revision,
        repository_relative_path,
    )
    from pipelines.build_model_v2_score_inputs import (  # type: ignore
        DEFAULT_ACCOUNTING_BUNDLE,
        DEFAULT_DATABASE,
        build_score_inputs,
    )
    from pipelines.build_model_v2_scores import build_scores  # type: ignore

from quantfore_research.validation.model_v2_coverage import (
    compare_clean_rebuilds,
    audit_model_v2_coverage,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CLASSIFICATION = Path(
    "experiments/model-v2-point-in-time-subtype-classification-v1.jsonl.gz"
)
DEFAULT_INPUT_LEDGER = Path("experiments/model-v2-branch-feature-inputs-v1.jsonl.gz")
DEFAULT_INPUT_MANIFEST = Path(
    "experiments/model-v2-branch-feature-inputs-v1.manifest.json"
)
DEFAULT_SCORE_LEDGER = Path("experiments/model-v2-branch-aware-scores-v1.jsonl.gz")
DEFAULT_SCORE_MANIFEST = Path(
    "experiments/model-v2-branch-aware-scores-v1.manifest.json"
)
DEFAULT_REPORT = Path("reports/data-audits/model-v2-coverage-readiness-v1.json")
DEFAULT_MARKDOWN = Path(
    "reports/reproducibility/model-v2-pre-shadow-readiness-v1.md"
)
DEFAULT_REBUILD_ROOT = Path("tmp/model-v2-clean-rebuild-v1")
IMPLEMENTATION_SOURCES = (
    Path("packages/research/quantfore_research/validation/model_v2_coverage.py"),
    Path("pipelines/audit_model_v2_coverage_readiness.py"),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_rows(path: Path) -> Iterator[Mapping[str, Any]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON row at {path}:{line_number}") from exc
            if not isinstance(row, Mapping):
                raise ValueError(f"JSON row is not an object at {path}:{line_number}")
            yield row


def _load_json(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON document is not an object: {path}")
    return value


def _clean_rebuild_root(path: Path) -> None:
    resolved = path.resolve()
    expected_parent = (REPOSITORY_ROOT / "tmp").resolve()
    if resolved.parent != expected_parent or not resolved.name.startswith(
        "model-v2-clean-rebuild-"
    ):
        raise ValueError("clean rebuild root must be a dedicated repository tmp directory")
    if resolved.exists():
        shutil.rmtree(resolved)
    resolved.mkdir(parents=True)


def _rebuild_fingerprint(root: Path) -> dict[str, str]:
    return {
        "feature_input_ledger": _sha256_file(root / "feature-inputs.jsonl.gz"),
        "feature_input_manifest": _sha256_file(root / "feature-inputs.manifest.json"),
        "score_ledger": _sha256_file(root / "scores.jsonl.gz"),
        "score_manifest": _sha256_file(root / "scores.manifest.json"),
    }


def _run_one_clean_rebuild(
    *,
    run_number: int,
    root: Path,
    accounting_bundle: Path,
    database: Path,
    classification: Path,
) -> dict[str, str]:
    _clean_rebuild_root(root)
    print(f"Clean rebuild {run_number}/2: preparing point-in-time inputs...", flush=True)
    build_score_inputs(
        accounting_bundle=accounting_bundle,
        database=database,
        classification_ledger=classification,
        output=root / "feature-inputs.jsonl.gz",
        manifest_path=root / "feature-inputs.manifest.json",
        work_database=root / "work.sqlite",
        keep_work_database=False,
    )
    print(f"Clean rebuild {run_number}/2: scoring branch cohorts...", flush=True)
    build_scores(
        input_path=root / "feature-inputs.jsonl.gz",
        output_path=root / "scores.jsonl.gz",
        manifest_path=root / "scores.manifest.json",
        minimum_branch_cross_section=20,
    )
    fingerprint = _rebuild_fingerprint(root)
    print(
        f"Clean rebuild {run_number}/2 complete: score_sha256="
        f"{fingerprint['score_ledger']}",
        flush=True,
    )
    return fingerprint


def run_two_clean_rebuilds(
    *,
    root: Path,
    accounting_bundle: Path,
    database: Path,
    classification: Path,
    canonical_input_ledger: Path,
    canonical_score_ledger: Path,
    keep_artifacts: bool,
) -> dict[str, Any]:
    first = _run_one_clean_rebuild(
        run_number=1,
        root=root,
        accounting_bundle=accounting_bundle,
        database=database,
        classification=classification,
    )
    second = _run_one_clean_rebuild(
        run_number=2,
        root=root,
        accounting_bundle=accounting_bundle,
        database=database,
        classification=classification,
    )
    evidence = compare_clean_rebuilds(
        first=first,
        second=second,
        canonical={
            "feature_input_ledger": _sha256_file(canonical_input_ledger),
            "score_ledger": _sha256_file(canonical_score_ledger),
        },
    )
    evidence.update(
        {
            "outcomes_accessed": False,
            "rebuild_root": repository_relative_path(root),
            "frozen_inputs": {
                "accounting_bundle_manifest_sha256": _sha256_file(
                    accounting_bundle / "manifest.json"
                ),
                "accounting_facts_sha256": _sha256_file(
                    accounting_bundle / "fundamentals.json"
                ),
                "warehouse_sha256": _sha256_file(database),
                "classification_ledger_sha256": _sha256_file(classification),
            },
        }
    )
    if not keep_artifacts and root.exists():
        shutil.rmtree(root)
    return evidence


def _percent(value: Any) -> str:
    return f"{float(value) * 100:.2f}%" if value is not None else "N/A"


def render_markdown(report: Mapping[str, Any]) -> str:
    criteria = report["criteria"]
    reconciliation = report["reconciliation"]
    coverage = report["coverage"]
    rebuild = report["reproducibility"]
    decision = report["decision"]
    lines = [
        "# Model V2 Pre-Shadow Readiness v1",
        "",
        "`claims_eligible=false`",
        "",
        f"- Decision: `{decision}`",
        "- Outcome access: `false`",
        "- Threshold changes after failure: `false`",
        "",
        "## Decision",
        "",
    ]
    if decision.startswith("PASS"):
        lines.append(
            "Model V2 passes the locked outcome-blind coverage and reproducibility gates."
        )
    else:
        lines.append(
            "Model V2 is **not ready for the executable pre-shadow lock**. The failure is "
            "a breadth and coverage result; no return metric was opened and no threshold "
            "was changed."
        )
    lines.extend(
        [
            "",
            "## Locked gate results",
            "",
            "| Gate | Result | Observed | Threshold |",
            "| --- | --- | ---: | ---: |",
        ]
    )
    gate_rows = (
        (
            "Two clean rebuilds match",
            criteria["two_clean_rebuilds_match_exactly"],
            str(criteria["two_clean_rebuilds_match_exactly"]["passed"]).lower(),
        ),
        (
            "Every row has a stable disposition",
            criteria["every_expected_row_has_stable_disposition"],
            _percent(criteria["every_expected_row_has_stable_disposition"]["observed"]),
        ),
        (
            "Known branch/subtype every month",
            criteria["known_branch_or_subtype_every_month"],
            _percent(criteria["known_branch_or_subtype_every_month"]["minimum_observed"]),
        ),
        (
            "Final-score coverage every month",
            criteria["final_score_coverage_every_month"],
            _percent(criteria["final_score_coverage_every_month"]["minimum_observed"]),
        ),
        (
            "Active-branch coverage every month",
            criteria["active_branch_coverage_every_month"],
            f"{criteria['active_branch_coverage_every_month']['failed_branch_months']} failed branch-months",
        ),
        (
            "20 eligible names per active branch",
            criteria["eligible_names_per_active_branch_every_month"],
            f"{criteria['eligible_names_per_active_branch_every_month']['failed_branch_months']} failed branch-months",
        ),
        (
            "Five represented branches every month",
            criteria["represented_active_branches_every_month"],
            str(criteria["represented_active_branches_every_month"]["minimum_observed"]),
        ),
        (
            "Five represented sectors every month",
            criteria["represented_sectors_every_month"],
            str(criteria["represented_sectors_every_month"]["minimum_observed"]),
        ),
        (
            "No cross-branch fallback",
            criteria["cross_branch_fallback_count"],
            str(criteria["cross_branch_fallback_count"]["observed"]),
        ),
        (
            "No return metrics used",
            criteria["return_metrics_used"],
            "false",
        ),
    )
    for label, gate, observed in gate_rows:
        threshold = gate["threshold"]
        threshold_text = _percent(threshold) if isinstance(threshold, float) else str(threshold).lower()
        lines.append(
            f"| {label} | **{'PASS' if gate['passed'] else 'FAIL'}** | {observed} | {threshold_text} |"
        )

    lines.extend(
        [
            "",
            "## Reconciliation",
            "",
            f"All `{reconciliation['expected_stock_months']:,}` expected stock-months reconcile: "
            f"`{reconciliation['scored_stock_months']:,}` scored and "
            f"`{reconciliation['excluded_stock_months']:,}` excluded with stable reasons. "
            f"Aggregate final-score coverage is `{_percent(coverage['aggregate_final_score_coverage'])}`.",
            "",
            "## Branch coverage",
            "",
            "| Branch | Expected | Scored | Aggregate | Minimum monthly | Minimum names |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in coverage["branches"]:
        lines.append(
            f"| `{row['sector_branch']}` | {row['expected']:,} | {row['scored']:,} | "
            f"{_percent(row['aggregate_coverage'])} | {_percent(row['minimum_monthly_coverage'])} | "
            f"{row['minimum_monthly_eligible_names'] if row['minimum_monthly_eligible_names'] is not None else 'N/A'} |"
        )

    lines.extend(
        [
            "",
            "## Sector coverage",
            "",
            "| Sector | Expected | Scored | Coverage | Months represented |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in coverage["sectors"]:
        lines.append(
            f"| {row['sector']} | {row['expected']:,} | {row['scored']:,} | "
            f"{_percent(row['aggregate_coverage'])} | {row['months_represented_by_score']}/{row['observed_months']} |"
        )

    lines.extend(
        [
            "",
            "## Monthly coverage",
            "",
            "| Month | Expected | Scored | Coverage | Branches | Sectors | Minimum branch coverage |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in coverage["monthly"]:
        lines.append(
            f"| {row['prediction_date']} | {row['expected']} | {row['scored']} | "
            f"{_percent(row['final_score_coverage'])} | "
            f"{row['represented_active_branch_count']} | {row['represented_sector_count']} | "
            f"{_percent(row['minimum_active_branch_coverage'])} |"
        )

    lines.extend(
        [
            "",
            "## Clean rebuild proof",
            "",
            f"Two fresh runs used the same frozen inputs. Exact rebuild match: "
            f"`{str(rebuild['all_rebuild_artifacts_matched']).lower()}`. Canonical ledger "
            f"reproduction: `{str(rebuild['canonical_ledgers_reproduced']).lower()}`.",
            "",
            "| Artifact | Rebuild 1 | Rebuild 2 | Match |",
            "| --- | --- | --- | --- |",
        ]
    )
    for name, row in rebuild["artifacts"].items():
        lines.append(
            f"| `{name}` | `{row['first_sha256']}` | `{row['second_sha256']}` | "
            f"**{'PASS' if row['matched'] else 'FAIL'}** |"
        )
    lines.extend(
        [
            "",
            "## Claims boundary",
            "",
            "This report is an outcome-blind engineering and coverage audit. It does not "
            "establish signal efficacy, portfolio value, investability, suitability, or "
            "performance. A failed gate is retained; Sprint 10.6 must not create an "
            "executable shadow lock for this implementation.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the outcome-blind Model V2 coverage and clean-rebuild gates."
    )
    parser.add_argument("--accounting-bundle", type=Path, default=DEFAULT_ACCOUNTING_BUNDLE)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--classification", type=Path, default=DEFAULT_CLASSIFICATION)
    parser.add_argument("--input-ledger", type=Path, default=DEFAULT_INPUT_LEDGER)
    parser.add_argument("--input-manifest", type=Path, default=DEFAULT_INPUT_MANIFEST)
    parser.add_argument("--score-ledger", type=Path, default=DEFAULT_SCORE_LEDGER)
    parser.add_argument("--score-manifest", type=Path, default=DEFAULT_SCORE_MANIFEST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--rebuild-root", type=Path, default=DEFAULT_REBUILD_ROOT)
    parser.add_argument("--keep-rebuild-artifacts", action="store_true")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        rebuild = run_two_clean_rebuilds(
            root=args.rebuild_root,
            accounting_bundle=args.accounting_bundle,
            database=args.database,
            classification=args.classification,
            canonical_input_ledger=args.input_ledger,
            canonical_score_ledger=args.score_ledger,
            keep_artifacts=args.keep_rebuild_artifacts,
        )
        report = audit_model_v2_coverage(
            classification_rows=_json_rows(args.classification),
            score_rows=_json_rows(args.score_ledger),
            score_manifest=_load_json(args.score_manifest),
            input_manifest=_load_json(args.input_manifest),
            rebuild_evidence=rebuild,
        )
        report["inputs"] = {
            "classification_ledger": {
                "path": repository_relative_path(args.classification),
                "sha256": _sha256_file(args.classification),
            },
            "feature_input_ledger": {
                "path": repository_relative_path(args.input_ledger),
                "sha256": _sha256_file(args.input_ledger),
            },
            "feature_input_manifest": {
                "path": repository_relative_path(args.input_manifest),
                "sha256": _sha256_file(args.input_manifest),
            },
            "score_ledger": {
                "path": repository_relative_path(args.score_ledger),
                "sha256": _sha256_file(args.score_ledger),
            },
            "score_manifest": {
                "path": repository_relative_path(args.score_manifest),
                "sha256": _sha256_file(args.score_manifest),
            },
        }
        report["implementation_sources"] = [
            {
                "path": path.as_posix(),
                "sha256": _sha256_file(REPOSITORY_ROOT / path),
            }
            for path in IMPLEMENTATION_SOURCES
        ]
        report["code_revision"] = get_code_revision()
        _write_json(args.report, report)
        _write_text(args.markdown, render_markdown(report))
    except (OSError, ValueError, AssertionError, json.JSONDecodeError) as exc:
        print(f"Model V2 coverage audit failed: {exc}", file=sys.stderr)
        return 1
    print(f"Coverage decision: {report['decision']}")
    print(f"JSON report: {repository_relative_path(args.report)}")
    print(f"Readiness report: {repository_relative_path(args.markdown)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
