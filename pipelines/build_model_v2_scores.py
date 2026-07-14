"""Build deterministic, branch-aware Model V2 scores from point-in-time inputs."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator, Mapping, Optional, Sequence, TextIO

try:
    import _bootstrap  # noqa: F401
    from _common import get_code_revision, repository_relative_path
except ModuleNotFoundError:
    from pipelines import _bootstrap  # type: ignore  # noqa: F401
    from pipelines._common import get_code_revision, repository_relative_path  # type: ignore

from quantfore_research.features.model_v2 import (
    APPLICABLE,
    MISSING,
    MODEL_V2_FEATURE_VERSION,
    MODEL_V2_FORMULA_VERSION,
    UNIVERSAL_DEFINITIONS,
    RawModelV2Feature,
    ScalarValue,
    branch_schema_document,
    build_model_v2_feature_batch,
)
from quantfore_research.scoring.model_v2 import (
    FAMILY_WEIGHTS,
    MINIMUM_BRANCH_CROSS_SECTION,
    MODEL_V2_MODEL_VERSION,
    MODEL_V2_NORMALIZATION_VERSION,
    ModelV2CohortScore,
    SecurityModelV2Score,
    normalize_model_v2_cohort,
)


DEFAULT_OUTPUT = Path("experiments/model-v2-branch-aware-scores-v1.jsonl.gz")
DEFAULT_MANIFEST = Path("experiments/model-v2-branch-aware-scores-v1.manifest.json")
FORBIDDEN_OUTCOME_KEYS = frozenset(
    {
        "return",
        "returns",
        "forward_return",
        "forward_returns",
        "outcome",
        "outcomes",
        "rank_ic",
        "alpha",
        "excess_return",
        "benchmark_return",
        "future_price",
    }
)
UNIVERSAL_BY_NAME = {row.name: row for row in UNIVERSAL_DEFINITIONS}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATION_SOURCES = (
    Path("packages/research/quantfore_research/features/model_v2.py"),
    Path("packages/research/quantfore_research/scoring/model_v2.py"),
    Path("pipelines/build_model_v2_scores.py"),
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _implementation_sources() -> list[dict[str, str]]:
    return [
        {"path": path.as_posix(), "sha256": _sha256_file(REPOSITORY_ROOT / path)}
        for path in IMPLEMENTATION_SOURCES
    ]


def _decimal(value: Any, *, field: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a decimal") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field} must be finite")
    return parsed


def _reject_outcomes(value: Any, *, path: str = "row") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower()
            if normalized in FORBIDDEN_OUTCOME_KEYS:
                raise ValueError(f"outcome field is prohibited in Model V2 scoring input: {path}.{key}")
            _reject_outcomes(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_outcomes(nested, path=f"{path}[{index}]")


def _scalar_inputs(value: Any) -> dict[str, ScalarValue]:
    if not isinstance(value, Mapping):
        raise ValueError("accounting_inputs must be an object")
    result = {}
    for name, raw in value.items():
        if not isinstance(raw, Mapping):
            raise ValueError(f"accounting input must be an object: {name}")
        lineage = raw.get("lineage_ids", ())
        if not isinstance(lineage, list) or not all(isinstance(item, str) and item for item in lineage):
            raise ValueError(f"accounting input lineage_ids must be non-empty strings: {name}")
        unit = raw.get("unit")
        if not isinstance(unit, str) or not unit.strip():
            raise ValueError(f"accounting input unit is required: {name}")
        result[str(name)] = ScalarValue(
            value=_decimal(raw.get("value"), field=f"accounting_inputs.{name}.value"),
            unit=unit,
            lineage_ids=tuple(sorted(set(lineage))),
        )
    return result


def _universal_features(value: Any) -> dict[str, RawModelV2Feature]:
    if not isinstance(value, Mapping):
        raise ValueError("universal_features must be an object")
    if set(value) != set(UNIVERSAL_BY_NAME):
        missing = sorted(set(UNIVERSAL_BY_NAME) - set(value))
        extra = sorted(set(value) - set(UNIVERSAL_BY_NAME))
        raise ValueError(f"universal feature set mismatch; missing={missing} extra={extra}")
    result = {}
    for name, definition in UNIVERSAL_BY_NAME.items():
        raw = value[name]
        if not isinstance(raw, Mapping):
            raise ValueError(f"universal feature must be an object: {name}")
        lineage = raw.get("lineage_ids", ())
        if not isinstance(lineage, list) or not all(isinstance(item, str) and item for item in lineage):
            raise ValueError(f"universal lineage_ids must be non-empty strings: {name}")
        raw_value = raw.get("value")
        reason_code = raw.get("reason_code")
        if raw_value is None:
            if not isinstance(reason_code, str) or not reason_code.strip():
                raise ValueError(f"missing universal feature requires reason_code: {name}")
            result[name] = RawModelV2Feature(
                definition=definition,
                value=None,
                status=MISSING,
                reason_code=reason_code,
                reason_detail=str(raw.get("reason_detail") or reason_code),
                lineage_ids=tuple(sorted(set(lineage))),
            )
        else:
            if reason_code is not None:
                raise ValueError(f"available universal feature cannot have reason_code: {name}")
            result[name] = RawModelV2Feature(
                definition=definition,
                value=_decimal(raw_value, field=f"universal_features.{name}.value"),
                status=APPLICABLE,
                reason_code=None,
                reason_detail=None,
                lineage_ids=tuple(sorted(set(lineage))),
            )
    return result


def input_row_to_batch(row: Mapping[str, Any]):
    _reject_outcomes(row)
    security_id = row.get("security_id")
    if not isinstance(security_id, str) or not security_id.strip():
        raise ValueError("security_id is required")
    try:
        prediction_date = date.fromisoformat(str(row.get("prediction_date")))
    except ValueError as exc:
        raise ValueError("prediction_date must be ISO YYYY-MM-DD") from exc
    branch = row.get("sector_branch")
    if not isinstance(branch, str) or not branch.strip():
        raise ValueError("sector_branch is required")
    classification_eligible = row.get("classification_eligible")
    if not isinstance(classification_eligible, bool):
        raise ValueError("classification_eligible must be boolean")
    reasons = row.get("classification_reason_codes", [])
    if not isinstance(reasons, list) or not all(isinstance(item, str) and item for item in reasons):
        raise ValueError("classification_reason_codes must be a list of non-empty strings")
    classification_id = row.get("classification_id")
    if classification_id is not None and not isinstance(classification_id, str):
        raise ValueError("classification_id must be a string or null")
    if not classification_eligible:
        return build_model_v2_feature_batch(
            security_id=security_id,
            prediction_date=prediction_date,
            sector_branch=branch,
            classification_eligible=False,
            classification_reason_codes=reasons,
            classification_id=classification_id,
            accounting_inputs={},
            universal_features={},
        )
    return build_model_v2_feature_batch(
        security_id=security_id,
        prediction_date=prediction_date,
        sector_branch=branch,
        classification_eligible=True,
        classification_reason_codes=reasons,
        classification_id=classification_id,
        accounting_inputs=_scalar_inputs(row.get("accounting_inputs", {})),
        universal_features=_universal_features(row.get("universal_features", {})),
    )


def _open_text(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8")
    return path.open("r", encoding="utf-8")


def _input_rows(path: Path) -> Iterator[Mapping[str, Any]]:
    with _open_text(path) as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON at {path}:{line_number}") from exc
            if not isinstance(row, Mapping):
                raise ValueError(f"input row must be an object at {path}:{line_number}")
            yield row


def _cohorts(path: Path) -> Iterator[tuple[object, ...]]:
    current_date = None
    current = []
    prior_key = None
    for raw in _input_rows(path):
        batch = input_row_to_batch(raw)
        key = (batch.prediction_date.isoformat(), batch.security_id)
        if prior_key is not None and key <= prior_key:
            raise ValueError(
                "Model V2 scoring input must be strictly sorted by prediction_date, security_id"
            )
        prior_key = key
        if current_date is not None and batch.prediction_date != current_date:
            yield tuple(current)
            current = []
        current_date = batch.prediction_date
        current.append(batch)
    if current:
        yield tuple(current)


def _text(value: Optional[Decimal]) -> Optional[str]:
    return str(value) if value is not None else None


def _score_document(score: SecurityModelV2Score) -> dict[str, Any]:
    return {
        "security_id": score.security_id,
        "prediction_date": score.prediction_date.isoformat(),
        "sector_branch": score.sector_branch,
        "classification_id": score.classification_id,
        "eligible": score.eligible,
        "exclusion_reason_codes": list(score.exclusion_reason_codes),
        "final_score": _text(score.final_score),
        "composite_z": _text(score.composite_z),
        "family_z": {name: _text(value) for name, value in score.family_z.items()},
        "family_scores": {
            name: _text(value) for name, value in score.family_scores.items()
        },
        "family_available": dict(score.family_available),
        "family_valid_component_counts": dict(score.family_valid_component_counts),
        "family_required_component_counts": dict(score.family_required_component_counts),
        "family_minimum_valid_component_counts": dict(
            score.family_minimum_valid_component_counts
        ),
        "family_weights": {name: str(value) for name, value in score.family_weights.items()},
        "required_component_count": score.required_component_count,
        "valid_component_count": score.valid_component_count,
        "component_coverage": str(score.component_coverage),
        "components": [
            {
                "feature_name": row.feature_name,
                "family": row.family,
                "raw_value": _text(row.raw_value),
                "winsorized_value": _text(row.winsorized_value),
                "standardized_value": _text(row.standardized_value),
                "directed_value": _text(row.directed_value),
                "input_status": row.input_status,
                "input_reason_code": row.input_reason_code,
                "input_reason_detail": row.input_reason_detail,
                "normalization_reason_code": row.normalization_reason_code,
                "normalization_scope": row.normalization_scope,
                "normalization_group": row.normalization_group,
                "group_count": row.group_count,
                "group_mean": _text(row.group_mean),
                "group_std": _text(row.group_std),
                "winsor_lower": _text(row.winsor_lower),
                "winsor_upper": _text(row.winsor_upper),
                "lineage_ids": list(row.lineage_ids),
            }
            for row in score.components
        ],
    }


class _DeterministicGzipWriter:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.temporary = path.with_suffix(path.suffix + ".tmp")
        self.raw = None
        self.gzip_handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.raw = self.temporary.open("wb")
        self.gzip_handle = gzip.GzipFile(
            filename="", mode="wb", fileobj=self.raw, mtime=0
        )
        return self

    def write(self, document: Mapping[str, Any]) -> None:
        assert self.gzip_handle is not None
        self.gzip_handle.write(
            (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(
                "utf-8"
            )
        )

    def __exit__(self, exc_type, exc, traceback) -> None:
        assert self.gzip_handle is not None and self.raw is not None
        self.gzip_handle.close()
        self.raw.close()
        if exc_type is None:
            self.temporary.replace(self.path)
        elif self.temporary.exists():
            self.temporary.unlink()


def build_scores(
    *,
    input_path: Path,
    output_path: Path,
    manifest_path: Path,
    minimum_branch_cross_section: int,
) -> dict[str, Any]:
    schema = branch_schema_document()
    schema_body = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    row_count = 0
    eligible_count = 0
    cohort_count = 0
    branch_counts: Counter[str] = Counter()
    branch_eligible: Counter[str] = Counter()
    exclusion_reasons: Counter[str] = Counter()
    all_family_violations = 0
    cross_branch_fallback_count = 0
    with _DeterministicGzipWriter(output_path) as writer:
        for batches in _cohorts(input_path):
            result: ModelV2CohortScore = normalize_model_v2_cohort(
                batches,
                minimum_branch_cross_section=minimum_branch_cross_section,
            )
            cohort_count += 1
            for score in result.scores:
                writer.write(_score_document(score))
                row_count += 1
                branch_counts[score.sector_branch] += 1
                exclusion_reasons.update(score.exclusion_reason_codes)
                if score.eligible:
                    eligible_count += 1
                    branch_eligible[score.sector_branch] += 1
                    if not all(score.family_available.values()):
                        all_family_violations += 1
                cross_branch_fallback_count += sum(
                    row.normalization_scope == "BRANCH"
                    and row.normalization_group != score.sector_branch
                    for row in score.components
                )
    if not row_count:
        raise ValueError("Model V2 scoring input is empty")
    manifest = {
        "claims_eligible": False,
        "outcomes_accessed": False,
        "model_version": MODEL_V2_MODEL_VERSION,
        "feature_version": MODEL_V2_FEATURE_VERSION,
        "formula_version": MODEL_V2_FORMULA_VERSION,
        "normalization_version": MODEL_V2_NORMALIZATION_VERSION,
        "family_weights": {name: str(value) for name, value in FAMILY_WEIGHTS.items()},
        "family_weight_renormalization": False,
        "all_five_families_required": True,
        "cross_branch_fallback": False,
        "minimum_branch_cross_section": minimum_branch_cross_section,
        "input": {
            "path": repository_relative_path(input_path),
            "sha256": _sha256_file(input_path),
        },
        "output": {
            "path": repository_relative_path(output_path),
            "sha256": _sha256_file(output_path),
        },
        "branch_schema_sha256": hashlib.sha256(schema_body).hexdigest(),
        "branch_schema": schema,
        "implementation_sources": _implementation_sources(),
        "counts": {
            "cohorts": cohort_count,
            "rows": row_count,
            "eligible_rows": eligible_count,
            "excluded_rows": row_count - eligible_count,
            "eligible_rows_missing_any_family": all_family_violations,
            "cross_branch_fallback_count": cross_branch_fallback_count,
            "by_branch": dict(sorted(branch_counts.items())),
            "eligible_by_branch": dict(sorted(branch_eligible.items())),
            "exclusion_reason_codes": dict(sorted(exclusion_reasons.items())),
        },
        "code_revision": get_code_revision(),
    }
    if all_family_violations or cross_branch_fallback_count:
        raise AssertionError("Model V2 scoring invariants failed")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return manifest


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build outcome-blind Model V2 scores with branch-local normalization, "
            "all five families, and fixed family weights."
        )
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--minimum-branch-cross-section",
        type=int,
        default=MINIMUM_BRANCH_CROSS_SECTION,
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        manifest = build_scores(
            input_path=args.input,
            output_path=args.output,
            manifest_path=args.manifest,
            minimum_branch_cross_section=args.minimum_branch_cross_section,
        )
    except (OSError, ValueError, AssertionError) as exc:
        print(f"Model V2 scoring failed: {exc}", file=sys.stderr)
        return 1
    counts = manifest["counts"]
    print(
        "Model V2 scoring complete: "
        f"rows={counts['rows']} eligible={counts['eligible_rows']} "
        f"excluded={counts['excluded_rows']}"
    )
    print(f"Score ledger: {manifest['output']['path']}")
    print(f"Manifest: {repository_relative_path(args.manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
