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
    if unresolved_identity_count:
        blockers.append(
            {
                "code": "incomplete_permanent_identity_lineage",
                "message": f"{unresolved_identity_count} ticker labels still require identity resolution",
            }
        )
    unresolved_episodes = len(plan.get("unresolved_episodes", []))
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
    blockers.extend(
        [
            {
                "code": "delisting_evidence_pending",
                "message": "delisting dates and any available terminal returns are not yet frozen",
            },
            {
                "code": "independent_membership_reconciliation_pending",
                "message": "the three secondary membership samples do not yet agree exactly",
            },
        ]
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
        "license": license_evidence,
        "bundle": {
            "planned_path": str(bundle_path.resolve()),
            "manifest_sha256": None,
            "created": False,
        },
        "blockers": blockers,
    }


def render_markdown(report: dict[str, Any]) -> str:
    prices = report["prices"]
    identifiers = report["identifiers"]
    lines = [
        "# Free Point-in-Time Bundle Readiness v1",
        "",
        f"Status: **{report['status'].upper()}**",
        "",
        f"- Acquisition plan SHA-256: `{report['private_acquisition_plan']['sha256']}`",
        f"- Frozen prices: {prices['complete_symbol_count']} / {prices['expected_symbol_count']} symbols ({prices['frozen_price_row_count']} rows)",
        f"- Unique permanent-ID mappings: {identifiers['resolved_ticker_count']} / {identifiers['requested_ticker_count']}",
        f"- Planned bundle path: `{report['bundle']['planned_path']}`",
        "- Final manifest SHA-256: not created",
        "",
        "## Blocking findings",
        "",
    ]
    lines.extend(
        f"- `{row['code']}`: {row['message']}." for row in report["blockers"]
    )
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
