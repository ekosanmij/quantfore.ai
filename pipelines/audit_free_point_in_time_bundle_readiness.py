"""Audit local readiness for the free composite point-in-time equity bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Optional, Sequence


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRICE_ROOT = REPO_ROOT / "data/raw/free-point-in-time/tiingo-prices-v1"
DEFAULT_IDENTIFIER_REGISTRY = (
    REPO_ROOT / "data/raw/free-point-in-time/resolved-identifiers-v1.json"
)
DEFAULT_BUNDLE_PATH = (
    REPO_ROOT / "data/raw/free-point-in-time/composite-equity-bundle-v1"
)
DEFAULT_LICENSE_EVIDENCE = (
    REPO_ROOT
    / "data/raw/free-point-in-time/license-evidence/personal-internal-use-v1.json"
)
DEFAULT_LINEAGE_REGISTRY = (
    REPO_ROOT / "data/raw/free-point-in-time/lineage-evidence-v1/registry.json"
)
DEFAULT_LINEAGE_PRICE_ROOT = (
    REPO_ROOT / "data/raw/free-point-in-time/lineage-prices-v1"
)
DEFAULT_RECONCILED_LINEAGE = (
    REPO_ROOT / "data/raw/free-point-in-time/reconciled-lineage-v1.json"
)
DEFAULT_MEMBERSHIP_SAMPLES = (
    REPO_ROOT / "data/raw/free-point-in-time/wikipedia-membership-samples-v1/registry.json"
)
DEFAULT_PRICE_EXCLUSIONS = (
    REPO_ROOT / "data/raw/free-point-in-time/price-exclusions-v1.json"
)
DEFAULT_DELISTING_EVIDENCE = (
    REPO_ROOT / "data/raw/free-point-in-time/delisting-evidence-v1.json"
)
DEFAULT_JSON_OUTPUT = (
    REPO_ROOT / "reports/data-audits/free-pit-bundle-readiness-v1.json"
)


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


def build_readiness(
    *,
    plan_path: Path,
    expected_plan_hash: str,
    price_root: Path,
    identifier_registry_path: Path,
    bundle_path: Path,
    license_evidence_path: Optional[Path] = None,
    lineage_registry_path: Optional[Path] = None,
    lineage_price_root: Optional[Path] = None,
    reconciled_lineage_path: Optional[Path] = None,
    membership_samples_path: Optional[Path] = None,
    price_exclusions_path: Optional[Path] = None,
    delisting_evidence_path: Optional[Path] = None,
) -> dict[str, Any]:
    plan_body = plan_path.read_bytes()
    plan_hash = _sha256(plan_body)
    if plan_hash != expected_plan_hash.lower():
        raise ValueError("private acquisition plan SHA-256 does not match")
    plan = json.loads(plan_body)
    expected_by_batch = {
        int(row["batch_number"]): list(row["symbols"])
        for row in plan["safe_acquisition_batches"]
    }
    expected_symbols = {
        (batch, ticker)
        for batch, tickers in expected_by_batch.items()
        for ticker in tickers
    }
    completed_symbols: set[tuple[int, str]] = set()
    price_rows = 0
    for path in sorted(price_root.glob("batch-*/*/complete.json")):
        record = json.loads(path.read_text())
        identity = (int(record["batch_number"]), str(record["ticker"]))
        if identity not in expected_symbols or identity in completed_symbols:
            raise ValueError(f"unexpected or duplicate price completion: {identity}")
        if record.get("acquisition_plan_sha256") != plan_hash:
            raise ValueError(f"price completion has wrong plan hash: {identity}")
        for page in record.get("pages", []):
            page_path = path.parent / page["path"]
            if not page_path.is_file() or _sha256(page_path.read_bytes()) != page["sha256"]:
                raise ValueError(f"price page does not reproduce: {identity}")
        completed_symbols.add(identity)
        price_rows += int(record["price_row_count"])

    batches = []
    for batch, tickers in sorted(expected_by_batch.items()):
        complete = sum((batch, ticker) in completed_symbols for ticker in tickers)
        batches.append(
            {
                "batch_number": batch,
                "expected_symbol_count": len(tickers),
                "complete_symbol_count": complete,
                "status": "complete" if complete == len(tickers) else "in_progress",
            }
        )

    identifier_body = identifier_registry_path.read_bytes()
    identifiers = json.loads(identifier_body)
    if identifiers.get("acquisition_plan_sha256") != plan_hash:
        raise ValueError("identifier registry has wrong plan hash")
    identity_counts = {
        key: int(identifiers.get(key, 0))
        for key in (
            "requested_ticker_count",
            "processed_ticker_count",
            "resolved_ticker_count",
            "lineage_required_ticker_count",
            "ambiguous_ticker_count",
            "unresolved_ticker_count",
        )
    }
    expected_price_count = len(expected_symbols)
    completed_price_count = len(completed_symbols)
    verified_identity_count = 0
    verified_identity_tickers: list[str] = []
    verified_price_lineage_count = 0
    verified_price_lineage_tickers: list[str] = []
    lineage_registry_hash = None
    if lineage_registry_path is not None and lineage_registry_path.is_file():
        lineage_body = lineage_registry_path.read_bytes()
        lineage = json.loads(lineage_body)
        if (
            lineage.get("acquisition_plan_sha256") != plan_hash
            or lineage.get("identifier_registry_sha256") != _sha256(identifier_body)
        ):
            raise ValueError("lineage evidence registry has wrong source hashes")
        direct_plan = lineage.get("direct_price_plan", {})
        direct_plan_path = lineage_registry_path.parent / str(direct_plan.get("path", ""))
        if (
            not direct_plan_path.is_file()
            or _sha256(direct_plan_path.read_bytes()) != direct_plan.get("sha256")
        ):
            raise ValueError("direct lineage price plan does not reproduce")
        direct_rows = [
            row
            for row in lineage.get("episodes", [])
            if row.get("status") == "direct_ticker_verified"
        ]
        direct_batch_registry = (
            lineage_price_root / "batch-001/batch-registry.json"
            if lineage_price_root is not None
            else None
        )
        if direct_batch_registry is not None and direct_batch_registry.is_file():
            batch = json.loads(direct_batch_registry.read_text())
            if (
                batch.get("status") != "complete"
                or batch.get("acquisition_plan_sha256") != direct_plan["sha256"]
                or batch.get("complete_symbol_count") != len(direct_rows)
            ):
                raise ValueError("direct lineage price batch is incomplete")
            verified_identity_tickers = sorted(row["ticker"] for row in direct_rows)
            verified_identity_count = len(verified_identity_tickers)
            verified_price_lineage_tickers = list(verified_identity_tickers)
            verified_price_lineage_count = verified_identity_count
        lineage_registry_hash = _sha256(lineage_body)
    reconciled_lineage_hash = None
    unresolved_episode_ids: set[str] = set()
    if reconciled_lineage_path is not None and reconciled_lineage_path.is_file():
        reconciled_body = reconciled_lineage_path.read_bytes()
        reconciled = json.loads(reconciled_body)
        if reconciled.get("lineage_registry_sha256") != lineage_registry_hash:
            raise ValueError("reconciled lineage has wrong source registry hash")
        identity_rows = [
            row
            for row in reconciled.get("episodes", [])
            if row.get("status")
            in {"ready_for_bundle", "identity_verified_price_missing"}
        ]
        additional_identity_rows = [
            row
            for row in reconciled.get("additional_identities", [])
            if row.get("status") == "identity_verified"
        ]
        ready_rows = [row for row in identity_rows if row["status"] == "ready_for_bundle"]
        unresolved_episode_ids = {
            row["episode_id"]
            for row in reconciled.get("episodes", [])
            if row.get("status") != "ready_for_bundle"
        }
        for row in ready_rows:
            for price in row["selected_identity"]["usable_prices"]:
                completion_path = Path(price["completion_path"])
                if (
                    not completion_path.is_file()
                    or _sha256(completion_path.read_bytes())
                    != price["completion_sha256"]
                ):
                    raise ValueError("reconciled lineage price evidence does not reproduce")
        verified_identity_tickers = sorted(
            {row["ticker"] for row in [*identity_rows, *additional_identity_rows]}
        )
        verified_identity_count = len(verified_identity_tickers)
        verified_price_lineage_tickers = sorted(row["ticker"] for row in ready_rows)
        verified_price_lineage_count = len(verified_price_lineage_tickers)
        reconciled_lineage_hash = _sha256(reconciled_body)
    blockers = []
    if completed_price_count != expected_price_count:
        blockers.append(
            {
                "code": "incomplete_price_acquisition",
                "message": f"{completed_price_count} of {expected_price_count} safe symbols are frozen",
            }
        )
    unresolved_identity_count = (
        identity_counts["lineage_required_ticker_count"]
        + identity_counts["ambiguous_ticker_count"]
        + identity_counts["unresolved_ticker_count"]
    )
    unresolved_identity_count = max(0, unresolved_identity_count - verified_identity_count)
    if unresolved_identity_count:
        blockers.append(
            {
                "code": "incomplete_permanent_identity_lineage",
                "message": f"{unresolved_identity_count} ticker labels still require identity resolution",
            }
        )
    exclusion_evidence: dict[str, Any] = {
        "path": None,
        "sha256": None,
        "exclusion_count": 0,
        "minimum_monthly_coverage": None,
        "coverage_gate_passed": False,
        "failed_month_count": None,
        "first_sustained_passing_month": None,
    }
    excluded_episode_count = 0
    if price_exclusions_path is not None and price_exclusions_path.is_file():
        exclusion_body = price_exclusions_path.read_bytes()
        exclusion_document = json.loads(exclusion_body)
        if not (
            exclusion_document.get("schema_version") == "free-pit-price-exclusions-v1"
            and exclusion_document.get("reconciled_lineage_sha256")
            == reconciled_lineage_hash
            and exclusion_document.get("lineage_registry_sha256")
            == lineage_registry_hash
        ):
            raise ValueError("price exclusion evidence is invalid or stale")
        exclusion_ids = {row["episode_id"] for row in exclusion_document["exclusions"]}
        if exclusion_document.get("unaccounted_episode_count") != 0:
            raise ValueError("price exclusions leave active-window episodes unaccounted")
        for row in exclusion_document["exclusions"]:
            evidence_path = Path(row["evidence_path"])
            if not evidence_path.is_file() or _sha256(evidence_path.read_bytes()) != row["evidence_sha256"]:
                raise ValueError("price exclusion evidence does not reproduce")
        excluded_episode_count = len(exclusion_ids)
        exclusion_evidence = {
            "path": str(price_exclusions_path.resolve()),
            "sha256": _sha256(exclusion_body),
            "exclusion_count": excluded_episode_count,
            "minimum_monthly_coverage": exclusion_document["minimum_monthly_coverage"],
            "coverage_gate_passed": exclusion_document["coverage_gate_passed"],
            "failed_month_count": exclusion_document["failed_month_count"],
            "first_sustained_passing_month": exclusion_document[
                "first_sustained_passing_month"
            ],
        }
        if not exclusion_document["coverage_gate_passed"]:
            blockers.append(
                {
                    "code": "price_exclusion_coverage_failed",
                    "message": (
                        "explicit price exclusions reduce minimum monthly coverage to "
                        f"{exclusion_document['minimum_monthly_coverage']}, below 0.95"
                    ),
                }
            )
    unresolved_episodes = 0 if excluded_episode_count else max(
        0, len(plan.get("unresolved_episodes", [])) - verified_price_lineage_count
    )
    if unresolved_episodes:
        blockers.append(
            {
                "code": "unresolved_price_alias_episodes",
                "message": f"{unresolved_episodes} membership episodes require price/corporate-action lineage",
            }
        )
    license_evidence: dict[str, Any] = {
        "confirmed": False,
        "scope": None,
        "path": None,
        "sha256": None,
    }
    if license_evidence_path is None or not license_evidence_path.is_file():
        blockers.append(
            {
                "code": "license_scope_unconfirmed",
                "message": "personal/internal-use licence evidence is not frozen",
            }
        )
    else:
        license_body = license_evidence_path.read_bytes()
        license_document = json.loads(license_body)
        if not (
            license_document.get("schema_version")
            == "free-pit-personal-use-confirmation-v1"
            and license_document.get("confirmed_by_user") is True
            and license_document.get("use_scope") == "personal_internal_research"
            and license_document.get("commercial_use") is False
            and license_document.get("redistribution_permitted_or_intended") is False
        ):
            raise ValueError("personal/internal-use licence evidence is invalid")
        terms = license_document.get("tiingo_terms", {})
        terms_path = license_evidence_path.parent / str(terms.get("path", ""))
        if (
            not terms_path.is_file()
            or _sha256(terms_path.read_bytes()) != terms.get("sha256")
        ):
            raise ValueError("frozen Tiingo terms do not reproduce")
        license_evidence = {
            "confirmed": True,
            "scope": "personal_internal_research",
            "path": str(license_evidence_path.resolve()),
            "sha256": _sha256(license_body),
            "commercial_use": False,
            "redistribution_permitted_or_intended": False,
            "terms_sha256": terms["sha256"],
        }
    membership_reconciliation: dict[str, Any] = {
        "complete": False,
        "path": None,
        "sha256": None,
        "sample_count": 0,
    }
    if membership_samples_path is None or not membership_samples_path.is_file():
        blockers.append(
            {
                "code": "independent_membership_reconciliation_pending",
                "message": "three independent revision-pinned membership samples are not frozen",
            }
        )
    else:
        membership_body = membership_samples_path.read_bytes()
        membership = json.loads(membership_body)
        if not (
            membership.get("schema_version")
            == "free-pit-wikipedia-membership-samples-v1"
            and membership.get("sample_count") == 3
            and membership.get("all_identity_exact_match") is True
        ):
            raise ValueError("independent membership samples do not reconcile")
        for sample in membership["samples"]:
            raw_path = membership_samples_path.parent / sample["path"]
            if not raw_path.is_file() or _sha256(raw_path.read_bytes()) != sample["sha256"]:
                raise ValueError("Wikipedia membership sample does not reproduce")
        membership_reconciliation = {
            "complete": True,
            "path": str(membership_samples_path.resolve()),
            "sha256": _sha256(membership_body),
            "sample_count": 3,
            "samples": membership["samples"],
        }
    delisting_evidence: dict[str, Any] = {
        "complete": False,
        "path": None,
        "sha256": None,
        "ended_listing_count": 0,
        "unavailable_outcome_count": 0,
    }
    if delisting_evidence_path is None or not delisting_evidence_path.is_file():
        blockers.append(
            {
                "code": "delisting_evidence_pending",
                "message": "delisting dates and any available terminal returns are not yet frozen",
            }
        )
    else:
        delisting_body = delisting_evidence_path.read_bytes()
        delisting = json.loads(delisting_body)
        if not (
            delisting.get("schema_version") == "free-pit-delisting-evidence-v1"
            and delisting.get("status") == "complete"
            and delisting.get("reconciled_lineage_sha256") == reconciled_lineage_hash
            and delisting.get("price_exclusions_sha256") == exclusion_evidence["sha256"]
            and delisting.get("unavailable_outcome_count") == excluded_episode_count
            and delisting.get("source_capability", {}).get(
                "separate_delisting_return_available"
            )
            is False
        ):
            raise ValueError("delisting evidence is invalid or stale")
        for row in delisting["bound_price_completions"]:
            completion_path = Path(row["completion_path"])
            if not completion_path.is_file() or _sha256(completion_path.read_bytes()) != row["completion_sha256"]:
                raise ValueError("delisting price completion does not reproduce")
        for row in delisting["unavailable_outcomes"]:
            metadata_path = Path(row["evidence_path"])
            if not metadata_path.is_file() or _sha256(metadata_path.read_bytes()) != row["evidence_sha256"]:
                raise ValueError("unavailable delisting metadata does not reproduce")
        delisting_evidence = {
            "complete": True,
            "path": str(delisting_evidence_path.resolve()),
            "sha256": _sha256(delisting_body),
            "ended_listing_count": delisting["ended_listing_count"],
            "unavailable_outcome_count": delisting["unavailable_outcome_count"],
            "separate_delisting_return_available": False,
        }
    manifest_path = bundle_path / "manifest.json"
    manifest_sha256 = (
        _sha256(manifest_path.read_bytes()) if manifest_path.is_file() else None
    )
    ready = not blockers
    return {
        "schema_version": "free-pit-bundle-readiness-v1",
        "status": "ready" if ready else "in_progress",
        "claims_eligible": False,
        "private_acquisition_plan": {
            "path": str(plan_path.resolve()),
            "sha256": plan_hash,
        },
        "prices": {
            "root": str(price_root.resolve()),
            "expected_symbol_count": expected_price_count,
            "complete_symbol_count": completed_price_count,
            "frozen_price_row_count": price_rows,
            "batches": batches,
        },
        "identifiers": {
            "registry_path": str(identifier_registry_path.resolve()),
            "registry_sha256": _sha256(identifier_body),
            **identity_counts,
        },
        "lineage": {
            "registry_path": (
                str(lineage_registry_path.resolve())
                if lineage_registry_path is not None and lineage_registry_path.is_file()
                else None
            ),
            "registry_sha256": lineage_registry_hash,
            "reconciled_path": (
                str(reconciled_lineage_path.resolve())
                if reconciled_lineage_path is not None
                and reconciled_lineage_path.is_file()
                else None
            ),
            "reconciled_sha256": reconciled_lineage_hash,
            "verified_identity_count": verified_identity_count,
            "verified_identity_tickers": verified_identity_tickers,
            "verified_price_lineage_count": verified_price_lineage_count,
            "verified_price_lineage_tickers": verified_price_lineage_tickers,
            "remaining_identity_count": unresolved_identity_count,
            "remaining_episode_count": unresolved_episodes,
            "excluded_episode_count": excluded_episode_count,
        },
        "price_exclusions": exclusion_evidence,
        "delisting_evidence": delisting_evidence,
        "license": license_evidence,
        "membership_reconciliation": membership_reconciliation,
        "bundle": {
            "planned_path": str(bundle_path.resolve()),
            "manifest_sha256": manifest_sha256,
            "created": manifest_sha256 is not None,
        },
        "blockers": blockers,
    }


def render_markdown(report: dict[str, Any]) -> str:
    prices = report["prices"]
    identifiers = report["identifiers"]
    lineage = report["lineage"]
    lines = [
        "# Free Point-in-Time Bundle Readiness v1",
        "",
        f"Status: **{report['status'].upper()}**",
        "",
        f"- Acquisition plan SHA-256: `{report['private_acquisition_plan']['sha256']}`",
        f"- Frozen prices: {prices['complete_symbol_count']} / {prices['expected_symbol_count']} symbols ({prices['frozen_price_row_count']} rows)",
        f"- Unique permanent-ID mappings: {identifiers['resolved_ticker_count']} / {identifiers['requested_ticker_count']}",
        f"- Additional historical identities verified: {lineage['verified_identity_count']}",
        f"- Historical episodes with verified price lineage: {lineage['verified_price_lineage_count']}",
        f"- Planned bundle path: `{report['bundle']['planned_path']}`",
        (
            f"- Candidate manifest SHA-256: `{report['bundle']['manifest_sha256']}`"
            if report["bundle"]["created"]
            else "- Candidate manifest SHA-256: not created"
        ),
        "",
        "## Blocking findings",
        "",
    ]
    if report["blockers"]:
        lines.extend(
            f"- `{row['code']}`: {row['message']}." for row in report["blockers"]
        )
    else:
        lines.append("- None.")
    return "\n".join(lines) + "\n"


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit free PIT bundle readiness.")
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--expected-plan-hash", required=True)
    parser.add_argument("--price-root", type=Path, default=DEFAULT_PRICE_ROOT)
    parser.add_argument(
        "--identifier-registry", type=Path, default=DEFAULT_IDENTIFIER_REGISTRY
    )
    parser.add_argument("--bundle-path", type=Path, default=DEFAULT_BUNDLE_PATH)
    parser.add_argument(
        "--license-evidence", type=Path, default=DEFAULT_LICENSE_EVIDENCE
    )
    parser.add_argument(
        "--lineage-registry", type=Path, default=DEFAULT_LINEAGE_REGISTRY
    )
    parser.add_argument(
        "--lineage-price-root", type=Path, default=DEFAULT_LINEAGE_PRICE_ROOT
    )
    parser.add_argument(
        "--reconciled-lineage", type=Path, default=DEFAULT_RECONCILED_LINEAGE
    )
    parser.add_argument(
        "--membership-samples", type=Path, default=DEFAULT_MEMBERSHIP_SAMPLES
    )
    parser.add_argument(
        "--price-exclusions", type=Path, default=DEFAULT_PRICE_EXCLUSIONS
    )
    parser.add_argument(
        "--delisting-evidence", type=Path, default=DEFAULT_DELISTING_EVIDENCE
    )
    parser.add_argument("--json-output", type=Path, default=DEFAULT_JSON_OUTPUT)
    parser.add_argument("--markdown-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    try:
        report = build_readiness(
            plan_path=args.plan,
            expected_plan_hash=args.expected_plan_hash,
            price_root=args.price_root,
            identifier_registry_path=args.identifier_registry,
            bundle_path=args.bundle_path,
            license_evidence_path=args.license_evidence,
            lineage_registry_path=args.lineage_registry,
            lineage_price_root=args.lineage_price_root,
            reconciled_lineage_path=args.reconciled_lineage,
            membership_samples_path=args.membership_samples,
            price_exclusions_path=args.price_exclusions,
            delisting_evidence_path=args.delisting_evidence,
        )
        markdown_path = args.markdown_output or args.json_output.with_suffix(".md")
        _atomic_write(args.json_output, _json_bytes(report))
        _atomic_write(markdown_path, render_markdown(report).encode())
    except (KeyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"free PIT readiness audit failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"status={report['status']} prices={report['prices']['complete_symbol_count']}/"
        f"{report['prices']['expected_symbol_count']} blockers={len(report['blockers'])}"
    )
    return 0 if report["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
