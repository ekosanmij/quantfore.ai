from __future__ import annotations

import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
PIPELINES_ROOT = REPOSITORY_ROOT / "pipelines"
if str(PIPELINES_ROOT) not in sys.path:
    sys.path.insert(0, str(PIPELINES_ROOT))

import audit_model_v3_expanded_universe_feasibility as audit_pipeline  # noqa: E402


NOW = datetime(2026, 7, 14, 16, 0, tzinfo=timezone.utc)


def _write_rebuild(path: Path, rows: list[dict]) -> dict:
    raw = b"".join(
        (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for row in rows
    )
    body = gzip.compress(raw, mtime=0)
    path.write_bytes(body)
    return {
        "path": path.name,
        "sha256": hashlib.sha256(body).hexdigest(),
        "row_count": len(rows),
    }


def _rows(boundary: str, names_per_branch: int = 25) -> list[dict]:
    branches = audit_pipeline.design_v3.BRANCHES[:5]
    sectors = [
        "Industrials",
        "Financials",
        "Insurance",
        "Real Estate",
        "Information Technology",
    ]
    rows = []
    for branch_index, branch in enumerate(branches):
        for index in range(names_per_branch):
            security_id = f"SEC-{branch_index:02d}-{index:03d}"
            rows.append(
                {
                    "information_boundary": boundary,
                    "security_id": security_id,
                    "issuer_id": f"ISSUER-{security_id}",
                    "historical_ticker": f"T{branch_index}{index}",
                    "domicile": "US",
                    "primary_exchange": "NASDAQ",
                    "security_type": "COMMON_STOCK",
                    "membership_effective_from": "2017-01-01",
                    "membership_effective_to": None,
                    "identity_effective_from": "2017-01-01",
                    "identity_effective_to": None,
                    "source_available_at": "2017-01-30T23:59:59Z",
                    "branch": branch,
                    "gics_sector": sectors[branch_index],
                    "source_snapshot_ids": ["snapshot-1"],
                    "structural_disposition": "EXPECTED_MEMBER",
                    "reason_code": "STRUCTURALLY_ELIGIBLE",
                }
            )
    return rows


def _manifest(tmp_path: Path, rows: list[dict], boundary: str) -> Path:
    rebuild_1 = _write_rebuild(tmp_path / "rebuild-1.jsonl.gz", rows)
    rebuild_2 = _write_rebuild(tmp_path / "rebuild-2.jsonl.gz", rows)
    manifest = {
        "schema_version": "model-v3-expanded-universe-membership-evidence-v1",
        "universe_id": "us-listed-common-equity-pit-v1",
        "claims_eligible": False,
        "outcomes_accessed": False,
        "prohibited_columns_read": [],
        "information_boundaries": [boundary],
        "rebuilds": [rebuild_1, rebuild_2],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_missing_expanded_universe_input_fails_closed():
    report = audit_pipeline.build_audit(
        repository_root=REPOSITORY_ROOT,
        generated_at=NOW,
        candidate_manifest=Path("data/raw/model-v3/does-not-exist.json"),
    )

    assert report["decision"] == "FAIL_LINEAGE_OR_REPRODUCIBILITY"
    assert report["status"] == "BLOCKED_MISSING_EXPANDED_UNIVERSE_INPUT"
    assert report["criteria"]["F0"]["passed"] is True
    assert all(
        report["criteria"][gate]["passed"] is False
        for gate in ("F1", "F2", "F3", "F4", "F5", "F6", "F7")
    )
    assert report["authorization"]["data_acquisition_authorized"] is False
    assert report["authorization"]["score_rebuild_authorized"] is False


def test_qualifying_fixture_passes_all_structural_gates(tmp_path):
    boundary = "2017-01-31"
    manifest = _manifest(tmp_path, _rows(boundary), boundary)

    report = audit_pipeline.build_audit(
        repository_root=REPOSITORY_ROOT,
        generated_at=NOW,
        candidate_manifest=manifest,
        expected_boundaries=[boundary],
    )

    assert report["decision"] == "PASS_STRUCTURALLY_FEASIBLE"
    assert all(result["passed"] for result in report["criteria"].values())
    assert report["criteria"]["F1"]["observed"] == 25
    assert report["criteria"]["F2"]["observed"] == 20
    assert report["authorization"]["data_acquisition_authorized"] is True
    assert report["authorization"]["score_rebuild_authorized"] is False


def test_small_populated_branch_fails_without_being_deactivated(tmp_path):
    boundary = "2017-01-31"
    rows = _rows(boundary)
    small_branch = audit_pipeline.design_v3.BRANCHES[5]
    rows.extend(
        {
            **row,
            "security_id": f"SMALL-{index}",
            "issuer_id": f"SMALL-ISSUER-{index}",
            "historical_ticker": f"S{index}",
            "branch": small_branch,
        }
        for index, row in enumerate(rows[:10])
    )
    manifest = _manifest(tmp_path, rows, boundary)

    report = audit_pipeline.build_audit(
        repository_root=REPOSITORY_ROOT,
        generated_at=NOW,
        candidate_manifest=manifest,
        expected_boundaries=[boundary],
    )

    assert report["decision"] == "FAIL_UNIVERSE_STILL_TOO_SMALL"
    assert report["criteria"]["F1"]["observed"] == 10
    assert report["criteria"]["F1"]["passed"] is False
    assert small_branch in report["monthly"][0]["represented_active_branches"]


def test_prohibited_outcome_field_fails_lineage_gate(tmp_path):
    boundary = "2017-01-31"
    rows = _rows(boundary)
    rows[0]["forward_return"] = 0.1
    manifest = _manifest(tmp_path, rows, boundary)

    report = audit_pipeline.build_audit(
        repository_root=REPOSITORY_ROOT,
        generated_at=NOW,
        candidate_manifest=manifest,
        expected_boundaries=[boundary],
    )

    assert report["decision"] == "FAIL_LINEAGE_OR_REPRODUCIBILITY"
    assert report["criteria"]["F0"]["passed"] is False
    assert report["authorization"]["data_acquisition_authorized"] is False


def test_real_blocked_report_reproduces_exactly():
    report = audit_pipeline.verify_audit(repository_root=REPOSITORY_ROOT)

    assert report["status"] == "BLOCKED_MISSING_EXPANDED_UNIVERSE_INPUT"
    assert report["outcomes_accessed"] is False
    assert report["authorization"]["july_2026_backfill_allowed"] is False
