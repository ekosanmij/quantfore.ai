import hashlib
import json

from pipelines.plan_sec_filing_evidence import build_filing_plan


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n")


def test_filing_plan_deduplicates_fact_observations(tmp_path):
    facts = {
        "cik": 1,
        "facts": {
            "us-gaap": {
                "Assets": {
                    "units": {
                        "USD": [
                            {"form": "10-K", "filed": "2020-02-01", "accn": "0000000001-20-000001"},
                            {"form": "10-K", "filed": "2020-02-01", "accn": "0000000001-20-000001"},
                        ]
                    }
                }
            }
        },
    }
    facts_path = tmp_path / "CIK0000000001/facts.json"
    write_json(facts_path, facts)
    facts_hash = hashlib.sha256(facts_path.read_bytes()).hexdigest()
    write_json(
        tmp_path / "CIK0000000001/complete.json",
        {"cik": "0000000001", "companyfacts": {"path": "facts.json", "sha256": facts_hash}},
    )
    registry_path = tmp_path / "registry.json"
    write_json(
        registry_path,
        {"status": "complete", "complete_cik_count": 1, "identifier_registry_sha256": "a" * 64},
    )
    registry_hash = hashlib.sha256(registry_path.read_bytes()).hexdigest()

    plan = build_filing_plan(
        sec_root=tmp_path,
        expected_registry_hash=registry_hash,
    )

    assert plan["filing_count"] == 1
    assert plan["filings"][0]["source_url"].endswith(
        "/000000000120000001/0000000001-20-000001-index.html"
    )
