import hashlib
import json

from pipelines.audit_free_point_in_time_bundle_readiness import build_readiness


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + "\n")


def test_readiness_reports_partial_bundle_without_creating_manifest(tmp_path):
    plan_path = tmp_path / "plan.json"
    write_json(
        plan_path,
        {
            "safe_acquisition_batches": [
                {"batch_number": 1, "symbols": ["AAA", "BBB"]}
            ],
            "unresolved_episodes": [{"ticker": "OLD"}],
        },
    )
    plan_hash = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    price_root = tmp_path / "prices"
    ticker_dir = price_root / "batch-001/AAA"
    page = ticker_dir / "page.json"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"[]")
    write_json(
        ticker_dir / "complete.json",
        {
            "batch_number": 1,
            "ticker": "AAA",
            "acquisition_plan_sha256": plan_hash,
            "price_row_count": 1,
            "pages": [
                {"path": "page.json", "sha256": hashlib.sha256(b"[]").hexdigest()}
            ],
        },
    )
    identifiers = tmp_path / "identifiers.json"
    write_json(
        identifiers,
        {
            "acquisition_plan_sha256": plan_hash,
            "requested_ticker_count": 3,
            "processed_ticker_count": 3,
            "resolved_ticker_count": 1,
            "lineage_required_ticker_count": 1,
            "ambiguous_ticker_count": 1,
            "unresolved_ticker_count": 0,
        },
    )

    report = build_readiness(
        plan_path=plan_path,
        expected_plan_hash=plan_hash,
        price_root=price_root,
        identifier_registry_path=identifiers,
        bundle_path=tmp_path / "bundle",
    )

    assert report["status"] == "in_progress"
    assert report["prices"]["complete_symbol_count"] == 1
    assert report["bundle"]["created"] is False
    assert report["bundle"]["manifest_sha256"] is None
    assert {row["code"] for row in report["blockers"]} >= {
        "incomplete_price_acquisition",
        "incomplete_permanent_identity_lineage",
        "unresolved_price_alias_episodes",
        "delisting_evidence_pending",
        "license_scope_unconfirmed",
    }
