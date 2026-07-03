import hashlib
import json

from pipelines.acquire_sec_filing_evidence import (
    acquire_filing_evidence,
    parse_filing_index,
)


HTML = b"""<!doctype html>
<html><body>
<div class="infoHead">Filing Date</div><div class="info">2020-02-03</div>
<div class="infoHead">Accepted</div><div class="info">2020-02-03 17:12:11</div>
<span class="companyName">Example Corp (Filer) CIK: 0000000123</span>
<p class="identInfo"><acronym title="Standard Industrial Code">SIC</acronym>:
<a href="/cgi-bin/browse-edgar?action=getcompany&SIC=3571">3571</a></p>
Accession 0000000123-20-000001
</body></html>"""


def plan_body():
    return (json.dumps({
        "schema_version": "free-pit-sec-filing-evidence-plan-v1",
        "filings": [{
            "cik": "0000000123",
            "accession": "0000000123-20-000001",
            "form": "10-K",
            "filed": "2020-02-03",
            "source_url": "ignored",
        }],
    }, sort_keys=True) + "\n").encode()


def test_filing_index_extracts_utc_acceptance_and_target_sic():
    parsed = parse_filing_index(
        HTML,
        cik="0000000123",
        accession="0000000123-20-000001",
        expected_filed="2020-02-03",
    )

    assert parsed == {
        "filed": "2020-02-03",
        "planned_filed": "2020-02-03",
        "filed_matches_plan": True,
        "accepted_at": "2020-02-03T22:12:11Z",
        "sic": "3571",
        "sic_available": True,
    }
    mismatch = parse_filing_index(
        HTML,
        cik="0000000123",
        accession="0000000123-20-000001",
        expected_filed="2020-02-04",
    )
    assert mismatch["filed_matches_plan"] is False


def test_filing_acquisition_is_content_addressed_and_resumable(tmp_path):
    class Client:
        calls = 0

        def get(self, url):
            self.calls += 1
            return HTML

    body = plan_body()
    client = Client()
    first = acquire_filing_evidence(
        client=client,
        plan_body=body,
        output_root=tmp_path,
    )
    second = acquire_filing_evidence(
        client=client,
        plan_body=body,
        output_root=tmp_path,
    )

    assert first["complete_filing_count"] == 1
    assert first["sic_available_count"] == 1
    assert second["reused_filing_count"] == 1
    assert client.calls == 1
    completion = next(tmp_path.glob("CIK*/*.complete.json"))
    record = json.loads(completion.read_text())
    raw = completion.parent / record["path"]
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == record["sha256"]


def test_verified_orphan_accession_is_explicitly_accounted_for(tmp_path):
    class Client:
        def get(self, url):
            raise AssertionError("verified unavailable accession must not be fetched")

    result = acquire_filing_evidence(
        client=Client(),
        plan_body=plan_body(),
        output_root=tmp_path,
        verified_unavailable_accessions=["0000000123-20-000001"],
    )

    assert result["status"] == "complete"
    assert result["complete_filing_count"] == 0
    assert result["unavailable_filing_count"] == 1
    assert result["accounted_filing_count"] == 1
    unavailable = next(tmp_path.glob("CIK*/*.unavailable.json"))
    record = json.loads(unavailable.read_text())
    assert record["reason_code"] == "SEC_ARCHIVE_ORPHAN_ACCESSION"
    assert record["fact_use_permitted"] is False
