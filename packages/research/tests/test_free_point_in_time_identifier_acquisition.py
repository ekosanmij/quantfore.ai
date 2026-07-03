import hashlib
import json

import pytest

from pipelines.acquire_free_point_in_time_identifiers import (
    IdentifierAcquisitionError,
    OpenFigiClient,
    _parse_response,
    acquire_identifiers,
)


class Client:
    max_jobs = 2

    def __init__(self):
        self.calls = 0

    def map(self, jobs):
        self.calls += 1
        return json.dumps(
            [
                {
                    "data": [
                        {
                            "ticker": job["idValue"],
                            "marketSector": "Equity",
                            "securityType2": "Common Stock",
                            "shareClassFIGI": f"FIGI-{job['idValue']}",
                        }
                    ]
                }
                for job in jobs
            ]
        ).encode()


def plan():
    return {
        "safe_acquisition_batches": [
            {"batch_number": 1, "symbols": ["AAA", "BRK-B"]}
        ],
        "unresolved_episodes": [{"ticker": "OLD"}],
    }


def test_identifier_download_is_resumable_and_maps_share_classes(tmp_path):
    client = Client()
    first = acquire_identifiers(
        client=client,
        plan=plan(),
        plan_sha256="a" * 64,
        output_root=tmp_path,
        request_delay_seconds=0,
    )
    second = acquire_identifiers(
        client=client,
        plan=plan(),
        plan_sha256="a" * 64,
        output_root=tmp_path,
        request_delay_seconds=0,
    )

    assert client.calls == 2
    assert first["status"] == "complete"
    assert first["resolved_ticker_count"] == 2
    assert first["lineage_required_ticker_count"] == 1
    assert next(
        row for row in first["mappings"] if row["ticker"] == "OLD"
    )["candidate_status"] == "unique"
    assert second["reused_request_count"] == 2
    assert {row["ticker"] for row in second["mappings"]} == {"AAA", "BRK-B", "OLD"}
    record = next(tmp_path.glob("*.complete.json"))
    completion = json.loads(record.read_text())
    body = (tmp_path / completion["path"]).read_bytes()
    assert hashlib.sha256(body).hexdigest() == completion["response_sha256"]


def test_response_rejects_wrong_result_count():
    with pytest.raises(IdentifierAcquisitionError, match="count"):
        _parse_response(b"[]", ["AAA"])


def test_anonymous_client_rejects_more_than_five_jobs():
    with pytest.raises(ValueError, match="1-5"):
        OpenFigiClient().map([{}] * 6)
